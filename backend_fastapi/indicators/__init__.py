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

# 自动发现并注册所有指标模块：新增一个 <id>.py 指标文件即被自动枚举，
# 无需在本文件手动 import（每个指标文件用 @indicator 在导入时登记）。
import importlib
import pkgutil

_EXCLUDE = {"__init__", "base", "registry", "data"}
for _finder, _name, _ispkg in pkgutil.iter_modules(__path__):
    if _ispkg or _name in _EXCLUDE:
        continue
    try:
        importlib.import_module(f"{__name__}.{_name}")
    except Exception as _exc:  # 单个指标文件异常不应拖垮整个包
        import sys
        print(f"[indicators] 跳过无法导入的指标模块 {_name}: {_exc}", file=sys.stderr)

__all__ = [
    "compute", "list_indicators", "REGISTRY",
    "indicator", "ParamSpec", "IndicatorMeta",
    "data", "base", "registry",
]
