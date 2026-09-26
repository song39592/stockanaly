# -*- coding: utf-8 -*-
"""指标模块包入口。

对外只暴露两个公共函数：
  - compute(code, indicator_id, params=None)  计算单个指标，返回与 K 线对齐的序列
  - list_indicators()                         返回全部已注册指标的元数据（供前端列清单）

所有指标的数据都来自本包内的 `data` 模块；**任何指标实现文件都不得直接
import `price_store` / `akshare` / 任何外部源**，只能 `from .data import get_ohlcv`。
详见同目录 `ARCHITECTURE.md`（新增指标前必读）。
"""
from .base import compute, REGISTRY, indicator, ParamSpec, IndicatorMeta
from .registry import list_indicators
from . import data, base, registry
from . import ma, macd, rsi  # 触发指标注册（每个指标一个文件，新增文件在此 import）

__all__ = [
    "compute", "list_indicators", "REGISTRY",
    "indicator", "ParamSpec", "IndicatorMeta",
    "data", "base", "registry",
]
