# -*- coding: utf-8 -*-
"""筹码密集上沿（主图叠加 HTOP）：位置最靠上的那座筹码峰的峰顶价位。

与 COST（峰位价）的区别 —— 两者是不同的点，别混用：
- 峰位价（COST）：最胖的那座山（占比最高）的峰顶；
- 本指标：位置最高的那座山（哪怕它比别的山矮得多）的峰顶。

判据只看位置、不看高度：找局部极大值，取其中价格最高的一个。
计算内核在 formulas.peak，本文件只做展示包装。
"""
from __future__ import annotations

import pandas as pd

from formulas.peak import top_price_peak_price_series

from .base import ParamSpec, indicator, series_hline
from .data import chip_rows_of, code_of


@indicator(
    id="HTOP",
    name="筹码密集上沿",
    category="chip",
    panel="main",
    params=[
        ParamSpec("formula", "choice", "tri_decay", label="筹码公式"),
        ParamSpec("min_pct", "float", 0.0, min=0.0, max=10.0, label="峰最小占比%"),
    ],
    styles={"筹码密集上沿": {"color": "#FF4D4F", "width": 1.4}},
)
def chip_top_peak(df: pd.DataFrame, *, ctx=None, formula: str = "tri_decay",
                  min_pct: float = 0.0) -> dict:
    rows, frames = chip_rows_of(df, formula_id=formula, code=code_of(ctx))
    prices = top_price_peak_price_series(rows, frames.centers, min_pct)
    return {"series": [series_hline("筹码密集上沿", pd.Series(prices, index=df.index))]}
