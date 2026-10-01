# -*- coding: utf-8 -*-
"""示例策略：MACD 金叉 / 死叉（趋势跟踪）。

体现标准策略格式（策略主动拉取）：
    数据     data.load_bars(ctx.codes, fields=inputs, adjust=ctx.adjust)
    额外参数 fast / slow / signal（MACD 三参数）+ position（建仓系数）
    输出     list[Signal]：金叉买入（带仓位系数）、死叉卖出（每条带 code）

DIF 上穿 DEA（金叉）买入；下穿（死叉）卖出。
"""
from __future__ import annotations

from .core.base import strategy, ParamSpec, Signal
from .core import data


@strategy(
    id="macd",
    name="MACD 金叉/死叉",
    category="趋势",
    description="MACD 的 DIF 上穿 DEA（金叉）买入，下穿（死叉）卖出；输出买卖时点与仓位系数。",
    inputs=["close"],
    outputs="buy/sell 信号 + 仓位系数(weight)",
    params=[
        ParamSpec("fast", "int", 12, min=2, max=60, label="快线 EMA"),
        ParamSpec("slow", "int", 26, min=5, max=120, label="慢线 EMA"),
        ParamSpec("signal", "int", 9, min=2, max=60, label="信号 EMA"),
        ParamSpec("position", "float", 1.0, min=0.1, max=1.0, label="建仓系数"),
    ],
)
def macd(ctx, fast: int = 12, slow: int = 26, signal: int = 9,
         position: float = 1.0) -> list:
    # 主动拉取本策略所需的字段（inputs=["close"]）；缓存命中，无额外 I/O。
    bars = data.load_bars(ctx.codes, ctx.start, ctx.end,
                          fields=["close"], adjust=ctx.adjust)
    ema_f_span = int(fast)
    ema_s_span = int(slow)
    dea_span = int(signal)
    pos = float(position)

    signals: list[Signal] = []
    for code, df in bars.items():
        close = df["close"].astype(float)
        if len(close) < max(ema_s_span, dea_span) + 1:
            continue
        ema_f = close.ewm(span=ema_f_span, adjust=False).mean()
        ema_s = close.ewm(span=ema_s_span, adjust=False).mean()
        dif = ema_f - ema_s
        dea = dif.ewm(span=dea_span, adjust=False).mean()

        above = (dif > dea).tolist()
        dates = [str(d)[:10] for d in close.index]
        for i in range(1, len(dates)):
            if above[i] and not above[i - 1]:          # 金叉
                signals.append(Signal(code=code, time=dates[i], action="buy", weight=pos))
            elif (not above[i]) and above[i - 1]:      # 死叉
                signals.append(Signal(code=code, time=dates[i], action="sell", weight=0.0))
    return signals
