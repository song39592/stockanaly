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
from . import data, base, registry, scan

# 自动发现并注册所有指标模块：新增一个 <id>.py 指标文件即被自动枚举，
# 无需在本文件手动 import（每个指标文件用 @indicator 在导入时登记）。
#
# ⚠️ `_EXCLUDE` 里除框架模块外还有**非指标模块**：`golden` 是数值基准自检，
#    不是指标（没有 @indicator 装饰器）—— 但本目录下的 .py 都会被自动 import，
#    不排除它就会在包加载时顺手执行一遍（纯浪费，且 `python -m indicators.golden`
#    会报 "found in sys.modules" 警告）。
import importlib

from core.registry_scan import iter_module_names

_EXCLUDE = {"__init__", "base", "registry", "data", "scan", "golden"}
for _name in iter_module_names(__path__[0], _EXCLUDE):
    try:
        importlib.import_module(f"{__name__}.{_name}")
    except Exception as _exc:  # 单个指标文件异常不应拖垮整个包
        import sys
        print(f"[indicators] 跳过无法导入的指标模块 {_name}: {_exc}", file=sys.stderr)

# LOAD 校验（元数据 + 函数签名；冒烟需指标显式声明 smoke=True）：
# 不过关的指标会被移出 REGISTRY —— 既不进 /api/indicators 的清单，也无法被 compute。
# 与筹码公式层（features/chip/formulas/core/registry.py 的 scan）同一套思路，
# 详见 scan.py 顶部的「为什么形态不同 / 为什么冒烟默认关闭」。
SCAN_REPORT = scan.scan()

__all__ = [
    "compute", "list_indicators", "REGISTRY",
    "indicator", "ParamSpec", "IndicatorMeta",
    "data", "base", "registry", "scan",
]
