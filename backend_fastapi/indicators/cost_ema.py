# -*- coding: utf-8 -*-
"""EMA 成交均价成本线（**主图叠加**，对应通达信 X_3）。

原始公式：
    X_3 := 0.01 * EMA(AMOUNT, 13) / EMA(VOL, 13);

一句话口径：**近 N 日的资金成本线**（成交额 EMA ÷ 成交量 EMA，再做手→股换算）。
价格站在它上方 = 近期买入的钱大多是浮盈的，反之即浮亏。

为什么要单独画一条：价量偏离度（JWZZ，副图）就是拿收盘价和这条线比出来的，
偏离度能画在副图，这条线本身却是**价格量纲**，必须留在主图与 K 线同轴 ——
一个指标只能声明一个 panel（见 ARCHITECTURE.md §5），故拆成两个指标：
本文件画主图成本线，`jwzz.py` 画副图偏离度，两者共用
`formulas.jwzz.ema_cost_line`（周期一致才对得上账）。

数据只通过 data.get_ohlcv 获取，本文件不直接碰存储层（见 ARCHITECTURE.md §3）。
"""
from __future__ import annotations

import pandas as pd

from .base import indicator, ParamSpec, series_line
# 计算层在中立内核包 formulas：与策略（strategies/jwzz_cross.py）共用同一份，
# 本文件只负责把结果包装成主图展示 —— 指标与策略数值天然一致。
from formulas.jwzz import DEFAULT_PERIOD, ema_cost_line


@indicator(
    id="ECOST",
    name="EMA成交均价成本线",
    category="trend",
    panel="main",                 # 价格量纲，与 K 线共用价格轴
    params=[ParamSpec("period", "int", DEFAULT_PERIOD, min=2, max=250,
                       label="EMA周期")],
    # 序列样式（颜色 / 线宽）：随计算结果一起下发，前端据此渲染，
    # 新增指标就不必再改前端那套按序列名 switch 的配色表。
    # 金色粗线：与 K 线价格同轴，是主图上的一条成本线。
    styles={"X_3": {"color": "#F0A028", "width": 2.0}},
)
def cost_ema(df: pd.DataFrame, period: int = DEFAULT_PERIOD) -> dict:
    cost = ema_cost_line(df, period)
    return {"series": [series_line("X_3", cost)]}
