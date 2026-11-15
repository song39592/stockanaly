# -*- coding: utf-8 -*-
"""价量偏离度（JWZZ）与其成本线（X_3）的**计算层** —— 指标与策略共用的单一来源。

位于**中立内核包** `formulas/`（见该包 `__init__.py`）：它既不是指标、也不是策略、
更不是筹码 —— 只是「一段被两处复用的纯计算」，所以住在中立的地方。

与 `formulas` 下其它内核同理：指标侧只做展示包装、策略侧直接复用，
两边数值天然一致，不会因为各写一遍而漂移。

    X_3  := 0.01 * EMA(AMOUNT, 13) / EMA(VOL, 13);
    JWZZ := (CLOSE - X_3) / X_3 * 1000;
    JW_M := SMA(JWZZ, 13, 1), COLORYELLOW, LINETHICK1;

口径要点：
  * **0.01 是手->股换算**：成交额单位元、成交量单位手，元 / 手 / 100 = 元/股。
    通达信的 VOL 单位同样是手，故该系数原样成立。
  * **必须先各自 EMA 再相除**：`EMA(成交额) / EMA(成交量)` 是**量的加权**，
    与「先算日均价再取 EMA」（价的平均）不是一回事，两者不可互换。
  * `SMA(X, N, 1)` 的第三参 1 = 递推权重，等价于 `ewm(alpha=1/N, adjust=False)`
    （与本项目 RSI / KDJ 的 1/N 平滑同口径）。

⚠️ **成交额必须与 close 同口径**：复权（qfq / hfq）只缩放 open/high/low/close，
成交额与成交量不变；数据层（`indicators/data.py`、`strategies/core/data.py`，
规则见 `core/bars_schema.py`）负责把成交额乘上同一个复权因子，
本模块拿到的 amount 与 close 已是同口径。
否则除权除息日两者错位，偏离度会跳出**假信号**。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# 成交额（元）/ 成交量（手）-> 元/股：除以 100（1 手 = 100 股）
AMOUNT_TO_PRICE = 0.01

DEFAULT_PERIOD = 13


def ema_cost_line(df: pd.DataFrame, period: int = DEFAULT_PERIOD) -> pd.Series:
    """X_3 成本线（元/股），索引与 `df` 完全同序。

    `period` = EMA 周期（通达信原式为 13）。

    边界处理：
      * 数据缺 `amount` / `volume` 列 -> 整条 NaN（不静默用 close 顶替：
        那就不是这个指标了，用户看到的会是一个假的东西）；
      * EMA(成交量) 为 0（长期无成交）-> 该点 NaN，不产生 inf。
    """
    p = max(2, int(period))
    index = df.index
    if "amount" not in df.columns or "volume" not in df.columns:
        return pd.Series(float("nan"), index=index)

    amount = pd.to_numeric(df["amount"], errors="coerce")
    volume = pd.to_numeric(df["volume"], errors="coerce")
    # span=p 与通达信 EMA(X, p) 的平滑系数 2/(p+1) 一致（adjust=False 等价于递推）
    ema_amount = amount.ewm(span=p, adjust=False).mean()
    ema_volume = volume.ewm(span=p, adjust=False).mean()
    cost = AMOUNT_TO_PRICE * ema_amount / ema_volume.where(ema_volume > 0)
    return cost


def jwzz_series(df: pd.DataFrame, period: int = DEFAULT_PERIOD) -> pd.Series:
    """JWZZ 偏离度（‰）：收盘价相对成本线的偏离，单位千分比。

    +1000 = 收盘价是成本线的 2 倍；-500 = 只有一半。
    """
    cost = ema_cost_line(df, period)
    close = pd.to_numeric(df["close"], errors="coerce")
    return (close - cost) / cost.where(cost.abs() > 0) * 1000.0


def sma1_series(series: pd.Series, period: int = DEFAULT_PERIOD) -> pd.Series:
    """通达信 `SMA(X, N, 1)`：递推平滑 Y = (X + (N-1)*Y') / N。

    等价于 `ewm(alpha=1/N, adjust=False)`（初值取第一个有效值，与通达信一致）。
    """
    n = max(2, int(period))
    return series.ewm(alpha=1.0 / n, adjust=False).mean()


# ---------------------------------------------------------------------------
# 面板：把「{code: 行情}」算成「统一交易日轴」上的 JWZZ 面板
# ---------------------------------------------------------------------------
# 为什么下沉到这里（而不是留在某个策略里）：
#   「穿越判定 + 状态机 + 停牌沿用 + 对齐统一轴」这段逻辑被**两个策略**用到
#   （JWZZ 上穿/下穿、JWZZ 持有到下穿）。各写一份 = 同一口径两份实现，
#   将来两边算出两个数、两边都不报错。故它是**被两处复用的纯计算**，
#   归中立内核包，依赖方向仍是「指标 → formulas ← 策略」。


@dataclass
class JwzzPanel:
    """统一交易日轴上的 JWZZ 面板（矩阵行 = `cols` 里的代码，列 = `days`）。

    字段：
        cols  参与计算的代码顺序（与各矩阵的行一一对应）
        days  统一交易日轴（各标的交易日的并集，升序）
        bull  C×T bool，当日是否处于强势区（JWZZ > 阈值）
              **停牌沿用上一个有值的状态**（停牌不清仓、不改状态）
        val   C×T float32，当日偏离度值（停牌沿用上一个有值）
        dn    C×T bool，当日是否发生「下穿阈值」事件（只有有行情的日子才可能为 True）
        bar   C×T bool，当日该票**是否有行情**（停牌日为 False）
              —— 「买不进 / 没得买」的判定要用它：停牌的票买不进去，
              不能占掉一次买入名额。
    """

    cols: list[str] = field(default_factory=list)
    days: list[str] = field(default_factory=list)
    bull: np.ndarray = field(default_factory=lambda: np.zeros((0, 0), dtype=bool))
    val: np.ndarray = field(default_factory=lambda: np.zeros((0, 0), dtype=np.float32))
    dn: np.ndarray = field(default_factory=lambda: np.zeros((0, 0), dtype=bool))
    bar: np.ndarray = field(default_factory=lambda: np.zeros((0, 0), dtype=bool))


def jwzz_panel(bars: dict[str, pd.DataFrame],
               period: int = DEFAULT_PERIOD,
               threshold: float = 95.0,
               smooth_period: int = 0) -> JwzzPanel:
    """把 `{code: DataFrame}` 算成统一交易日轴上的 JWZZ 面板。

    参数：
        bars        {code: DataFrame}，需含 close / volume / amount（缺列 → 该票跳过）
        period      成本线 EMA 周期（默认 13）
        threshold   强势阈值（‰，默认 95）
        smooth_period > 1 时用 `SMA(JWZZ, N, 1)` 平滑线；0/1 = 原始 JWZZ

    口径要点：
      * **上穿** = 当前 > 阈值 且（前值 ≤ 阈值 或 前值不可用）——
        首个有效值也算上穿（从无效区进入强势区），否则强势新股要等下一次真穿越才建仓；
      * **下穿** = 当前 ≤ 阈值 且 前值 > 阈值（严格穿越事件）；
      * **停牌不清仓**：无行情的日子沿用上一个有值的状态与数值；
      * 根数不足 `period + 1` 或整条无有效值的票直接不参与（不进矩阵）。
    """
    p = max(2, int(period))
    need = p + 1
    thr = float(threshold)
    smooth_n = max(0, int(smooth_period))

    seen: set[str] = set()
    for df in (bars or {}).values():
        if df is None:
            continue
        for d in df.index:
            seen.add(str(d)[:10])
    days = sorted(seen)
    if not days:
        return JwzzPanel()
    day_pos = {d: i for i, d in enumerate(days)}
    T = len(days)

    cols: list[str] = []
    bull_rows: list[np.ndarray] = []
    val_rows: list[np.ndarray] = []
    dn_rows: list[np.ndarray] = []
    bar_rows: list[np.ndarray] = []

    for code, df in (bars or {}).items():
        if df is None or len(df) < need:
            continue
        dev = jwzz_series(df, p)
        raw = dev.to_numpy(dtype="float64")
        finite = np.isfinite(raw)
        if not finite.any():
            continue
        if smooth_n > 1:
            arr = sma1_series(dev, smooth_n).to_numpy(dtype="float64")
        else:
            arr = raw
        arr = np.where(finite, arr, np.nan)

        prev = np.empty_like(arr)
        prev[1:] = arr[:-1]
        prev[0] = np.nan
        up = (arr > thr) & ((prev <= thr) | ~np.isfinite(prev))
        dn = (arr <= thr) & (prev > thr)

        # 状态机：上穿置 True、下穿置 False；用「最后一次事件」向量化求解。
        # ⚠️ 只对「前面确实有事件」的位置赋值：last == -1 表示该位置之前
        #    没有任何穿越事件（状态应为 False）。若直接 clip(-1 → 0) 取第一个事件，
        #    会把「事件发生前」的日子全判成与首个事件同状态。
        ev = np.zeros(len(arr), dtype=np.int8)
        ev[up] = 1
        ev[dn] = -1
        idxs = np.flatnonzero(ev)
        state = np.zeros(len(arr), dtype=bool)
        if idxs.size:
            last = np.searchsorted(idxs, np.arange(len(arr)), side="right") - 1
            has_ev = last >= 0
            state[has_ev] = ev[idxs[last[has_ev]]] > 0

        idx = np.fromiter((day_pos[str(d)[:10]] for d in df.index),
                          dtype=np.int64, count=len(df))
        row_state = np.full(T, -1, dtype=np.int8)
        row_val = np.zeros(T, dtype=np.float32)
        row_dn = np.zeros(T, dtype=bool)
        row_bar = np.zeros(T, dtype=bool)
        row_state[idx[finite]] = state[finite].astype(np.int8)
        row_val[idx[finite]] = arr[finite].astype(np.float32)
        row_dn[idx[dn]] = True
        row_bar[idx] = True

        # 前向填充（停牌不清仓）：预热段之前无值 → 保持未持有
        last_pos = np.maximum.accumulate(np.where(row_state >= 0, np.arange(T), -1))
        valid = last_pos >= 0
        if valid.any():
            src = np.where(valid, last_pos, 0)
            row_state = np.where(valid, row_state[src], -1).astype(np.int8)
            row_val = np.where(valid, row_val[src], 0.0).astype(np.float32)

        cols.append(code)
        bull_rows.append(row_state > 0)
        val_rows.append(row_val)
        dn_rows.append(row_dn)
        bar_rows.append(row_bar)

    if not cols:
        return JwzzPanel(days=days)
    return JwzzPanel(
        cols=cols,
        days=days,
        bull=np.stack(bull_rows),
        val=np.stack(val_rows),
        dn=np.stack(dn_rows),
        bar=np.stack(bar_rows),
    )
