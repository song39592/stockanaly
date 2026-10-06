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
from features.chip.formulas.core import scr


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
    # 第 10 项：SCR90 / SCR70 都走统一内核（NaN 语义），本地 _percentile / _scr 已删。
    # 口径不变：仍是 chip_rows_of 的**带锁仓修正**数据，与周榜/策略的 offline 口径不同。
    matrix = np.asarray(rows, dtype=float)
    s90 = scr.scr_series(matrix, centers, 5, 95)
    s70 = scr.scr_series(matrix, centers, 15, 85)
    index = df.index
    return {"series": [
        series_line("集中度90", pd.Series(s90, index=index)),
        series_line("集中度70", pd.Series(s70, index=index)),
    ]}
