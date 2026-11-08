# -*- coding: utf-8 -*-
"""日线标准输入列与复权口径 —— 全项目**单一来源**。

## 为什么有这一个文件

指标包（`indicators/data.py`）与策略包（`strategies/core/data.py`）各自取日线，
历史上**列集合与复权口径是各写一份**的 → 加一列要改两处，漏一处就静默失真：
策略与指标会算出两个不同的数，而两边都不报错（最难查的一类 bug）。

本模块把两件事收敛成一份：

  1. `STANDARD_BAR_COLUMNS` —— 日线标准输入列（指标与策略共用同一份定义）；
  2. `adjust_price_scaled_columns()` —— 把「量纲 = 价」的列按复权因子缩放，
     使其与 close 严格同口径。

## 为什么要按因子缩放成交额

复权（qfq / hfq）只缩放 open/high/low/close，`volume` / `amount` **不动**
（见 `price_store.load_bars`）。而「成交额 ÷ 成交量」是**价格量纲**：
不缩放的话，得到的均价是未复权价，而 close 是复权价 —— 除权除息日两者错位，
`(close - cost) / cost` 这类偏离度会凭空跳出**假信号**（策略据此买卖 = 真亏）。

    均价_adj = 均价_raw × factor = (amount × factor) / volume

volume 不变（真实成交量），故均价与 close 严格同口径。
raw 口径没有 factor 列（`price_store` 直接返回原始 bar）→ 原样不动，本来就自洽。
"""
from __future__ import annotations

import pandas as pd

# 日线标准输入列：指标与策略的数据层都从这里取，避免两份定义漂移。
#   open/high/low/close  价格（复权后）
#   volume               成交量（手，不受复权影响）
#   amount               成交额（元）—— 量纲为价，需按因子缩放，见下方函数
STANDARD_BAR_COLUMNS = ("open", "high", "low", "close", "volume", "amount")

# 量纲 = 价、需要随复权因子一起缩放的列。
# ⚠️ 加新列时先问一句：它是「比值量纲」（涨跌幅 / 振幅 → 复权不影响，无需缩放）
#    还是「价差 / 价量纲」（→ 必须缩放）？只有后者才放进这个元组。
PRICE_SCALED_COLUMNS = ("amount",)


def adjust_price_scaled_columns(df: pd.DataFrame) -> pd.DataFrame:
    """把「量纲 = 价」的列按复权因子缩放，使其与 close 同口径。

    - 无 `factor` 列（raw 口径 / 数据源没给）→ 原样返回；
    - 无需缩放的列（如 volume）→ 不动；
    - 返回**新**对象（不原地改），调用方拿到的仍是只读缓存对象。
    """
    if df is None or getattr(df, "empty", True):
        return df
    if "factor" not in df.columns:
        return df
    targets = [c for c in PRICE_SCALED_COLUMNS if c in df.columns]
    if not targets:
        return df
    out = df.copy()
    factor = pd.to_numeric(out["factor"], errors="coerce")
    for col in targets:
        out[col] = pd.to_numeric(out[col], errors="coerce") * factor
    return out