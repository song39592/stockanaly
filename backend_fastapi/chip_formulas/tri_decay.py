# -*- coding: utf-8 -*-
"""筹码分布公式 · **三角形分布 + 换手率衰减**（默认公式）。

算法（逐日演进）：
    T_t  当日换手率（小数）= 成交量(股) ÷ 流通股本(股)
    A    衰减系数 decay（默认 1.0）
    L    锁仓修正系数 lockup_decay（默认 1.0，**暂不启用**）
    k_t = min(1, T_t × A × L)          # 当日被换手的筹码比例
    存量：chips *= (1 - k_t)            # 老筹码按换手比例衰减
    新增：把 k_t 按**三角形分布**铺到当日 [low, high]，峰值在当日均价（vwap）

返回**相对**筹码量（非负即可，core 会按行归一化到 100%）。

锁仓修正（预留，未启用）：真实衰减应剔除不参与流通的锁定筹码（前十大流通股东、
限售股等）——它们不随换手衰减，全按流通盘算会**高估**衰减速度。本项目没有股东数据，
故 lockup_decay 固定 1.0，只留参数接口；接入数据源后传入真实比例即可，主体无需改动。
"""
from __future__ import annotations

import numpy as np

from .core.base import ChipContext, ParamSpec, chip_formula
from .core.spread import triangle_cdf


@chip_formula(
    id="tri_decay",
    name="三角形分布 · 换手率衰减",
    category="chip",
    description="当日成交按三角形分布（峰值=当日均价）摊到 [low, high]，"
                "存量筹码按换手率 × 衰减系数衰减。",
    params=[
        ParamSpec("decay", "float", 1.0, min=0.0, max=5.0, label="衰减系数"),
        ParamSpec("lockup_decay", "float", 1.0, min=0.0, max=1.0,
                  label="锁仓修正（缺股东数据，1.0=不修正）"),
    ],
)
def tri_decay(ctx: ChipContext, decay: float = 1.0,
              lockup_decay: float = 1.0) -> np.ndarray:
    """返回 shape = (交易日数, 分箱数) 的相对筹码量矩阵。"""
    n = ctx.days
    bins = ctx.bins
    chips = np.zeros(bins, dtype=float)
    out = np.zeros((n, bins), dtype=float)

    for i in range(n):
        k = min(1.0, max(0.0, float(ctx.turnover[i]) * decay * lockup_decay))
        if k > 0:
            chips *= (1.0 - k)                              # 存量衰减
            day_lo = float(ctx.low[i])
            day_hi = float(ctx.high[i])
            dlo = max(day_lo, ctx.p_lo)
            dhi = min(day_hi, ctx.p_hi)
            if day_hi <= day_lo or dhi <= dlo:
                # 一字板（整日成交集中在一个价）/ 当日区间落在价格轴之外
                probe = day_lo if day_hi <= day_lo else (dlo + dhi) / 2.0
                idx = int(np.searchsorted(ctx.edges, probe, side="right") - 1)
                chips[min(max(idx, 0), bins - 1)] += k
            else:
                peak = float(ctx.vwap[i]) if np.isfinite(ctx.vwap[i]) else (dlo + dhi) / 2.0
                peak = min(max(peak, dlo), dhi)             # 均价必须落在当日区间内
                chips += k * np.diff(triangle_cdf(ctx.edges, dlo, dhi, peak))
        out[i] = chips
    return out
