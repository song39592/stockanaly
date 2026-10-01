# -*- coding: utf-8 -*-
"""N 日新高突破（动量）。

收盘价创近 N 日（不含当日，避免未来函数）新高时持有，否则空仓。返回 0/1 仓位序列。
"""
from __future__ import annotations

import pandas as pd

from .base import strategy, ParamSpec


@strategy(
    id="breakout",
    name="N日新高突破",
    category="动量",
    description="收盘价突破近 N 日最高价（不含当日）时持有，做动量突破。",
    params=[
        ParamSpec("n", "int", 20, min=5, max=250, label="回看天数"),
    ],
)
def breakout(df: pd.DataFrame, n: int = 20) -> pd.Series:
    n = int(n)
    # shift(1)：用截至昨日的 N 日高点做判断，避免用到当日收盘价造成前视偏差。
    hh = df["close"].rolling(n).max().shift(1)
    return (df["close"] > hh).astype(float)
