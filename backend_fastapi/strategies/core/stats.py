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

【成本分层】按「能否用 Δw × NAV 精确表达」分层，不是按「重要不重要」：
    进 equity（比例项，权重口径即可精确算出）
        佣金 commission / 印花税 stamp_duty（卖出单边）/ 滑点 slippage
        最低佣金 min_commission = max(notional × rate, 5.0)  ← 不需要股数
    只进 trades 明细（需要股数与价格粒度）
        整手 lot_size / 涨跌停 / 停牌 → 只产出「可行性」指标，不改净值

为什么这样分：本引擎的主要用途是**横向比较多策略的信号质量**，`equity` 需要一个
干净、与执行细节无关的口径；但把成本整体赶出净值会让成本模型退化成摆设，
而最低佣金恰好是能用金额精确表达的，不该被浪费。

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
      "trades": [ {...逐笔明细...} ],                # 由编排层取出落报告，不进 HTTP 响应
      "flags": [str, ...],                          # 空值/降级原因，永不静默
    }
"""
from __future__ import annotations

import math
import warnings

import numpy as np
import pandas as pd

SCHEMA_VERSION = 2
_TRADING_DAYS = 252.0      # 年化折算：固定日线，见模块 docstring
_EPS = 1e-12
# 单日涨跌幅超过这个值即判定为**数据错误**（而非行情）。A 股单日涨跌停上限是
# ±10%（创业板/科创板 ±20%、北交所 ±30%），留到 50% 的余量足以覆盖新股上市首日
# 与 ST 板的特殊情形，同时足够宽松、不会误杀真实行情。
# 实测本地库因复权因子缺失出现过 -88% ~ -96% 的假跌幅（详见 compute 里的说明）。
_IMPOSSIBLE_RET = 0.5
_BOARD_LIMIT = 0.198       # 创业板(30)/科创板(68) 涨跌停阈值
_MAIN_LIMIT = 0.098        # 主板阈值（ST 的 4.9% 需 ST 标识，本数据源没有，见 _limit_pct）

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


def _limit_pct(code: str) -> float:
    """该代码所属板块的涨跌停阈值。

    ⚠️ **ST 的 4.9% 判不出来**：涨跌停是真实市场规则（作用于**原始价**，
    hfq 会平滑除权跳空、掩盖真实涨跌停，故可行性判定一律用 raw），
    但本地行情里没有「是否 ST」的标识字段，所以 ST 票会按主板 9.8% 判，
    从而漏掉一部分 ST 的一字板。属于已知缺口，不静默——见「可行性」的 blocked 统计。
    """
    c = str(code)
    if c.startswith(("300", "301", "688", "689")):
        return _BOARD_LIMIT
    return _MAIN_LIMIT


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
    chg = (close / prev - 1.0).abs() if prev is not None else None
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
            if (high is not None and low is not None and len(high) and len(low)
                    and float(high.iloc[i]) == float(low.iloc[i])
                    and chg is not None and len(chg) and not pd.isna(chg.iloc[i])
                    and float(chg.iloc[i]) >= thr):
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
      raw_by_code        {code: DataFrame[high,low,close,volume]}（**未复权**），
                         只用于整手/涨跌停/停牌的可行性判定，不进净值；
                         调用方应只传**权重变动过**的 code（见 backtest.py）
      lot_size           每手股数，0 = 不做整手约束
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
    for j, code in enumerate(codes):
        w = positions_by_code[code].reindex(common).fillna(0.0).to_numpy(dtype=np.float32)
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

    for t in range(T):
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
    bench = R.mean(axis=1)                     # 与策略同池，零额外数据依赖
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
        "equity_model": "weight_idealized",      # 净值不含整手/涨跌停/停牌
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
    }

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


# ---------------------------------------------------------------- 单票明细（前端按需）

def trades_for_code(trades: list[dict] | None, code: str) -> list[dict]:
    """从已算好的逐笔明细里挑出某只标的（前端展开某票时按需调用）。

    **不再单票重算**：旧实现有独立的 `trade_detail()`，自己扫权重序列、用
    硬编码的 100000 折算股数，于是同一笔交易在「全市场明细」与「单票明细」里
    口径不一致（一个用真实 Δw×NAV、一个用假想资金带），报告没法交叉核对。
    组合权重口径下逐笔 pnl 就是全市场口径，直接过滤即可，顺带省掉一次重算。
    """
    return [t for t in (trades or []) if t.get("code") == code]


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