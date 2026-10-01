# -*- coding: utf-8 -*-
"""RSI 均值回归（反转）。

RSI 跌破超卖线时买入持有，回升至超买线之上时空仓。返回 0/1 仓位序列。
"""
from __future__ import annotations

import pandas as pd

from .base import strategy, ParamSpec


@strategy(
    id="rsi_reversal",
    name="RSI 均值回归",
    category="反转",
    description="RSI 跌破超卖线买入、涨过超买线卖出，做均值回归。",
    params=[
        ParamSpec("period", "int", 14, min=2, max=60, label="RSI 周期"),
        ParamSpec("oversold", "int", 30, min=5, max=50, label="超卖线"),
        ParamSpec("overbought", "int", 70, min=50, max=95, label="超买线"),
    ],
)
def rsi_reversal(df: pd.DataFrame, period: int = 14, oversold: int = 30,
                 overbought: int = 70) -> pd.Series:
    period = int(period)
    oversold = int(oversold)
    overbought = int(overbought)
    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, pd.NA)
    rsi = (100 - 100 / (1 + rs)).fillna(50)
    # 低于超卖线持仓；高于超买线空仓；中间维持上一状态（用 ffill 实现持仓延续）。
    pos = (rsi < oversold).where(rsi > overbought, 0.0).astype(float)
    return pos.ffill().fillna(0.0)
