# -*- coding: utf-8 -*-
"""筹码峰公式 · EMA换手峰 + 锁仓染色模型（用户定义，与 tri_decay 同一衰减引擎）。

三层口径（用户给定）：
  · 蓝（base）：完全等同 tri_decay —— 存量筹码按「换手率 × 衰减系数 × 锁仓系数」衰减，
                当日成交按三角形分布（峰值=均价 vwap）铺到 [low, high] 后注入。
                即「蓝色 = TRI 的筹码峰」，外形与 tri_decay 完全一致。
  · 灰（gray）：锁仓沉积。当日换手 ≤ M_low×B 的档位标记为沉淀态，该档位蓝的 R（默认 0.5）
                染成灰（灰 = 蓝 × R，幂等不累积，恒 ≤ 蓝的一半）；
                强换手（> M_high×B）冲刷解除沉淀。灰随蓝同一衰减系数同步衰减。
  · 红（red）：强换手叠加。当日换手 > M_high×B 时，把「超出自然换手的额外部分」(k−kB)
                按当日三角分布注入为红；红同样按同一衰减系数衰减（与蓝同节奏）。

  B[t] = 1 / N[t]，N[t] 为「100% 换手周期」（由换手率前缀和反推，同 tri_decay 口径）。

输出：
  · `ema_lock(ctx, **params)` —— @chip_formula 注册，返回 (蓝底, {"red": 红, "gray": 灰})；
    蓝底经 core 按行归一化到 100；红/灰与蓝底同尺度归一化后经独立通道进入 frames。
  · `compute_ema_lock(ctx, **params)` —— 结构化结果（蓝/红/灰已同尺度归一化到 100、锁仓浓度），
    供指标侧走 dist 双分布（共用价格轴的热力图）使用。
"""
from __future__ import annotations

import dataclasses as dc

import numpy as np

from .core.base import ChipContext, ParamSpec, chip_formula
from .core.spread import triangle_cdf


@dc.dataclass
class EmaLockResult:
    """EMA 锁仓模型的结构化结果（已按行归一化到 100）。"""
    dates: list[str]            # 与 ctx 同序（含预热）
    centers: np.ndarray         # 价格轴（分箱中心）
    blue: np.ndarray            # (days, bins) 蓝底：全量换手 EMA 堆积（TRI 注入），行和=100
    red: np.ndarray             # (days, bins) 强换手 EMA 叠层（红），行和=100
    gray: np.ndarray            # (days, bins) 锁仓染灰（灰），行和=100
    lock_ratio: np.ndarray      # (days, bins) 锁仓浓度 = gray/(blue+gray+red)，范围 0~1
    blue_rel: np.ndarray        # (days, bins) 蓝底相对量（未归一）
    red_rel: np.ndarray         # (days, bins) 红相对量（未归一）
    gray_rel: np.ndarray        # (days, bins) 灰相对量（未归一）
    close: np.ndarray


def _turnover_to_n(turnover: np.ndarray) -> np.ndarray:
    """由换手率序列反推每日的『100% 换手周期』N[t]。

    令 pref[i+1] = Σ_{0..i} turnover；对第 t 日，找最大的 j≤t 使
    Σ_{j..t} turnover ≥ 1.0（即 pref[t+1] − pref[j] ≥ 1.0 → pref[j] ≤ pref[t+1]−1），
    则 N[t] = t − j + 1。

    早段 / 短窗口退化处理：当累计换手仍 <1.0 时，原本 N[t]=t+1（随 t 增长），
    会把 B=1/N 压到虚高 → 没有一天达到 M_high×B 阈值 → 整幅空白（实测 days=60/120
    时全 0）。改用「全窗口日均换手率」外推的 N：N_est = n / pref[n]，即把整段历史
    的换手率均值当作该票稳定的 100% 换手周期。这样 N 只取决于该票的换手属性、
    不再随**输出窗口长短**退化——短可见周期也能正常出红（且长窗口下早段帧的取值也更合理）。
    """
    n = len(turnover)
    pref = np.concatenate(([0.0], np.cumsum(turnover)))   # pref[k] = Σ turnover[0..k-1]
    # 全窗口均值外推的 100% 换手周期（换手率均值 = pref[n]/n → N = 1/均值 = n/pref[n]）
    N_est = (n / pref[n]) if pref[n] > 1e-12 else float(n)
    N = np.empty(n, dtype=float)
    for t in range(n):
        total = pref[t + 1]
        if total < 1.0:
            N[t] = N_est                       # 累计不足 100%：用均值外推，避免 B 虚高
            continue
        target = total - 1.0
        j = int(np.searchsorted(pref, target, side="right")) - 1   # 最大 j 使 pref[j] ≤ target
        N[t] = max(1, t - j + 1)
    return N


def _compute(ctx: ChipContext, M_low: float, M_high: float, R: float, k: float,
             decay: float = 1.0) -> EmaLockResult:
    """三态主计算（蓝底 / 红叠层 / 灰折旧），三层共用 tri_decay 的衰减引擎。

    蓝（blue）  ：等同 tri_decay —— 存量按 k_t = 换手×decay×锁仓系数 衰减，当日成交按三角分布注入。
    红（red）   ：强换手日（to > M_high×B）把超出自然换手的额外量 (k−kB) 注入，同 k_t 衰减。
    灰（gray）  ：锁仓档位（to ≤ M_low×B 覆盖）把蓝的 R 染灰（幂等，灰 = 蓝×R，恒 ≤ 蓝的一半）；
                 强换手冲刷解除沉淀。灰随蓝同衰减（由蓝派生，蓝衰减则灰同衰减）。
    k 参数为历史 EMA 记忆长度，现已不参与计算（保留以兼容旧参数）。
    """
    n = ctx.days
    bins = ctx.bins
    turnover = np.asarray(ctx.turnover, dtype=float)
    edges = np.asarray(ctx.edges, dtype=float)
    p_lo, p_hi = float(ctx.p_lo), float(ctx.p_hi)
    lockup = float(getattr(ctx, "lockup_factor", 1.0) or 1.0)   # 1/(1-r)，与 tri_decay 一致

    N = _turnover_to_n(turnover)
    B = 1.0 / N                                   # 自然换手率（逐日）

    chips_blue = np.zeros(bins, dtype=float)       # 蓝底：TRI 衰减堆积
    chips_red = np.zeros(bins, dtype=float)        # 红叠层：强换手叠加（同衰减）
    gray_mask = np.zeros(bins, dtype=float)        # 1 = 该档当前沉淀态；0 = 已解除
    blue_rel = np.zeros((n, bins), dtype=float)
    red_rel = np.zeros((n, bins), dtype=float)
    gray_rel = np.zeros((n, bins), dtype=float)

    for t in range(n):
        Bt = float(B[t])
        to = float(turnover[t])
        # —— TRI 注入：当日成交按三角分布铺到 [low, high]（峰值=vwap，Σ=1）——
        dlo = max(float(ctx.low[t]), p_lo)
        dhi = min(float(ctx.high[t]), p_hi)
        if dhi <= dlo:                             # 一字板 / 当日区间落在价格轴外
            probe = (float(ctx.low[t]) if float(ctx.high[t]) <= float(ctx.low[t])
                     else (dlo + dhi) / 2.0)
            idx = int(np.searchsorted(edges, probe, side="right") - 1)
            idx = min(max(idx, 0), bins - 1)
            w = np.zeros(bins)
            w[idx] = 1.0
        else:
            peak = (float(ctx.vwap[t]) if np.isfinite(ctx.vwap[t])
                    else (dlo + dhi) / 2.0)
            peak = min(max(peak, dlo), dhi)        # 均价必须落在当日区间内
            w = np.diff(triangle_cdf(edges, dlo, dhi, peak))   # Σ=1

        k_t = min(1.0, max(0.0, to * decay * lockup))      # 当日被换手比例（同 tri_decay）
        kB = min(1.0, max(0.0, Bt * decay * lockup))       # 自然换手对应的比例

        # ① 蓝底：衰减 + 三角注入（与 tri_decay 完全一致）
        if k_t > 0:
            chips_blue *= (1.0 - k_t)
            chips_blue += k_t * w

        # ② 红叠层：与蓝同衰减；强换手日注入「超出自然换手的额外部分」
        chips_red *= (1.0 - k_t)
        if to > M_high * Bt:
            extra = max(0.0, k_t - kB)
            if extra > 0:
                chips_red += extra * w

        # ③ 灰折旧（幂等）：锁仓档位把蓝的 R 染灰，强换手冲刷解除沉淀
        if to <= M_low * Bt:
            gray_mask[w > 1e-12] = 1.0
        elif to > M_high * Bt:
            gray_mask[w > 1e-12] = 0.0
        chips_gray = gray_mask * R * chips_blue

        blue_rel[t] = chips_blue
        red_rel[t] = chips_red
        gray_rel[t] = chips_gray

    # 蓝 / 红 / 灰 **同一尺度**归一化（都用蓝底的 rowsum）：
    # 红、灰都是蓝底的子集（灰 = 蓝×R、红 ⊂ 蓝），同分母后 red_norm / gray_norm 都不会超过蓝，
    # 在蓝底上染色才正确。蓝底本身归一化后即 TRI 标准筹码峰（外形与 tri_decay 一致）。
    base_sum = blue_rel.sum(axis=1)
    safe = np.where(base_sum > 0, base_sum, 1.0)
    blue_norm = blue_rel / safe[:, None] * 100.0
    red_norm = red_rel / safe[:, None] * 100.0
    gray_norm = gray_rel / safe[:, None] * 100.0
    tot = blue_rel + red_rel + gray_rel
    tot_safe = np.where(tot > 0, tot, 1.0)
    lock_ratio = np.where(tot > 0, gray_rel / tot_safe, 0.0)

    return EmaLockResult(
        dates=list(ctx.dates), centers=np.asarray(ctx.centers, dtype=float),
        blue=blue_norm, red=red_norm, gray=gray_norm, lock_ratio=lock_ratio,
        blue_rel=blue_rel, red_rel=red_rel, gray_rel=gray_rel,
        close=np.asarray(ctx.close, dtype=float),
    )


@chip_formula(
    id="ema_lock",
    name="EMA换手峰·锁仓染色",
    index="EMLK",
    category="chip",
    description="蓝=TRI(三角形分布+换手率衰减)标准筹码峰；低换手(≤M_low×B)把当前蓝的R染灰(锁仓)，"
                "强换手(>M_high×B)把超出自然换手的额外量染红；灰/红与蓝共用同一衰减系数。",
    params=[
        ParamSpec("M_low", "float", 0.7, min=0.1, max=3.0, label="锁仓倍量阈值"),
        ParamSpec("M_high", "float", 2.0, min=0.5, max=5.0, label="强换手倍量阈值"),
        ParamSpec("R", "float", 0.5, min=0.05, max=1.0, label="每次染灰比例"),
        ParamSpec("decay", "float", 1.0, min=0.0, max=5.0, label="衰减系数(同TRI)"),
        ParamSpec("k", "float", 0.5, min=0.1, max=2.0, label="EMA记忆(已弃用,保留兼容)"),
    ],
)
def ema_lock(ctx: ChipContext, M_low: float = 0.7, M_high: float = 2.0,
             R: float = 0.5, k: float = 0.5, decay: float = 1.0):
    """@chip_formula 入口：返回 **(蓝底, {"red": 红叠层, "gray": 灰折旧})** 元组。

    蓝底 = TRI 分布（与 tri_decay 完全一致，按行归一化到 100）；红/灰与蓝底同尺度归一化，
    经 core 的 red / gray 通道进入响应 frames，前端以 brgMode 渲染「蓝底 + 红叠层 + 灰折旧」三层。
    """
    res = _compute(ctx, M_low, M_high, R, k, decay=decay)
    # display = 蓝底（纯蓝，不含红/灰叠层），按行归一到 100；
    # red / gray 经独立通道输出，前端以 brgMode 渲染「蓝底 + 红叠层 + 灰折旧」。
    return (res.blue, {"red": res.red, "gray": res.gray})


def compute_ema_lock(ctx: ChipContext, M_low: float = 0.7, M_high: float = 2.0,
                     R: float = 0.5, k: float = 0.5, decay: float = 1.0) -> EmaLockResult:
    """结构化入口（已归一化）：供指标侧 dist 双分布 / 热力图使用。"""
    return _compute(ctx, M_low, M_high, R, k, decay=decay)


def compute_ema_lock_frames(code: str, params: dict | None = None,
                            days: int | None = None, bins: int = 80,
                            adjust: str = "qfq", period: str = "day") -> EmaLockResult:
    """按 code 取数并算 EMA 锁仓模型（code 级入口，复用包内唯一数据入口 data.load_input）。

    返回含预热段的完整结果；调用方按窗口切片即可（与 chipformulas.compute_matrix 对齐）。
    若只想在现有筹码窗口使用，直接用 formula_id=ema_lock 走 compute() 即可，不必调本函数。
    """
    from .core import data as _data
    params = params or {}
    inp = _data.load_input(code, days=days, bins=bins, adjust=adjust, period=period)
    ctx = ChipContext(
        dates=inp.dates, open=inp.open, high=inp.high, low=inp.low, close=inp.close,
        volume=inp.volume, turnover=inp.turnover, vwap=inp.vwap,
        float_shares=inp.float_shares, edges=inp.edges, centers=inp.centers,
        p_lo=float(inp.edges[0]), p_hi=float(inp.edges[-1]), adjust=inp.adjust,
        lockup_ratio=inp.lockup_ratio, lockup_factor=inp.lockup_factor,
    )
    M_low = float(params.get("M_low", 0.7))
    M_high = float(params.get("M_high", 2.0))
    R = float(params.get("R", 0.5))
    k = float(params.get("k", 0.5))
    decay = float(params.get("decay", 1.0))
    return compute_ema_lock(ctx, M_low, M_high, R, k, decay=decay)
