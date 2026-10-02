# -*- coding: utf-8 -*-
"""筹码成本线（主图叠加）：平均成本 + 峰位价。

都是**价格量纲**，与 K 线共用价格轴，故 panel="main"——平均成本线叠在主图上，
一眼能看出现价在成本线上方还是下方（= 获利盘还是套牢盘）。

数据来源：`data.chip_rows_of(df, formula)` 复用筹码公式算出的分布矩阵，
本文件不碰筹码算法，也不自行取数（指标约束见 ARCHITECTURE.md §3）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .base import ParamSpec, indicator, series_line
from .data import chip_rows_of


@indicator(
    id="chip_cost",
    name="筹码成本线",
    category="chip",
    panel="main",                    # 与价格同量纲 → 叠加在主图
    params=[
        ParamSpec("formula", "choice", "tri_decay", label="筹码公式"),
    ],
)
def chip_cost(df: pd.DataFrame, formula: str = "tri_decay") -> dict:
    rows, frames = chip_rows_of(df, formula_id=formula)
    centers = frames.centers
    avg, peak = [], []
    for i in range(len(rows)):
        row = rows[i]
        if np.all(np.isnan(row)):
            avg.append(float("nan"))
            peak.append(float("nan"))
            continue
        total = float(row.sum())
        avg.append(float((centers * row).sum() / total) if total > 0 else float("nan"))
        peak.append(float(centers[int(np.argmax(row))]))
    index = df.index
    return {"series": [
        series_line("平均成本", pd.Series(avg, index=index)),
        series_line("峰位价", pd.Series(peak, index=index)),
    ]}
