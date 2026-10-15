# -*- coding: utf-8 -*-
"""KDJ（下方副图，摆动类：K / D / J）。

数据只通过 data.get_ohlcv 获取，本文件不直接碰存储层（见 ARCHITECTURE.md §3）。

RSV 窗口由 period 决定（默认 9）；K、D 用固定的 1/3 平滑（2/3 前值 + 1/3 当前），
与通达信 KDJ 一致：K=2/3·K_pre+1/3·RSV，D=2/3·D_pre+1/3·K，J=3K-2D。
"""
from __future__ import annotations

import pandas as pd

from .base import indicator, ParamSpec, series_line
from .data import get_ohlcv


@indicator(
    id="KDJ",
    name="KDJ",
    category="oscillator",
    panel="lower",
    params=[ParamSpec("period", "int", 9, 2, 100, label="周期")],
)
def kdj(df: pd.DataFrame, period=9) -> dict:
    period = int(period)
    low_n = df["low"].rolling(period).min()
    high_n = df["high"].rolling(period).max()
    denom = high_n - low_n
    rsv = (df["close"] - low_n) / denom * 100.0
    rsv = rsv.where(denom != 0, 50.0)           # 一字板（高低相等）时 RSV 置 50，避免 0/0
    # 固定 1/3 平滑（alpha=1/3）对应递推 2/3·前 + 1/3·现；adjust=False 贴合递推定义
    k = rsv.ewm(alpha=1.0 / 3.0, adjust=False).mean()
    d = k.ewm(alpha=1.0 / 3.0, adjust=False).mean()
    j = 3.0 * k - 2.0 * d
    return {
        "series": [
            series_line("K", k),
            series_line("D", d),
            series_line("J", j),
        ]
    }
