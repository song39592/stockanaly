# -*- coding: utf-8 -*-
"""指标注册机制与计算入口（标准化输出）。

新增任何指标都通过 `@indicator(...)` 装饰器登记到 REGISTRY，compute() 负责：
  1. 经 data.get_ohlcv 取数（指标实现不直接碰存储层）；
  2. 调用指标函数；
  3. 把结果序列化为**标准化输出**（见 ARCHITECTURE.md §6）：
       {id, name, panel, dates, series:[{name, kind, data}]}

面板位置（panel）：每个指标声明自己画在哪里，前端据此分配渲染层：
  - "main"   主图叠加（共用主图价格坐标轴），如 MA / EMA / BOLL
  - "lower"  主图下方副图（共享时间轴、独立 y 轴，可多个堆叠），如 MACD / RSI / KDJ
  - "right"  主图右侧副图（与主图等高并列、独立 y 轴），如需要独立纵轴的指标
  - "none"   不显示：仅注册、可计算（用于导出 / 内部辅助 / 暂不开放），前端不把它放入任何渲染面板

指标函数约定：
  def my_indicator(df: pd.DataFrame, **params) -> dict:
      # df 由 data.get_ohlcv 提供，索引为 date，列含 close/open/high/low/volume
      return {"series": [series_line("MA5", df["close"].rolling(5).mean()),
                         series_bar("VOL", df["volume"])]}
  * 每个序列用 series_line / series_bar 构造；kind 缺省 "line"。
  * 每个 Series 必须以 df 的 date 索引对齐（直接基于 df 计算即可）；
  * 前段不足周期的位点用 NaN 表示（pandas 滚动/移位自然产生），不要裁掉索引；
  * 数值为 float，NaN 会被序列化为 null，前端按 date 对齐叠加。
"""
from __future__ import annotations

import dataclasses as dc
from typing import Any, Callable

import pandas as pd

from . import data

REGISTRY: dict[str, "IndicatorMeta"] = {}

VALID_PANELS = ("main", "lower", "right", "none")
VALID_KINDS = ("line", "bar")


@dc.dataclass
class ParamSpec:
    """指标参数规格，供前端动态渲染输入控件与后端校验。"""
    name: str
    type: str                 # "int" | "float" | "choice"
    default: Any
    min: float | None = None
    max: float | None = None
    choices: list | None = None
    label: str = ""


@dc.dataclass
class IndicatorMeta:
    id: str
    name: str
    category: str             # "trend" | "oscillator" | "volatility" ...
    panel: str                # "main" | "lower" | "right" 放置面板
    params: list[ParamSpec]
    func: Callable


def series_line(name: str, data) -> dict:
    """构造一条「线」型序列（标准化输出单元）。"""
    return {"name": name, "kind": "line", "data": data}


def series_bar(name: str, data) -> dict:
    """构造一条「柱」型序列（标准化输出单元），如 MACD 柱、成交量。"""
    return {"name": name, "kind": "bar", "data": data}


def indicator(id: str, name: str, category: str, panel: str, params: list[ParamSpec]):
    """装饰器：把指标函数登记进 REGISTRY。

    panel 必须是 "main" / "lower" / "right" / "none" 之一，否则在模块导入时即报错，
    便于尽早发现错误的面板声明。
    """
    if panel not in VALID_PANELS:
        raise ValueError(f"panel 必须是 {VALID_PANELS}，收到: {panel}")
    def deco(func: Callable) -> Callable:
        if id in REGISTRY:
            raise ValueError(f"指标 id 重复: {id}")
        REGISTRY[id] = IndicatorMeta(
            id=id, name=name, category=category,
            panel=panel, params=params, func=func,
        )
        return func
    return deco


def _normalize_series(result: dict, df: pd.DataFrame) -> list[dict]:
    """把指标函数返回值归一化为标准化 series 列表，并序列化 data。"""
    raw = result.get("series")
    if raw is None:                                   # 兼容旧 {"lines": {name: Series}}
        raw = [{"name": n, "kind": "line", "data": s}
               for n, s in result.get("lines", {}).items()]
    out: list[dict] = []
    for item in raw:
        kind = item.get("kind", "line")
        if kind not in VALID_KINDS:
            kind = "line"
        data = item.get("data")
        if not isinstance(data, pd.Series):
            data = pd.Series(data, index=df.index)
        out.append({
            "name": item["name"],
            "kind": kind,
            "data": [None if pd.isna(v) else float(v) for v in data.values],
        })
    return out


def compute(code: str, indicator_id: str, params: dict | None = None) -> dict:
    """计算单个指标，返回**标准化输出**。

    返回结构（详见 ARCHITECTURE.md §6）：
      {
        "id": <指标id>,
        "name": <名称>,
        "panel": "main"|"lower"|"right",
        "dates": ["2024-01-02", ...],                 # 与 K 线完全同序
        "series": [                                  # 该指标的一条/多条序列
          {"name": "MA5", "kind": "line", "data": [..]},
          {"name": "MACD", "kind": "bar",  "data": [..]}
        ]                                            # data 中 NaN -> null
      }
    """
    meta = REGISTRY.get(indicator_id)
    if meta is None:
        raise KeyError(f"未知指标: {indicator_id}")

    df = data.get_ohlcv(code)                 # 唯一取数点
    if df is None or df.empty:
        raise RuntimeError(f"无行情数据: {code}")

    result = meta.func(df, **(params or {}))
    dates = [str(d)[:10] for d in df.index]
    series_out = _normalize_series(result, df)

    return {
        "id": indicator_id,
        "name": meta.name,
        "panel": meta.panel,
        "dates": dates,
        "series": series_out,
    }
