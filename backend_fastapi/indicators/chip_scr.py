# -*- coding: utf-8 -*-
"""筹码集中度 SCR（下方副图）：90% / 70% 筹码区间的相对宽度。

    SCR90 = (P95 - P5) / (P95 + P5)
    SCR70 = (P85 - P15) / (P85 + P15)

数值**越小越集中**（筹码挤在一个窄价位带），越大越发散。与 SCR 选股
（`chip_service.py`）是同一族概念，但那里是截面选股，这里是单票的时间序列。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .base import ParamSpec, indicator, series_line
from .data import chip_rows_of


def _percentile(centers: np.ndarray, row: np.ndarray, q: float) -> float:
    """按累计筹码取价格分位（线性插值）。"""
    total = float(row.sum())
    if total <= 0:
        return float("nan")
    return float(np.interp(q / 100.0, np.cumsum(row) / total, centers))


def _scr(low: float, high: float) -> float:
    if not np.isfinite(low) or not np.isfinite(high) or (high + low) == 0:
        return float("nan")
    return (high - low) / (high + low)


@indicator(
    id="chip_scr",
    name="筹码集中度",
    category="chip",
    panel="lower",
    params=[
        ParamSpec("formula", "choice", "tri_decay", label="筹码公式"),
    ],
)
def chip_scr(df: pd.DataFrame, formula: str = "tri_decay") -> dict:
    rows, frames = chip_rows_of(df, formula_id=formula)
    centers = frames.centers
    s90, s70 = [], []
    for i in range(len(rows)):
        row = rows[i]
        if np.all(np.isnan(row)):
            s90.append(float("nan"))
            s70.append(float("nan"))
            continue
        s90.append(_scr(_percentile(centers, row, 5), _percentile(centers, row, 95)))
        s70.append(_scr(_percentile(centers, row, 15), _percentile(centers, row, 85)))
    index = df.index
    return {"series": [
        series_line("集中度90", pd.Series(s90, index=index)),
        series_line("集中度70", pd.Series(s70, index=index)),
    ]}
