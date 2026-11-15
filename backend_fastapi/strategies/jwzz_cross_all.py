# -*- coding: utf-8 -*-
"""策略：JWZZ 上穿阈值买入 / 下穿阈值卖出（全量、无持仓上限）。

通达信原始语义：

    X_3  := 0.01 * EMA(AMOUNT, 13) / EMA(VOL, 13);
    JWZZ := (CLOSE - X_3) / X_3 * 1000;
    强势 := 95;

JWZZ = 收盘价相对「N 日资金成本线」的偏离度（‰），+95 表示股价高出成本线 9.5%。
本策略把它当**状态**：上穿阈值视为进入强势并买入，下穿视为退出并卖出。

## 与 jwzz_cross（取前 N）的区别

`jwzz_cross` 是「每日取当日强势票按 JWZZ 降序取前 top_n 只」的**组合选股**口径，
带持仓数量上限、便于横向对比。本策略是**直接的信号口径**：

    · 不做持仓数量上限（top_n），所有处于强势状态的票一视同仁、等权持有；
    · 某票上穿 95 → 进入持仓；下穿 95 → 清仓（weight=0）；
    · 持仓数随市场强弱自然伸缩（强势票多就多拿，少就少拿）。

二者底层状态机完全一致（上穿置 True、下穿置 False，停牌沿用上一有值状态），
只是"取哪些票"的口径不同。计算层同用 formulas/jwzz，与副图指标逐日可对照。

## 实盘注意

与 jwzz_cross 同样：原始 JWZZ 在震荡期被反复穿越，持仓可能只有 1~2 天、
交易成本吃掉收益。缓解手段（沿用其设计）：
  * smooth_period > 0：改用 JW_M = SMA(JWZZ, N, 1) 触发，假突破减少；
    量级约为原始 1/5~1/6，阈值需相应调到 20~30；
  * 调高 threshold：只在偏离度显著走强时才建仓。
"""
from __future__ import annotations

import numpy as np

from .core.base import strategy, ParamSpec, Signal
from .core import data
from formulas.jwzz import DEFAULT_PERIOD, jwzz_panel


@strategy(
    id="jwzz_cross_all",
    name="JWZZ 上穿/下穿(全量)",
    category="趋势",
    description="价量偏离度 JWZZ 上穿阈值（默认 95‰）即买入、下穿即卖出；"
                "不限制持仓数量，所有强势票等权持有（直接的『上穿买、下穿卖』）。",
    inputs=["close", "volume", "amount"],
    outputs="目标权重向量(组合占比) + 因子值(meta)",
    requires_pool=False,
    params=[
        ParamSpec("cost_period", "int", DEFAULT_PERIOD, min=2, max=250,
                   label="成本EMA周期"),
        ParamSpec("threshold", "float", 95.0, min=-500.0, max=2000.0,
                   label="触发阈值(‰)"),
        ParamSpec("smooth_period", "int", 0, min=0, max=250,
                   label="平滑周期(0=原始JWZZ；>0需把阈值调低约5倍)"),
        ParamSpec("position", "float", 1.0, min=0.1, max=1.0, label="总仓位系数"),
    ],
)
def jwzz_cross_all(ctx, cost_period: int = DEFAULT_PERIOD, threshold: float = 95.0,
                   smooth_period: int = 0, position: float = 1.0) -> list:
    codes = [str(c).strip() for c in (ctx.codes or []) if str(c).strip()]
    if not codes:
        return []
    period = max(2, int(cost_period))
    thr = float(threshold)
    smooth_n = max(0, int(smooth_period))
    pos = min(1.0, max(0.0, float(position)))

    # 主动拉取所需字段（inputs=["close","volume","amount"]）；缓存命中，无额外 I/O
    bars = data.load_bars(ctx.codes, ctx.start, ctx.end,
                          fields=["close", "volume", "amount"], adjust=ctx.adjust)

    # ---- 统一交易日轴 + 强势状态（**口径在 formulas/jwzz.py**，与本策略同源）----
    # 穿越判定 / 状态机 / 停牌沿用 / 统一轴对齐都在公式层，
    # 与 `jwzz_cross`、`jwzz_hold` 共用同一份实现 —— 各写一份 = 同一口径两个数、
    # 两边都不报错。
    panel = jwzz_panel(bars, period=period, threshold=thr, smooth_period=smooth_n)
    if not panel.cols:
        return []
    day_index = panel.days
    cols = panel.cols
    bull_m = panel.bull                             # C × T，bool（每票一行）
    strength_m = panel.val                           # C × T，float32

    signals: list[Signal] = []
    held: set[str] = set()                          # ★ 自己维护持仓集合

    for t, day in enumerate(day_index):
        # 全量口径：所有处于强势状态的票都纳入（不做持仓数量上限）。
        day_bull = np.flatnonzero(bull_m[:, t])
        picks = {cols[i] for i in day_bull} if day_bull.size else set()
        if picks == held:
            continue                                # 成分没变 → 不发信号（ffill 维持）

        # 掉出的票显式 weight=0：不发的话这一天不会进入引擎的 rebalance_dates，
        # 「移出」根本没被登记，上一期持仓会继续持有。
        for code in sorted(held - picks):
            signals.append(Signal(code=code, time=day, weight=0.0,
                                  reason="JWZZ 离开强势区/下穿",
                                  meta={"dropped": 1}))

        if picks:
            # 等权：每只 = 总仓位 / 当前强势票数（持仓数随市场强弱伸缩）
            w = pos / len(picks)
            value_of = {cols[i]: float(strength_m[i, t]) for i in range(len(cols))}
            for rank, code in enumerate(sorted(picks), start=1):
                signals.append(Signal(
                    code=code, time=day, weight=w, reason="JWZZ 上穿阈值",
                    meta={"value": round(value_of.get(code, 0.0), 3), "rank": rank},
                ))
        held = picks

    return signals
