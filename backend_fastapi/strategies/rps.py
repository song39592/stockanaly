# -*- coding: utf-8 -*-
"""RPS 相对价格强度策略（动量 / 相对强度）。

RPS（Relative Price Strength，相对价格强度）衡量一只股票在最近 N 个交易日的
区间涨幅，在全部股票中的相对排名百分比。本实现严格对齐「陶博士 / 通达信」的
RPS 公式设置方法（参考 https://zhuanlan.zhihu.com/p/1975584996946383784）：

    EXTRS  = (C - REF(C, N)) / REF(C, N)          # N 日区间涨幅
    RPS    = 百分比排名(EXTRS) × 100               # 横截面百分比排名，0–100

其中「百分比排名」即：在某交易日，全市场所有有数据标的中，涨幅不弱于该股的
数量 ÷ 总数（pandas `rank(pct=True)` 等价口径），再 ×100。通达信里该值先
×1000 存入扩展数据、显示时 ÷10，结果同样是 0–100。RPS = 100 表示该股涨幅最强
（跑赢全市场），RPS = 0 最弱；常取 RPS50 / RPS120 / RPS250，高亮阈值 M = 90。

本策略把 RPS 当作「强势选股」指标使用：
    * 当某股 RPS 上穿「买入阈值」（默认 90，即进入全市场前 10% 强势）时买入；
    * 当 RPS 跌破「卖出阈值」（默认 70）时清仓，两阈值间形成滞回带避免反复摩擦。

实现要点：
    * 横截面排名：同一交易日对所有有数据的标的按 N 日涨幅排名，缺失者不参与。
    * 复权口径：用后复权(hfq)价算涨幅，与回测盈亏口径一致，消除除权假涨跌。
    * 数据     data.load_bars(ctx.codes, fields=["close"], adjust=ctx.adjust)。
"""
from __future__ import annotations

import pandas as pd

from .core.base import strategy, ParamSpec, Signal
from .core import data


@strategy(
    id="rps",
    name="RPS 相对强度",
    category="相对强度",
    description="计算个股 N 日涨幅在全市场的相对排名百分比(RPS)，强势(RPS≥阈值)买入、走弱(RPS<阈值)卖出。",
    inputs=["close"],
    outputs="buy/sell 信号 + 仓位系数(weight)",
    params=[
        ParamSpec("n", "int", 120, min=20, max=250, label="RPS 周期(交易日)"),
        ParamSpec("buy_threshold", "float", 90.0, min=50.0, max=99.0, label="买入阈值 RPS≥"),
        ParamSpec("sell_threshold", "float", 70.0, min=10.0, max=90.0, label="卖出阈值 RPS<"),
        ParamSpec("position", "float", 1.0, min=0.1, max=1.0, label="建仓系数"),
    ],
)
def rps(ctx, n: int = 120, buy_threshold: float = 90.0,
        sell_threshold: float = 70.0, position: float = 1.0) -> list:
    n = int(n)
    buy_t = float(buy_threshold)
    sell_t = float(sell_threshold)
    pos = float(position)
    if sell_t >= buy_t:
        # 卖出阈值必须低于买入阈值，否则滞回带退化、逻辑无意义；兜底修正。
        sell_t = min(sell_t, buy_t - 1.0)

    bars = data.load_bars(ctx.codes, ctx.start, ctx.end,
                          fields=["close"], adjust=ctx.adjust)
    if not bars:
        return []

    # 横截面面板：列=标的，行=交易日（并集日历，缺失=NaN）。
    closes = {code: df["close"].astype(float) for code, df in bars.items()}
    panel = pd.DataFrame(closes)
    if panel.empty:
        return []

    # 每个标的的 N 日涨幅 EXTRS = (C - REF(C, N)) / REF(C, N)。
    prev = panel.shift(n)
    ret = panel / prev - 1.0

    # 横截面百分比排名（对齐通达信「扩展数据→百分比排名」）：同一行（同一交易日）
    # 对所有有数据的标的按 EXTRS 排名，结果 0–1，×100 即 RPS。缺失值不参与排名。
    rps_panel = ret.rank(axis=1, pct=True).mul(100.0)

    signals: list[Signal] = []
    for code in panel.columns:
        s = rps_panel[code]
        holding = False
        for d, val in s.items():
            if pd.isna(val):
                continue
            if not holding and val >= buy_t:
                signals.append(Signal(code=code, time=str(d)[:10], action="buy",
                                      weight=pos, reason=f"RPS={val:.1f}"))
                holding = True
            elif holding and val < sell_t:
                signals.append(Signal(code=code, time=str(d)[:10], action="sell",
                                      weight=0.0, reason=f"RPS={val:.1f}"))
                holding = False
    return signals
