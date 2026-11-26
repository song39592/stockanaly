# -*- coding: utf-8 -*-
"""股票价格异常波动（异动 / 严重异常波动）—— **中立纯计算内核**。

被两处复用：① 异动提示页（全市场当日名单）；② 回测执行层的「异动放弃买入」留痕。
两处各写一份必然漂移（同一天一处判异动、一处不判，且两边都不报错），故下沉到
`formulas/`：依赖方向恒为「指标 → formulas ← 策略」。本模块**不取数、不读库、
不联网**，输入 Series / DataFrame，输出同序 DataFrame。

## 口径（逐条写死，别各处自行解释）

### 普通异动（满足任一即当日异动）

1. **价格偏离**（交易所规则 5.4.2）：
   连续 3 个交易日的**累计偏离值**达到 ±阈值 ——

       deviation_3[t] = (close[t] / close[t-3] - 1) - (idx[t] / idx[t-3] - 1)

   即「期末收盘 / 期初前收盘 − 对应分类指数期末 / 期初前收盘」。
   ⚠️ 是 **3 日累计的比值口径**，不是把 3 天涨跌幅简单相加（期初是 *前* 收盘）。
   阈值：主板 ±20%、创业板/科创板 ±30%、ST/*ST ±12%（见 `threshold_for_board`）。
   ⚠️ `close` 必须是**复权口径**（qfq/hfq）——比值要求同一复权基准，期间除权除息
   由复权价天然吸收；用未复权价会让除权日凭空跳出一个假异动。

2. **换手率异动**：连续 3 日日均换手率 ÷ 前 5 日日均换手率 ≥ 30 倍
   **且**这 3 日累计换手率 ≥ 20%。`turnover` 是小数（0.0123 = 1.23%）。

### 严重异常波动（满足任一即严重异动，交易所公布投资者分类交易统计）

- 10 个交易日内出现 **4 次同向异动事件**（`severe_count`）；
- 10 个交易日内存在累计偏离值 ≥ +100% 或 ≤ −50%（`severe_dev10`）；
- 30 个交易日内存在累计偏离值 ≥ +200% 或 ≤ −70%（`severe_dev30`）。

两条偏离判据的窗口是「**N 日以内**」而非「恰好 10/30 日」：对 N = 3…上限逐个算
同一比值口径的偏离值，任一 N 达标即触发（东财对奥精医疗 2026-11-19 用 14 日
−73.98% 触发 30 日 −70% 规则，固定 30 日窗口只有 −58% 会漏掉）。

`severe_count` 的「次」是**异动事件**，不是「异动日」：一旦某日 3 日偏离达标，
下一次事件从**该日起重新起算**（3 个交易日后才可能再达标）。连续 4 天都满足
滚动 3 日偏离只算 1 次事件 —— 逐日计数会把华东重机(002685)、*ST航图(688066)、
*ST瑞茂(600180) 误判成严重异动。北纬科技(002148) 用事件计数得 3 次，正好对应
东财「次日再异动即第 4 次」的预测。

## 无前视 / lag 语义

`state[t]` 只用到 ≤ t 的收盘数据 —— 这是「当日收盘后」的状态，用于**展示**
（提示页名单）或**复盘标注**。若用于驱动 **t+1 的买入决策**，必须传 `lag=1`：
所有输出整体右移一天，`state[t]` 变为「截至 t-1 收盘的异动状态」。
把 `lag=0` 的结果拿去判当天买入就是前视（当天收盘价当天拿不到）。
这与 `formulas.market.trend_state_detail` 的 `lag` 语义一致。

## 「判不出来」不等于「没异动」

数据缺失 / 窗口不足时 `valid=False`，此时 `is_abnormal` / `is_severe` 均为 False。
调用方必须先看 `valid` 再决定是否放行 —— 与 `trend_state_detail` 同理：
「判不出来」应退回基线行为（正常买入），不能当成「判为不异动」而据此下结论。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .market import align_to_days

# 阈值（小数）。**唯一来源**：改口径只改这里，调用方不要各写一份。
THRESHOLD_MAIN = 0.20          # 沪深主板
THRESHOLD_ST = 0.12            # ST / *ST
THRESHOLD_INNOVATION = 0.30    # 创业板 / 科创板 / 北交所
TURNOVER_RATIO = 30.0          # 3日均量 / 前5日均量
TURNOVER_CUM = 0.20            # 3日累计换手率

SEVERE_COUNT_WINDOW = 10       # 10 个交易日内
SEVERE_COUNT_TIMES = 4         # 同向异动次数
SEVERE_DEV10_UP = 1.00         # 10 日 +100%
SEVERE_DEV10_DN = -0.50        # 10 日 -50%
SEVERE_DEV30_UP = 2.00         # 30 日 +200%
SEVERE_DEV30_DN = -0.70        # 30 日 -70%

_ABNORMAL_COLUMNS = (
    "deviation_3", "abnormal_price", "abnormal_turnover", "is_abnormal",
    "direction", "dev10", "dev30", "severe_count", "severe_dev10",
    "severe_dev30", "is_severe", "valid",
    # 预警字段（供「异动预警」页生成「将触发」备注；不改变上面任何判定语义）：
    # event_count_up / event_count_dn：10 日内同向异动事件次数（severe_count 的计数来源）
    # dev3_ratio：|deviation_3| / 阈值（<1 表示距价格异动还差多少）
    # near_abnormal：未触发但接近阈值（默认 ≥0.8 倍）
    "event_count_up", "event_count_dn", "dev3_ratio", "near_abnormal",
)

# 预警：距价格异动阈值的比例达到此值即算「接近」（0.8 = 还差 20% 涨停幅度）
NEAR_ABNORMAL_RATIO = 0.8


def threshold_for_board(board: str, is_st: bool = False) -> float:
    """板块 → 价格偏离阈值（唯一映射）。

    `board` 取 `strategies.core.universe._board_key` 的取值：
    `main` / `chinext` / `star` / `bse`。北交所阈值口径暂无交易所明文，
    这里按创新层 30% 处理（与涨跌幅限制一致），如后续有明文只改这一处。
    """
    if is_st:
        return THRESHOLD_ST
    return THRESHOLD_INNOVATION if str(board) in ("chinext", "star", "bse") else THRESHOLD_MAIN


def _dev_pair(cur_s, cur_i, prev_s, prev_i):
    """Single-window ratio deviation -- the ONLY formula for deviation window."""
    return (cur_s / prev_s - 1.0) - (cur_i / prev_i - 1.0)


def _window_dev_extremes(s: pd.Series, idx: pd.Series,
                         lo: int, hi: int) -> tuple[pd.Series, pd.Series]:
    """对每个 N∈[lo,hi] 算比值偏离，返回逐日 (最大, 最小)。

    用「N 日以内任一达标」而不是「恰好 N 日」—— 东财对奥精医疗用 14 日窗口
    触发 30 日 −70% 规则；固定 30 日会漏。
    """
    sv = s.to_numpy(dtype=float)
    iv = idx.to_numpy(dtype=float)
    n = len(sv)
    lo, hi = int(lo), int(hi)
    with np.errstate(divide="ignore", invalid="ignore"):
        mx_arr = np.full(n, -np.inf)
        mn_arr = np.full(n, np.inf)
        for k in range(lo, min(hi, n - 1) + 1):
            d = _dev_pair(sv[k:], iv[k:], sv[:-k], iv[:-k])
            ok = ~np.isnan(d)          # pandas.max skips NaN, keeps +/-inf
            np.maximum(mx_arr[k:], d, out=mx_arr[k:], where=ok)
            np.minimum(mn_arr[k:], d, out=mn_arr[k:], where=ok)
    mx_arr[~np.isfinite(mx_arr)] = np.nan
    mn_arr[~np.isfinite(mn_arr)] = np.nan
    return pd.Series(mx_arr, index=s.index), pd.Series(mn_arr, index=s.index)


def _abnormal_event_counts(s: pd.Series, idx: pd.Series, threshold: float,
                           eps: float) -> tuple[pd.Series, pd.Series]:
    """异动**事件**计数（逐日 up/dn 的 10 日滚动和），事件之间不重叠。

    某日 3 日偏离达标即记 1 次事件，并把下次事件的起算基准移到当日；连续多日
    满足滚动 3 日偏离只算 1 次。全程只用到 ≤ t 的数据，无前视。
    """
    sv = s.to_numpy(dtype=float)
    iv = idx.to_numpy(dtype=float)
    up = pd.Series(0, index=s.index, dtype=np.int32)
    dn = pd.Series(0, index=s.index, dtype=np.int32)
    base = 0
    for i in range(len(sv)):
        if i - base < 3:
            continue
        if not (np.isfinite(sv[i]) and np.isfinite(iv[i])
                and np.isfinite(sv[base]) and sv[base] > 0
                and np.isfinite(iv[base]) and iv[base] > 0):
            continue
        dev = (sv[i] / sv[base] - 1.0) - (iv[i] / iv[base] - 1.0)
        if abs(dev) >= threshold - eps:
            (up if dev > 0 else dn).iloc[i] = 1
            base = i
    n = int(SEVERE_COUNT_WINDOW)
    return up.rolling(n, min_periods=1).sum(), dn.rolling(n, min_periods=1).sum()


def abnormal_detail(close, idx_close, turnover, *,
                    threshold: float = THRESHOLD_MAIN,
                    turnover_ratio: float = TURNOVER_RATIO,
                    turnover_cum: float = TURNOVER_CUM,
                    lag: int = 0) -> pd.DataFrame:
    """逐日异动状态。**纯计算**：不取数、不读库、不联网。

    参数
    ----
    close : Series
        个股**复权**收盘价，index = 交易日（`YYYY-MM-DD`）。
    idx_close : Series
        对应分类指数的收盘点位，index = 交易日。代码由
        `core.indices.benchmark_index_for(code)` 唯一决定：
        沪主板 sh000002 / 深主板 sz399107 / 创业板 sz399102 / 科创板 sh000688（科创50）。
        ⚠️ 科创板必须用科创50，用科创综指(sh000680) 会与东财差 2~9pp。
        内部按 `market.align_to_days` 前向填充对齐到 `close` 的日期轴。
    turnover : Series
        个股每日换手率（小数），index 同 `close`。可由
        `indicators.data.get_turnover` 提供。
    threshold : float
        价格偏离阈值（见 `threshold_for_board`）。默认主板 0.20。
    lag : int
        0 = 当日状态（展示 / 复盘）；1 = 用于 t+1 决策的**无前视**状态。

    返回
    ----
    DataFrame，index 同 `close`，列见 `_ABNORMAL_COLUMNS`：

    - `deviation_3` / `dev10` / `dev30`：3 / 10 / 30 日累计偏离值（小数）；
    - `abnormal_price` / `abnormal_turnover`：两类普通异动是否触发；
    - `is_abnormal`：两者任一（两列都为 False 时才是 False）；
    - `direction`：+1 = 向上异动、−1 = 向下异动、0 = 未异动 / 判不出来；
    - `severe_count` / `severe_dev10` / `severe_dev30`：三类严重异动（口径见模块 docstring）；
    - `is_severe`：三者任一；
    - `valid`：是否判得出来（指数 / 换手率缺失或窗口不足 → False）；
    - `event_count_up` / `event_count_dn`：10 日窗口内同向异动**事件**次数
      （`severe_count` 用的就是它 ≥4 的判定，现显式输出供「预警」页显示还差几次）；
    - `dev3_ratio`：`|deviation_3| / threshold`，<1 表示还没触发价格异动、
      数值越接近 1 越接近触发；换手型异动不看这个比例；
    - `near_abnormal`：`valid & ~is_abnormal & (dev3_ratio ≥ NEAR_ABNORMAL_RATIO)`
      —— 距价格异动阈值还差不超过 20%，用于「将触发」提示（不是东财规则，
      是产品层的预警定义）。
    """
    s = pd.Series(close).astype(float)
    if getattr(s, "empty", True):
        return pd.DataFrame(columns=_ABNORMAL_COLUMNS, dtype=float)

    t = pd.Series(turnover).astype(float)
    t.index = pd.Index([str(x)[:10] for x in t.index])
    t = t.reindex(s.index)                       # 缺换手率 → NaN → 换手条件不触发

    idx = align_to_days(pd.Series(idx_close).astype(float), [str(d)[:10] for d in s.index])
    idx.index = s.index
    valid = s.notna() & idx.notna() & (idx > 0)

    thr = abs(float(threshold))
    # 浮点容差：0.2 的比值运算常得 0.19999999999999996，严格 >= 会把「恰好达标」
    # 误判为不达标。1e-9 远小于任何有业务意义的阈值差，只吃掉表示误差。
    eps = 1e-9
    out = pd.DataFrame(index=s.index)
    out["deviation_3"] = (s / s.shift(3) - 1.0) - (idx / idx.shift(3) - 1.0)
    valid &= out["deviation_3"].notna()

    # 价格偏离：|累计偏离| ≥ 阈值。方向由偏离符号决定（0 视为无方向）。
    price_hit = out["deviation_3"].abs() >= thr - eps
    price_hit &= valid

    # 换手率异动：3日均 / 前5日均 ≥ 30 且 3日累计 ≥ 20%
    t3 = t.rolling(3, min_periods=3).mean()
    t5_prev = t.shift(3).rolling(5, min_periods=5).mean()
    t3_sum = t.rolling(3, min_periods=3).sum()
    turn_hit = (t3 / t5_prev.replace(0.0, np.nan) >= float(turnover_ratio)) \
        & (t3_sum >= float(turnover_cum)) & valid
    turn_hit = turn_hit.fillna(False)

    out["abnormal_price"] = price_hit.fillna(False).astype(bool)
    out["abnormal_turnover"] = turn_hit.astype(bool)
    out["is_abnormal"] = (out["abnormal_price"] | out["abnormal_turnover"]) & valid
    out["direction"] = np.where(
        out["is_abnormal"] & (out["deviation_3"] > 0), 1,
        np.where(out["is_abnormal"] & (out["deviation_3"] < 0), -1, 0),
    ).astype(np.int8)

    # ---- 严重异常波动 ----
    # 展示用的固定 10/30 日偏离值（arbitrage 复核 / 报告留痕）
    out["dev10"] = (s / s.shift(10) - 1.0) - (idx / idx.shift(10) - 1.0)
    out["dev30"] = (s / s.shift(30) - 1.0) - (idx / idx.shift(30) - 1.0)
    # 触发判定用「N 日以内任一达标」，不是固定窗口（见模块 docstring）
    mx10, mn10 = _window_dev_extremes(s, idx, 3, 10)
    mx30, mn30 = _window_dev_extremes(s, idx, 3, 30)
    severe_dev10 = ((mx10 >= SEVERE_DEV10_UP - eps)
                    | (mn10 <= SEVERE_DEV10_DN + eps)) & valid
    severe_dev30 = ((mx30 >= SEVERE_DEV30_UP - eps)
                    | (mn30 <= SEVERE_DEV30_DN + eps)) & valid

    up_cnt, dn_cnt = _abnormal_event_counts(s, idx, thr, eps)
    need = int(SEVERE_COUNT_TIMES)
    severe_count = ((up_cnt >= need) | (dn_cnt >= need)) & valid

    # 预警字段：事件次数直接暴露（severe_count 就是 up_cnt/dn_cnt >= 4 的布尔）
    out["event_count_up"] = up_cnt.fillna(0).astype(np.int32)
    out["event_count_dn"] = dn_cnt.fillna(0).astype(np.int32)
    out["dev3_ratio"] = (out["deviation_3"].abs() / thr).where(valid)
    out["near_abnormal"] = (
        valid & (~out["is_abnormal"])
        & (out["dev3_ratio"] >= float(NEAR_ABNORMAL_RATIO))
    ).fillna(False).astype(bool)

    out["severe_count"] = severe_count.fillna(False).astype(bool)
    out["severe_dev10"] = severe_dev10.fillna(False).astype(bool)
    out["severe_dev30"] = severe_dev30.fillna(False).astype(bool)
    out["is_severe"] = (out["severe_count"] | out["severe_dev10"] | out["severe_dev30"]) & valid
    out["valid"] = valid.fillna(False).astype(bool)

    out = out[list(_ABNORMAL_COLUMNS)]
    k = max(0, int(lag))
    if k:
        out = out.shift(k)
        # shift 后数值列保持 NaN（判不出来），布尔 / 方向列回落成 False / 0
        # shift 后 bool 列被提升成 object（混了 NaN）。用 eq(True) 而非 fillna：
        # NaN == True → False，语义一致且无 object 降型的 FutureWarning。
        for c in ("abnormal_price", "abnormal_turnover", "is_abnormal",
                  "severe_count", "severe_dev10", "severe_dev30", "is_severe", "valid",
                  "near_abnormal"):
            out[c] = out[c].eq(True)
        # 事件次数 shift 后混 NaN 提升为 float，回落成 int（0 = 判不出来时的安全值）
        for c in ("event_count_up", "event_count_dn"):
            out[c] = out[c].fillna(0).astype(np.int32)
        out["direction"] = out["direction"].eq(1).astype(np.int8) + out["direction"].eq(-1).astype(np.int8) * -1
    return out


def abnormal_state(close, idx_close, turnover, *,
                   threshold: float = THRESHOLD_MAIN,
                   lag: int = 0) -> np.ndarray:
    """`abnormal_detail` 的简版：只返回逐日 `is_abnormal` 的 bool 数组。

    回测执行层「异动放弃买入」用这个即可；需要区分普通 / 严重、方向、偏离值时
    用 `abnormal_detail`。
    """
    return abnormal_detail(close, idx_close, turnover, threshold=threshold,
                           lag=lag)["is_abnormal"].to_numpy(dtype=bool)
