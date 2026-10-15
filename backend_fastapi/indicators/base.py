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
  - "none"   不显示：仅注册、可计算（用于导出 / 内部辅助 / 暂不开放），前端不把它放入任何渲染面板

**为什么没有 "right"（右侧面板）—— 这是刻意收回，不是遗漏。**
它曾作为枚举值预留，但承诺了一个本层给不出的东西。右侧窗口（筹码峰）是
「按价位分布的直方图」：x 轴是价格分箱、y 轴是占比；而指标体系产出的是
「按日期对齐的序列」：x 轴是交易日、y 轴是数值。两者连**维度都不同**，塞进同一套
panel 只会为了统一而扭曲其中一个（前端要为 right 单独写一套与 K 线不对齐的缩放）。
筹码峰因此走自己的渲染单元（`launcher/StockPage.cs` 的 `ChipPanel`）与自己的接口
`/api/chip/dist`，但**算法与筹码类指标共用同一份筹码矩阵**
（`chip_formulas.compute_matrix` 缓存，两条路径共享）。

把 "right" 从 `VALID_PANELS` 删掉，是让「本层没有右侧面板」成为**被 LOAD 校验的事实**，
而不是一个没人用的占位值 —— 否则每个加指标的人都会先问一遍「能不能放 right」。
若将来真有需要独立纵轴的**序列**指标，请复用 "lower"（它本就支持多序列堆叠）。

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
import inspect
from typing import Callable

import pandas as pd

from core.jsonutil import json_safe          # 第 09 项：出口清洗
from core.paramspec import ParamSpec         # 第 13 项：定义收敛到 core/paramspec.py

from . import data

REGISTRY: dict[str, "IndicatorMeta"] = {}

VALID_PANELS = ("main", "lower", "none")      # 无 "right"，理由见模块 docstring
VALID_KINDS = ("line", "bar")


@dc.dataclass
class IndicatorMeta:
    id: str
    name: str
    category: str             # "trend" | "oscillator" | "volatility" ...
    panel: str                # "main" | "lower" | "none"（none = 仅计算不渲染）；无 "right"
    params: list[ParamSpec]
    func: Callable
    file: str = ""            # 定义该指标的源文件绝对路径（由装饰器经 inspect 取得）


def series_line(name: str, data) -> dict:
    """构造一条「线」型序列（标准化输出单元）。"""
    return {"name": name, "kind": "line", "data": data}


def series_bar(name: str, data) -> dict:
    """构造一条「柱」型序列（标准化输出单元），如 MACD 柱、成交量。"""
    return {"name": name, "kind": "bar", "data": data}


def indicator(id: str, name: str, category: str, panel: str,
              params: list[ParamSpec]):
    """装饰器：把指标函数登记进 REGISTRY。

    panel 必须是 "main" / "lower" / "none" 之一，否则在模块导入时即报错，
    便于尽早发现错误的面板声明（"right" 已刻意收回，理由见模块 docstring）。

    启动时还会经 `scan.scan()` 做元数据 + 签名 + 合成数据自检，不过关的会被移出
    `REGISTRY`（详见 scan.py）。**不需要**在这里声明"要不要参与自检"—— 依赖
    行情/全市场数据的指标会自动判为跳过。
    """
    if panel not in VALID_PANELS:
        raise ValueError(f"panel 必须是 {VALID_PANELS}，收到: {panel}")
    def deco(func: Callable) -> Callable:
        if id in REGISTRY:
            raise ValueError(f"指标 id 重复: {id}")
        try:
            src = inspect.getsourcefile(func) or ""
        except Exception:                       # 极端情况下取不到文件也不影响注册
            src = ""
        REGISTRY[id] = IndicatorMeta(
            id=id, name=name, category=category,
            panel=panel, params=params, func=func, file=src,
        )
        return func
    return deco


def signature_problems(meta: "IndicatorMeta") -> list[str]:
    """签名检查（LOAD 时）：首参必须是 df，其余参数都要有默认值且与 ParamSpec 对齐。

    与筹码公式的同名函数同义（都是「声明与实现对不上就拦下」），只是首参名从
    `ctx` 换成 `df`。
    """
    problems: list[str] = []
    try:
        sig = inspect.signature(meta.func)
    except (TypeError, ValueError) as exc:
        return [f"无法解析签名：{exc}"]
    names = [p.name for p in sig.parameters.values()
             if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)]
    if not names or names[0] != "df":
        problems.append(
            f"首参必须是 df（pandas.DataFrame），实际是 {names[0] if names else '无参数'}")
    declared = {p.name for p in meta.params}
    for name in names[1:]:
        param = sig.parameters[name]
        if param.default is inspect.Parameter.empty:
            problems.append(f"参数 {name} 缺少默认值")
        if name not in declared:
            problems.append(f"参数 {name} 未在 ParamSpec 中声明")
    for spec in meta.params:
        if spec.name not in names:
            problems.append(f"ParamSpec 声明的 {spec.name} 不是函数参数")
    return problems


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
            # 走公共清洗（第 09 项）：指标值本就是浮点，语义与原先
            # 「NaN→None，否则 float()」一致，另多挡住 ±Inf。
            "data": [json_safe(v) for v in data.values],
        })
    return out


def compute(code: str, indicator_id: str, params: dict | None = None,
            period: str = "day") -> dict:
    """计算单个指标，返回**标准化输出**。

    `period` 是 K 线周期（day / week / month）：**必须与 K 线展示的周期一致**，
    否则周线蜡烛会配上日线指标（MA5 变成 5 日而非 5 周），口径是错的。

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

    df = data.get_ohlcv(code, period=period)   # 唯一取数点
    if df is None or df.empty:
        raise RuntimeError(f"无行情数据: {code}")
    # 指标函数只收 df，拿不到代码；这里挂在 df.attrs 上供需要按代码再取数的
    # 指标使用（如筹码类指标要经 data.get_chip_frames(code) 拿分布矩阵）。
    df.attrs["code"] = code
    # 周期也挂上：横截面类指标（如 RPS）要按同一周期取「全市场」数据，
    # 否则周线蜡烛会配上日线的 RPS（详见 data.period_of）。
    df.attrs["period"] = period

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
