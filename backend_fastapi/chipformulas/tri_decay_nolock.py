# -*- coding: utf-8 -*-
"""tri_decay 的无锁仓修正变体（策略里叫「全部用1」）：锁仓系数强制为 1。

与 tri_decay 完全同算法，只是把 `ctx.lockup_factor` / `lockup_ratio` 强制成
1.0 / 0.0 —— 等价于「锁仓系数 = 1，不做锁仓放大」。用于对比「带锁仓系数 L」与
「不修正」两种口径下的筹码峰（PEAK）差异。

实现上直接复用 `tri_decay`（用 dataclasses.replace 造一个锁仓关掉的 ctx），
不复制算法，所以两处永远同口径。
"""
from __future__ import annotations

from dataclasses import replace

from .core.base import ChipContext, ParamSpec, chip_formula
from .tri_decay import tri_decay


@chip_formula(
    id="tri_decay_nolock",
    name="三角形分布 · 无锁仓修正",
    index="TRINL",
    category="chip",
    description="与 tri_decay 同算法，但强制锁仓系数=1（不放大换手），"
                "用于对比锁仓修正前后的筹码分布。",
    params=[
        ParamSpec("decay", "float", 1.0, min=0.0, max=5.0, label="衰减系数"),
    ],
)
def tri_decay_nolock(ctx: ChipContext, decay: float = 1.0) -> "np.ndarray":
    """锁仓关掉的 ctx 上跑一遍 tri_decay。"""
    ctx_nolock = replace(ctx, lockup_factor=1.0, lockup_ratio=0.0)
    return tri_decay(ctx_nolock, decay=decay)
