# -*- coding: utf-8 -*-
"""策略注册机制与计算入口（标准化接口）。

新增任何策略都通过 `@strategy(...)` 装饰器登记到 REGISTRY。策略函数约定：

    def my_strategy(df: pd.DataFrame, **params) -> pd.Series:
        # df 由 data.get_ohlcv 提供，索引为 date，列含 open/high/low/close/volume
        # 返回「目标仓位序列」：任意非零值视为 1（满仓持有），0 视为空仓。
        # 序列必须与 df 同索引（直接基于 df 计算即可，前段不足用 NaN）。
        return (df["close"].rolling(5).mean() > df["close"].rolling(20).mean()).astype(float)

run() 负责：
  1. 经 data.get_ohlcv 取数（策略实现不直接碰存储层）；
  2. 调用策略函数；
  3. 把返回值归一化为 0/1 仓位序列（与 df 对齐、NaN→0）。

回测引擎（backtest.py）消费该 0/1 序列，按「次日生效 + 换手成本」合成组合收益。
"""
from __future__ import annotations

import dataclasses as dc
from typing import Any, Callable

import pandas as pd

from . import data


REGISTRY: dict[str, "StrategyMeta"] = {}


@dc.dataclass
class ParamSpec:
    """策略参数规格，供前端动态渲染输入控件与后端校验。"""
    name: str
    type: str                 # "int" | "float" | "choice"
    default: Any
    min: float | None = None
    max: float | None = None
    choices: list | None = None
    label: str = ""


@dc.dataclass
class StrategyMeta:
    id: str
    name: str
    category: str
    description: str
    params: list[ParamSpec]
    func: Callable


def strategy(id: str, name: str, category: str, description: str = "",
             params: list[ParamSpec] | None = None):
    """装饰器：把策略函数登记进 REGISTRY。id 必须唯一，重复会抛 ValueError。"""
    if id in REGISTRY:
        raise ValueError(f"策略 id 重复: {id}")

    def deco(func: Callable) -> Callable:
        REGISTRY[id] = StrategyMeta(
            id=id, name=name, category=category,
            description=description, params=params or [], func=func,
        )
        return func
    return deco


def list_strategies() -> list[dict]:
    """返回全部已注册策略的元数据（不含计算函数）。"""
    out: list[dict] = []
    for meta in REGISTRY.values():
        out.append({
            "id": meta.id,
            "name": meta.name,
            "category": meta.category,
            "description": meta.description,
            "params": [dc.asdict(p) for p in meta.params],
        })
    out.sort(key=lambda x: (x["category"], x["id"]))
    return out


def run(strategy_id: str, params: dict | None, df: pd.DataFrame) -> pd.Series:
    """按策略算出「目标仓位序列」（0/1），索引与 df 对齐。

    返回序列：1 = 满仓持有，0 = 空仓；NaN 视作 0。回测引擎据此安排次日仓位。
    """
    meta = REGISTRY.get(strategy_id)
    if meta is None:
        raise KeyError(f"未知策略: {strategy_id}")
    if df is None or df.empty:
        raise RuntimeError("无行情数据，无法运行策略")

    raw = meta.func(df, **(params or {}))
    pos = pd.Series(raw, index=df.index).fillna(0.0)
    # 归一化为 0/1：任意非零仓位都当作满仓（简化模型，不做部分仓位）。
    pos = (pos != 0).astype(float)
    return pos
