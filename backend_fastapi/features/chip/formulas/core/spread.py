# -*- coding: utf-8 -*-
"""筹码摊布的**分布原语**：把「当日新增的一笔筹码」铺到当日价格区间上。

公式文件按需取用：给定分箱边界 `points`，返回各点处的**累积分布 F(p)**，
相邻边界相减（np.diff）即得每个分箱分到的份额——比「按分箱中心采样」精确，
且不需要在每个公式里重复推导。

两个约定（所有原语都必须遵守）：
  1. 先把求值点钳到 [lo, hi] 内再套公式。价格轴通常比当日振幅宽，
     直接套会得到「越界越大」的伪值（下方算成 1、上方算成 0），令分箱增量为负。
  2. 返回值是累积概率，单调递增、落在 [0, 1]，F(lo)=0、F(hi)=1。
"""
from __future__ import annotations

import numpy as np


def _clip(points: np.ndarray, lo: float, hi: float) -> np.ndarray:
    return np.clip(points, lo, hi)


def triangle_cdf(points: np.ndarray, lo: float, hi: float, peak: float) -> np.ndarray:
    """三角形分布：密度在 [lo, peak] 线性上升、[peak, hi] 线性下降，峰值在 peak。

    peak 落在边界上时自动退化为单调升 / 单调降。
    """
    span = hi - lo
    if span <= 0:
        return np.zeros_like(points, dtype=float)
    p = _clip(points, lo, hi)
    left = peak - lo
    right = hi - peak
    if left <= 0:                                   # 峰值贴左边界：退化为单调下降
        cdf = 1.0 - (hi - p) ** 2 / (span * right)
    elif right <= 0:                                # 峰值贴右边界：退化为单调上升
        cdf = (p - lo) ** 2 / (span * left)
    else:
        cdf = np.where(p <= peak,
                       (p - lo) ** 2 / (span * left),
                       1.0 - (hi - p) ** 2 / (span * right))
    return np.clip(np.nan_to_num(cdf), 0.0, 1.0)


def uniform_cdf(points: np.ndarray, lo: float, hi: float) -> np.ndarray:
    """均匀分布：当日成交在 [lo, hi] 内等概率（最朴素的摊布口径）。"""
    span = hi - lo
    if span <= 0:
        return np.zeros_like(points, dtype=float)
    p = _clip(points, lo, hi)
    return np.clip((p - lo) / span, 0.0, 1.0)
