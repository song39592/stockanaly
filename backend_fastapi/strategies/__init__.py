# -*- coding: utf-8 -*-
"""策略回测模块包入口。

目录约定（具体策略 vs 基础框架分离）：
  strategies/
    <id>.py          具体策略（可放任意多个，文件名即 id，留在这一层）
    core/            基础执行框架（策略不得修改）：
      data.py        数据层   —— 唯一取数入口（load_bars）+ 原始分类数据（名称/上市日/市值）
      universe.py    股票池层 —— 按规则生成回测代码（含进程内缓存），不依赖编排层
      base.py        契约层   —— @strategy / Signal / StrategyContext；信号→权重→流水
      stats.py       统计层   —— 目标权重 + 收盘价 → 净值与指标（与策略无关）
      backtest.py    编排层   —— 股票池 codes → 策略信号 → 统计，串起来并规范化返回
      registry.py    校验中心 —— 扫描策略文件、接口校验、登记 REGISTRY
      runcontrol.py  运行控制 —— 暂停/继续/停止（base 与 data 共用）
    _validated.json  校验缓存（自动生成）

对外暴露（统一从 .core 转发）：
  - list_strategies() / run / signals_to_positions / StrategyContext
  - REGISTRY / strategy / ParamSpec / Signal / StrategyMeta
  - data / universe / base / stats / backtest / registry

包加载即触发 core.registry.scan()：扫描本目录下的具体策略文件，做接口校验，
仅通过者登记进 REGISTRY 并可被选中回测（详见 ARCHITECTURE.md）。
"""
from .core.base import (
    run, signals_to_positions, StrategyContext, REGISTRY, strategy,
    ParamSpec, Signal, StrategyMeta, list_strategies,
)
from .core import data, universe, base, stats, backtest, registry  # noqa: F401

# 校验 / 注册：扫描本目录的具体策略，通过才登记、可选中。
registry.scan()

__all__ = [
    "run", "signals_to_positions", "StrategyContext", "list_strategies", "REGISTRY",
    "strategy", "ParamSpec", "Signal", "StrategyMeta",
    "data", "universe", "base", "stats", "backtest", "registry",
]
