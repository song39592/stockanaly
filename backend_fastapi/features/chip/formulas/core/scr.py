# -*- coding: utf-8 -*-
"""SCR 序列计算内核（第 10 项）。

**这里只有算法，没有口径。** 调用方各自决定用哪套数据：

| 调用方 | 数据来源 | 锁仓修正 | 用哪个函数 |
|---|---|---|---|
| `features/chip/rank_service.py`（SCR90 周榜） | compute_matrix(offline=True) | **无**（与周榜同口径） | scr_series |
| `strategies/scr90.py`（策略） | compute_matrix(offline=True) | **无**（与周榜同口径） | scr_series |
| `indicators/chip_scr.py`（副图 SCR90/SCR70） | chip_rows_of(df) | **带**（qfq 口径） | scr_series（两次：5/95 与 15/85） |
| `features/chip/formulas/core/base.py`（单帧统计） | compute_matrix(...) | **带** | scr_frame |

⚠️ **合并的只是算法内核，调用参数一律不动** —— 上表里「有无锁仓修正」是**业务口径差异**，
不是该 unify 掉的重复。合并时把参数也统一，会让周榜与策略的数值整体偏移。

## 两种空值语义（刻意不同，不是笔误）

- `scr_series`：**NaN** 语义。给序列类消费者（pandas Series、DataFrame 对齐），
  NaN 参与「该日不参与排名 / 断点」的自然语义。
- `scr_frame`：**None** 语义。给单帧统计（要 JSON 序列化给前端）——
  JSON 里没有 NaN，`null` 比 NaN 诚实。

## 分母判空：为什么是 `den == 0` 而不是 `if p5 and p95`

分位价格**可以是 0**（极端低价股 / 分箱下沿正好落在 0）。
`if p5 and p95` 在 `p5 == 0.0` 时为假 → 静默返回 None/NaN，
把「分位恰好为 0」误判成「筹码为空」。另两处历史实现用的都是 `den == 0`，**那才是对的**。
本模块统一按 `den == 0` 判断（第 10 项修掉这个缺陷）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _percentile(centers: np.ndarray, row: np.ndarray, q: float) -> float:
    """按累计筹码取价格分位（线性插值）。总筹码为 0 / 非有限时返回 NaN。"""
    total = float(row.sum())
    if not np.isfinite(total) or total <= 0:
        return float("nan")
    cum = np.cumsum(row) / total
    return float(np.interp(q / 100.0, cum, centers))


def _ratio(p_lo: float, p_hi: float) -> float:
    """(P_hi - P_lo) / (P_hi + P_lo)；分母为 0 或分位非有限时返回 NaN。"""
    den = p_hi + p_lo
    if not np.isfinite(den) or den == 0:
        return float("nan")
    return (p_hi - p_lo) / den


def scr_value(p_lo: float, p_hi: float) -> float:
    """已取好的两个分位 → SCR 值（非有限或分母为 0 时 NaN）。"""
    return _ratio(p_lo, p_hi)


def scr_series(pct: np.ndarray, centers: np.ndarray, q_lo: float = 5,
               q_hi: float = 95) -> np.ndarray:
    """筹码矩阵逐帧换算成 SCR 序列（**NaN 语义**）。

    `pct` 形状 (帧数 × 分箱数)、每行合计约 100；`centers` 为各分箱价格。
    某帧筹码全空（全 0）或分母为 0 时该帧留 NaN —— 调用方按「该日不参与排名」处理。

    第 10 项合并的主函数：原先 `chip_rank_service` 与 `strategies/scr90.py`
    各有一份**逐字符雷同**的实现，`indicators/chip_scr.py` 还有一份同算法的单帧版。
    """
    matrix = np.asarray(pct, dtype=float)
    if matrix.ndim != 2:
        raise ValueError("pct 必须是二维（帧数 × 分箱数），实际 %s 维" % matrix.ndim)
    xs = np.asarray(centers, dtype=float)
    out = np.full(matrix.shape[0], np.nan)
    for i in range(matrix.shape[0]):
        row = matrix[i]
        total = float(row.sum()) if row.size else 0.0
        if not np.isfinite(total) or total <= 0:
            continue
        out[i] = _ratio(_percentile(xs, row, q_lo), _percentile(xs, row, q_hi))
    return out


def scr_frame(pct_row: np.ndarray, centers: np.ndarray, q_lo: float = 5,
              q_hi: float = 95):
    """单帧的 SCR 值（**None 语义**）；空筹码 / 分母为 0 → None。

    给要 JSON 序列化的单帧统计用。顺带修掉原先 `if p5 and p95` 把
    「分位恰为 0」误判成空筹码的缺陷（第 10 项）。
    """
    row = np.asarray(pct_row, dtype=float)
    if row.size == 0:
        return None
    total = float(row.sum())
    if not np.isfinite(total) or total <= 0:
        return None
    xs = np.asarray(centers, dtype=float)
    val = _ratio(_percentile(xs, row, q_lo), _percentile(xs, row, q_hi))
    return None if not np.isfinite(val) else val


def scr_series_pd(res, q_lo: float = 5, q_hi: float = 95) -> "pd.Series":
    """`scr_series` 的 pandas 包装：索引取 `res.dates`（周榜与策略的共同用法）。

    收这一层是因为「矩阵 → 带日期索引的 Series」在两处调用方**完全一致**；
    pandas 只在这一层出现，**内核本身保持纯 numpy**。
    """
    return pd.Series(scr_series(res.pct, res.centers, q_lo, q_hi),
                     index=list(res.dates))