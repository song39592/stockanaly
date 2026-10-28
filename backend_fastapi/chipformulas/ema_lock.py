# -*- coding: utf-8 -*-
"""筹码峰公式 · 方案③：EMA 换手峰 + 锁仓染色模型。

设计文档（用户给定）：chip-ema-lock-model.md。核心思想与 tri_decay（三角形分布 + 乘法衰减）不同——
这里用 **EMA 时间权重**做衰减，并把筹码分成两态：

  · 红（强换手分布）：仅「换手率 > M_high×B」的交易日，把 (turnover−B) 按三角分布铺到当日
    [low, high]（峰值=当日均价 vwap），再按 EMA 递归堆积；衰减完全由 EMA 的 (1−α) 提供。
  · 灰（锁仓分布）：仅「换手率 ≤ M_low×B」的交易日，把**当前蓝底**按比例 R 染灰。
    染灰只是给已沉淀的筹码打状态标记，**不把筹码从蓝里搬走**，因此灰恒为蓝的子集
    （灰 ≤ 蓝）：首次锁仓灰=0.5×蓝，再次锁仓灰 += 0.5×(蓝−灰) → 0.75、0.875… 逐步逼近蓝，
    永不越过蓝。灰与蓝、红用**同一个衰减系数**，两次锁仓之间灰/蓝比例保持不变。

**B 与 EMA 记忆长度都随票自适应**（不像 tri_decay 用固定衰减系数）：
  · N[t] = 100% 换手周期，由换手率序列「前缀和 + bisect」反推；B[t] = 1 / N[t]；
  · span[t] = N[t] × k（用换手周期归一化，使不同票记忆长度含义一致），α[t] = 2 / (span[t]+1)。

**EMA 递推的两种读法**（见文档 §4 与 §2 的张力），本实现采用更稳健的一种：
  每一天都执行 `chips_red = α×injected + (1−α)×chips_red`——
    - 活跃日：injected = (turnover−B) × 当日分箱权重（>0，堆积新筹码）；
    - 滤除日 / 锁仓日：injected = 0，只剩 (1−α) 衰减（分布随时间自然遗忘，不会在静默期冻结）。
  锁仓日再额外做红→灰染灰。若想要「静默期完全冻结、不衰减」的严格读法，把主循环里
  非活跃日的 EMA 步进去掉即可（保留注释版）。

输出：
  · `ema_lock(ctx, **params)` —— @chip_formula 注册，返回**红**单矩阵（供现有筹码窗口，
    经 core 按行归一化到 100）；
  · `compute_ema_lock(ctx, **params)` —— 结构化结果（红/灰已按行归一化到 100、锁仓浓度），
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
    red: np.ndarray             # (days, bins) 强换手 EMA 叠层（红）：蓝的子集，单箱 ≤ 蓝
    gray: np.ndarray            # (days, bins) 锁仓染灰（灰）：蓝的子集，单箱 ≤ 蓝
    lock_ratio: np.ndarray      # (days, bins) 锁仓浓度 = gray/blue，范围 0~1
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


def _compute(ctx: ChipContext, M_low: float, M_high: float, R: float, k: float) -> EmaLockResult:
    """三态主计算（蓝底 / 红叠层 / 灰折旧），红蓝均用 EMA 衰减（正常的 K 控制）。

    蓝（blue）  ：每日**全量**换手按三角分布（TRI）铺到 [low, high]，EMA 递归堆积
                `chips = a×(换手×w) + (1−a)×chips`（a 由平均换手周期 N 与 k 决定，固定）。
                蓝是**全量存量**，任何一步都不会被抽走 —— 灰/红都只是它的子集。
    红（red）   ：仅「换手率 > M_high×B」的强换手日注入当日 `换手×w`，同样 EMA 衰减，
                是蓝底中来自强换手日的子集（天然单箱 ≤ 蓝），叠在蓝上染红。
    灰（gray）  ：「换手率 ≤ M_low×B」的锁仓日把**当前蓝底**按 R（默认 0.5）染灰。
                染灰是状态标记，不从蓝里搬量：灰 += R×(蓝−灰) → 0.5、0.75、0.875… 逼近蓝，
                恒有 灰 ≤ 蓝；灰与蓝、红用同一 EMA 系数衰减，锁仓间隔内灰/蓝比例不变。
    """
    n = ctx.days
    bins = ctx.bins
    turnover = np.asarray(ctx.turnover, dtype=float)
    edges = np.asarray(ctx.edges, dtype=float)
    p_lo, p_hi = float(ctx.p_lo), float(ctx.p_hi)

    N = _turnover_to_n(turnover)
    B = 1.0 / N                                   # 自然换手率（逐日）
    Nm = N[np.isfinite(N) & (N > 0)]
    a = 2.0 / (float(np.mean(Nm)) * k + 1.0) if Nm.size else 0.1   # 固定 EMA 系数（正常的 K 控制衰减）

    chips_blue = np.zeros(bins, dtype=float)       # 蓝底：全量 EMA 堆积（只增不减的相对量）
    chips_red = np.zeros(bins, dtype=float)        # 红叠层：强换手 EMA 堆积（蓝的子集）
    chips_gray = np.zeros(bins, dtype=float)       # 灰：锁仓染灰量（蓝的子集，恒 ≤ 蓝）
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

        # ① 蓝底：全量 EMA 堆积（正常的 K 衰减；a 固定，强换手日 to 大 → 注入大）
        chips_blue = a * (to * w) + (1.0 - a) * chips_blue

        # ② 红叠层：同 EMA 衰减；仅强换手日注入 to×w（强换手日 to 大 → 红色明显且不超蓝）
        chips_red = (1.0 - a) * chips_red
        if to > M_high * Bt:
            chips_red += a * (to * w)

        # ③ 灰折旧：锁仓日把「当前蓝底」按 R（默认 0.5）染灰 —— **只打状态标记，不从蓝里搬量**。
        #    灰 += R×(蓝−灰)：首次 0.5×蓝，再锁仓 0.75×蓝，再 0.875×蓝 … 逐步逼近蓝，
        #    恒有 灰 ≤ 蓝（R ≤ 1 时归纳成立），所以画面上灰色永远画不出蓝底的右边界。
        #    灰与蓝、红用同一个 EMA 系数 a 衰减：锁仓间隔内灰/蓝比例保持不变（衰减一致）。
        chips_gray = (1.0 - a) * chips_gray
        if to <= M_low * Bt:
            chips_gray += R * (chips_blue - chips_gray)

        blue_rel[t] = chips_blue
        red_rel[t] = chips_red
        gray_rel[t] = chips_gray

    # 蓝 / 红 / 灰 **同一分母**（蓝底的全量行和）：
    #   · 蓝是存量全量，行和 = 100；灰、红都是蓝的子集，同分母后**单箱必有 灰 ≤ 蓝、红 ≤ 蓝**；
    #   · 前端 brgMode 三层用同一个 maxPct 缩放，灰/红就永远画不出蓝底之外。
    # ⚠️ 旧版把灰从蓝里抽走、再用 (蓝+灰) 作分母：蓝被 core 再归一到 100 后与灰/红不同尺度，
    #    实测 496/500 帧出现「灰 > 蓝」（画面上灰/红捅出蓝底）——不要改回这种做法。
    base_sum = blue_rel.sum(axis=1)
    safe = np.where(base_sum > 0, base_sum, 1.0)
    blue_norm = blue_rel / safe[:, None] * 100.0
    red_norm = red_rel / safe[:, None] * 100.0
    gray_norm = gray_rel / safe[:, None] * 100.0
    blue_safe = np.where(blue_rel > 0, blue_rel, 1.0)
    lock_ratio = np.where(blue_rel > 0, gray_rel / blue_safe, 0.0)

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
    description="三态筹码峰：蓝底=全量换手 TRI 分布（EMA 衰减，正常 K 控制），"
                "红叠层=强换手日(to>M_high×B)的当日筹码，灰=锁仓日(to≤M_low×B)把蓝底按 R 染灰。",
    params=[
        # 默认 M_low=0.7（非方案③文档 §9 写的 1.0）：实测 M_low=1.0 时锁仓日占比高达 ~66%，
        # 远超文档 §10 自估的 25~40%；M_low=0.7 实测 ~35%，正好命中 §10 目标。B=1/N 为换手率均值，
        # 换手率右偏，turnover ≤ M_low×B 覆盖日数随 M_low 下降而减少。
        ParamSpec("M_low", "float", 0.7, min=0.1, max=3.0, label="锁仓倍量阈值"),
        ParamSpec("M_high", "float", 2.0, min=0.5, max=5.0, label="强换手倍量阈值"),
        ParamSpec("R", "float", 0.5, min=0.05, max=1.0, label="每次染灰比例"),
        ParamSpec("k", "float", 0.5, min=0.1, max=2.0, label="EMA记忆(k=N×k)"),
    ],
)
def ema_lock(ctx: ChipContext, M_low: float = 0.7, M_high: float = 2.0,
             R: float = 0.5, k: float = 0.5):
    """@chip_formula 入口：返回 **(蓝底, {"red": 红叠层, "gray": 灰折旧})** 元组。

    蓝底 = 全量 TRI 分布（按行归一化到 100）；红/灰与蓝底同尺度归一化，经 core 的
    red / gray 通道进入响应 frames，前端以 brgMode 渲染「蓝底 + 红叠层 + 灰折旧」三层。
    """
    res = _compute(ctx, M_low, M_high, R, k)
    # display = 蓝底（纯蓝，不含红/灰叠层），按行归一到 100；
    # red / gray 经独立通道输出，前端以 brgMode 渲染「蓝底 + 红叠层 + 灰折旧」。
    return (res.blue, {"red": res.red, "gray": res.gray})


def compute_ema_lock(ctx: ChipContext, M_low: float = 0.7, M_high: float = 2.0,
                     R: float = 0.5, k: float = 0.5) -> EmaLockResult:
    """结构化入口（已归一化）：供指标侧 dist 双分布 / 热力图使用。"""
    return _compute(ctx, M_low, M_high, R, k)


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
    return compute_ema_lock(ctx, M_low, M_high, R, k)
