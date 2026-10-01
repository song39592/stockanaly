# -*- coding: utf-8 -*-
"""MACD 金叉/死叉（趋势跟踪）。

DIF 上穿 DEA（金叉）时持有，下穿（死叉）时空仓。返回 0/1 仓位序列。
"""
from __future__ import annotations

import pandas as pd

from .base import strategy, ParamSpec


@strategy(
    id="macd_cross",
    name="MACD 金叉",
    category="趋势",
    description="MACD 的 DIF 上穿 DEA（金叉）持有，下穿（死叉）空仓。",
    params=[
        ParamSpec("fast", "int", 12, min=2, max=60, label="快线 EMA"),
        ParamSpec("slow", "int", 26, min=5, max=120, label="慢线 EMA"),
        ParamSpec("signal", "int", 9, min=2, max=60, label="信号 EMA"),
    ],
)
def macd_cross(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.Series:
    fast = int(fast)
    slow = int(slow)
    signal = int(signal)
    ema_f = df["close"].ewm(span=fast, adjust=False).mean()
    ema_s = df["close"].ewm(span=slow, adjust=False).mean()
    dif = ema_f - ema_s
    dea = dif.ewm(span=signal, adjust=False).mean()
    return (dif > dea).astype(float)
