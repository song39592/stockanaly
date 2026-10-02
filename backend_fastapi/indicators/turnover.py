# -*- coding: utf-8 -*-
"""换手率（**不显示**：panel="none"）。

panel="none" 的用途（见 ARCHITECTURE.md §5）：照常注册、可经 compute / batch 计算，
但前端不把它放进任何渲染面板——用于导出、AI 调研投喂、或给其它逻辑复用。
换手率本身不值得占一条副图（成交量柱已把同一信息画出来了），但它是筹码衰减的
驱动量，排障与导出时常要单独看，故保留为「能算、能枚举、不显示」。

口径：成交量(手) × 100 ÷ 流通股本(股)，按 [0,1] 截断后 ×100 转成百分数。
缺流通股本时整条序列为 NaN（不静默补 0）。
"""
from __future__ import annotations

import pandas as pd

from .base import indicator, series_line
from .data import get_turnover


@indicator(
    id="turnover",
    name="换手率",
    category="volume",
    panel="none",                    # 能枚举、能算、不显示（供导出 / 内部复用）
    params=[],
)
def turnover(df: pd.DataFrame) -> dict:
    code = str(df.attrs.get("code") or "").strip()
    if not code:
        series = pd.Series([float("nan")] * len(df), index=df.index)
    else:
        try:
            series = get_turnover(code).reindex(df.index) * 100.0
        except RuntimeError:                 # 缺流通股本：整条留空，不静默补 0
            series = pd.Series([float("nan")] * len(df), index=df.index)
    return {"series": [series_line("换手率%", series)]}
