# -*- coding: utf-8 -*-
"""策略：JWZZ **买入后持有到下穿**才卖（槽位制 + 每日定额定只数）。

与 `jwzz_cross`（JWZZ 上穿/下穿）的**根本区别**：

| | `jwzz_cross` | 本策略 |
|---|---|---|
| 持仓 | 每日**重新**选前 N 只 | **买入即持有**，直到自己下穿 |
| 卖出 | 掉出前 N **或** 下穿 | **只有下穿** |
| 换手 | 极高（年换手 200+ 倍、平均持仓 1.8 天） | 低（持有天数由 JWZZ 决定） |
| 选股 | 每日的横截面排名 | 买入那一刻的横截面排名，之后不换 |

一句话：本策略把 JWZZ 当**「进入强势 → 买入、退出强势 → 卖出」的状态机**，
买入之后不再因为排名下滑而甩货，因此换手与成本都远低于 `jwzz_cross`。

## 规则（口径逐条写死，避免歧义）

1. **卖出**：持仓票的 JWZZ **下穿阈值**（默认 95‰）当天卖出，**只看这一条**。
   停牌的票自然不会有下穿事件（公式层只在该票有行情的日子产生事件），
   于是停牌 → 继续持有；跌停卖不出 → 由引擎延后到开板那天（`sell_deferrals`）。
2. **买入候选**：当日 `JWZZ > 阈值` **且当日有行情**、**昨日未持有**的票，
   按 JWZZ **降序**排列（相同值按代码序，保证可复现）。
   - 「当日有行情」是硬条件：停牌的票买不进去，不能占掉一次买入名额。
   - 「昨日未持有」：已持有的票不再加仓（单票仓位 = 1 / 槽位数，不再单独设）。
3. **买几只**：每日最多买 `buys_per_day` 只（默认 1；设成 2 就是每天买前 2），
   且**受剩余槽位约束**（空位不足就少买）。
4. **槽位**：最多同时持有 `max_slots` 只（默认 10）。
   空位 = `max_slots − 昨日持仓数` —— **昨日卖出腾出的槽位当天不能用、次日才补**
   （卖出与买入分开一天，更接近实盘：卖出资金次日才可再投）。
   **槽位满且没有票下穿 → 当天买不进**（这是策略规则，不是错误）。
5. **仓位**：**单票仓位自动 = 1 / `max_slots`**（占组合净值比例）——
   10 个槽位 → 每只 10%；20 个槽位 → 每只 5%。**不再让用户单独设仓位**：
   一旦仓位与槽位分成两个参数，就可能配出「0.1 × 20 = 200%」这种越界组合，
   跑起来才报错（引擎契约：同一事件日 Σweight ≤ 1）。
   不满槽位时，剩余部分是**现金**（引擎口径：Σweight < 1 的差额即现金）。

## 「买不进」有两层，含义不同

| 层 | 情形 | 表现 |
|---|---|---|
| **策略层** | 槽位满 / 候选不足 | 当天**不发买入信号**；被挡下的候选数记在信号的 `meta.blocked` |
| **引擎层** | 隔日**一字涨停**买不进 | 该份额度当日就是**现金**、**不顺延**到次日开盘价买（引擎 `weight_strict_fill` 口径）；该票**仍占用策略槽位**，引擎按**实际持仓**每日重试建仓，开板当天自动补上 |

⚠️ 引擎那条「每日重试」很重要：它不是「顺延」，而是「每天都当成一次建仓」——
d1 一字板买不进（当天空仓），d2 若不再一字板就真的买进 d2。
若 d2 又是一字板，继续当天空仓。实测（`jwzz top_n=1`）曾因「顺延到次日开盘价买入」
把收益抬到远高于真实可实现的水平，故改成「买不到就不买」，更保守也更诚实。

## 与指标同源

JWZZ 的计算、**穿越判定、状态机、停牌沿用、统一交易日轴对齐**都在
`formulas/jwzz.py`（中立内核包）—— 与指标副图、`jwzz_cross` 共用**同一份实现**，
策略选股与副图显示逐日可对照；两边不会各算各的而漂移。
"""
from __future__ import annotations

import numpy as np

from .core.base import strategy, ParamSpec, Signal
from .core import data
from formulas.jwzz import DEFAULT_PERIOD, jwzz_panel


@strategy(
    id="jwzz_hold",
    name="JWZZ 持有到下穿",
    category="趋势",
    description="每日买入 JWZZ 最高的一只（可设每日买入只数，N=2 时买前 2），"
                "单票仓位=1/槽位数（不满槽位留现金）；只有 JWZZ 下穿阈值才卖出；"
                "持仓槽位满且没有票下穿时，当天买不进。",
    inputs=["close", "volume", "amount"],
    outputs="目标权重向量(组合占比) + 因子值(meta)",
    requires_pool=False,
    # v2：单票仓位改为「1/槽位数」（不满槽位留现金），移除了 v1 的 position_each 参数
    version="2",
    params=[
        ParamSpec("cost_period", "int", DEFAULT_PERIOD, min=2, max=250,
                   label="成本EMA周期"),
        ParamSpec("threshold", "float", 95.0, min=-500.0, max=2000.0,
                   label="触发阈值(‰)"),
        ParamSpec("smooth_period", "int", 0, min=0, max=250,
                   label="平滑周期(0=原始JWZZ；>0需把阈值调低约5倍)"),
        ParamSpec("max_slots", "int", 10, min=1, max=100,
                   label="最大持仓槽位(单票仓位=1/槽位数)"),
        ParamSpec("buys_per_day", "int", 1, min=1, max=20,
                   label="每日买入只数(按JWZZ排名取前N)"),
    ],
)
def jwzz_hold(ctx, cost_period: int = DEFAULT_PERIOD, threshold: float = 95.0,
              smooth_period: int = 0, max_slots: int = 10,
              buys_per_day: int = 1) -> list:
    codes = [str(c).strip() for c in (ctx.codes or []) if str(c).strip()]
    if not codes:
        return []
    period = max(2, int(cost_period))
    thr = float(threshold)
    smooth_n = max(0, int(smooth_period))
    slots = max(1, int(max_slots))
    # 单票仓位 = 1 / 槽位数（**不单独设**）：满槽时 Σweight 恰好 = 1，
    # 不满槽时 < 1（差额即现金）→ **不可能**再配出「Σweight > 1」的越界组合。
    # 以前仓位与槽位是两个参数，0.1 × 20 = 2.0 这类组合只能等跑起来才报错。
    w_each = 1.0 / slots
    n_buy = max(1, int(buys_per_day))

    # 主动拉取所需字段（inputs=["close","volume","amount"]）；缓存命中，无额外 I/O
    bars = data.load_bars(ctx.codes, ctx.start, ctx.end,
                          fields=["close", "volume", "amount"], adjust=ctx.adjust)

    # 穿越判定 / 状态机 / 停牌沿用 / 统一轴对齐 —— 与指标、jwzz_cross 共用同一份
    panel = jwzz_panel(bars, period=period, threshold=thr, smooth_period=smooth_n)
    if not panel.cols:
        return []
    cols = panel.cols
    days = panel.days
    bull_m = panel.bull          # C×T，当日是否强势（停牌沿用上一有值状态）
    val_m = panel.val             # C×T，当日偏离度值
    dn_m = panel.dn               # C×T，当日是否发生「下穿阈值」事件
    bar_m = panel.bar             # C×T，当日该票是否有行情（停牌为 False）
    row_of = {c: i for i, c in enumerate(cols)}

    signals: list[Signal] = []
    held: set[str] = set()       # ★ 策略视角的当前持仓（昨日结束时的集合）

    for t, day in enumerate(days):
        # ---- 1) 卖出：当日下穿阈值的持仓票 ----
        to_sell = {c for c in held if dn_m[row_of[c], t]}

        # ---- 2) 可用槽位：按**昨日**持仓数算 ----
        #    昨日卖出腾出的槽位今天不可用 → 卖出与买入分开一天（次日再补）
        slots_used = len(held)
        free = slots - slots_used

        # ---- 3) 买入候选：当日强势 + 当日有行情 + 昨日未持有；JWZZ 降序 ----
        cand_rows = [i for i in np.flatnonzero(bull_m[:, t])
                     if bar_m[i, t] and cols[i] not in held]
        cand_rows.sort(key=lambda i: -float(val_m[i, t]))
        take = max(0, min(free, n_buy, len(cand_rows)))
        to_buy = [cols[i] for i in cand_rows[:take]]
        blocked = len(cand_rows) - len(to_buy)     # 因槽位满/每日限额被挡下的候选数

        if not to_sell and not to_buy:
            continue                                # 无变动 → 不发信号（ffill 维持持仓）

        new_held = (held - to_sell) | set(to_buy)

        # 掉出的票显式 weight=0：不发的话这一天不会进入引擎的 rebalance_dates，
        # 「移出」根本没被登记，上一期持仓会继续持有。
        for code in sorted(to_sell):
            signals.append(Signal(
                code=code, time=day, weight=0.0,
                reason="JWZZ 下穿阈值卖出",
                meta={"value": round(float(val_m[row_of[code], t]), 3),
                      "slots_used": slots_used, "blocked": blocked},
            ))

        # ⚠️ 必须**整组重发**全部持仓：引擎把「调仓日未出现的 code」记为 0，
        #    少发一条 = 静默清仓。
        for code in sorted(new_held):
            is_new = code in to_buy
            meta = {
                "value": round(float(val_m[row_of[code], t]), 3),
                "slots_used": slots_used,
                "blocked": blocked,
            }
            if is_new:
                meta["rank"] = to_buy.index(code) + 1   # 当日候选里的 JWZZ 排名
            signals.append(Signal(
                code=code, time=day, weight=w_each,
                reason="JWZZ 强势买入" if is_new else "JWZZ 强势持有",
                meta=meta,
            ))

        held = new_held

    return signals
