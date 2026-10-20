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

    纯 numpy 向量化：用前缀和 + “首个不满足索引”的扫描，**一次算完整段**，
    不再逐帧 Python 循环（原逐帧循环 ~1250 次/只，约 30ms/只）。
    """
    pct = np.asarray(pct, dtype=float)
    centers = np.asarray(centers, dtype=float)
    n, b = pct.shape
    out = np.full(n, np.nan)
    bad = np.isnan(pct).all(axis=1)
    good = ~bad
    if not good.any():
        return out

    gi = np.where(good)[0]                              # 只在（非全 NaN）行上算
    P = pct[gi]
    # NaN 箱当 0 仅用于求和；是否「可延续」由 valid 控制（NaN 箱会打断累加）。
    valid = ~np.isnan(P)
    rows = np.where(valid, P, 0.0)
    band = max(0.0, float(band_pct)) / 100.0
    t = max(0.0, float(tol)) / 100.0

    peak_i = np.nanargmax(P, axis=1)                   # 每帧峰位箱 (m,)
    peak_price = centers[peak_i]                       # (m,)
    half = band * np.abs(peak_price)                   # (m,) 价格带宽

    # 价格带掩码（随票不同）：|centers[j] - 峰位价| <= 带宽
    in_band = np.abs(centers[None, :] - peak_price[:, None]) <= half[:, None]   # (m,b)

    csum = np.cumsum(rows, axis=1)                     # 前缀和
    csum2 = np.concatenate([np.zeros((len(gi), 1)), csum], axis=1)  # csum2[:, j+1] = csum[:, j]

    # ---- 右侧：peak_i+1 起，要求 in_band 且 row[j] < row[j-1]*(1-t)，首个不满足即停 ----
    not_ok_r = np.ones((len(gi), b), dtype=bool)
    not_ok_r[:, 1:] = ~(valid[:, 1:]
                        & (rows[:, 1:] < rows[:, :-1] * (1.0 - t))
                        & in_band[:, 1:])
    idx_r = np.where(not_ok_r, np.arange(b), b)
    next_false = np.minimum.accumulate(idx_r[:, ::-1], axis=1)[:, ::-1]  # 首个 >=k 的 not_ok 索引
    pi1 = peak_i + 1
    col_r = np.where(pi1 < b, pi1, 0)                  # 防越界取数（pi1==b 时结果会被覆盖）
    k = next_false[np.arange(len(gi)), col_r]
    k = np.where(pi1 < b, k, b)
    e = np.minimum(k - 1, b - 1)
    right_sum = csum[np.arange(len(gi)), e] - csum[np.arange(len(gi)), peak_i]
    right_sum[pi1 >= b] = 0.0                          # 峰值在末箱 → 无右侧

    # ---- 左侧：peak_i-1 向下，要求 in_band 且 row[j] < row[j+1]*(1-t)，首个不满足即停 ----
    not_ok_l = np.ones((len(gi), b), dtype=bool)
    not_ok_l[:, :-1] = ~(valid[:, :-1]
                         & (rows[:, :-1] < rows[:, 1:] * (1.0 - t))
                         & in_band[:, :-1])
    idx_l = np.where(not_ok_l, np.arange(b), -1)
    prev_false = np.maximum.accumulate(idx_l, axis=1)  # 最后一个 <=j 的 not_ok 索引（无则 -1）
    pi0 = peak_i - 1
    col_l = np.clip(pi0, 0, b - 1)                     # 防 -1 越界取数（pi0<0 时结果会被覆盖）
    m = prev_false[np.arange(len(gi)), col_l]
    m = np.where(pi0 >= 0, m, -1)
    left_sum = (csum[np.arange(len(gi)), np.clip(pi0, 0, b - 1)]
                - csum2[np.arange(len(gi)), np.clip(m, -1, b - 1) + 1])
    left_sum[pi0 < 0] = 0.0                            # 峰值在首箱 → 无左侧

    total = rows[np.arange(len(gi)), peak_i] + right_sum + left_sum
    out[gi] = total
    return out
