# -*- coding: utf-8 -*-
"""均线 MA（主图叠加）。

数据只通过 data.get_ohlcv 获取，本文件不直接碰存储层（见 ARCHITECTURE.md §3）。
"""
from __future__ import annotations

import pandas as pd

from .base import indicator, ParamSpec, series_line


@indicator(
    id="ma",
    name="均线 MA",
    category="trend",
    panel="main",
    params=[ParamSpec("periods", "choice", [5, 10, 20, 60], label="周期")],
)
def ma(df: pd.DataFrame, periods=None) -> dict:
    if periods is None:
        periods = [5, 10, 20, 60]
    if isinstance(periods, (int, float)):
        periods = [int(periods)]
    series = []
    for p in periods:
        p = int(p)
        series.append(series_line(f"MA{p}", df["close"].rolling(p).mean()))
    return {"series": series}
