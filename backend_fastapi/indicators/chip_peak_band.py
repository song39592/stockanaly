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
    rows, frames = chip_rows_of(df, formula_id=formula)
    centers = np.asarray(frames.centers, dtype=float)
    band_pct = max(0.0, float(band_pct))
    out = []
    for i in range(len(rows)):
        row = rows[i]
        if np.all(np.isnan(row)):
            out.append(float("nan"))
            continue
        out.append(float(_peak_band(row, centers, band_pct, tol)))
    return {"series": [series_line("峰簇占比%", pd.Series(out, index=df.index))]}


def _peak_band(row: np.ndarray, centers: np.ndarray, band_pct: float, tol: float) -> float:
    """沿峰值两侧累加「连续衰减」箱；遇回升、或价格超出峰位 ±band_pct% 即停。

    返回主峰簇占比（%）。`centers` 为各价格分箱中心（与 `row` 等长）。
    """
    tol = max(0.0, float(tol)) / 100.0            # 百分比 → 比例
    n = len(row)
    peak_i = int(np.nanargmax(row))
    peak = float(row[peak_i])
    peak_price = float(centers[peak_i])
    half = band_pct / 100.0 * abs(peak_price)     # 价格带宽（围绕峰位）
    total = peak
    # 向右：超出价格带宽或非连续衰减（回升）即停
    j = peak_i + 1
    while j < n:
        if abs(float(centers[j]) - peak_price) > half:
            break
        if row[j] >= row[j - 1] * (1.0 - tol):    # 不再衰减 → 停
            break
        total += float(row[j])
        j += 1
    # 向左：对称
    j = peak_i - 1
    while j >= 0:
        if abs(float(centers[j]) - peak_price) > half:
            break
        if row[j] >= row[j + 1] * (1.0 - tol):
            break
        total += float(row[j])
        j -= 1
    return total
