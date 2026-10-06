# -*- coding: utf-8 -*-
"""筹码分布公式 · **三角形分布 + 换手率衰减**（默认公式）。

算法（逐日演进）：
    T_t  当日换手率（小数）= 成交量(股) ÷ 流通股本(股)
    A    衰减系数 decay（默认 1.0，公式参数）
    L    锁仓修正系数 ctx.lockup_factor = 1/(1-r)（自动，见下）
    k_t = min(1, T_t × A × L)          # 当日被换手的筹码比例
    存量：chips *= (1 - k_t)            # 老筹码按换手比例衰减
    新增：把 k_t 按**三角形分布**铺到当日 [low, high]，峰值在当日均价（vwap）

返回**相对**筹码量（非负即可，core 会按行归一化到 100%）。

锁仓修正（已接入）：r = 前十大**流通**股东中「占流通股比例 > 5%」者的合计占比，
修正系数 L = 1/(1-r)（例：15.00% + 9.35% → 1/(1-24.35%) = 1.32）。

**为什么阈值是 5%**：5% 是**举牌线**（要约收购 / 权益变动披露界限）。持股超过 5% 的
股东受减持规则约束、属长期持有人，其筹码**不进入日常流通**，故不计入可自由流通部分。
这是「原则上扣除前十大流通股东超 5% 的即可」的依据。

**为什么系数 > 1（衰减变快）**：正因为这部分筹码不参与换手，同样的成交量只能在**剩下的
可自由流通筹码**内部倒手，实际换手速度天然更快——筹码交换更快是**符合预期**的结果，
把名义换手率放大 1/(1-r) 倍正是还原这一点（也等价于用「扣除锁定后的自由流通股本」
而不是名义流通股本去重算换手率）。

r 由 `core/data.py` 取数（失败回退 0，即 L=1）。
注：「5% 以下但属于锁仓」的情形需人工判断，本公式只自动处理 >5% 部分。
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
    ],
)
def tri_decay(ctx: ChipContext, decay: float = 1.0) -> np.ndarray:
    """返回 shape = (交易日数, 分箱数) 的相对筹码量矩阵。"""
    n = ctx.days
    bins = ctx.bins
    lockup = float(getattr(ctx, "lockup_factor", 1.0) or 1.0)   # 1/(1-r)，自动
    chips = np.zeros(bins, dtype=float)
    out = np.zeros((n, bins), dtype=float)

    for i in range(n):
        k = min(1.0, max(0.0, float(ctx.turnover[i]) * decay * lockup))
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
