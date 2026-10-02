# -*- coding: utf-8 -*-
"""筹码获利比例（下方副图）：成本低于**当日**收盘价的筹码占比（%）。

0~100 的量纲，与主图共享时间轴、独立 y 轴，故 panel="lower"。
看它随时间的变化，就是「获利盘是越堆越多还是在被洗出去」。

注意用的是**当日**收盘价做分界（不是最新收盘价），否则曲线会混进未来信息。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .base import ParamSpec, indicator, series_line
from .data import chip_rows_of


@indicator(
    id="chip_profit",
    name="筹码获利比例",
    category="chip",
    panel="lower",                   # 0~100，独立纵轴
    params=[
        ParamSpec("formula", "choice", "tri_decay", label="筹码公式"),
    ],
)
def chip_profit(df: pd.DataFrame, formula: str = "tri_decay") -> dict:
    rows, frames = chip_rows_of(df, formula_id=formula)
    centers = frames.centers
    close = df["close"].astype(float).to_numpy()
    out = []
    for i in range(len(rows)):
        row = rows[i]
        if np.all(np.isnan(row)):
            out.append(float("nan"))
            continue
        out.append(float(row[centers < close[i]].sum()))     # 每行合计已是 100
    return {"series": [series_line("获利比例%", pd.Series(out, index=df.index))]}
