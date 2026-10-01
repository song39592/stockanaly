# -*- coding: utf-8 -*-
"""策略回测模块包入口。

目录约定（具体策略 vs 基础框架分离）：
  strategies/
    macd.py          具体策略（可放任意多个 <id>.py，留在这一层）
    core/            基础执行框架（base/data/stats/backtest/registry），策略不得修改
    _validated.json  校验缓存（自动生成）

对外暴露（统一从 .core 转发）：
  - list_strategies() / run / signals_to_positions / StrategyContext
  - REGISTRY / strategy / ParamSpec / Signal / StrategyMeta
  - data / base / stats / backtest / registry

包加载即触发 core.registry.scan()：扫描本目录下的具体策略文件，做接口校验，
仅通过者登记进 REGISTRY 并可被选中回测（详见 core/ARCHITECTURE.md）。
"""
from .core.base import (
    run, signals_to_positions, StrategyContext, REGISTRY, strategy,
    ParamSpec, Signal, StrategyMeta, list_strategies,
)
from .core import data, base, stats, backtest, registry  # noqa: F401

# 校验 / 注册：扫描本目录的具体策略，通过才登记、可选中。
registry.scan()

__all__ = [
    "run", "signals_to_positions", "StrategyContext", "list_strategies", "REGISTRY",
    "strategy", "ParamSpec", "Signal", "StrategyMeta",
    "data", "base", "stats", "backtest", "registry",
]
