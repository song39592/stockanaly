# -*- coding: utf-8 -*-
"""策略契约层（三层之一：base / strategy / stats）。

职责单一、互不越界：
    data.py   数据层   —— 策略/回测唯一取数入口（load_bars），策略主动来拉；
    base.py   契约层   —— @strategy 声明「需要的字段 + 额外参数」，运行策略，返回 list[Signal]；
    stats.py  统计层   —— 消费「目标仓位 + 收盘价」，算净值与指标，与策略无关。

【策略主动拉取】每个策略需要的数据列不同，所以由策略自己声明 inputs（如 ["close"] /
["close","volume"]），并在函数体内调用 data.load_bars(ctx.codes, fields=inputs) 主动拉取，
而非被动接收一个统一对象。base 只做「登记 + 调用 + 标准化输出」。

策略函数唯一约定：

    @strategy(id="macd", name="MACD", category="趋势", inputs=["close"],
              params=[ParamSpec("fast","int",12), ...])
    def macd(ctx: StrategyContext, fast: int = 12, slow: int = 26, ...) -> list[Signal]:
        bars = data.load_bars(ctx.codes, ctx.start, ctx.end,
                              fields=["close"], adjust=ctx.adjust)
        signals = []
        for code, df in bars.items():
            close = df["close"].astype(float)
            ...                                   # 计算
            signals.append(Signal(code=code, time=date,
                                  action="buy", weight=pos))
        return signals

Signal 字段：
    code    股票代码（必填，标识该信号属于哪只标的）
    time    事件日（YYYY-MM-DD，须落在行情日期内）
    action  "buy" 建仓 / "sell" 平仓
    weight  仓位系数 0..1（buy=目标仓位占比；sell 恒置 0）
    reason  可选说明
"""
from __future__ import annotations

import dataclasses as dc
from typing import Any, Callable

import pandas as pd


REGISTRY: dict[str, "StrategyMeta"] = {}


@dc.dataclass
class ParamSpec:
    """策略额外参数规格：供前端渲染控件 + 后端校验/填充默认值。"""
    name: str
    type: str                 # "int" | "float" | "choice"
    default: Any
    min: float | None = None
    max: float | None = None
    choices: list | None = None
    label: str = ""


@dc.dataclass
class StrategyContext:
    """回测范围上下文：策略据此主动拉取所需行情。"""
    codes: list[str]
    start: str | None = None
    end: str | None = None
    adjust: str = "hfq"       # 复权口径
    frequency: str = "day"


@dc.dataclass
class Signal:
    """一只标的上的一次买卖信号。"""
    code: str
    time: str
    action: str = "buy"       # "buy" | "sell"
    weight: float = 0.0       # 仓位系数 0..1
    reason: str = ""

    def to_dict(self) -> dict:
        return {"code": self.code, "time": str(self.time)[:10],
                "action": self.action, "weight": round(float(self.weight), 6),
                "reason": self.reason}


@dc.dataclass
class StrategyMeta:
    id: str
    name: str
    category: str
    description: str
    inputs: list[str]         # 策略自己会去拉的字段，如 ["close"] / ["close","volume"]
    outputs: str
    params: list[ParamSpec]
    func: Callable


def strategy(id: str, name: str, category: str, description: str = "",
             inputs: list[str] | None = None,
             outputs: str = "buy/sell 信号 + 仓位系数",
             params: list[ParamSpec] | None = None):
    """装饰器：把策略登记进 REGISTRY。id 必须唯一，重复会抛 ValueError。"""
    if id in REGISTRY:
        raise ValueError(f"策略 id 重复: {id}")

    def deco(func: Callable) -> Callable:
        REGISTRY[id] = StrategyMeta(
            id=id, name=name, category=category, description=description,
            inputs=list(inputs or ["close"]), outputs=outputs,
            params=params or [], func=func,
        )
        return func
    return deco


def list_strategies() -> list[dict]:
    """返回全部已注册策略的元数据（不含计算函数），供前端列清单 + 渲染参数表单。"""
    out: list[dict] = []
    for meta in REGISTRY.values():
        out.append({
            "id": meta.id,
            "name": meta.name,
            "category": meta.category,
            "description": meta.description,
            "inputs": meta.inputs,
            "outputs": meta.outputs,
            "params": [dc.asdict(p) for p in meta.params],
        })
    out.sort(key=lambda x: (x["category"], x["id"]))
    return out


def _as_signals(raw) -> list[Signal]:
    """把策略返回值标准化为 list[Signal]。

    契约（由 LOAD 时冒烟测试保证）：策略必须返回 list[Signal]，
    每个 Signal 须带 code、action ∈ {buy, sell}、weight ∈ [0,1]。

    此处为**严格校验**：遇到类型错误 / 缺 code / 非法 action / 越界 weight
    直接抛错，不再静默纠正或丢弃——以此暴露策略实现 bug。
    （正常路径上策略已通过 LOAD 校验，本函数实际不会触发；异常由路由层转成 400。）
    """
    if not isinstance(raw, list):
        raise TypeError(f"策略返回值必须是 list[Signal]，实际是 {type(raw).__name__}")

    out: list[Signal] = []
    for it in raw:
        if not isinstance(it, Signal):
            raise TypeError(f"信号元素必须是 Signal，实际是 {type(it).__name__}")
        if not it.code:
            raise ValueError(f"存在不带 code 的信号：{it!r}")
        act = (it.action or "").strip().lower()
        if act not in ("buy", "sell"):
            raise ValueError(f"非法 action：{it.action!r}（必须是 buy/sell）")
        w = float(it.weight or 0.0)
        if not (0.0 <= w <= 1.0):
            raise ValueError(f"weight 超出 [0,1]：{it.weight!r}")
        w = 0.0 if act == "sell" else w
        out.append(Signal(code=it.code, time=str(it.time)[:10],
                          action=act, weight=w, reason=it.reason))
    out.sort(key=lambda x: (x.code, x.time))
    return out


def _fill_params(meta: StrategyMeta, params: dict | None) -> dict:
    """用默认值填充未提供的参数，并只保留声明过的键（避免 stray key 触发 TypeError）。"""
    params = params or {}
    return {p.name: params.get(p.name, p.default) for p in meta.params}


def run(strategy_id: str, ctx: "StrategyContext", params: dict | None) -> list[Signal]:
    """调用策略（策略内部自行经 data.load_bars 拉数据），返回标准化信号。"""
    meta = REGISTRY.get(strategy_id)
    if meta is None:
        raise KeyError(f"未知策略: {strategy_id}")
    if not ctx or not ctx.codes:
        raise ValueError("回测范围为空，无法运行策略")
    kwargs = _fill_params(meta, params)
    return _as_signals(meta.func(ctx, **kwargs))


def signals_to_positions(signals: list[Signal],
                         close_by_code: dict[str, pd.Series]) -> dict[str, pd.Series]:
    """把扁平信号（含 code）按标的展开成「目标仓位序列」{code: Series}。

    每个 code 的仓位序列对齐到该 code 自身的行情日期（来自 close_by_code），
    事件日设置仓位，其余前向填充（首个事件之前为 0/空仓）。
    """
    by_code: dict[str, list[Signal]] = {}
    for s in signals or []:
        by_code.setdefault(s.code, []).append(s)

    out: dict[str, pd.Series] = {}
    for code, close in close_by_code.items():
        idx = [str(d)[:10] for d in close.index]
        w = pd.Series(0.0, index=idx)
        for s in by_code.get(code, []):
            d = str(s.time)[:10]
            if d in w.index:
                w.loc[d] = max(0.0, min(1.0, float(s.weight)))
        out[code] = w.ffill().fillna(0.0)
    return out
