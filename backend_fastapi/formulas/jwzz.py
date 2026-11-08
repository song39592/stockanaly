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
