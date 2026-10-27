# -*- coding: utf-8 -*-
"""策略回测基础执行框架（与具体策略分离）。

本目录按「取数 → 组池 → 契约 → 统计 → 编排 → 校验」分层，各文件职责单一：

    data.py        数据层   —— 唯一取数入口（load_bars）+ 原始分类数据（名称/上市日/市值）
    universe.py    股票池层 —— 按规则生成回测代码（含进程内缓存），不依赖编排层
    base.py        契约层   —— @strategy / Signal / StrategyContext；信号→权重→流水
    stats.py       统计层   —— 目标权重 + 收盘价 → 净值与指标（与策略无关）
    backtest.py    编排层   —— 股票池 codes → 策略信号 → 统计，串起来并规范化返回
    registry.py    校验中心 —— 扫描策略文件、接口校验、登记 REGISTRY
    runcontrol.py  运行控制 —— 暂停/继续/停止（base 与 data 共用）

具体策略文件（如 macd.py）放在上一级 strategies/ 目录，导入时 `from .core import ...`，
不应修改本目录下的框架文件。
"""
from .base import (
    run, signals_to_positions, StrategyContext, REGISTRY, strategy,
    ParamSpec, Signal, StrategyMeta, list_strategies,
)
from . import data, universe, base, stats, backtest, registry  # noqa: F401
