# -*- coding: utf-8 -*-
"""策略：JWZZ 上穿阈值买入 / 下穿阈值卖出（价量偏离度）。

通达信原始语义：

    X_3  := 0.01 * EMA(AMOUNT, 13) / EMA(VOL, 13);
    JWZZ := (CLOSE - X_3) / X_3 * 1000;
    强势 := 95;

JWZZ = 收盘价相对「N 日资金成本线」的偏离度（‰），+95 表示股价高出成本线 9.5%。
本策略把它当**状态**：上穿阈值视为进入强势并买入，下穿视为退出并卖出。

## 为什么是「状态」而非「逐次信号」

组合权重口径下策略输出**目标权重向量**，买卖由引擎对相邻调仓日的权重做 diff
派生。若每只票各自在穿越那天发一次信号，会退化成「每次穿越全仓换人」
（macd.py 文档记录过这个坑）。故与 MACD 策略同构：
  1. 逐票算 JWZZ → 进入/退出强势的状态序列；
  2. 每日取当日强势票按 JWZZ 降序取前 top_n 只 → **完整目标组合**；
  3. 组合**变化**时才发信号（成分不变靠引擎 ffill 维持持仓）；
  4. 掉出的票**显式发 weight=0**（不发则该日不进 rebalance_dates，移出没被登记）。

## 「上穿」的判定口径

标准 CROSS 要求「前值 ≤ 阈值 且 当前 > 阈值」。但**首个有效值**（EMA 预热后
第一根 K）没有前值可比，本策略也算作上穿 —— 即「从无效区进入强势区」。
否则强势新股要等到下一次真穿越才建仓，凭空晚一步。

## 与指标同源

计算层在 `formulas/jwzz.py`（中立内核包），与副图指标 JWZZ / 主图 ECOST 共用同一份
实现（strategies/ARCHITECTURE.md 硬要求）：策略选股与副图显示逐日可对照。
成交额由数据层按复权因子同步缩放（与 close 同口径），除权除息日不出假信号。

## 实盘注意（实测，5 只票 / 1 年）

用**原始 JWZZ** 触发时：阈值 95‰ 在震荡行情里被反复穿越，实测持仓只有 1~2 天
（avg_positions 0.037、11 次权重变动 / 5 笔平仓），交易成本会吃掉大部分收益。
两个可选缓解手段：
  * `smooth_period` > 0：改用副图里的黄线 JW_M = SMA(JWZZ, N, 1) 触发，
    假突破明显减少（代价是进出场更迟）。⚠️ **量级不同**：平滑后幅度约为原始的
    1/5~1/6（实测 JWZZ max 162 → JW_M max 27），阈值要相应调低到 20~30 才有信号；
  * 调高 `threshold`：只在偏离度显著走强时才建仓。
"""
from __future__ import annotations

import numpy as np

from .core.base import strategy, ParamSpec, Signal
from .core import data
from formulas.jwzz import DEFAULT_PERIOD, jwzz_series, sma1_series


@strategy(
    id="jwzz_cross",
    name="JWZZ 上穿/下穿",
    category="趋势",
    description="价量偏离度 JWZZ 上穿阈值（默认 95‰）视为进入强势状态并买入，"
                "下穿阈值视为退出并卖出；每日按偏离度降序取前 N 只等权持有。",
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
        ParamSpec("top_n", "int", 10, min=1, max=200, label="持仓数量上限"),
        ParamSpec("position", "float", 1.0, min=0.1, max=1.0, label="总仓位系数"),
    ],
)
def jwzz_cross(ctx, cost_period: int = DEFAULT_PERIOD, threshold: float = 95.0,
               smooth_period: int = 0, top_n: int = 10,
               position: float = 1.0) -> list:
    codes = [str(c).strip() for c in (ctx.codes or []) if str(c).strip()]
    if not codes:
        return []
    period = max(2, int(cost_period))
    thr = float(threshold)
    smooth_n = max(0, int(smooth_period))
    top_n = max(1, int(top_n))
    pos = min(1.0, max(0.0, float(position)))
    need = period + 1

    # 主动拉取所需字段（inputs=["close","volume","amount"]）；缓存命中，无额外 I/O
    bars = data.load_bars(ctx.codes, ctx.start, ctx.end,
                          fields=["close", "volume", "amount"], adjust=ctx.adjust)

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
        if len(df) < need:
            continue
        # 计算层与指标同源（formulas.jwzz）
        dev = jwzz_series(df, period)
        raw = dev.to_numpy(dtype="float64")
        finite = np.isfinite(raw)
        if not finite.any():
            continue
        # 触发序列：默认用原始 JWZZ（用户原语义）；smooth_period > 0 时改用
        # SMA(JWZZ, N, 1)（副图里的黄线 JW_M）—— 原始值在阈值附近会反复穿越，
        # 实测持仓只有 1~2 天、交易成本吃掉大部分收益；用平滑线触发能显著减少
        # 假突破（代价是进出场更迟）。
        if smooth_n > 1:
            arr = sma1_series(dev, smooth_n).to_numpy(dtype="float64")
        else:
            arr = raw
        arr = np.where(finite, arr, np.nan)

        # 穿越判定：当前 > 阈值 且（前值 ≤ 阈值 或 前值不可用=首个有效值）
        prev = np.empty_like(arr)
        prev[1:] = arr[:-1]
        prev[0] = np.nan
        up = (arr > thr) & ((prev <= thr) | ~np.isfinite(prev))
        dn = (arr <= thr) & (prev > thr)

        # 状态机：上穿置 True、下穿置 False；用「最后一次事件」向量化求解
        ev = np.zeros(len(arr), dtype=np.int8)
        ev[up] = 1
        ev[dn] = -1
        idxs = np.flatnonzero(ev)
        state = np.zeros(len(arr), dtype=bool)
        if idxs.size:
            last = np.searchsorted(idxs, np.arange(len(arr)), side="right") - 1
            # ⚠️ 必须只对「前面确实有事件」的位置赋值：last == -1 表示该位置之前
            #    没有任何穿越事件（状态应为 False）。若直接 clip(-1 → 0) 取第一个事件，
            #    会把「事件发生前」的日子全判成与首个事件同状态 —— 表现为
            #    「偏离度还是负的却被当成强势买入」。
            has_ev = last >= 0
            state[has_ev] = ev[idxs[last[has_ev]]] > 0

        idx = np.fromiter((day_pos[str(d)[:10]] for d in df.index),
                          dtype=np.int64, count=len(df))
        # 统一轴上的两行：-1/0 表示「该票当日无值」，停牌日沿用上一个有值状态
        row_state = np.full(T, -1, dtype=np.int8)
        row_val = np.zeros(T, dtype=np.float32)
        row_state[idx[finite]] = state[finite].astype(np.int8)
        row_val[idx[finite]] = arr[finite].astype(np.float32)
        # 前向填充（停牌不清仓）：预热段之前无值 → 保持未持有
        last_pos = np.maximum.accumulate(np.where(row_state >= 0, np.arange(T), -1))
        valid = last_pos >= 0
        if valid.any():
            src = np.where(valid, last_pos, 0)
            row_state = np.where(valid, row_state[src], -1).astype(np.int8)
            row_val = np.where(valid, row_val[src], 0.0).astype(np.float32)

        cols.append(code)
        rows_bull.append(row_state > 0)
        rows_strength.append(row_val)

    if not cols:
        return []

    bull_m = np.stack(rows_bull, axis=0)             # C × T，bool（每票一行）
    strength_m = np.stack(rows_strength, axis=0)     # C × T，float32

    signals: list[Signal] = []
    held: set[str] = set()                          # ★ 自己维护持仓集合

    for t, day in enumerate(day_index):
        day_bull = np.flatnonzero(bull_m[:, t])
        if day_bull.size:
            # 偏离度降序；同值保持列序（stable），保证结果可复现、不依赖 dict 顺序
            order = day_bull[np.argsort(-strength_m[day_bull, t], kind="stable")]
            picks = {cols[i] for i in order[:top_n]}
        else:
            picks = set()
        if picks == held:
            continue                                # 成分没变 → 不发信号（ffill 维持）

        # 掉出的票显式 weight=0：不发的话这一天不会进入引擎的 rebalance_dates，
        # 「移出」根本没被登记，上一期持仓会继续持有。
        for code in sorted(held - picks):
            signals.append(Signal(code=code, time=day, weight=0.0,
                                  reason="JWZZ 离开强势区/跌出前N",
                                  meta={"dropped": 1}))

        if picks:
            w = pos / len(picks)
            value_of = {cols[i]: float(strength_m[i, t]) for i in range(len(cols))}
            for rank, code in enumerate(sorted(picks), start=1):
                signals.append(Signal(
                    code=code, time=day, weight=w, reason="JWZZ 上穿阈值",
                    meta={"value": round(value_of.get(code, 0.0), 3), "rank": rank},
                ))
        held = picks

    return signals