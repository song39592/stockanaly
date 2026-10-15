# -*- coding: utf-8 -*-
"""BOLL 布林带（主图叠加：中轨 / 上轨 / 下轨）。

数据只通过 data.get_ohlcv 获取，本文件不直接碰存储层（见 ARCHITECTURE.md §3）。
"""
from __future__ import annotations

import pandas as pd

from .base import indicator, ParamSpec, series_line


@indicator(
    id="BOLL",
    name="BOLL 布林带",
    category="volatility",
    panel="main",
    params=[
        ParamSpec("period", "int", 20, 2, 120, label="中轨周期"),
        ParamSpec("mult", "float", 2.0, 1.0, 4.0, label="倍数"),
    ],
)
def boll(df: pd.DataFrame, period=20, mult=2.0) -> dict:
    period = int(period)
    mid = df["close"].rolling(period).mean()
    std = df["close"].rolling(period).std(ddof=0)   # 总体标准差（经典布林定义）
    upper = mid + float(mult) * std
    lower = mid - float(mult) * std
    return {
        "series": [
            series_line("中轨", mid),
            series_line("上轨", upper),
            series_line("下轨", lower),
        ]
    }
