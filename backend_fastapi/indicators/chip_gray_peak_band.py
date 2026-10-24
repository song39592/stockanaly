# -*- coding: utf-8 -*-
"""灰色峰簇占比（GRAYP）：EMLK 三态中灰色（锁仓）分布的主峰簇占比（%）。

参考 PEAKB（chip_peak_band）：把输入从「全部筹码 pct」换成「灰色分布 gray」，
复用同一份 peak_band_series 算主峰簇占比。gray 本身即以全部筹码(100%)为分母的
占比分布（灰色部分占全部筹码的比例），故峰簇累加结果即「灰色主峰簇占全部筹码的比例」，
分母仍是全部筹码 100%。

仅 EMLK(ema_lock) 等三态公式输出 gray；其他公式 gray 为 None，返回全 NaN。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .base import ParamSpec, indicator, series_line
from .data import get_chip_frames, chip_rows_for, code_of
from chipformulas.core.peak import peak_band_series


@indicator(
    id="GRAYP",
    name="灰色峰簇占比",
    category="chip",
    panel="lower",                     # 0~100，独立纵轴
    params=[
        ParamSpec("formula", "choice", "ema_lock", label="筹码公式(需输出灰色)"),
        ParamSpec("band_pct", "float", 20.0, min=1.0, max=100.0, label="峰宽(价格±%)"),
        ParamSpec("tol", "float", 0.5, min=0.0, max=5.0, label="衰减容差%"),
    ],
)
def chip_gray_peak_band(df: pd.DataFrame, formula: str = "ema_lock",
                        band_pct: float = 20.0, tol: float = 0.5) -> dict:
    # 计算层（峰簇占比）在 chipformulas.core.peak，与 PEAKB 共用同一份实现；
    # 本函数只负责取灰色通道矩阵并包装成副图展示。
    frames = get_chip_frames(code_of(df), formula_id=formula, bins=80)
    if frames.gray is None:
        out = np.full(len(df), np.nan)
        return {"series": [series_line("灰色峰簇占比%", pd.Series(out, index=df.index))]}
    dates = [str(d)[:10] for d in df.index]
    gray_rows = chip_rows_for(dates, frames, field="gray")
    out = peak_band_series(gray_rows, frames.centers, band_pct, tol)
    return {"series": [series_line("灰色峰簇占比%", pd.Series(out, index=df.index))]}
