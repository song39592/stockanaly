# -*- coding: utf-8 -*-
"""【辅助模块，不注册指标】成交均价成本线的计算内核。

为什么单独成文件：成本线（X_3）要同时供两个指标使用——
  * `cost_ema.py`：画在**主图**上的成本线（价格量纲，与 K 线同轴）；
  * `jwzz.py`：画在**副图**上的价量偏离度（相对量纲，‰）。
两者必须用**同一条**成本线（周期不同就会对不上：主图画的是 13 日成本，
副图算的却是 20 日偏离，用户对不上账）。故计算内核只写一份，两边各自 import。

⚠️ 本文件**不含** `@indicator` 装饰器 —— 它只是计算函数。
（与 `rps_data.py` 同类：包里允许有「辅助模块」，指标索引页会把它显示成灰字「辅助模块」。）

## 口径（对应通达信公式）

    X_3 := 0.01 * EMA(AMOUNT, 13) / EMA(VOL, 13);

- `AMOUNT` = 成交额（元），`VOL` = 成交量（**手**）。
- **0.01 是手→股的换算**：元 ÷ 手 ÷ 100（1 手 = 100 股）= 元/股。
  通达信的 VOL 单位同样是手，故该系数在通达信里同样成立，公式可原样搬过来。
- **必须先各自 EMA 再相除**：先算「日均价 = 成交额 ÷ 成交量」再取 EMA，是**价的平均**；
  而 `EMA(成交额) ÷ EMA(成交量)` 是**量的加权**。两者数值不同、含义也不同，
  实现时不可互相替换（本项目的 `data.get_vwap()` 是前一种口径，不能拿来顶替）。
"""
from __future__ import annotations

import pandas as pd

# 成交额（元）÷ 成交量（手）→ 元/股：除以 100（1 手 = 100 股）
AMOUNT_TO_PRICE = 0.01

DEFAULT_PERIOD = 13


def ema_cost_line(df: pd.DataFrame, period: int = DEFAULT_PERIOD) -> pd.Series:
    """X_3 成本线（元/股），索引与 `df` 完全同序。

    `period` = EMA 周期（通达信原式为 13）。

    边界处理：
      * 数据源缺 `amount` 列 → 整条 NaN（不静默用 close 顶替：那就不是这个指标了）；
      * EMA(成交量) 为 0（长期无成交）→ 该点 NaN，不产生 inf。
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
