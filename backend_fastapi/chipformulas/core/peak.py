# -*- coding: utf-8 -*-
"""筹码峰簇占比（PEAKB）的**计算层** —— 指标与策略共用的单一来源。

与 PEAK（只取最高单箱）不同，本模块把峰值两侧的**连续衰减**箱也归入同一座峰：
从最高箱向两侧累加，只要仍在「比内侧更低」地单调衰减就继续，一旦遇到回升
（已到谷底 / 进入另一座峰的上升沿）即停 —— 保证累加的只属于同一座峰，
不会把邻近次峰也吃进来。

对「峰宽」的限制用**价格带宽**（而非累计占比封顶）：以最高峰所在价位为中心，
向上下各取 `band_pct`（默认 20）% 的价格区间，区间之外的箱一律不计入。

`peak_band` 算单帧；`peak_band_series` 算整段（与每日筹码矩阵同序）。
指标侧（indicators/chip_peak_band.py）只负责把本层结果包装成副图展示；
策略侧（strategies/peak_top.py、peak_thresh.py）直接复用本层做横截面选股，
两处数值天然一致，不再手动同步两边。
"""
from __future__ import annotations

import numpy as np


def peak_band(row: np.ndarray, centers: np.ndarray, band_pct: float, tol: float) -> float:
    """沿峰值两侧累加「连续衰减」箱；遇回升、或价格超出峰位 ±band_pct% 即停。

    返回主峰簇占比（%）。`centers` 为各价格分箱中心（与 `row` 等长）。
    """
    band_pct = max(0.0, float(band_pct))
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


def peak_band_series(pct: np.ndarray, centers: np.ndarray,
                     band_pct: float = 20.0, tol: float = 0.5) -> np.ndarray:
    """对每日筹码矩阵（行=日期、列=价格分箱，行合计 100）逐帧算主峰簇占比。

    返回长度 = 帧数的 ndarray；整行 NaN 的帧保持 NaN（缺数据日）。
    """
    pct = np.asarray(pct, dtype=float)
    centers = np.asarray(centers, dtype=float)
    out = np.full(pct.shape[0], np.nan)
    for i in range(pct.shape[0]):
        row = pct[i]
        if np.all(np.isnan(row)):
            continue
        out[i] = peak_band(row, centers, band_pct, tol)
    return out
