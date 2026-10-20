# -*- coding: utf-8 -*-
"""筹码峰簇占比（下方副图 PEAKB）：每日筹码分布中「主峰」所占比例（%）。

与 PEAK（只取最高单箱）不同，本指标把峰值两侧的**连续衰减**箱也归入同一座峰：
从最高箱向两侧累加，只要仍在「比内侧更低」地单调衰减就继续，一旦遇到回升
（已到谷底 / 进入另一座峰的上升沿）即停 —— 保证累加的只属于同一座峰，
不会把邻近次峰也吃进来（对应需求「连续衰减的才能归到一个峰里」）。

对「峰宽」的限制改用**价格带宽**（而非累计占比封顶）：以最高峰所在价位为中心，
向上下各取 `band_pct`（默认 20）% 的价格区间，区间之外的箱一律不计入
（对应需求「最高峰价格上下 20% 截断，不是直接最大 20 截断」）。

行合计为 100，故结果即主峰簇占比（%）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .base import ParamSpec, indicator, series_line
from .data import chip_rows_of
from chipformulas.core.peak import peak_band_series


@indicator(
    id="PEAKB",
    name="筹码峰簇占比",
    category="chip",
    panel="lower",                     # 0~100，独立纵轴
    params=[
        ParamSpec("formula", "choice", "tri_decay", label="筹码公式"),
        ParamSpec("band_pct", "float", 20.0, min=1.0, max=100.0, label="峰宽(价格±%)"),
        ParamSpec("tol", "float", 0.5, min=0.0, max=5.0, label="衰减容差%"),
    ],
)
def chip_peak_band(df: pd.DataFrame, formula: str = "tri_decay",
                   band_pct: float = 20.0, tol: float = 0.5) -> dict:
    # 计算层（峰簇占比）在 chipformulas.core.peak，与策略共用同一份实现；
    # 本函数只负责把结果包装成副图展示 —— 指标与策略数值天然一致，不必手动两边同步。
    rows, frames = chip_rows_of(df, formula_id=formula)
    centers = np.asarray(frames.centers, dtype=float)
    out = peak_band_series(rows, centers, band_pct, tol)
    return {"series": [series_line("峰簇占比%", pd.Series(out, index=df.index))]}
