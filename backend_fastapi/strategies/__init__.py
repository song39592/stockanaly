# -*- coding: utf-8 -*-
"""策略回测模块包入口。

对外暴露：
  - list_strategies()        返回全部已注册策略的元数据（供前端列清单 + 渲染参数表单）
  - run(strategy_id, params, df)  经策略函数算出「目标仓位序列」（0/1）
  - REGISTRY / strategy / ParamSpec  注册机制（与 indicators 包同款）
  - data                     本包唯一数据入口（见 data.py）
  - backtest.run_backtest    回测引擎（组合多标的、算净值与指标）

每个策略独立成文件、文件名即其 id（如 ma_cross.py），新增策略只需新建文件，
本文件会在包加载时自动扫描目录并 import，触发 @strategy 装饰器完成注册。
详见同目录 ARCHITECTURE.md（新增策略前必读）。
"""
from .base import (
    run,                     # run(strategy_id, params, df) -> 0/1 仓位序列
    REGISTRY,
    strategy,
    ParamSpec,
    StrategyMeta,
    list_strategies,
)
from . import data, base, registry, backtest  # noqa: F401

# 自动发现并注册所有策略模块：新增一个 <id>.py 策略文件即被自动枚举，
# 无需在本文件手动 import（每个策略文件用 @strategy 在导入时登记）。
import importlib
import pkgutil

_EXCLUDE = {"__init__", "base", "registry", "data", "backtest"}
for _finder, _name, _ispkg in pkgutil.iter_modules(__path__):
    if _ispkg or _name in _EXCLUDE:
        continue
    try:
        importlib.import_module(f"{__name__}.{_name}")
    except Exception as _exc:  # 单个策略文件异常不应拖垮整个包
        import sys
        print(f"[strategies] 跳过无法导入的策略模块 {_name}: {_exc}", file=sys.stderr)

__all__ = [
    "run", "list_strategies", "REGISTRY",
    "strategy", "ParamSpec", "StrategyMeta",
    "data", "base", "registry", "backtest",
]
