# -*- coding: utf-8 -*-
"""策略：JWZZ 上穿/下穿 + **跳过炸板买入**（`jwzz_cross` 的安全版）。

## 与 `jwzz_cross` 的关系

**选股口径完全同源**（同一个 `formulas/jwzz.py` 面板、同一套阈值/排名规则），
**只多一条买入限制**：

> 买入日当天**涨停开盘、盘中炸板**（突破失败）的票 → **这一笔不买**。

其余（卖出规则、权重、换手、槽位…）**一律沿用 `jwzz_cross`**，
所以两者的回测结果可以直接对照 —— 差异只来自这一条规则。

## 为什么要有这条规则（用户的实盘规则）

实盘是「隔夜单」：昨晚挂单，今天开盘成交。**今天涨停开盘但盘中炸板** →
说明开盘那一下没成交（板子没开），单子作废 → **这一笔放弃**，
不追突破失败的票。

## 在本引擎里的落点（口径必须写死，否则两边算的不是一件事）

  * 本引擎的成交口径是「**信号日收盘成交**」（`w_{t-1}` 承担第 `t` 日收益，
    当日收盘算信号），**不是**「次日开盘成交」→ **买入日 = 信号日**，
    策略在信号日收盘时**已经看得到**当天的 open/high/low/close → **不需要前视**。
  * 故判定落在**买入日当天**：
    `open ≥ 涨停价 × (1 − 0.2%)`（涨停开盘，容差吸收涨停价取整到分的误差）且
    `low < 涨停价`（盘中炸板）→ **放弃这一笔买入**。
  * **一字板（开盘 = 最低 = 涨停价）不算炸板**：它不是「没成交」而是**根本买不进**，
    由引擎的严格成交模型处理（该份额度当日为现金、不重分配）——
    两处不重复拦截，否则「买不进」会被误记成「策略主动放弃」，归因错位。
  * **只影响买入，不影响卖出**：已持有的票不会因为当天炸板被踢出组合
    （卖出规则仍是 `jwzz_cross` 那套：掉出前 N 或下穿即卖）。
  * **放弃的名额留成现金、不补位**：炸板票占住自己的名额、后面的候选**不递补**，
    权重分母仍是**名额数**（不是实际持仓数）→ Σw < 1 的差额即现金。
    ⚠️ 这与引擎的严格成交模型**同一口径**：「一字涨停买不进 → 该份额度当日就是现金、
    **不重分配给其他票**」。若改成「下一名候选补位」，被放弃的收益/风险就转给了别人 ——
    那不是「这一笔不买」，那是「换一只买」，口径完全不同。

⚠️ 涨停幅度 / 涨停价 / 炸板判定的**唯一实现**在 `core/limit_price.py` ——
与统计层的可行性判定（严格成交模型）**同一份**，改一处即两处生效。

留痕与开关：当天放弃的笔数记在 `meta.skipped_broken`；**即使组合没变也发一次整组**
（权重不变 → 引擎 diff 无交易、不花钱），否则这次放弃在报告里彻底看不见。
开关 `skip_broken_limit_up`（0/1，默认 **1**）；置 0 时与 `jwzz_cross` 逐条信号完全一致
（单测 `test_limit_patterns` 有断言）。

## 与指标同源

计算层在 `formulas/jwzz.py`（中立内核包），与副图指标 JWZZ / 主图 ECOST 共用同一份
实现（strategies/ARCHITECTURE.md 硬要求）：策略选股与副图显示逐日可对照；
两边不会因为各算各的而漂移。
成交额由数据层按复权因子同步缩放（与 close 同口径），除权除息日不出假信号。
"""
from __future__ import annotations

import numpy as np

from .core.base import strategy, ParamSpec, Signal
from .core import data
from core.limit_price import broken_limit_up_panel
from formulas.jwzz import DEFAULT_PERIOD, jwzz_panel


@strategy(
    id="jwzz_cross_safe",
    name="JWZZ 上穿/下穿（跳过炸板）",
    category="趋势",
    description="与「JWZZ 上穿/下穿」同一套选股口径，只多一条买入限制："
                "买入日当天涨停开盘、盘中炸板的票视为隔夜单未成交 → 这一笔不买"
                "（一字板不算炸板：那是买不进，由引擎成交模型处理）；"
                "卖出规则不变（掉出前 N 或下穿即卖）。",
    inputs=["close", "volume", "amount"],
    outputs="目标权重向量(组合占比) + 因子值(meta)",
    requires_pool=False,
    version="1",
    params=[
        ParamSpec("cost_period", "int", DEFAULT_PERIOD, min=2, max=250,
                   label="成本EMA周期"),
        ParamSpec("threshold", "float", 95.0, min=-500.0, max=2000.0,
                   label="触发阈值(‰)"),
        ParamSpec("smooth_period", "int", 0, min=0, max=250,
                   label="平滑周期(0=原始JWZZ；>0需把阈值调低约5倍)"),
        ParamSpec("top_n", "int", 10, min=1, max=200, label="持仓数量上限"),
        ParamSpec("position", "float", 1.0, min=0.1, max=1.0, label="总仓位系数"),
        ParamSpec("skip_broken_limit_up", "int", 1, min=0, max=1,
                   label="跳过炸板买入(0=否,1=是)"),
    ],
)
def jwzz_cross_safe(ctx, cost_period: int = DEFAULT_PERIOD, threshold: float = 95.0,
                    smooth_period: int = 0, top_n: int = 10,
                    position: float = 1.0, skip_broken_limit_up: int = 1) -> list:
    codes = [str(c).strip() for c in (ctx.codes or []) if str(c).strip()]
    if not codes:
        return []
    period = max(2, int(cost_period))
    thr = float(threshold)
    smooth_n = max(0, int(smooth_period))
    top_n = max(1, int(top_n))
    pos = min(1.0, max(0.0, float(position)))
    # 「涨停开盘但盘中炸板 → 放弃这一笔买入」（默认开；口径见模块 docstring）
    skip_broken = int(skip_broken_limit_up or 0) > 0

    # 主动拉取所需字段（inputs=["close","volume","amount"]）；缓存命中，无额外 I/O
    # ⚠️ 炸板过滤要 open / low → **仅在开启时**才多取两列
    fields = ["close", "volume", "amount"] + (["open", "low"] if skip_broken else [])
    bars = data.load_bars(ctx.codes, ctx.start, ctx.end,
                          fields=fields, adjust=ctx.adjust)

    # ---- 统一交易日轴 + 强势状态（**口径在 formulas/jwzz.py**，与 jwzz_cross 同源）----
    panel = jwzz_panel(bars, period=period, threshold=thr, smooth_period=smooth_n)
    if not panel.cols:
        return []
    day_index = panel.days
    cols = panel.cols
    bull_m = panel.bull                             # C × T，bool（每票一行）
    strength_m = panel.val                           # C × T，float32

    signals: list[Signal] = []
    held: set[str] = set()                          # ★ 自己维护持仓集合

    # ---- 炸板矩阵（C×T）：某票某日是否「涨停开盘且盘中炸板」 ----
    #    口径与统计层的可行性判定**同一份**（core/limit_price.py）。
    broken_m = broken_limit_up_panel(bars, cols, day_index) if skip_broken else None

    for t, day in enumerate(day_index):
        day_bull = np.flatnonzero(bull_m[:, t])
        skipped_broken = 0
        picks: set[str] = set()
        slots = 0                                  # 名额数（权重分母）
        if day_bull.size:
            # 偏离度降序；同值保持列序（stable），保证结果可复现、不依赖 dict 顺序
            order = day_bull[np.argsort(-strength_m[day_bull, t], kind="stable")]
            head = order[:top_n]
            slots = len(head)                      # 名额数 = 本来会取的前 top_n 只
            if broken_m is None:
                picked = list(head)
            else:
                # ⚠️ **不补位**：被放弃的炸板票**占住自己的名额**，后面的候选不递补
                #    → 那个名额**留成现金**（不重分配给其他票）。
                #    这与引擎的严格成交模型同一口径：「一字涨停买不进 → 该份额度当日
                #    就是现金、不重分配给其他票」。
                # ⚠️ **已持有的票不过滤** —— 这条规则只管「买不买」，
                #    不能把持仓踢出组合（卖出规则仍沿用 jwzz_cross）。
                picked: list = []
                for i in head:
                    if cols[i] not in held and broken_m[i, t]:
                        skipped_broken += 1       # 涨停开盘后炸板 → 放弃这一笔
                        continue
                    picked.append(i)
            picks = {cols[i] for i in picked}
        # 成分没变 → 不发信号（ffill 维持）。
        # ⚠️ 但**当天有炸板被跳过**时例外：仍发一次整组 —— 权重不变 → 引擎 diff 出来
        #    没有交易（不花钱、不改净值），只为把「今天放弃了哪一笔」**留痕**。
        #    否则这次放弃在报告里彻底看不见（= 静默改数据）。
        if picks == held and skipped_broken == 0:
            continue

        # 掉出的票显式 weight=0：不发的话这一天不会进入引擎的 rebalance_dates，
        # 「移出」根本没被登记，上一期持仓会继续持有。
        for code in sorted(held - picks):
            signals.append(Signal(code=code, time=day, weight=0.0,
                                  reason="JWZZ 离开强势区/跌出前N",
                                  meta={"dropped": 1,
                                        "skipped_broken": skipped_broken}))

        if picks:
            # ⚠️ 权重分母是**名额数 slots**，不是实际持仓数：
            #    放弃的名额**留成现金**（Σw < 1 的差额即现金），不抬到别人头上。
            w = pos / slots if slots > 0 else 0.0
            value_of = {cols[i]: float(strength_m[i, t]) for i in range(len(cols))}
            for rank, code in enumerate(sorted(picks), start=1):
                signals.append(Signal(
                    code=code, time=day, weight=w, reason="JWZZ 上穿阈值",
                    meta={"value": round(value_of.get(code, 0.0), 3), "rank": rank,
                          "skipped_broken": skipped_broken},
                ))
        held = picks

    return signals
