# -*- coding: utf-8 -*-
"""MACD（下方副图，线 + 柱）。

数据只通过 data.get_ohlcv 获取，本文件不直接碰存储层（见 ARCHITECTURE.md §3）。
"""
from __future__ import annotations

import pandas as pd

from .base import indicator, ParamSpec, series_line, series_bar


@indicator(
    id="macd",
    name="MACD",
    category="trend",
    panel="lower",
    params=[
        ParamSpec("fast", "int", 12, 2, 60, label="快线周期"),
        ParamSpec("slow", "int", 26, 5, 120, label="慢线周期"),
        ParamSpec("signal", "int", 9, 2, 60, label="信号周期"),
    ],
)
def macd(df: pd.DataFrame, fast=12, slow=26, signal=9) -> dict:
    close = df["close"]
    ema_fast = close.ewm(span=int(fast), adjust=False).mean()
    ema_slow = close.ewm(span=int(slow), adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=int(signal), adjust=False).mean()
    hist = (dif - dea) * 2
    return {
        "series": [
            series_line("DIF", dif),
            series_line("DEA", dea),
            series_bar("MACD", hist),
        ]
    }
