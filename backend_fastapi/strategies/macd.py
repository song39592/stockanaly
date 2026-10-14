# -*- coding: utf-8 -*-
"""示例策略：MACD 金叉 / 死叉（趋势跟踪）—— 组合权重契约的最小样板。

原始语义是「金叉买入、死叉卖出」，即**状态**：一只票从金叉那天开始持有，
一直拿到死叉为止。组合权重口径下必须把它显式写成
「当前处于多头状态的票 = 目标组合」，否则逐票各自发信号会自我摧毁
（某票金叉那天只发它一只 → Σw=1 → 100% 砸进这一只；下一只金叉又全仓换人）。

实现（状态矩阵 + 完整目标组合）：
  1. 逐票算 DIF/DEA，规约到**统一交易日轴**上，堆成两个矩阵：
     `bull`(bool, C×T) 与 `strength`(float32, C×T)；
  2. 每个交易日取当日所有多头票，按 strength 降序取前 `top_n` 只，
     构成**完整目标组合**；
  3. 目标组合**发生变化**时才发信号（成分没变就不发，靠引擎的 ffill 维持持仓）；
  4. 掉出的票**显式发 weight=0**，入选的票发 `position / len(组合)`。

第 4 条的「必须」有个坑：若某日发出**零条**信号，该日不会进入引擎的
`rebalance_dates`（= 全部信号日期的并集），清仓就不会被登记、上一期持仓继续持有。

⚠️ **为什么用矩阵而不是 dict[code][date]**：全市场 5578 只 × 730 天是 407 万个
「每票每天一个 dict 条目」，连 key 带 tuple 约 250B → **约 1GB**，在可用内存只有
1.2GB 的机器上会直接把后端压垮（这是「全市场同步回测 500」的真正原因之一）。
改成两个矩阵：bool 4MB + float32 16MB，差 60 倍。

`position` 决定总仓位（Σweight = position），现金 = 1 − position。
"""
from __future__ import annotations

import numpy as np

from .core.base import strategy, ParamSpec, Signal
from .core import data


@strategy(
    id="macd",
    name="MACD 金叉/死叉",
    category="趋势",
    description="DIF 上穿 DEA（金叉）视为进入多头状态、下穿（死叉）退出；"
                "每日取处于多头状态的票按信号强度排序取前 N 只等权持有。",
    inputs=["close"],
    outputs="目标权重向量(组合占比) + 因子值(meta)",
    params=[
        ParamSpec("fast", "int", 12, min=2, max=60, label="快线 EMA"),
        ParamSpec("slow", "int", 26, min=5, max=120, label="慢线 EMA"),
        ParamSpec("signal", "int", 9, min=2, max=60, label="信号 EMA"),
        ParamSpec("top_n", "int", 20, min=1, max=200, label="持仓数量上限"),
        ParamSpec("position", "float", 1.0, min=0.1, max=1.0, label="总仓位系数"),
    ],
)
def macd(ctx, fast: int = 12, slow: int = 26, signal: int = 9,
         top_n: int = 20, position: float = 1.0) -> list:
    codes = [str(c).strip() for c in (ctx.codes or []) if str(c).strip()]
    if not codes:
        return []

    fast, slow, sig = int(fast), int(slow), int(signal)
    top_n = max(1, int(top_n))
    pos = min(1.0, max(0.0, float(position)))
    need = max(fast, slow, sig) + 1

    # 主动拉取本策略所需的字段（inputs=["close"]）；缓存命中，无额外 I/O
    bars = data.load_bars(ctx.codes, ctx.start, ctx.end,
                          fields=["close"], adjust=ctx.adjust)

    # ---- 统一交易日轴（组合决策必须在同一天上做）----
    seen: set[str] = set()
    for df in bars.values():
        for d in df.index:
            seen.add(str(d)[:10])
    if not seen:
        return []
    day_index = sorted(seen)
    day_pos = {d: i for i, d in enumerate(day_index)}
    T = len(day_index)

    cols: list[str] = []
    rows_bull: list[np.ndarray] = []
    rows_strength: list[np.ndarray] = []

    for code, df in bars.items():
        close = df["close"].astype(float)
        if len(close) < need:
            continue
        ema_f = close.ewm(span=fast, adjust=False).mean()
        ema_s = close.ewm(span=slow, adjust=False).mean()
        dif = ema_f - ema_s
        dea = dif.ewm(span=sig, adjust=False).mean()

        idx = np.fromiter((day_pos[str(d)[:10]] for d in close.index),
                          dtype=np.int64, count=len(close))
        dv = dif.to_numpy(dtype=np.float32)
        ev = dea.to_numpy(dtype=np.float32)
        ok = np.isfinite(dv) & np.isfinite(ev)      # NaN 预热段不算多头
        if not ok.any():
            continue

        with np.errstate(divide="ignore", invalid="ignore"):
            strength = np.abs(dv - ev) / np.abs(ev)
        strength = np.nan_to_num(strength, nan=0.0, posinf=0.0, neginf=0.0)

        bull = np.zeros(T, dtype=bool)
        bull[idx[ok]] = dv[ok] > ev[ok]
        srow = np.zeros(T, dtype=np.float32)
        srow[idx[ok]] = strength[ok]

        cols.append(code)
        rows_bull.append(bull)
        rows_strength.append(srow)

    if not cols:
        return []

    bull_m = np.stack(rows_bull, axis=0)             # C × T，bool（每票一行）
    strength_m = np.stack(rows_strength, axis=0)     # C × T，float32

    signals: list[Signal] = []
    held: set[str] = set()                          # ★ 自己维护持仓集合

    for t, day in enumerate(day_index):
        day_bull = np.flatnonzero(bull_m[:, t])
        if day_bull.size == 0:
            continue
        # 强度降序；同强度保持列序（stable），保证结果可复现、不依赖 dict 顺序
        order = day_bull[np.argsort(-strength_m[day_bull, t], kind="stable")]
        top = order[:top_n]
        picks = {cols[i] for i in top}

        if picks == held:
            continue                                # 成分没变 → 不发信号（ffill 维持）

        # 掉出的票显式 weight=0：不发的话这一天不会进入引擎的 rebalance_dates，
        # 「移出」根本没被登记，上一期持仓会继续持有。
        for code in sorted(held - picks):
            signals.append(Signal(code=code, time=day, weight=0.0,
                                  reason="MACD 死叉/跌出前N", meta={"dropped": 1}))

        # 完整目标组合：等权分配总仓位
        w = (pos / len(picks)) if picks else 0.0
        strength_of = {cols[i]: float(strength_m[i, t]) for i in top}
        for rank, code in enumerate(sorted(picks), start=1):
            signals.append(Signal(
                code=code, time=day, weight=w, reason="MACD 多头（DIF>DEA）",
                meta={"strength": round(strength_of.get(code, 0.0), 6), "rank": rank},
            ))
        held = picks

    return signals