# -*- coding: utf-8 -*-
"""锁仓系数 L = 1/(1 - r)（**不显示**：panel="none"）。

r 为前十大**流通**股东中合计占比 > 5% 者（举牌线以上、属长期持有的锁仓筹码）的合计占比；
L 即把它们剔除出可自由流通盘后的换手放大系数，喂给筹码衰减公式
（tri_decay 里 k = min(1, 换手率 × decay × L)）。

本指标**只算、不渲染**（panel="none"，见 ARCHITECTURE.md §5）：作为「能枚举、能算、不显示」
的派生量，与 `chipformulas.core.data.lockup_factor_of` 共用同一份口径，供 PEAK 指标 /
策略在需要「当前锁仓系数」时直接调用，避免各算各的漂移。取数失败回退 1.0。
"""
from __future__ import annotations

import pandas as pd

from .base import indicator, series_line


@indicator(
    id="LOCKUP",
    name="锁仓系数",
    category="chip",
    panel="none",                    # 能枚举、能算、不显示（供指标 / 策略复用）
    params=[],
)
def chip_lockup(df: pd.DataFrame) -> dict:
    code = str(df.attrs.get("code") or "").strip()
    if not code:
        # 合成数据自检（scan 用、无 code）走这里：不触发任何外部 import，避免与
        # indicators.data ↔ chipformulas.core.data 的循环依赖在导入期互锁。
        series = pd.Series([float("nan")] * len(df), index=df.index)
    else:
        # 惰性导入：仅在真正带 code 计算时才 import chipformulas.core.data，
        # 此时所有模块已完全加载，不会撞上半初始化的循环依赖。
        from chipformulas.core.data import lockup_factor_of
        L = lockup_factor_of(code)              # 1/(1-r)，失败回退 1.0
        series = pd.Series([L] * len(df), index=df.index)
    return {"series": [series_line("锁仓系数L", series)]}
