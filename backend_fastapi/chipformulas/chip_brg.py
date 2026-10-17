# -*- coding: utf-8 -*-
"""筹码峰公式 · 蓝/红/灰三态模型。

设计（用户给定）：base 为「原先的筹码峰」全量堆积（不衰减），在其上叠加两层状态：

  · 蓝（base）     全量堆积的筹码存量，每把当日换手按三角分布铺到价格轴并累加，
                  只增不减。即「历史上在这个价格档位成交过多少」。
  · 红（red）      强换手叠层：当 to > 2B 时，把 (to − B) 按三角分布铺到当天 [low, high]
                  并累加。这是「超出自然换手的部分」，代表被活跃交易的额外筹码。
  · 灰（gray）     沉淀折旧：当 to < B/2 时，把当天 [low, high] 区间标记为沉淀态；
                  沉淀态的灰色量恒为「该档位当前蓝 × gray_ratio（默认 0.5）」，
                  即灰色是蓝色的一半，随 base 同步增长、不会被后续新量稀释。

颜色映射（三层，不混色）：
  base 用蓝色铺底；强换手日对应的档位额外叠红色；低换手沉淀的档位逐渐变灰。

对比
----
  · ema_lock：   单一红峰 + 灰叠层（分布量染色）
  · 本模型：    base（蓝底）+ red（红叠层）+ gray（灰折旧）→ 三分布，红/灰各自独立累积

集成说明
--------
入口 `chip_brg()` 返回 `(display, {"gray": gray_norm, "red": red_norm})`：
  · display = base 按行归一化到 100（纯蓝底筹码峰，红叠层单列）；
  · gray / red 与 display 同一 rowsum 归一化，经 core 的 gray / red 通道进入响应 frames。
"""
from __future__ import annotations

import dataclasses as dc

import numpy as np

from .core.base import ChipContext, ParamSpec, chip_formula
from .core.spread import triangle_cdf


@dc.dataclass
class BgrResult:
    """蓝/红/灰模型的结构化结果。"""
    dates: list[str]             # 与 ctx 同序（含预热）
    centers: np.ndarray          # 价格轴（分箱中心）
    base: np.ndarray             # (days, bins) 全量堆积存量（蓝底），只增不减
    red: np.ndarray              # (days, bins) 强换手叠层（红）
    gray: np.ndarray             # (days, bins) 沉淀折旧量（灰）
    blue: np.ndarray             # (days, bins) 当前有效蓝（base 中未沉淀部分）
    display: np.ndarray          # (days, bins) = base（纯蓝底），已按行归一化到 100
    close: np.ndarray


def _turnover_to_n(turnover: np.ndarray) -> np.ndarray:
    """由换手率序列反推每日的『100% 换手周期』N[t]。"""
    n = len(turnover)
    pref = np.concatenate(([0.0], np.cumsum(turnover)))
    N_est = (n / pref[n]) if pref[n] > 1e-12 else float(n)
    N = np.empty(n, dtype=float)
    for t in range(n):
        total = pref[t + 1]
        if total < 1.0:
            N[t] = N_est
            continue
        target = total - 1.0
        j = int(np.searchsorted(pref, target, side="right")) - 1
        N[t] = max(1, t - j + 1)
    return N


def _tri_inject(ctx: ChipContext, t: int, turnover_t: float) -> np.ndarray:
    """把第 t 日的成交量按三角分布铺到价格轴（峰值=vwap），返回 Σ=1 的权重向量。"""
    n_bins = ctx.bins
    edges = np.asarray(ctx.edges, dtype=float)
    p_lo, p_hi = float(ctx.p_lo), float(ctx.p_hi)
    dlo = max(float(ctx.low[t]), p_lo)
    dhi = min(float(ctx.high[t]), p_hi)

    if dhi <= dlo:   # 一字板 / 当日区间落在价格轴外 → 集中到最近档位
        probe = float(ctx.low[t]) if float(ctx.high[t]) <= float(ctx.low[t]) else (dlo + dhi) / 2.0
        idx = int(np.searchsorted(edges, probe, side="right") - 1)
        idx = min(max(idx, 0), n_bins - 1)
        w = np.zeros(n_bins)
        w[idx] = 1.0
        return w

    peak = float(ctx.vwap[t]) if np.isfinite(ctx.vwap[t]) else (dlo + dhi) / 2.0
    peak = min(max(peak, dlo), dhi)
    w = np.diff(triangle_cdf(edges, dlo, dhi, peak))   # Σ=1
    return w


def _compute(ctx: ChipContext,
             M_high: float, M_low: float, gray_ratio: float) -> BgrResult:
    """蓝/红/灰三态主计算。"""
    n = ctx.days
    bins = ctx.bins
    turnover = np.asarray(ctx.turnover, dtype=float)

    N = _turnover_to_n(turnover)
    B = 1.0 / N                      # 自然换手率（逐日）

    base_total = np.zeros((n, bins), dtype=float)   # 全量堆积（蓝底），只增不减
    red = np.zeros((n, bins), dtype=float)          # 强换手叠层（红）
    gray = np.zeros((n, bins), dtype=float)         # 沉淀折旧（灰）
    blue = np.zeros((n, bins), dtype=float)         # 当前有效蓝（base 中未沉淀部分）

    base_acc = np.zeros(bins, dtype=float)
    red_acc = np.zeros(bins, dtype=float)
    gray_mask = np.zeros(bins, dtype=float)   # 1 = 该档位被低换手日覆盖（沉淀态）

    for t in range(n):
        Bt = float(B[t])
        to = float(turnover[t])
        w = _tri_inject(ctx, t, to)                 # Σ=1 的当日分箱权重，覆盖 [low, high]

        # —— ① base（蓝底）：全量堆积，只增不减 ——
        base_acc += to * w
        base_total[t] = base_acc

        # —— ② red（红叠层）：强换手日 (to − B) 在 [low, high] 累加 ——
        if to > M_high * Bt:
            red_acc += (to - Bt) * w
        red[t] = red_acc

        # —— ③ gray（灰折旧）：低换手日覆盖的档位标记为沉淀；
        #     灰量 = 该档位【当前蓝】× gray_ratio（默认 1/2），随 base 同步增长。
        #     这样「灰 = 当前蓝 × 0.5」恒定成立，不会被后续新注入的蓝稀释。
        if to < M_low * Bt:
            gray_mask[w > 1e-12] = 1.0
        gray[t] = gray_mask * gray_ratio * base_acc
        blue[t] = base_acc - gray[t]

    # 主分布 display = base（纯蓝底，不含红叠层），按行归一化到 100
    rowsum = base_total.sum(axis=1)
    safe = np.where(rowsum > 0, rowsum, 1.0)
    display_norm = base_total / safe[:, None] * 100.0

    # 红 / 灰 必须与 display 同尺度（用同一 rowsum 归一化）：
    # 前端按 pct 的 maxPct 统一缩放，单箱宽度 red/gray ≤ pct，红叠层与灰折旧不会失真。
    red_norm = red / safe[:, None] * 100.0
    gray_norm = gray / safe[:, None] * 100.0

    return BgrResult(
        dates=list(ctx.dates),
        centers=np.asarray(ctx.centers, dtype=float),
        base=base_total,
        red=red_norm,
        gray=gray_norm,
        blue=blue,
        display=display_norm,
        close=np.asarray(ctx.close, dtype=float),
    )


@chip_formula(
    id="chip_brg",
    name="蓝红灰筹码峰",
    index="BRG",
    category="chip",
    description="base 全量堆积（蓝底）+ 红叠层（强换手 to>2B 的 to−B）+ 灰折旧（低换手 to<B/2 区间灰=蓝×gray_ratio）。"
                "display=base；红/灰经独立通道输出供三层染色。",
    params=[
        ParamSpec("M_high", "float", 2.0, min=0.5, max=5.0, label="强换手倍量阈值(×B)"),
        ParamSpec("M_low", "float", 0.5, min=0.1, max=3.0, label="低换手倍量阈值(×B)"),
        ParamSpec("gray_ratio", "float", 0.5, min=0.01, max=1.0, label="每沉淀日转灰比例"),
    ],
)
def chip_brg(ctx: ChipContext, M_high: float = 2.0, M_low: float = 0.5,
             gray_ratio: float = 0.5):
    """@chip_formula 入口：返回 (display, {"gray": 灰浓度, "red": 红叠层})。

    display = base 按行归一化到 100；gray / red 与 display 同尺度归一化，
    分别经 core 的 gray / red 通道进入响应 frames.gray / frames.red。
    """
    res = _compute(ctx, M_high, M_low, gray_ratio)
    return (res.display, {"gray": res.gray, "red": res.red})


def compute_chip_brg(ctx: ChipContext, M_high: float = 2.0, M_low: float = 0.5,
                     gray_ratio: float = 0.5) -> BgrResult:
    """结构化入口：供指标侧 / 热力图使用。"""
    return _compute(ctx, M_high, M_low, gray_ratio)


def compute_chip_brg_frames(code: str, params: dict | None = None,
                            days: int | None = None, bins: int = 80,
                            adjust: str = "qfq", period: str = "day") -> BgrResult:
    """按 code 取数并算蓝/红/灰模型（code 级入口）。"""
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
    return compute_chip_brg(
        ctx,
        M_high=float(params.get("M_high", 2.0)),
        M_low=float(params.get("M_low", 0.5)),
        gray_ratio=float(params.get("gray_ratio", 0.5)),
    )
