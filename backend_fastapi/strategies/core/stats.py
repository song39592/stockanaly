# -*- coding: utf-8 -*-
"""统计引擎（与策略完全解耦，可独立复用/单测）。

输入：每只标的的「目标权重序列」+ 收盘价序列（+ 可选的 raw OHLCV 做可行性判定），
输出：组合净值曲线、统计指标、逐笔明细。本文件不引用任何策略实现，也不直接取数。

【组合权重模型】（相对旧版「等权资金带」的破坏性变更）
    w_t        目标权重（占组合净值比例，Σ ≤ 1），由 base.signals_to_positions 给出；
    w_{t-1}    次日生效（当日收盘定权重，次日承担收益）；
    分配_i(t)  = 组合净值(t) × w_i(t)
    现金(t)    = 组合净值(t) × (1 − Σ w_i(t))      收益 0

旧口径是 `port = Σ(w×ret) / N`（N = 有数据的股票数），组合日收益 = 各票收益的**均值**，
于是「只持 top_n 只」时收益幅度天然被压成 `top_n / N`——全市场 5559 只时
**资金利用率仅 1.72%**，且同一策略在 500 只池与 5559 只池结果差 11 倍，不可横向对比。
新口径与池子大小彻底解耦。

【成本与成交分层】按「能否用 Δw × NAV 精确表达」分层，不是按「重要不重要」：
    进 equity（比例项，权重口径即可精确算出）
        佣金 commission / 印花税 stamp_duty（卖出单边）/ 滑点 slippage
        最低佣金 min_commission = max(notional × rate, 5.0)  ← 不需要股数
        一字板买不进 → 该份额度当日就是现金、不重分配（见 compute 的「严格成交模型」段）
        一字跌停卖不出 → 清零延后到第一个能卖的日子（同上）
    只进 trades 明细（需要股数与价格粒度）
        整手 lot_size / 停牌 → 只产出「可行性」指标，不改净值

为什么这样分：前一组都能用「权重 × 净值」精确表达，代价只是改一个权重；
后一组需要股数与价格粒度（整手按手数取整、停牌当天没有价格），强行进净值会把
理想权重曲线改造成事件驱动模拟。本引擎的主要用途是**横向比较多策略的信号质量**，
`equity` 需要一个干净、可用权重精确表达的口径；但把成本整体赶出净值会让成本
模型退化成摆设，而最低佣金恰好是能用金额精确表达的，不该被浪费。
⚠️ 一字板「买不进」进净值后，空出来的仓位**不会**被别的票接手——这会让
`capital_utilization` 下降（Σw 变小），是**如实反映**而不是打折。

【trades 与 equity 同口径】逐笔 pnl 用**现金流归因**（真实的 Δw × NAV 进出），
与净值曲线用同一套成本公式，故「Σ逐笔 pnl」与「total_return 的金额」可互相印证。
    pnl = 期末持仓市值 + Σ卖出金额 − Σ买入金额 − 该票分摊的成本
逐笔 pnl 是**归因值**，跨票加总与净值变动不逐分相等（复利交叉项），但口径一致、可核对。

返回结构：
    {
      "dates": [YYYY-MM-DD, ...],
      "equity": [float|null, ...],                  # 组合净值（以 initial_capital 起）
      "equity_benchmark": [float|null,...]|None,   # 等权全池买入持有（默认基准）
      "metrics": {...},                             # schema_version = 2
      "per_stock": [{"code","pnl","total_return","trades","closed"}, ...],
      "trades": [ {...逐笔明细...} ],                # 全量逐笔（编排层裁剪后进响应 / 落报告）
      "flags": [str, ...],                          # 空值/降级原因，永不静默
    }
"""
from __future__ import annotations

import bisect
import math
import warnings

import numpy as np
import pandas as pd

# 涨跌停口径的**唯一实现**在 core/limit_price.py（统计层的可行性判定与
# 策略层的「炸板不买」共用同一份）。这里按旧名转出，调用点与单测
# （stats._limit_prices / stats._is_limit_up …）都保持不变。
from core.limit_price import (
    ACT_CANCEL as _ACT_CANCEL,
    POLICY_KEEP as _POLICY_KEEP,
    POLICY_LABELS as _POLICY_LABELS,
    is_limit_down as _is_limit_down,
    is_limit_up as _is_limit_up,
    limit_open_action as _limit_open_action,
    limit_pct as _limit_pct,
    limit_prices as _limit_prices,
)
# 「等权全池」这条基准口径的**唯一实现**在中立内核包 formulas/market.py
# （择时类策略算「大盘状态」时用的是同一份 —— 两处各写一份 = 同一口径两个数）。
from formulas.market import equal_weight_returns

SCHEMA_VERSION = 2
_TRADING_DAYS = 252.0      # 年化折算：固定日线，见模块 docstring
_EPS = 1e-12
# 单日涨跌幅超过这个值即判定为**数据错误**（而非行情）。A 股单日涨跌停上限是
# ±10%（创业板/科创板 ±20%、北交所 ±30%），留到 50% 的余量足以覆盖新股上市首日
# 与 ST 板的特殊情形，同时足够宽松、不会误杀真实行情。
# 实测本地库因复权因子缺失出现过 -88% ~ -96% 的假跌幅（详见 compute 里的说明）。
_IMPOSSIBLE_RET = 0.5
# 涨跌停幅度常量（BOARD_LIMIT / MAIN_LIMIT）在 core/limit_price.py —— 那里是唯一实现。

# 单股仓位上限的判定容差（相对）：必须**大于** float32 的表示误差（约 1.2e-7），
# 否则「上限正好等于某个权重」时会因浮点表示差异被误判成超限。
_CAP_TOL = 1e-6


def cap_weight(value: float, cap: float | None) -> float:
    """把一个**目标权重**压到单股仓位上限以内 —— 单股仓位上限的**唯一口径**。

    语义：**只压低、绝不抬高**（上限比原权重大时原样返回）。
    `cap` 为 None 或 ≤ 0 → 不限。

    ⚠️ `compute` 里对权重矩阵用的是**向量化**版本（同容差 `_CAP_TOL`）；
    单测 `test_position_cap` 断言两者结果一致 —— 两边口径漂移，
    就会导致「回测净值」与「当日推荐金额」对不上同一套仓位。
    """
    if cap is None or cap <= 0.0:
        return float(value)
    if value > cap * (1.0 + _CAP_TOL):
        return float(cap)
    return float(value)

# 逐笔明细**进 HTTP 响应**的上限（页面表格一行一笔成交）。
# 全市场一次回测能到十几万笔（实测 15 万+ = 24 MB JSON、解析要 10 秒）——
# 所以不能全量下发；而页面端实测 DataGridView 填 2 万行要 **4.3 秒**
# （UI 线程直接卡住），故只带最近 5000 笔：够看清「最近发生了什么」，
# 全量逐笔仍以报告文件为准（report.MAX_TRADES = 2 万笔）。
MAX_TRADES_RESPONSE = 5000

# (code, date) → (reason, meta)，由 set_trade_reasons() 注入，供逐笔明细挂归因。
# 单独设一个模块级槽而不是层层透传：逐笔明细在 `_build_trades` 里逐段构造，
# 而这份索引只有一份，透传会让签名多出 5 个参数。
_TRADE_REASONS: dict = {}

_SELL_BY_ABSENCE = "跌出目标组合（本次调仓未输出）"


def _fin(v, default=None):
    """把 NaN / ±inf / complex 统一收敛成 `default`（默认 None）。

    ## 为什么必须做
    FastAPI 用**标准库** `json.dumps` 序列化响应，而它对非有限值是**硬失败**的：
        ValueError: Out of range float values are not JSON compliant      (inf / nan)
    一个 `inf` 就会让**整个响应变成 500**，前端拿到的正文是纯文本
    "Internal Server Error"，于是报成「无效的 JSON 基元: Internal.」——
    错误信息离真实原因隔了三层，极难定位（2026-10-14 实测踩过）。

    净值**可以合法地**变成非有限：
      · `max_drawdown = nav / running_max`，而 running_max 在净值跌破 0 时会是 0
        → 除零 → ±inf；
      · `annual_return = (nav/初始) ** (252/T)`，底数为负而指数是小数
        → Python 返回 **complex**，json 同样序列化不了；
      · `pct_change()` 在「昨收为 0」时给 inf，而 `fillna(0)` 只管 NaN 不管 inf。

    所以**每个**对外的浮点都要过一道 `_fin`，而不是指望写的人记得判。
    """
    if v is None:
        return default
    if isinstance(v, (complex, np.complexfloating)):
        return default
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def json_safe(obj):
    """递归清洗任意结构里的非有限浮点，使其一定能被 `json.dumps` 接受。

    作为**最后一道防线**加在响应出口：前面每个指标都判过有限性，
    但 `per_stock` / `trades` / 用户塞进 meta 的字段很难穷尽 ——
    与其赌「一定没有」，不如在出口统一兜住。
    代价是对大响应多走一次遍历（回测响应量级为万级字段，可忽略）。
    """
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    if isinstance(obj, bool) or obj is None or isinstance(obj, (int, str)):
        return obj
    if isinstance(obj, (float, np.floating, complex, np.complexfloating)):
        return _fin(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.ndarray):
        return [json_safe(v) for v in obj.tolist()]
    return obj


def _native(series: pd.Series):
    """Series → JSON 友好的 list。

    ⚠️ 判的是 **isfinite 而不是 isnan**：净值序列里出现 inf 时
    `math.isnan(inf)` 是 False，原来那版会把 inf 原样放进去 → 整个响应 500。
    """
    return [_fin(v) for v in series.values]


# ---------------------------------------------------------------- 涨跌停口径
# ⚠️ **唯一实现已下沉到 `core/limit_price.py`**：
#    涨跌停幅度 / 涨停价 / 一字板判定 既被本模块（可行性判定 + 严格成交模型）用，
#    也被策略层的「涨停开盘但炸板 → 放弃买入」过滤用 —— 两处各写一份就是
#    「同一口径两个数、两边都不报错」。
#    上面按旧名（`_limit_pct` / `_limit_prices` / `_is_limit_up` / `_is_limit_down`）
#    转出，调用点与单测都保持不变；改口径请改 `core/limit_price.py`。
#
#    口径要点（详见该文件）：幅度按板块（主板 10%、双创 20%）；
#    涨停价 = round(昨收 × (1 ± 幅度), 2)；一字涨停 = 全天最低价 ≥ 涨停价（买不进）；
#    一字跌停 = 全天最高价 ≤ 跌停价（卖不出）；买卖方向各判各的。
#    ⚠️ 已知缺口：ST 2025-04 前的 5%、北交所 30% 未覆盖。
#    ⚠️ 涨跌停作用于**原始价**，hfq 会平滑除权跳空、掩盖真实涨跌停，
#    故本模块的可行性判定一律用 raw。


def _close_at_or_before(rclose: pd.Series, raw_dates: list[str], day: str):
    """day 当天（或之前最后一个有效交易日）的未复权收盘价。

    卖出日停牌 → 当天没有 K 线，取该日之前最后一个有效收盘价
    （与成交明细「退到最后一个有效交易日」的取价口径一致）。
    """
    if not day:
        return None
    i = bisect.bisect_right(raw_dates, day) - 1
    if i < 0:
        return None
    try:
        return float(rclose.iloc[i])
    except (IndexError, TypeError, ValueError):
        return None


def _window(sl: pd.Series | np.ndarray, tail: int | None):
    """短窗口（最近 N 个交易日）：截尾。"""
    if tail and len(sl) > tail:
        return sl[-tail:]
    return sl


# ---------------------------------------------------------------- 可行性判定

def _feasibility(code: str, raw: pd.DataFrame | None, dates: list[str],
                 buy_days: list[int], sell_days: list[int]) -> dict:
    """整手 / 涨跌停 / 停牌的可行性判定（**只影响明细，不影响净值**）。

    返回 {"feasible","blocked_by","blocked_counts","lot_fail"}：
      feasible      该笔是否可在真实市场里成交；
      blocked_by    首个阻断原因（None 表示无阻断）；
      blocked_counts{"suspended","limit_up","limit_down"} 各阻断笔数。

    `raw` 是**未复权** OHLCV，缺该票数据时一律按「可成交」处理并由调用方计数为
    「无法判定」——不能因为缺数据就静默当成可成交。
    """
    out = {"feasible": True, "blocked_by": None,
           "blocked_counts": {"suspended": 0, "limit_up": 0, "limit_down": 0}}
    if raw is None or not len(raw):
        return out

    vol = raw["volume"] if "volume" in raw.columns else None
    high = raw["high"] if "high" in raw.columns else None
    low = raw["low"] if "low" in raw.columns else None
    close = raw["close"] if "close" in raw.columns else None
    prev = close.shift(1) if close is not None else None
    thr = _limit_pct(code)

    pos_of = {d: i for i, d in enumerate([str(x)[:10] for x in raw.index])}
    for is_buy, days in ((True, buy_days), (False, sell_days)):
        for t in days:
            d = dates[t]
            i = pos_of.get(d)
            if i is None:                     # raw 缺这一天 → 无法判定
                out["blocked_by"] = out["blocked_by"] or "no_raw_data"
                continue
            if vol is not None and len(vol) and float(vol.iloc[i]) == 0.0:
                out["blocked_counts"]["suspended"] += 1
                out["feasible"] = False
                out["blocked_by"] = out["blocked_by"] or "suspended"
                continue
            if (high is None or low is None or close is None or prev is None
                    or not len(high) or not len(low) or not len(prev)):
                continue
            pc = prev.iloc[i]
            if pd.isna(pc) or float(pc) <= 0:
                continue
            hi, lo = float(high.iloc[i]), float(low.iloc[i])
            # 买卖方向各判各的：买入侧只认「买不进」（一字涨停），
            # 卖出侧只认「卖不出」（一字跌停）。一字跌停时**买入是能成交的**
            # （板上是卖单），一字涨停时**卖出是能成交的**（板上是买单）。
            blocked = (_is_limit_up(hi, lo, float(pc), thr) if is_buy
                       else _is_limit_down(hi, lo, float(pc), thr))
            if blocked:
                key = "limit_up" if is_buy else "limit_down"
                out["blocked_counts"][key] += 1
                out["feasible"] = False
                out["blocked_by"] = out["blocked_by"] or key
    return out


# ---------------------------------------------------------------- 主计算

def compute(positions_by_code: dict, close_by_code: dict, *,
            initial_capital: float = 100000.0,
            commission: float = 0.0003,
            stamp_duty: float = 0.001,
            slippage: float = 0.0005,
            min_commission: float = 5.0,
            benchmark_close: pd.Series | None = None,
            raw_by_code: dict | None = None,
            signal_meta: dict | None = None,
            lot_size: int = 100,
            tail: int | None = None,
            ruin_threshold: float | None = None,
            position_cap: float | None = None,
            limit_open_policy: str = _POLICY_KEEP,
            abnormal_by_code: dict | None = None,
            abnormal_filter: tuple | list | None = None,
            max_buy_price: float | None = None,
            drop_warn_ratio: float = 0.5) -> dict:
    """按目标权重序列 + 收盘价序列，统计组合净值、指标与逐笔明细。

    参数：
      positions_by_code  {code: Series(目标权重, 索引 YYYY-MM-DD)}
      close_by_code      {code: Series(hfq 收盘价, 索引 YYYY-MM-DD)}
      initial_capital    初始资金
      commission/stamp_duty/slippage/min_commission
                         成本模型（**全部进 equity**，口径见模块 docstring）
      benchmark_close    指数收盘价（可选）。**不给也会输出默认基准**：
                         等权全池买入持有（零数据依赖、与策略同池）
      raw_by_code        {code: DataFrame[open,high,low,close,volume]}（**未复权**），
                         只用于整手/涨跌停/停牌/**涨停开盘应对**的可行性判定，不进净值；
                         调用方应只传**权重变动过**的 code（见 backtest.py）
      lot_size           每手股数，0 = 不做整手约束
      limit_open_policy 「涨停开盘应对」三档（见 core/limit_price.py）：
                         keep=不撤单（现状）/ cancel=撤单（涨停开盘当日不建仓）/
                         reseal=涨停条件单（炸板后收盘仍封板才进，否则撤单）。
                         **全局生效**（所有策略同一套口径），撤单=该份额度当日为现金、
                         不重分配给其他票（与「一字涨停买不进」同一口径）。
      abnormal_by_code  {code: abnormal_detail(..., lag=1) 的逐日 DataFrame}，
                         已裁到回测区间；由 data.load_abnormal_for 独立取数（含预热）。
      abnormal_filter   过滤类型集合（price/turnover/severe/near），空=不过滤。
                         **买入时**判定：与一字板/撤单并列，拦下的建仓额度当日为现金、
                         不重分配；`valid=False`（判不出来）一律放行并显式计数。
      max_buy_price     买入价上限（元，未复权）。**信号日收盘价** > 此值即放弃建仓，
                         与一字板/撤单/异动并列，份额度当日为现金、不重分配。
                         None / <=0 = 不限制（与旧回测一致）。读数缺失 → 放行并显式计数。
                         ⚠️ 只管**建仓**；已持有的不受影响（按原策略卖出）。
      tail               只统计**最后 N 个交易日**（短窗口）
      drop_warn_ratio    持仓数骤降超过此比例记 WARNING

    ⚠️ `Σweight > 1` 由 base._as_signals 在信号层就抛错了，这里不再兜底归一化——
    静默归一化会把策略 bug 改写成「半仓运行」，既不可解释也极难排查。
    """
    # 归因索引在这里**每次重置**：若由调用方单独调 set_trade_reasons()，上一次回测的
    # (code,date)→reason 会残留到这一次，导致明细里的理由来自别的策略/别的回测区间。
    # 显式入参 + 入口即重置，是唯一能杜绝「跨次回测串味」的做法。
    set_trade_reasons(signal_meta or {})

    codes: list[str] = []
    for code, pos in (positions_by_code or {}).items():
        close = close_by_code.get(code)
        if close is None or len(close) < 2 or pos is None or len(pos) < 2:
            continue
        codes.append(code)
    if not codes:
        raise RuntimeError("没有可统计的标的")

    # 对齐到「主日历」：全量回测（5000+ 只）时交集会被次新股压到只剩几天
    # （一只 9 月底上市的票只含几个交易日，一交集全组合就没了），故取并集，
    # 各标的缺失交易日 reindex 后按 0 收益补 —— 与「未持有即现金」口径一致。
    common = sorted({str(d)[:10] for c in codes for d in close_by_code[c].index})
    if tail and len(common) > tail:
        common = common[-tail:]
    if len(common) < 2:
        raise RuntimeError("对齐后的共同交易日不足，无法统计")
    T, C = len(common), len(codes)

    # W = 目标权重矩阵，T×C；R = 日收益矩阵，T×C。
    # ⚠️ 用 float32：全市场 5578 只 × 730 天，float64 每张矩阵 32MB，W+R+dw_all
    #    就是 96MB；float32 直接砍半。本机可用内存只有 1.2GB 时这点不是小事。
    W = np.zeros((T, C), dtype=np.float32)
    R = np.zeros((T, C), dtype=np.float32)
    # ⚠️ **数据错误防护**（这条是组合权重口径下必须加的，见下）。
    #    实测本地库里有单日 -88% ~ -96% 的 hfq「跌幅」：复权因子表停在 2024-06，
    #    2026 年的送转事件没补上，于是除权跳空**原样留在 hfq 里**。
    #    A 股单日涨跌停是 ±10%（双创 ±20%、北交所 ±30%），-90% 只能是数据错误。
    #
    #    为什么以前没炸：旧口径把每只票的收益**除以池子大小 N**（5578），
    #    一只票 -96% 只贡献 -0.017%，被彻底稀释掉了。组合权重给每只票真实权重后，
    #    满仓时一只票就能把净值打到 -95%，多来几只直接归零 ——
    #    实测全市场 macd 就是 total_return = **-100.1%**。
    #    也就是说「除以 N」这个 bug 一直在**掩盖数据质量问题**，拆掉它必须补上防护。
    n_bad = 0
    bad_codes: set[str] = set()
    # ---- 单股仓位上限（默认不限 = 按策略给的仓位执行）----
    # 口径：策略给的**目标权重**逐票取 min(权重, 上限)；上限是「占组合净值比例」。
    # ⚠️ 为什么截在这一步（权重矩阵是权重进入净值/成交/成本/明细的**唯一入口**）：
    #    在这里截断 → 净值、严格成交模型（一字板买不进即空仓）、成本、胜率、逐笔明细
    #    全部自动一致；在编排层改 positions 则要另外维护一套「被截断了多少」的统计与留痕。
    # ⚠️ 截断只变小不变大 → `Σweight ≤ 1` 的硬约束不可能被破坏。
    # ⚠️ 截断**必须留痕**：静默改权重 = 静默改数据（以为按策略仓位在跑、实际被压低，
    #    却没人知道）→ 统计进 metrics，并在 flags 里给一条人读说明。
    cap = None
    if position_cap is not None and float(position_cap) > 0:
        cap = min(1.0, float(position_cap))
    capped_codes: set[str] = set()
    capped_points = 0
    for j, code in enumerate(codes):
        w = positions_by_code[code].reindex(common).fillna(0.0).to_numpy(dtype=np.float32)
        if cap is not None:
            over = w > np.float32(cap) * (1.0 + _CAP_TOL)   # 容差与 cap_weight 一致
            n_over = int(over.sum())
            if n_over:
                capped_codes.add(code)
                capped_points += n_over
                w = np.where(over, np.float32(cap), w)
        W[:, j] = w
        px = close_by_code[code].reindex(common).astype(float)
        r = px.pct_change()
        # inf 来自「昨收为 0」的分母，NaN 来自停牌/未交易日 —— 一律按 0 收益处理
        r = r.replace([np.inf, -np.inf], np.nan).fillna(0.0)
        bad = r.abs() > _IMPOSSIBLE_RET
        nb = int(bad.sum())
        if nb:
            n_bad += nb
            bad_codes.add(code)
            r = r.mask(bad, 0.0)      # 判为数据错误，当日按 0 收益，并计入 flags
        R[:, j] = r.to_numpy(dtype=np.float32)

    # ---- 严格成交模型：一字板买不进即空仓、跌停卖不出即延后清仓 ----
    # 权重模型：t_b 日发出的权重 w 在 t_b+1 日才作为持仓产生收益（wp = W[t_b]）。
    #   买入侧：t_b 日一字涨停（买不进）→ 该份额度当日就是**现金**，
    #           **不重分配给其他票**（其余票保持各自的 w，Σw 自然 < 1）。
    #   卖出侧：清零日一字跌停（卖不掉）→ 继续持有，把清零推到第一个能卖的日子。
    # 这两类都是「权重口径能精确表达」的约束（不像整手/停牌需要股数与价格粒度），
    # 所以进 equity；空出来的仓位直接反映在 capital_utilization / avg_positions 上，
    # **不需要另加指标**。
    # ⚠️ 旧口径是「涨停买不进 → 顺延至次日开盘价买入」，那等于**假设次日一定买得到**，
    #   实测把 top_n=1 的收益抬到远高于真实可实现的水平（见 _is_limit_up 的说明）。
    #   现在改成「买不到就不买」，更保守也更诚实。
    #
    # ⚠️ 判定必须基于**实际持仓**而不是「策略目标权重是否跳升」：d1 买不进 → d1 实际
    #   空仓 → d2 策略仍选中它时 d2 **仍然是一次建仓**，若 d2 也是一字板就必须再拦一次。
    #   按"目标权重跳升"判定会漏掉这种连续买不进的情况，等于默认买进了。
    n_buy_blocked = 0        # 因一字涨停买不进、**根本没发生**的建仓次数
    n_sell_deferred = 0       # 因一字跌停卖不出、清零被延后的次数
    n_buy_canceled = 0       # 因「涨停开盘应对」策略撤单、**根本没发生**的建仓次数
    canceled_weight = 0.0    # 被撤单的建仓涉及的目标权重合计（Σw，可跨日累加）
    n_abnormal_skipped = 0   # 因**买入时异动**放弃的建仓次数（与一字板/撤单分开归因）
    abnormal_weight = 0.0    # 被异动放弃的建仓目标权重合计
    n_abnormal_undecidable = 0  # 异动判不出来（valid=False）而默认放行的建仓次数
    abnormal_active_dates: set[str] = set()   # 异动过滤真正生效的交易日（报告要显式声明）
    n_price_skipped = 0       # 因信号日收盘价 > max_buy_price 放弃的建仓次数
    price_skipped_weight = 0.0  # 被高价放弃的建仓目标权重合计
    n_price_undecidable = 0   # 信号日收盘价缺失而默认放行的建仓次数
    # 建仓失败事件（隔日一字涨停 → 买不进）：**逐笔**记下来。
    # 这些建仓根本没有成交，所以不会出现在 trades 明细里（那里一行 = 一次买卖），
    # 只有总数的话报告说不清"哪只票、哪天、为什么没买成" → 单独给明细。
    blocked_builds: list[dict] = []
    # 清仓延后事件（隔日一字跌停 → 卖不出）：**逐笔**记下来。
    # 与建仓失败对称：这些清仓没有在计划日成交（延后到开板那天才卖），
    # trades 明细里看不出「本来是哪天要卖的、最后哪天以什么价卖的」。
    deferred_sells: list[dict] = []
    if raw_by_code:
        for j, code in enumerate(codes):
            raw = raw_by_code.get(code)
            if raw is None or "high" not in getattr(raw, "columns", []) \
               or "low" not in getattr(raw, "columns", []) \
               or "close" not in getattr(raw, "columns", []):
                continue
            rcols = {str(d)[:10]: i for i, d in enumerate(raw.index)}
            rclose = raw["close"]
            rhi = raw["high"]
            rlo = raw["low"]
            # open 只为「涨停开盘应对」用（keep/cancel/reseal）；缺列 → 判不了 → 不撤单
            ropen = raw["open"] if "open" in getattr(raw, "columns", []) else None
            thr = _limit_pct(code)
            target = W[:, j].copy()      # 策略目标权重
            actual = np.zeros(T, dtype=np.float32)   # 实际持仓权重（成交之后）

            # 异动状态：data.load_abnormal_for 已裁到 [start,end] 且 lag=1，
            # 这里只做「买入时」查表，不重新计算、不另写判据（唯一实现在 formulas）。
            ab_blocked = None
            ab_undecidable = None
            ab_detail = (abnormal_by_code or {}).get(code)
            if abnormal_filter and ab_detail is not None:
                from formulas.abnormal import abnormal_block as _abnormal_block
                # 对齐到回测的 common[t]（detail 索引与 common 同源），缺日按「判不出来」
                aligned = ab_detail.reindex(common)
                ab_blocked, ab_undecidable = _abnormal_block(aligned, abnormal_filter)

            def _blocked(t: int, up: bool) -> bool:
                """t 日是否一字板（up=True 判涨停买不进 / False 判跌停卖不出）。"""
                i = rcols.get(common[t])
                if i is None or i == 0:
                    return False
                pc = float(rclose.iloc[i - 1])
                if pc <= 0:
                    return False
                hi, lo = float(rhi.iloc[i]), float(rlo.iloc[i])
                return (_is_limit_up(hi, lo, pc, thr) if up
                        else _is_limit_down(hi, lo, pc, thr))

            def _cancel_by_policy(t: int) -> bool:
                """t 日是否因「涨停开盘应对」策略而撤单（当日不建仓）。

                口径在 `core/limit_price.py`（与统计层同一份）：
                  keep   不撤单（现状）
                  cancel 涨停开盘一律撤单
                  reseal 炸板票只有「收盘仍封住涨停价」才进，否则撤单
                ⚠️ 与「一字涨停买不进」**分开归因**：一字板是**物理约束**（板上全是买单），
                策略撤单是**主动放弃** —— 两者的报告口径不能混。
                """
                if limit_open_policy == _POLICY_KEEP or ropen is None:
                    return False
                i = rcols.get(common[t])
                if i is None or i == 0:
                    return False
                pc = float(rclose.iloc[i - 1])
                if pc <= 0:
                    return False
                act = _limit_open_action(float(ropen.iloc[i]), float(rhi.iloc[i]),
                                         float(rlo.iloc[i]), float(rclose.iloc[i]),
                                         pc, thr, limit_open_policy)
                return act == _ACT_CANCEL

            def _signal_close(t: int) -> float:
                """信号日（common[t-1]）**未复权**收盘价；缺失返回 NaN（判不出来）。

                ⚠️ 必须用 raw_by_code 的原始价：绝对价格上限是用户看到的实际股价，
                `close_by_code` 的 hfq 价经过复权缩放，与这个阈值不是一个量纲。
                """
                if t <= 0 or max_buy_price is None or float(max_buy_price) <= 0:
                    return float("nan")
                i = rcols.get(common[t - 1])
                if i is None:
                    return float("nan")
                v = float(rclose.iloc[i])
                return v if np.isfinite(v) and v > 0 else float("nan")

            pending_sell = -1        # 被一字跌停挡住、尚未卖掉的清仓日（-1 = 没有待处理清仓）
            sell_events: list[dict] = []      # 本票的清仓延后事件（价格在循环后统一补）
            for t in range(T):
                held = actual[t - 1] if t > 0 else 0.0
                tgt = float(target[t])
                if tgt > _EPS:
                    if held <= _EPS:
                        # 建仓：买不进就是空仓（不重分配给其他票）
                        if _blocked(t, True):
                            n_buy_blocked += 1
                            blocked_builds.append({
                                "code": code,
                                # 执行日 = 权重生效那天（= 信号日的**隔日**）；
                                # 信号日 = common[t-1]（t_b 日发出的权重 t_b+1 才生效）
                                "date": common[t],
                                "signal_date": common[t - 1] if t > 0 else "",
                                "reason": "limit_up",
                            })
                            continue           # actual[t] 保持 0
                        # 「涨停开盘应对」：策略撤单 → 当日不建仓。
                        # 口径与上面的一字板**一致**：该份额度当日就是现金、
                        # **不重分配给其他票**（引擎的每日重试保留：次日不再涨停开盘则自动补上）。
                        if _cancel_by_policy(t):
                            n_buy_canceled += 1
                            canceled_weight += float(tgt)
                            blocked_builds.append({
                                "code": code,
                                "date": common[t],
                                "signal_date": common[t - 1] if t > 0 else "",
                                "reason": "limit_open_cancel",
                                "weight": float(tgt),
                            })
                            continue           # actual[t] 保持 0（该份额度当日为现金）
                        # 买入时异动过滤：与一字板（物理约束）/ 撤单（策略主动放弃）
                        # 并列的第三道拦截 —— 策略主动放弃（异动风险），单独归因。
                        # state 来自 lag=1：t 日判定只用 t-1 收盘及更早数据，无前视。
                        if ab_blocked is not None and bool(ab_blocked[t]):
                            n_abnormal_skipped += 1
                            abnormal_weight += float(tgt)
                            blocked_builds.append({
                                "code": code,
                                "date": common[t],
                                "signal_date": common[t - 1] if t > 0 else "",
                                "reason": "abnormal",
                                "filter_types": list(abnormal_filter),
                                "weight": float(tgt),
                            })
                            abnormal_active_dates.add(common[t])
                            continue           # actual[t] 保持 0（该份额度当日为现金）
                        if ab_undecidable is not None and bool(ab_undecidable[t]):
                            # 判不出来 = 不知道，不等于「没有」——默认放行，但显式计数，
                            # 报告里要写清「本次有多少次是判不出来而放行的」。
                            n_abnormal_undecidable += 1
                        # 买入价上限：信号日收盘价（未复权）高于上限 → 主动放弃建仓。
                        # 与异动并列（策略主动放弃，非物理约束），份额度当日为现金不重分配。
                        sig_close = _signal_close(t)
                        if np.isfinite(sig_close):
                            if sig_close > float(max_buy_price):
                                n_price_skipped += 1
                                price_skipped_weight += float(tgt)
                                blocked_builds.append({
                                    "code": code,
                                    "date": common[t],
                                    "signal_date": common[t - 1] if t > 0 else "",
                                    "reason": "price_above_max",
                                    "signal_close": round(sig_close, 2),
                                    "max_price": float(max_buy_price),
                                    "weight": float(tgt),
                                })
                                continue           # actual[t] 保持 0
                        elif max_buy_price is not None and float(max_buy_price) > 0:
                            # 价格取不到 = 不知道，不等于「没超过」——默认放行、显式计数
                            n_price_undecidable += 1
                    if pending_sell >= 0:
                        # 延后清仓期间策略又改主意（重新要这只票）→ 这次清仓**没发生**
                        sell_events.append({
                            "code": code,
                            "planned_date": common[pending_sell],   # 原本要清仓那天
                            "date": "",                              # 没卖出去
                            "deferred_days": t - pending_sell,        # 被挡了几天
                            "status": "cancelled",
                            "reason": "limit_down",
                        })
                        pending_sell = -1
                    actual[t] = tgt             # 有仓或买得进 → 按目标权重持有
                else:
                    if held <= _EPS:
                        continue               # 本来就空仓
                    if _blocked(t, False):
                        actual[t] = held        # 卖不掉 → 继续持有原权重
                        n_sell_deferred += 1
                        if pending_sell < 0:
                            pending_sell = t    # 第一次被挡 → 记下「原本要清仓那天」
                        continue
                    actual[t] = 0.0             # 卖得掉 → 清零
                    if pending_sell >= 0:
                        # 延后到今天才卖得掉 → **实际卖出日 = 今天**（开板那天），
                        # 卖出价 = 当天收盘价（权重模型：权重变化一律按收盘价成交）
                        sell_events.append({
                            "code": code,
                            "planned_date": common[pending_sell],
                            "date": common[t],
                            "deferred_days": t - pending_sell,
                            "status": "sold",
                            "reason": "limit_down",
                        })
                        pending_sell = -1
            # 卖出价（未复权收盘价）：实际卖出日停牌没有 K 线时，
            # 取该日之前最后一个有效收盘价（与成交明细的取价口径一致）。
            raw_dates = sorted(rcols.keys())
            for ev in sell_events:
                if ev.get("date"):
                    ev["price"] = _close_at_or_before(rclose, raw_dates, ev["date"])
            deferred_sells.extend(sell_events)
            W[:, j] = actual

    rate = commission + slippage

    nav = np.empty(T, dtype=float)
    nav[0] = float(initial_capital)
    ret = np.zeros(T, dtype=float)
    util = np.zeros(T, dtype=float)          # Σw_{t-1}：当日真正在场的仓位
    n_pos = np.zeros(T, dtype=float)         # 当日非零持仓只数
    turnover = np.zeros(T, dtype=float)      # Σ|Δw|
    n_events = 0                             # 权重变动次数
    dw_all = np.zeros((T, C), dtype=np.float32)   # 逐日 Δw，供逐笔明细复用
    # 成本累计（按类型分桶）：净值被成本吃光时，用户需要一眼看出是哪一项吃掉的。
    # 实测：日频调仓 top_n=20 的 MACD 年换手 139 倍，仅成本一年就吃掉约 32%——
    # 净值 -100% 时若没有这几个字段，用户只会以为「策略失效」，看不到真凶。
    cost_sum = {"commission": 0.0, "stamp_duty": 0.0, "slippage": 0.0, "min_commission": 0.0}
    cost_total = 0.0

    # 「破产」截断：组合净值跌破 ruin_threshold × 初始资金（None=不截断；0.0=归零即断）
    # 时清掉所有持仓，其后交易日强制空仓、净值冻结；逐笔明细（用 W/dw_all）也随之截断。
    bankrupt_idx = -1

    for t in range(T):
        if bankrupt_idx >= 0:
            wp = np.zeros(C)
            W[t] = 0.0                      # 已破产：清仓，后续交易日强制空仓
        else:
            wp = W[t - 1] if t > 0 else np.zeros(C)
        dw = W[t] - wp
        dw_all[t] = dw
        gross = float((wp * R[t]).sum())
        util[t] = float(wp.sum())
        n_pos[t] = int((wp > _EPS).sum())
        turnover[t] = float(np.abs(dw).sum())
        n_events += int((np.abs(dw) > _EPS).sum())

        # ⚠️ t=0 **不特判**：w_prev 全 0 → gross=0，但 dw[0] = W[0] 就是**首次建仓**，
        # 建仓同样要付佣金/滑点（卖出侧此时为 0，无印花税）。旧口径在这里 `continue`
        # 掉了（turnover[0]=0），等于白送第一次建仓的成本 —— 收益被系统性高估。
        # 统一走下面的公式，不开口子。
        nav_prev = nav[t - 1] if t > 0 else float(initial_capital)
        buy = np.clip(dw, 0.0, None) * nav_prev
        sell = np.clip(-dw, 0.0, None) * nav_prev
        cost_slip = float((buy.sum() + sell.sum()) * slippage)
        cost_duty = float(sell.sum() * stamp_duty)
        if min_commission > 0:
            b_ok, s_ok = dw > _EPS, dw < -_EPS
            # 拆成「按比例部分」与「最低佣金补足部分」，两者之和才是真实佣金
            floor = float(np.where(b_ok, np.maximum(buy * commission, min_commission), 0.0).sum()
                          + np.where(s_ok, np.maximum(sell * commission, min_commission), 0.0).sum())
            cost_pro = float((buy.sum() + sell.sum()) * commission)
            cost_min = max(0.0, floor - cost_pro)
        else:
            cost_pro = float((buy.sum() + sell.sum()) * commission)
            cost_min = 0.0
        cost = cost_pro + cost_min + cost_slip + cost_duty
        cost_sum["commission"] += cost_pro
        cost_sum["min_commission"] += cost_min
        cost_sum["slippage"] += cost_slip
        cost_sum["stamp_duty"] += cost_duty
        cost_total += cost

        ret[t] = gross - (cost / nav_prev if nav_prev > 0 else 0.0)
        nav[t] = nav_prev * (1.0 + ret[t])
        # 破产检测：仅未破产时判一次；nav[t] 已含当日（破产当天仍是原持仓）
        if bankrupt_idx < 0 and ruin_threshold is not None:
            _limit = initial_capital * ruin_threshold
            if nav[t] <= _limit:
                bankrupt_idx = t

    ret_s = pd.Series(ret, index=common)
    nav_s = pd.Series(nav, index=common)

    # ---- 指标 ----
    flags: list[str] = []
    if n_bad:
        # 命中就必须喊出来：这些点被当成 0 收益处理了，不说的话就是**静默改数据**。
        sample = "、".join(sorted(bad_codes)[:8])
        flags.append(
            f"bad_price_data: 屏蔽了 {n_bad} 个「单日涨跌幅 >50%」的异常点"
            f"（A 股不可能出现，判定为复权因子缺失/脏数据），涉及 {len(bad_codes)} 只票"
            f"（如 {sample}）。这些日按 0 收益计入，**净值与真实行情存在偏差**，"
            f"建议刷新对应股票的复权因子后重跑。")
    if capped_points:
        flags.append(
            f"position_cap: 单股仓位上限 {cap:.2%} 生效 —— {len(capped_codes)} 只票的"
            f"目标权重被压低（累计 {capped_points} 个「交易日×票」）。"
            f"**实际单股仓位低于策略设定**，空出来的仓位就是现金，"
            f"资金利用率随之下降（看 capital_utilization / avg_positions）。")
    if limit_open_policy != _POLICY_KEEP and n_buy_canceled:
        flags.append(
            "limit_open: 涨停开盘应对 = " + str(_POLICY_LABELS.get(limit_open_policy, limit_open_policy))
            + " —— " + str(n_buy_canceled) + " 次建仓主动撤单（涉及目标权重合计 "
            + format(canceled_weight, ".3f") + "）。这些建仓没有成交，该份额度当日就是现金、"
            "不重分配给其他票（与一字涨停买不进同一口径，但归因不同：一个是物理买不到，一个是策略放弃）。")
    total_return = float(nav[-1] / initial_capital - 1.0)
    # 成本吃光净值：必须**显式告警**，否则用户只看到 total_return=-100%，
    # 会误以为是策略或数据坏了，而真凶是换手率×费率。
    if initial_capital > 0 and cost_total / initial_capital > 0.5:
        worst = max(cost_sum.items(), key=lambda kv: kv[1])
        flags.append(
            f"cost_drag: 累计交易成本 {cost_total:.0f} 元 = 初始资金的 "
            f"{cost_total / initial_capital * 100:.0f}%"
            f"（年化约 {cost_total / initial_capital * _TRADING_DAYS / max(1, T) * 100:.0f}%），"
            f"最大单项是 {worst[0]}（{worst[1]:.0f} 元）。"
            f"年换手 {np.mean(turnover) * _TRADING_DAYS:.0f} 倍 × 费率 ≈ "
            f"{(0.0003 + 0.001 + 0.0005) * 100:.2f}%/单位换手，成本会吃光净值。"
            f"请调大调仓间隔或降低 top_n，或用 min_commission=0 做研究口径对照。")
    if bankrupt_idx >= 0:
        _n_after = T - bankrupt_idx - 1
        flags.append(
            f"bankruptcy: 组合净值于 {common[bankrupt_idx]} 跌破阈值"
            f"（{nav[bankrupt_idx] / initial_capital:.1%} of 初始资金），"
            f"其后 {_n_after} 个交易日强制空仓、净值冻结。"
            f"阈值由请求参数 ruin_threshold 控制（默认 0.0=净值归零截断）。")
    # ⚠️ 年化：净值**可以合法地变成非正**（成本吃光净值时 nav 会跌破 0）。
    #   负底数的小数次幂在 Python 里返回 **complex**，json 序列化直接抛异常；
    #   而这类结果的含义本身也不成立（年化增长率对「已爆仓」的曲线无意义），
    #   所以底数 ≤ 0 一律返回 None，而不是硬算一个数出来。
    if T > 1 and nav[-1] > 0:
        ann = float((nav[-1] / initial_capital) ** (_TRADING_DAYS / T) - 1.0)
        if not math.isfinite(ann):
            ann = None
    else:
        ann = None
    # ⚠️ 回撤同理：running_max 在净值跌破 0 时会是 0 → nav/running_max 除零 → ±inf。
    #   统一把非有限值收敛掉，别让一个 inf 毁掉整个响应。
    running_max = np.maximum.accumulate(nav)
    with np.errstate(divide="ignore", invalid="ignore"):
        dd = np.where(running_max > 0, nav / np.where(running_max > 0, running_max, 1.0) - 1.0, -1.0)
    max_dd = _fin(np.min(dd), default=0.0)
    std = float(np.std(ret)) if T > 1 else 0.0
    mean = float(np.mean(ret)) if T else 0.0
    sharpe = float(mean / std * math.sqrt(_TRADING_DAYS)) if std > 0 else 0.0
    win_rate_daily = float(np.mean(ret > 0)) if T else 0.0

    # ⚠️ 利用率/持仓数的均值**从第 2 天起算**：第 1 天 w_prev 恒为 0（当天收盘才建仓，
    # 次日才承担收益），那是**结构性**的零，不是「策略没仓位」。算进去会让短窗口被
    # 无谓稀释——5 天窗口会凭空少 20%，看起来像配置有问题，其实只是建仓日。
    live = slice(1, None) if T > 1 else slice(0, None)
    utilization = float(np.mean(util[live]))
    avg_positions = float(np.mean(n_pos[live]))
    rpu = (total_return / utilization) if utilization > 1e-9 else None

    # ---- 基准：默认等权全池买入持有 ----
    # 口径在 formulas/market.py（与择时策略共用同一份「等权池」定义）
    bench = equal_weight_returns(R)                # 与策略同池，零额外数据依赖
    equity_bench = float(initial_capital) * np.cumprod(1.0 + bench)
    bench_return = float(equity_bench[-1] / initial_capital - 1.0)

    alpha = beta = ir = te = None
    if benchmark_close is not None and len(benchmark_close) >= 2:
        try:
            b = benchmark_close.reindex(common).astype(float).pct_change()
            # ⚠️ inf 也必须一起换掉：fillna 只管 NaN，而「昨收为 0」会给出 inf，
            #   留着会让后面的 std/cov 变成 nan 或 inf，再顺着 alpha/beta/IR 传出去。
            b = b.replace([np.inf, -np.inf], np.nan).fillna(0.0)
            br = b.to_numpy(dtype=float)
            var = float(np.var(br))
            if var > 0:
                beta = float(np.cov(ret, br)[0, 1] / var)
                diff = ret - br
                alpha = float((mean - beta * float(np.mean(br))) * _TRADING_DAYS)
                sdiff = float(np.std(diff))
                te = float(sdiff * math.sqrt(_TRADING_DAYS))
                ir = float(np.mean(diff) / sdiff * math.sqrt(_TRADING_DAYS)) if sdiff > 0 else None
        except Exception as exc:               # noqa: BLE001 - 基准算不出不该拖垮回测
            flags.append(f"benchmark_unavailable: {exc}")
    else:
        flags.append("benchmark_unavailable: 未提供指数行情，alpha/beta/IR 未计算")

    # ---- 逐笔明细（现金流归因，与 equity 同口径）----
    trades, per_stock, closed_all = _build_trades(
        codes, common, W, dw_all, nav, raw_by_code or {},
        lot_size, commission, stamp_duty, slippage, min_commission)

    # `eff` = **已平仓**的持仓段（不含 mark-to-market 的未平仓段）。
    # ⚠️ 这里**不能**再按 `shares > 0` 过滤：净值是权重口径、根本不模拟股数，
    #   没有 raw 行情时 shares 恒为 0，一过滤就会把全部交易剔掉、胜率永远算不出来。
    #   「整手买不进」属于**可行性**视角，只影响 feasible/blocked_by，不影响 pnl 口径。
    eff = [t for t in trades if not t["open"] and t["pnl"] is not None]
    n_closed = len(eff)
    if n_closed == 0:
        win_rate_trade = profit_factor = expectancy = avg_hold = None
        flags.append("no_closed_trades: 区间内没有已平仓的交易，"
                     "交易胜率/盈亏比/单笔期望/持仓天数均不可计算")
    else:
        win_rate_trade = sum(1 for t in eff if t["pnl"] > 0) / n_closed
        win = sum(t["pnl"] for t in eff if t["pnl"] > 0)
        loss = abs(sum(t["pnl"] for t in eff if t["pnl"] < 0))
        avg_hold = float(np.mean([t["holding_days"] for t in eff]))
        invested = [t["buy_notional"] for t in eff if (t.get("buy_notional") or 0) > 0]
        expectancy = (float(np.mean([t["pnl"] for t in eff]) / float(np.mean(invested)))
                      if invested else None)
        # ⚠️ 分母为 0（全胜）时返回 None 而不是 inf —— json.dumps(inf) 产出
        # `Infinity`，**不是合法 JSON**，前端 JSON.parse 会直接抛异常。
        if loss > 0:
            profit_factor = float(win / loss)
        else:
            profit_factor = None
            flags.append("no_losing_trades: 区间内无亏损交易，profit_factor 不可计算（不返回 inf）")

    # 持仓骤降守卫（漏发信号 = 静默清仓）
    if drop_warn_ratio > 0:
        prev_n = None
        for t in range(T):
            cur_n = int((W[t] > _EPS).sum())
            if prev_n and cur_n < prev_n * (1.0 - drop_warn_ratio):
                warnings.warn(
                    f"{common[t]} 持仓数从 {prev_n} 骤降到 {cur_n}"
                    f"（>{drop_warn_ratio:.0%}）：若非刻意清仓，多半是策略漏发信号"
                    f"（调仓日未输出的 code 会被记为 0）。", RuntimeWarning, stacklevel=2)
            prev_n = cur_n

    metrics = {
        "schema_version": SCHEMA_VERSION,
        # 口径标识：一字板买不进即空仓 / 跌停卖不出即延后清仓 → 净值已含这两条约束。
        # 整手 / 停牌仍只影响明细可行性，不进净值。
        "equity_model": "weight_strict_fill",
        "total_return": total_return,
        "annual_return": ann,          # ⚠️ 不能 float(ann)：净值非正时 ann 是 None
        "max_drawdown": max_dd,
        "sharpe": sharpe,
        "win_rate_daily": win_rate_daily,
        "win_rate_trade": win_rate_trade,
        "profit_factor": profit_factor,
        "expectancy_pct": expectancy,
        "num_trades_eff": n_closed,
        "weight_change_count": n_events,
        "avg_positions": avg_positions,
        "max_positions": int(np.max(n_pos)) if T else 0,
        "trading_days": T,
        "bankrupt": bankrupt_idx >= 0,
        "bankrupt_day": (common[bankrupt_idx] if bankrupt_idx >= 0 else None),
        "bankrupt_nav": (float(nav[bankrupt_idx]) if bankrupt_idx >= 0 else None),
        "bankrupt_ratio": (float(nav[bankrupt_idx] / initial_capital)
                           if bankrupt_idx >= 0 and initial_capital > 0 else None),
        "turnover_annual": float(np.mean(turnover) * _TRADING_DAYS),
        # ---- 成本画像：净值被成本吃光时，用户要能一眼看出是哪一项吃的 ----
        # 实测：日频调仓 top_n=20 的 MACD 年换手 139 倍，仅成本一年就吃掉约 32%，
        # 三年下来 total_return = -100%。没有这几个字段就只会看到「策略失效」。
        "cost_total": float(cost_total),
        "cost_ratio": (float(cost_total / initial_capital)
                       if initial_capital > 0 else None),
        "cost_breakdown": {k: round(v, 2) for k, v in cost_sum.items()},
        "cost_drag_annual": (float(cost_total / initial_capital * _TRADING_DAYS / T)
                             if (T > 0 and initial_capital > 0) else None),
        "avg_holding_days": avg_hold,
        "capital_utilization": utilization,
        "return_per_utilized_capital": rpu,
        "benchmark_return": bench_return,
        "alpha": alpha, "beta": beta, "ir": ir, "tracking_error": te,
        # ---- 单股仓位上限（默认不限）----
        # None = 不限（按策略给的仓位执行）；非 None 时是实际生效的占比上限。
        # 被截断的规模一并给出 —— 静默改权重 = 静默改数据。
        "position_cap": cap,
        "position_capped_codes": len(capped_codes),
        "position_capped_points": capped_points,
    }

    # 单票破产标记：某票累计收益使 (1+total_return) 跌破 ruin_threshold 时标 bankrupt。
    if ruin_threshold is not None:
        for _ps in per_stock:
            _tr = _ps.get("total_return")
            try:
                if _tr is not None and (1.0 + float(_tr)) <= ruin_threshold:
                    _ps["bankrupt"] = True
            except (TypeError, ValueError):
                pass

    # ⚠️ **整个返回值过一遍 json_safe**：这是「一个 inf 毁掉整个响应」的最后防线。
    # 上游每个指标都已各自判过有限性，但净值序列 / per_stock / trades 里
    # 仍有大量浮点，且 `per_stock` 的 total_return 之类字段在净值变号时
    # 极易出现非有限值。与其逐处补判，不如在出口统一兜住 ——
    # 报错信息（"无效的 JSON 基元: Internal."）离真实原因隔了三层，
    # 这种问题必须在源头堵住，不能指望前端去猜。
    return json_safe({
        "dates": common,
        "equity": _native(nav_s),
        "equity_benchmark": _native(pd.Series(equity_bench, index=common)),
        "metrics": metrics,
        "per_stock": per_stock,
        "trades": trades,
        "flags": flags,
        # 严格成交模型下的"没成交"计数：这两次是**根本没有交易**（买不进 / 卖不掉），
        # 所以不会出现在 trades 明细里（trades 只记真的发生了的成交）。
        # 单独带出来是为了让报告能回答"有多少次想买没买成"——
        # 它不是新增的对比维度，只是把已进净值的约束**说清楚**。
        "fill_blocked": {"buy_blocked": n_buy_blocked,
                         "sell_deferred": n_sell_deferred},
        # 「涨停开盘应对」三档（全局生效）：**单独统计**，好让报告回答
        # 「这个条件拦掉了多少笔、值多少钱」（对照用两个独立回测范围，见 backtest）。
        "limit_open": {"policy": limit_open_policy,
                       "canceled_builds": n_buy_canceled,
                       "canceled_weight": round(float(canceled_weight), 6)},
        # 买入时异动过滤：与一字板（物理约束）/ 撤单（主动放弃）**分开归因**。
        # skipped = 拦下的建仓次数；undecidable = 判不出来（valid=False）而默认放行的次数；
        # active_dates = 过滤真正生效的交易日（报告显式声明「规则从哪天开始」）。
        "abnormal": {
            "filters": list(abnormal_filter or ()),
            "skipped_builds": n_abnormal_skipped,
            "skipped_weight": round(float(abnormal_weight), 6),
            "undecidable_passed": n_abnormal_undecidable,
            "active_dates": sorted(abnormal_active_dates),
        },
        # 买入价上限（信号日未复权收盘价 > 上限）：与异动过滤分开归因。
        # None / <=0 = 未启用（此时三个计数恒为 0，与旧回测完全一致）。
        "max_buy_price": {
            "limit": float(max_buy_price) if max_buy_price is not None and float(max_buy_price) > 0 else None,
            "skipped_builds": n_price_skipped,
            "skipped_weight": round(float(price_skipped_weight), 6),
            "undecidable_passed": n_price_undecidable,
        },
        # 建仓失败明细（隔日一字涨停 → 买不进）：**逐笔**事件，与上面的计数同源。
        # 已在净值里生效（该份额度当日就是现金、不重分配），这里只负责"说清楚"。
        "build_failures": blocked_builds,
        # 清仓延后明细（隔日一字跌停 → 卖不出）：与 build_failures 对称的一组事件。
        # status = "sold"（延后后实际卖出，带实际卖出日与卖出价）
        #        / "cancelled"（延后期间策略又改主意继续持有 → 没卖）
        "sell_deferrals": deferred_sells,
    })


def _build_trades(codes, common, W, dw_all, nav, raw_by_code,
                  lot_size, commission, stamp_duty, slippage, min_commission):
    """逐笔「一买一卖」明细 + 逐票汇总（现金流归因，与 equity 同一套成本公式）。

    向量化：先用 numpy 一次比较定位「权重真的变了」的下标，只对这些下标做 Python 迭代。
    全市场 5559 只 × 700 天若逐日 Python 循环是 390 万次迭代，报告生成会卡到不可接受。

    pnl = 期末持仓市值 + Σ卖出金额 − Σ买入金额 − 该票分摊的成本
    跨票加总与净值变动**不逐分相等**（复利交叉项），但口径一致、可核对。
    """
    trades: list[dict] = []
    per_stock: list[dict] = []
    closed_all = 0
    ev_t, ev_j = np.nonzero(np.abs(dw_all) > _EPS)

    # code → 列下标。**不能用 codes.index(code)**：那在 per-code 循环里是
    # O(C²)，全市场 5559 只就是 3000 万次字符串比较，白白吃掉几秒。
    j_of = {c: j for j, c in enumerate(codes)}
    by_code: dict[str, list[int]] = {}
    for t, j in zip(ev_t.tolist(), ev_j.tolist()):
        by_code.setdefault(codes[j], []).append(t)

    for code, days in by_code.items():
        j = j_of[code]
        w = W[:, j]
        dw = dw_all[:, j]
        raw = raw_by_code.get(code)
        raw_close = None
        if raw is not None and "close" in getattr(raw, "columns", []):
            raw_close = raw["close"]

        cur: dict | None = None          # 当前持仓段
        stock_pnl = 0.0
        stock_buy_amt = 0.0
        n_periods = 0
        n_closed = 0

        def _close(t_close: int | None):
            """结束当前持仓段，落一条 trade。"""
            nonlocal cur, stock_pnl, stock_buy_amt, n_periods, n_closed
            if cur is None:
                return
            end_nav = float(nav[t_close - 1] if (t_close is not None and t_close > 0)
                              else nav[len(nav) - 1])
            w_end = float(w[t_close]) if t_close is not None else float(w[len(w) - 1])
            is_open = t_close is None
            value = 0.0 if (t_close is not None) else w_end * end_nav
            # ⚠️ 一律 float() 收口：numpy 标量在 json.dumps 时会报
            #    "Object of type float64 is not JSON serializable"，而这些字段
            #    要随 HTTP 响应和报告落盘。round() 不会把 np.float64 变成 float。
            pnl = float(value + cur["sell_amt"] - cur["buy_amt"] - cur["cost"])
            hold = (len(common) - 1 - cur["open_i"]) if is_open else (t_close - cur["open_i"])
            rec = {
                "code": code,
                "buy_date": common[cur["open_i"]],
                "buy_notional": round(cur["buy_amt"], 2),
                "buy_reason": cur["buy_reason"],
                "buy_meta": cur["buy_meta"],
                "sell_date": None if is_open else common[t_close],
                "sell_notional": round(cur["sell_amt"], 2) if not is_open else None,
                "sell_reason": cur["sell_reason"] if not is_open else None,
                "sell_meta": cur["sell_meta"] if not is_open else None,
                "pnl": round(pnl, 2),
                "ret": (round(pnl / cur["buy_amt"], 4) if cur["buy_amt"] > 0 else None),
                "cost": round(float(cur["cost"]), 2),
                "holding_days": int(hold),
                "open": bool(is_open),
                "shares": cur["shares"],
                "buy_price": cur["buy_price"],
                "sell_price": None if is_open else cur["sell_price"],
                "feasible": cur["feasible_all"],
                "blocked_by": cur["blocked_by"],
            }
            feas = _feasibility(code, raw, common, cur["buy_days"], cur["sell_days"])
            rec["feasible"] = feas["feasible"]
            rec["blocked_by"] = feas["blocked_by"]
            rec["blocked_counts"] = feas["blocked_counts"]
            trades.append(rec)
            n_periods += 1
            if not is_open:
                n_closed += 1
            stock_pnl += pnl
            stock_buy_amt += cur["buy_amt"]
            cur = None

        for t in days:
            d = dw[t]
            nav_prev = float(nav[t - 1]) if t > 0 else float(nav[0])
            amt = abs(float(d)) * nav_prev
            is_buy = d > _EPS
            px = None
            if raw_close is not None:
                try:
                    r = raw_close.reindex(common[t: t + 1])
                    if len(r):
                        px = float(r.iloc[0])
                except Exception:            # noqa: BLE001
                    px = None
            # ⚠️ raw 序列在**停牌日**是 NaN。`int(NaN)` 会抛 ValueError 把整次回测带崩，
            #    而"这天没有价格"本身就是**停牌**的判定依据之一，不能当成 0 或跳过。
            #    统一在这里把无效价格收敛成 None，由下面的可行性判定去标 blocked_by。
            if px is not None and not (px == px and px > 0):   # NaN 或 <= 0
                px = None

            if cur is None:
                if not is_buy:                # 目标是 0：无持仓可平，跳过
                    continue
                cur = {"open_i": t, "buy_amt": 0.0, "sell_amt": 0.0, "cost": 0.0,
                       "buy_reason": "", "buy_meta": {}, "sell_reason": "", "sell_meta": {},
                       "shares": 0, "buy_price": px, "sell_price": None,
                       "feasible_all": True, "blocked_by": None,
                       "buy_days": [], "sell_days": []}

            if is_buy:
                cur["buy_amt"] += amt
                # 与 compute() 的净值口径**逐项对齐**：佣金取 max(金额×费率, 最低佣金)
                # （替换而非叠加），滑点按双边金额比例，卖出侧另加印花税。
                cur["cost"] += amt * slippage + (
                    max(amt * commission, min_commission) if min_commission > 0
                    else amt * commission)
                if not cur["buy_reason"]:
                    cur["buy_reason"], cur["buy_meta"] = _TRADE_REASONS.get(
                        (code, common[t]), ("", {}))
                if px:
                    cur["buy_price"] = cur["buy_price"] or px
                else:
                    # 无有效原始价 → 这笔无法定价成交（多半是停牌）
                    cur["feasible_all"] = False
                    cur["blocked_by"] = cur["blocked_by"] or "no_price"
                if lot_size > 0 and px:
                    sh = int(amt / (px * lot_size)) * lot_size
                    cur["shares"] += sh
                    if sh <= 0:
                        cur["feasible_all"] = False
                        cur["blocked_by"] = cur["blocked_by"] or "lot_size"
                cur["buy_days"].append(t)
            else:
                cur["sell_amt"] += amt
                cur["cost"] += amt * slippage + amt * stamp_duty + (
                    max(amt * commission, min_commission) if min_commission > 0
                    else amt * commission)
                if px:
                    cur["sell_price"] = px
                else:
                    cur["feasible_all"] = False
                    cur["blocked_by"] = cur["blocked_by"] or "no_price"
                if lot_size > 0 and px:
                    sh = int(amt / (px * lot_size)) * lot_size
                    if sh <= 0:
                        cur["feasible_all"] = False
                        cur["blocked_by"] = cur["blocked_by"] or "lot_size"
                cur["sell_days"].append(t)
                if float(w[t]) <= _EPS:
                    # 卖出来自「调仓日未输出」时索引查不到，用引擎合成的文案——
                    # 与 base.diff_trades 的口径保持一致，否则同一笔在两处显示不同理由。
                    cur["sell_reason"], cur["sell_meta"] = _TRADE_REASONS.get(
                        (code, common[t]), (_SELL_BY_ABSENCE, {}))
                    _close(t)

        _close(None)                          # 区间结束仍未平 → mark-to-market
        if n_periods:
            # 首笔交易摘要：供前端「按股票平铺」表格直接用，避免再异步拉逐笔明细。
            # 取该 code 买入日最早的一笔；未平仓的笔 sell_date 为空、ret 可能为 None。
            code_trades = [t for t in trades if t.get("code") == code]
            first = min(code_trades, key=lambda t: t.get("buy_date") or "") if code_trades else None
            first_ret = None
            if first is not None and first.get("ret") is not None:
                try:
                    first_ret = round(float(first["ret"]), 4)
                except (TypeError, ValueError):
                    first_ret = None
            per_stock.append({
                "code": code,
                "pnl": round(float(stock_pnl), 2),
                "total_return": (round(float(stock_pnl) / float(stock_buy_amt), 4)
                                 if stock_buy_amt > 0 else 0.0),
                "trades": n_periods,
                "closed": n_closed,
                "buy_date": first.get("buy_date") if first else "",
                "sell_date": first.get("sell_date") if first else "",
                "ret": first_ret,
                # 首笔买入时的策略因子（如 GRAYP），供前端结果表直接展示
                "grayp": ((first.get("buy_meta") or {}).get("grayp")
                          if first else None),
            })
        closed_all += n_closed

    per_stock.sort(key=lambda x: x["total_return"], reverse=True)
    trades.sort(key=lambda x: (x["code"], x["buy_date"]))
    return trades, per_stock, closed_all


# 由 backtest 注入：{(code, date): (reason, meta)}，供逐笔明细挂归因
def set_trade_reasons(index: dict) -> None:
    """注入 base.signal_meta_index 的产物，让逐笔明细带上 reason / meta。

    在 run_backtest / run_recommend 拿到标准化信号后立刻调一次。
    """
    global _TRADE_REASONS
    _TRADE_REASONS = index or {}


# ---------------------------------------------------------------- per_stock 截断

def truncate_per_stock(per_stock: list[dict], limit: int = 500) -> tuple[list[dict], dict]:
    """per_stock **双向截断**：收益降序头部 N + 尾部 N。

    旧版只取头部 500（共 5559 只），**看不到亏损端**，归因做不了。
    返回 (截断后的列表, 截断元信息)。
    """
    if not per_stock or limit <= 0 or len(per_stock) <= limit * 2:
        return per_stock, {"truncated": False, "total": len(per_stock)}
    # 前置条件：入参已按 total_return **降序**（compute 出口已排序）。
    head = per_stock[:limit]
    tail = per_stock[-limit:]
    return head + tail, {"truncated": True, "total": len(per_stock),
                         "kept": len(head) + len(tail),
                         "dropped_middle": len(per_stock) - len(head) - len(tail)}


def trades_for_response(trades: list[dict] | None,
                        limit: int = MAX_TRADES_RESPONSE) -> tuple[list[dict], dict]:
    """逐笔明细的**响应内联**裁剪：按买入日期倒序取最近 N 笔 + 截断元信息。

    ## 为什么按「买入日期倒序」而不是原顺序
    `_build_trades` 出口的逐笔是按 **(code, buy_date)** 排序的 —— 直接截前 N 笔
    等于只留下「代码靠前的那些票」，而用户翻这张表看的恰恰是**最近的成交**。
    故这里按买入日期倒序给：表头就是最近的交易，更早的在报告文件里。

    ## 为什么必须带元信息
    截断若不标注，就等于**静默改数据**：看表的人会以为「这就是全部成交」。
    故返回里同时给出总数与被丢掉的笔数（`trades_total` / `trades_truncated`），
    前端必须把「共 N 笔、显示 M 笔」写在表头上。
    """
    src = list(trades or [])
    total = len(src)
    if limit <= 0 or total <= limit:
        return src, {"truncated": False, "total": total, "kept": total,
                     "dropped": 0, "order": "buy_date_desc"}
    # 买入日期缺失（理论上不该有）排最后，不让空值顶掉真实交易。
    ordered = sorted(src, key=lambda t: (str(t.get("buy_date") or ""), str(t.get("code") or "")),
                     reverse=True)
    kept = ordered[:limit]
    return kept, {"truncated": True, "total": total, "kept": len(kept),
                  "dropped": total - len(kept), "order": "buy_date_desc"}