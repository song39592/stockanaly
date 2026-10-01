# -*- coding: utf-8 -*-
"""策略回测基础执行框架（与具体策略分离）。

本目录包含：数据层 data、契约层 base、统计层 stats、编排层 backtest、校验中心 registry。
具体策略文件（如 macd.py）放在上一级 strategies/ 目录，导入时 `from .core import ...`，
不应修改本目录下的框架文件。
"""
from .base import (
    run, signals_to_positions, StrategyContext, REGISTRY, strategy,
    ParamSpec, Signal, StrategyMeta, list_strategies,
)
from . import data, base, stats, backtest, registry  # noqa: F401
