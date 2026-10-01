# -*- coding: utf-8 -*-
"""双均线交叉（趋势跟踪）。

快线上穿慢线时满仓持有，下穿时清仓。返回与 df 同索引的 0/1 仓位序列。
数据只通过 data.get_ohlcv 获取，本文件不直接碰存储层（见 ARCHITECTURE.md）。
"""
from __future__ import annotations

import pandas as pd

from .base import strategy, ParamSpec


@strategy(
    id="ma_cross",
    name="双均线交叉",
    category="趋势",
    description="快均线上穿慢均线时持有，下穿时空仓。最经典的 trend-following 信号。",
    params=[
        ParamSpec("fast", "int", 5, min=2, max=60, label="快线周期"),
        ParamSpec("slow", "int", 20, min=5, max=250, label="慢线周期"),
    ],
)
def ma_cross(df: pd.DataFrame, fast: int = 5, slow: int = 20) -> pd.Series:
    fast = int(fast)
    slow = int(slow)
    ma_f = df["close"].rolling(fast).mean()
    ma_s = df["close"].rolling(slow).mean()
    return (ma_f > ma_s).astype(float)
