# -*- coding: utf-8 -*-
"""K 线序列服务：**行情消费方唯一的「取数 + 周期合样」出口**（第 17 项修正口径）。

> ⚠️ 原文写的是「全项目唯一」，**不准确**。准确说法：数据源层与运维脚本
> 不走这一层（见下方「已知例外」），但**所有行情消费方都走**。

为什么要有这一层：
  以前 `periods.resample_bars` 在三处各自调用（个股历史 / 指标 / 筹码），
  等于「同一根 K 线被定义了三遍」。三条路径今天碰巧规则一致，但只要有一条改了
  （比如周的边界规则），就会出现**指标看起来正常、实际与蜡烛错位**——
  这类 bug 最难查。合并到这里之后：
    * 用户看到的 K 线 == 指标计算的输入，对齐是**结构保证**，不靠自觉；
    * 合样只算一次并缓存，三个消费方共用。

## 已知例外（**刻意不走本层**，第 17 项登记）

1. `features/history/service.py` 的 `load_trusted_bars` 直接
   `price_store.load_bars(...)`：它需要捕获 `price_store.UntrustedDataError`
   做全量重抓，而 `get_bars` 不抛这个异常、也不暴露指纹校验。
   它只是**读单只票的日线**，周 / 月合样那一步仍走本层的 `resample`。
2. `features/stock/profile.py` 的涨停统计用
   `price_store.load_bars(code, adjust="raw")`：日线专用、不需要合样。

## 第 17 项修掉的两处「绕过」（原先是各自复刻）

3. 后复权算法：`indicators/data.py::_apply_hfq` 曾**自己实现了一遍**
   「哪天起该乘哪个因子」。现已收回 `price_store.apply_hfq_factors`，
   生效区间的判定全项目只有 `price_store.hfq_steps` 一份
   （`load_bars` 与面板共用），对拍 2500 个值零差异。
4. 周 / 月分桶：`indicators/data.py::_resample_panel` 曾自己用 `bucket_key`
   手写一遍「取桶内最后一根」。现已复用 `periods.bucket_last_indices`。

不落盘的原因：周 / 月 K 是**派生口径**（日线才是唯一落库数据），与「不存
qfq_factor」「指标不落库」同理——只存不可变数据，派生的一律现算，
免得引入失效 / 陈旧这类新的不可信来源。这里做的是内存缓存，不是文件。

分层（单向，无环）：
    history_routes ─┐
    indicators/data ─┼─→ kline_service ─→ price_store / periods
    chip_formulas   ─┘（经 indicators.data）
"""
from __future__ import annotations

import datetime as dt
from functools import lru_cache
from typing import Any

import pandas as pd
from . import price_store
from .jsonutil import json_safe
from .periods import normalize, resample_bars

# 参与输出的列（其余如 fetched_at / source 由调用方按需保留）
_BAR_COLUMNS = ("trade_date", "open", "high", "low", "close", "volume",
                "amount", "amplitude", "change_pct", "change_amount",
                "turnover", "merged_days", "period")


def resample(bars: list[dict[str, Any]], period: str) -> list[dict[str, Any]]:
    """周期合样的**唯一实现**（薄封装 periods.resample_bars，方便调用方只认本模块）。

    已做过指纹校验 / 已取到手上的日线，直接从这里合样，不必再走一遍取数。
    """
    return resample_bars(bars, normalize(period))


@lru_cache(maxsize=128)
def _frame(code: str, start: str | None, end: str | None, adjust: str,
           period: str) -> pd.DataFrame | None:
    rows = price_store.load_bars(code, adjust=adjust, start=start, end=end)
    if not rows:
        return None
    if period != "day":
        rows = resample_bars(rows, period)
    df = pd.DataFrame(rows)
    if "trade_date" in df.columns:
        df = df.rename(columns={"trade_date": "date"}).set_index("date").sort_index()
    return df[[c for c in df.columns]]


def get_bars(code: str, start: str | dt.date | None = None,
             end: str | dt.date | None = None, adjust: str = "qfq",
             period: str = "day") -> pd.DataFrame:
    """取某只股票的 **K 线序列**（已按周期合样、已复权），索引为日期字符串。

    这是「指标 / 筹码」的输入基准——它们不再自己取数、自己合样。

    参数：
      adjust  qfq（默认，展示）/ hfq（回测）/ raw
      period  day / week / month（周线 / 月线读取时现算）

    返回 **只读** DataFrame（缓存共享，调用方如需改动请先 copy）。
    无数据时返回空 DataFrame（由调用方决定是报错还是降级）。
    """
    period = normalize(period)
    df = _frame(code, _norm(start), _norm(end), price_store._store_adjust(adjust), period)
    if df is None:
        return pd.DataFrame(columns=[c for c in _BAR_COLUMNS if c != "trade_date"])
    return df


def to_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    """K 线序列 → 接口的 bars 结构（日期回到 `trade_date` 字段）。

    逐字段走 `core.jsonutil.json_safe`（第 09 项）替代原先的内联 `pd.isna` 判断：
    除了 NaN→None，还顺带处理 ±Inf 与 numpy 标量（int64/float64 → 原生类型），
    出口不会再因一个非法浮点值让整个接口 500。
    """
    if df is None or df.empty:
        return []
    out = df.copy()
    out.index = [str(d)[:10] for d in out.index]
    out = out.reset_index().rename(columns={"index": "trade_date", "date": "trade_date"})
    return [{k: json_safe(v) for k, v in row.items()}
            for row in out.to_dict("records")]


def _norm(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, dt.date):
        return value.strftime("%Y-%m-%d")
    return str(value)[:10]


def clear_cache() -> None:
    """测试或切换数据源后清空 K 线缓存。"""
    _frame.cache_clear()
