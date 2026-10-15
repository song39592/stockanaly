# -*- coding: utf-8 -*-
"""WR 威廉指标（下方副图，0–100）。

数据只通过 data.get_ohlcv 获取，本文件不直接碰存储层（见 ARCHITECTURE.md §3）。

WR = (N 日最高 - 收盘) / (N 日最高 - N 日最低) × 100，
取值 0（收盘在近 N 日最高，超买）~ 100（收盘在近 N 日最低，超卖）。
"""
from __future__ import annotations

import pandas as pd

from .base import indicator, ParamSpec, series_line
from .data import get_ohlcv


@indicator(
    id="WR",
    name="WR 威廉指标",
    category="oscillator",
    panel="lower",
    params=[ParamSpec("period", "int", 14, 2, 100, label="周期")],
)
def wr(df: pd.DataFrame, period=14) -> dict:
    period = int(period)
    high_n = df["high"].rolling(period).max()
    low_n = df["low"].rolling(period).min()
    denom = high_n - low_n
    wr_vals = (high_n - df["close"]) / denom * 100.0
    wr_vals = wr_vals.where(denom != 0, 0.0)      # 一字板（高低相等）时置 0，避免 0/0
    return {"series": [series_line(f"WR{period}", wr_vals)]}
