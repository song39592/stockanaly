# -*- coding: utf-8 -*-
"""EMA 指数移动平均（主图叠加）。

数据只通过 data.get_ohlcv 获取，本文件不直接碰存储层（见 ARCHITECTURE.md §3）。
"""
from __future__ import annotations

import pandas as pd

from .base import indicator, ParamSpec, series_line
from .data import get_ohlcv


@indicator(
    id="EMA",
    name="EMA 指数均线",
    category="trend",
    panel="main",
    params=[ParamSpec("periods", "choice", [5, 10, 20, 30, 60, 120, 250], label="周期")],
)
def ema(df: pd.DataFrame, periods=None) -> dict:
    if periods is None:
        periods = [5, 10, 20, 30, 60, 120, 250]
    if isinstance(periods, (int, float)):
        periods = [int(periods)]
    series = []
    for p in periods:
        p = int(p)
        # span=p 与通达信 EMA(CLOSE, p) 的平滑系数 2/(p+1) 一致（adjust=False 等价于递推）
        series.append(series_line(f"EMA{p}", df["close"].ewm(span=p, adjust=False).mean()))
    return {"series": series}
