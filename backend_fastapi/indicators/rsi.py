# -*- coding: utf-8 -*-
"""RSI（下方副图，摆动类）。

数据只通过 data.get_ohlcv 获取，本文件不直接碰存储层（见 ARCHITECTURE.md §3）。
"""
from __future__ import annotations

import pandas as pd

from .base import indicator, ParamSpec, series_line


@indicator(
    id="rsi",
    name="RSI",
    category="oscillator",
    panel="lower",
    params=[ParamSpec("period", "int", 14, 2, 100, label="周期")],
)
def rsi(df: pd.DataFrame, period=14) -> dict:
    period = int(period)
    close = df["close"]
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    # Wilder 平滑（alpha = 1/period）
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss
    rsi_vals = 100 - 100 / (1 + rs)
    return {"series": [series_line(f"RSI{period}", rsi_vals)]}
