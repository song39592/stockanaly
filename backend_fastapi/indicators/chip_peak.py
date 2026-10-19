# -*- coding: utf-8 -*-
"""筹码峰占比（下方副图）：每日筹码分布中最高峰（最集中的价格分箱）所占比例（%）。

每行分布合计为 100，取其中最大值即「最拥挤成本价位」上聚集的筹码比例：
数值越大说明筹码越向单一价位高度集中（主力成本越明确），越小说明成本越分散。

数据来源：`data.chip_rows_of(df, formula)` 复用筹码公式算出的分布矩阵，
本文件不碰筹码算法，也不自行取数（指标约束见 ARCHITECTURE.md §3）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .base import ParamSpec, indicator, series_line
from .data import chip_rows_of


@indicator(
    id="PEAK",
    name="筹码峰占比",
    category="chip",
    panel="lower",                     # 0~100，独立纵轴
    params=[
        ParamSpec("formula", "choice", "tri_decay", label="筹码公式"),
    ],
)
def chip_peak(df: pd.DataFrame, formula: str = "tri_decay") -> dict:
    rows, _ = chip_rows_of(df, formula_id=formula)
    out = []
    for i in range(len(rows)):
        row = rows[i]
        if np.all(np.isnan(row)):
            out.append(float("nan"))
            continue
        out.append(float(np.nanmax(row)))        # 最高峰分箱占比（行合计 100 → 即 %）
    return {"series": [series_line("峰占比%", pd.Series(out, index=df.index))]}
