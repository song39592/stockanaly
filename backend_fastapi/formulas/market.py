# -*- coding: utf-8 -*-
"""大盘状态（市场序列 + 趋势判定）—— **中立内核**，指标与策略共用的单一来源。

位于中立内核包 `formulas/`（见该包 `__init__.py`）：它既不是指标、也不是策略、
只是一段「被两处复用的纯计算」。

## 为什么要有这个内核

「大盘状态」这套口径会被**两处**用到：

  ① 统计层的**默认基准**（等权全池买入持有）—— `strategies/core/stats.py`；
  ② **择时类策略**的市场状态输入 —— `strategies/jwzz_cross_timing.py`。

两处各写一份 → 同一口径两个数、两边都不报错（最难查的一类 bug）。
故下沉到 `formulas/`：依赖方向恒为「指标 → formulas ← 策略」。

## 口径（逐条写死，别各处自行解释）

* **池等权指数**：按日对**全部成分**取日收益的算术平均（等价于「每日再平衡的
  等权组合」），再累乘成指数曲线。
  停牌 / 无行情的票当日收益按 **0** 计入（不是剔除）—— 与统计层的默认基准同源。
* **趋势判定**：`state[t] = close[t-1] > MA(period)[t-1]`，
  其中 `MA(period)[t-1] = mean(close[t-period .. t-1])`。

  ⚠️ **必须带滞后 1 天**：决策日 `t` 只允许使用 **≤ t-1** 的信息。
  写成 `close[t] > MA[t]` 就是**前视**（用当天收盘判当天要下的仓位）——
  在回测里会凭空多出一段 alpha，实盘则是当天根本拿不到的信号。

## 「大盘」用哪条序列

本模块**不生产**大盘序列，只定义「给定一条序列怎么判状态」。
序列本身有两个来源（见 `strategies/core/data.py`）：

  * `pool`：回测池等权指数 —— **内生**（池子涨 → 看多 → 买的就是这池子里的票），
    且**随回测池变化**（换池 = 换信号），只适合做对照；
  * 真指数（上证 / 沪深300 / 中证1000 / 中证全指 / 深证成指）—— **外生**，
    与回测池无关，跨池可比 → **实盘与横向对比用这个**。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def equal_weight_returns(returns_matrix) -> np.ndarray:
    """等权组合的日收益：逐日对**全部**成分取算术平均。

    `returns_matrix`：T×C 的日收益矩阵（numpy 数组或 DataFrame）。
    NaN 一律按 **0** 计入（停牌 / 无行情的票当日平盘，而不是从等权里消失）——
    否则「等权」的权重分母每天都在变，同一条曲线会因成分缺失而漂移。

    返回长度 T 的 numpy 数组。
    """
    arr = np.asarray(returns_matrix)
    if arr.ndim == 1:
        return np.nan_to_num(arr.astype(float, copy=False), nan=0.0, posinf=0.0, neginf=0.0)
    if arr.size == 0:
        return np.zeros((0,), dtype=float)
    if arr.dtype.kind != "f":
        arr = arr.astype(float)
    if not np.isfinite(arr).all():
        # 只有真的脏了才复制一份来清洗：全市场矩阵是 float32 的十几 MB，
        # 无脑 astype(float64) 会凭空多占一倍内存。
        arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)
    return arr.mean(axis=1)


def pool_index_from_closes(closes: pd.DataFrame) -> pd.Series:
    """由收盘价矩阵算「等权池指数」的日收益（与 `equal_weight_returns` 同一口径）。

    `closes`：index=日期、columns=代码 的收盘价 DataFrame（缺失日为 NaN）。

    处理顺序与统计层一致：先 `pct_change()` → inf/NaN 归零 → 再等权平均。
    ⚠️ 顺序不能换：先平均再归零会让「某只票当日无行情」污染整个等权值。
    """
    if closes is None or getattr(closes, "empty", True):
        return pd.Series(dtype=float)
    # ⚠️ `fill_method="pad"` **显式写出**：pandas 2.1 起默认行为已弃用（会告警），
    # 而这里的语义必须是「前值填充」——与统计层 `stats.py` 的 `px.pct_change()` 同口径
    # （停牌日按前收盘算 → 当日收益 0，而不是 NaN）。
    r = closes.pct_change(fill_method="pad")
    r = r.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return pd.Series(equal_weight_returns(r.to_numpy(dtype=float)), index=closes.index)


def pool_index_series(closes: pd.DataFrame, base: float = 1.0) -> pd.Series:
    """等权池指数的**净值曲线**（以 `base` 起算）。"""
    r = pool_index_from_closes(closes)
    if r.empty:
        return r
    return (base * (1.0 + r).cumprod()).rename("pool_index")


def moving_average(close: pd.Series, period: int) -> pd.Series:
    """简单移动平均（窗口不足的点为 NaN，不做「用已有值凑满窗口」的填充）。"""
    p = max(2, int(period))
    s = pd.Series(close).astype(float)
    return s.rolling(p, min_periods=p).mean()


def trend_state(close: pd.Series, period: int, lag: int = 1) -> np.ndarray:
    """**无前视**的趋势判定：决策日 t 只用 ≤ t-1 的信息。

        state[t] = close[t-lag] > MA(period)[t-lag]
        MA(period)[t-lag] = mean(close[t-lag-period+1 .. t-lag])

    `lag=1` 是实盘口径（今天开盘前只能看到昨天收盘）；
    `lag=0` 只用于**事后复盘**（如画图标注「这一天处于什么状态」），
    拿它去驱动仓位就是前视。

    返回与 `close` 等长的 bool 数组；窗口不足或数据缺失的点为 False（不猜）。
    ⚠️ 「False」在这里**混了两种含义**：「判定为向下」与「判不出来」——
    要区分用 `trend_state_detail`。
    """
    state, _ = trend_state_detail(close, period, lag=lag)
    return state


def trend_state_detail(close: pd.Series, period: int,
                       lag: int = 1) -> tuple[np.ndarray, np.ndarray]:
    """`trend_state` 的两值版：返回 `(state, valid)`。

    `state`：bool，True = 在 MA 之上；
    `valid`：bool，该点**是否判得出来**（窗口不足 / 数据缺失 → False）。

    ⚠️ 为什么必须区分：「判不出来」不能当成「判为向下」——
    前者应退回**基线行为**（不动仓位），后者才是「降仓/空仓」。
    否则序列开头的预热期会被当成下跌 → 白白空仓几天。
    """
    s = pd.Series(close).astype(float)
    ma = moving_average(s, period)
    k = max(0, int(lag))
    ref = s.shift(k)
    ma_ref = ma.shift(k)
    valid = (ref.notna() & ma_ref.notna()).to_numpy(dtype=bool)
    state = ((ref > ma_ref).fillna(False)).to_numpy(dtype=bool)
    return state, valid


def align_to_days(series: pd.Series, days: list[str]) -> pd.Series:
    """把一条序列对齐到给定的交易日轴（缺失日前向填充）。

    指数与个股共用交易所日历，正常应当完全对齐；前向填充只兜住
    「区间首日不是指数交易日」这类极少数情况（回测区间起点常落在节假日）。

    ⚠️ 实现要点：必须先在「原序列 ∪ 目标轴」的**并集**上做 ffill，再取目标轴 ——
    直接 `reindex(axis).ffill()` 会把**区间之前**的值丢掉（reindex 只保留目标轴），
    于是序列开头变成一串 NaN（前向填充填不上「前面没有值」的空）。
    """
    if series is None or len(series) == 0:
        return pd.Series(dtype=float, index=pd.Index(days, dtype=object))
    s = pd.Series(series).astype(float)
    s.index = pd.Index([str(x)[:10] for x in s.index])
    axis = pd.Index([str(d)[:10] for d in days], dtype=object)
    union = s.index.union(axis).sort_values()
    merged = s.reindex(union).ffill()
    return merged.reindex(axis)
