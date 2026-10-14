# -*- coding: utf-8 -*-
"""策略契约层（三层之一：data / base / stats）。

职责单一、互不越界：
    data.py   数据层   —— 策略/回测唯一取数入口（load_bars），策略主动来拉；
    base.py   契约层   —— @strategy 声明「需要的字段 + 额外参数」，运行策略，
                        把信号标准化为 list[Signal]，并做「信号→权重」「权重→流水」；
    stats.py  统计层   —— 消费「目标权重 + 收盘价」，算净值与指标，与策略无关。

【策略主动拉取】每个策略需要的数据列不同，所以由策略自己声明 inputs（如 ["close"] /
["close","volume"]），并在函数体内调用 data.load_bars(ctx.codes, fields=inputs) 主动拉取，
而非被动接收一个统一对象。base 只做「登记 + 调用 + 标准化输出」。

【组合权重模型】信号只表达「我想要什么样的持仓」：

    策略层  weight = 该标的**占组合净值**的比例，恒 ≥ 0，事件日 Σweight ≤ 1
    流水层  delta  = 权重变化，正=买/加仓、负=卖/减仓，**由引擎 diff 派生**

    策略**不写买卖方向**。买卖是「变化」而非「状态」：让策略手写 sell，
    漏发一条就永久错仓。新契约下引擎对相邻调仓日的权重向量做差分，
    自动派生 buy / sell / add / trim，漏发不可能、也不需要补。

策略函数唯一约定：

    @strategy(id="macd", name="MACD", category="趋势", inputs=["close"],
              params=[ParamSpec("fast","int",12), ...])
    def macd(ctx: StrategyContext, fast: int = 12, ...) -> list[Signal]:
        bars = data.load_bars(ctx.codes, ctx.start, ctx.end,
                              fields=["close"], adjust=ctx.adjust)
        signals, held = [], set()                  # held 必须自己维护
        for day in rebalance_days:
            held = rank_and_pick(day, top_n)        # 本期完整目标组合
            w = position / len(held) if held else 0.0
            for code in sorted(held):               # 整组重发，不只发变化的
                signals.append(Signal(code=code, time=day, weight=w,
                                      reason="DIF 上穿 DEA",
                                      meta={"dif": 0.0312, "rank": 3}))
        return signals

⚠️ **必须自己维护 held 并整组重发**：引擎把「调仓日未出现的 code」记为 0
（见 signals_to_positions 的 rebalance_dates 语义），少发一条 = 静默清仓。

Signal 字段：
    code    股票代码（必填，标识该信号属于哪只标的）
    time    事件日（YYYY-MM-DD，须落在行情日期内）
    weight  目标权重 = 占组合净值比例，≥ 0，同一事件日 Σweight ≤ 1
    reason  人读说明（落进交易明细，用于归因）
    meta    机器读因子值（落进交易明细，用于 IC / 分层）
"""
from __future__ import annotations

import dataclasses as dc
import math
import threading
import time
import warnings
from typing import Callable

import numpy as np
import pandas as pd

from core.paramspec import ParamSpec         # 定义收敛到 core/paramspec.py

# 运行期控制（暂停/继续/停止）住在独立模块里，因为 `data`（数据层）也要用：
# 它不能反向 import 契约层 base，而 base 也不该为异常类型去依赖 data。
# 这里转出一下，策略统一 `from .core.base import checkpoint` 即可。
from .runcontrol import (
    Cancelled, RunControl, RUN_CONTROL,
    install_control, checkpoint, is_paused, is_cancelled,
)


REGISTRY: dict[str, "StrategyMeta"] = {}

_SUM_EPS = 1e-6            # Σweight 的容差（浮点累加误差）
_DELTA_EPS = 1e-12         # 权重「真的变了」的判定阈值
_DROP_WARN = 0.5           # 持仓数骤降超过此比例 → WARNING（漏发信号的守卫）


@dc.dataclass
class StrategyContext:
    """回测范围上下文：策略据此主动拉取所需行情。"""
    codes: list[str]
    start: str | None = None
    end: str | None = None
    adjust: str = "hfq"       # 复权口径
    frequency: str = "day"


# ⚠️ 暂停/继续/停止的实现（RunControl / Cancelled / checkpoint / install_control …）
# 见 core/runcontrol.py —— 从这里转出给策略用，策略统一写
# `from .core.base import checkpoint`。


@dc.dataclass
class Signal:
    """一只标的一次「目标权重」的设定（不含买卖方向——方向由引擎 diff 派生）。"""
    code: str
    time: str
    weight: float = 0.0       # 目标权重 = 占组合净值比例，≥ 0
    reason: str = ""          # 人读说明
    meta: dict = dc.field(default_factory=dict)   # 机器读因子值

    def to_dict(self) -> dict:
        return {"code": self.code, "time": str(self.time)[:10],
                "weight": round(float(self.weight), 6),
                "reason": self.reason, "meta": dict(self.meta or {})}


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
    requires_pool: bool = False   # 是否依赖股票池历史（pool_members）；编排层据此做覆盖预检
    pool_warmup: int = 0          # 回测起点前需有多少天历史养状态（streak 连续性）


def strategy(id: str, name: str, category: str, description: str = "",
             inputs: list[str] | None = None,
             outputs: str = "目标权重向量 + 因子值(meta)",
             params: list[ParamSpec] | None = None,
             requires_pool: bool = False, pool_warmup: int = 0):
    """装饰器：把策略登记进 REGISTRY。id 必须唯一，重复会抛 ValueError。"""
    if id in REGISTRY:
        raise ValueError(f"策略 id 重复: {id}")

    def deco(func: Callable) -> Callable:
        REGISTRY[id] = StrategyMeta(
            id=id, name=name, category=category, description=description,
            inputs=list(inputs or ["close"]), outputs=outputs,
            params=params or [], func=func,
            requires_pool=requires_pool, pool_warmup=pool_warmup,
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
            "requires_pool": meta.requires_pool,
            "pool_warmup": meta.pool_warmup,
        })
    out.sort(key=lambda x: (x["category"], x["id"]))
    return out


# ---- 信号标准化 ----------------------------------------------------------

def _native_scalar(v):
    """np.float64 / np.int64 → Python 原生标量；无法转换返回 None（调用方丢弃该字段）。"""
    if v is None or isinstance(v, (str, bool, int, float)):
        return v
    item = getattr(v, "item", None)          # numpy 标量
    if callable(item):
        try:
            return item()
        except Exception:                    # noqa: BLE001
            return None
    return v if isinstance(v, (int, float)) else None


def _clean_meta(raw, warn: list) -> dict:
    """meta 清洗：nan/inf 丢弃、numpy 标量转原生、非标量 JSON 化。

    结构上「扁平优先、最多一层嵌套」：`{"dif": 0.03}` 可以，
    `{"ind": {"a": 1}}` 会被 JSON 化成字符串（仍可序列化，但下游聚合要二次解析）——
    故非标量一律告警，提示策略作者把因子拍平。
    """
    out: dict = {}
    if not raw:
        return out
    if not isinstance(raw, dict):
        warn.append("meta 不是 dict，已丢弃")
        return out
    for k, v in raw.items():
        v = _native_scalar(v)
        if v is None:
            continue
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            continue                          # nan/inf 静默丢弃，不拖垮整批
        if isinstance(v, (str, bool, int)):
            out[str(k)] = v
        elif isinstance(v, float):
            out[str(k)] = v
        else:                                # dict / list → JSON 字符串（保结构 + 可序列化）
            try:
                import json as _json
                out[str(k)] = _json.dumps(v, ensure_ascii=False, default=str)
                warn.append(f"meta['{k}'] 是嵌套结构，已 JSON 化（建议拍平）")
            except Exception:                # noqa: BLE001
                warn.append(f"meta['{k}'] 不可序列化，已丢弃")
    return out


def _coerce(it):
    """把策略返回的元素统一成 Signal（容忍 dict / tuple / pandas Series）。"""
    if isinstance(it, Signal):
        return it
    if isinstance(it, dict):
        return Signal(code=str(it.get("code") or ""),
                      time=str(it.get("time") or "")[:10],
                      weight=it.get("weight", 0.0) or 0.0,
                      reason=str(it.get("reason") or ""),
                      meta=it.get("meta") or {})
    if isinstance(it, (tuple, list)):         # (code, time, weight[, reason[, meta]])
        if len(it) < 3:
            raise TypeError(f"元组信号至少要 (code, time, weight)，实际 {len(it)} 项")
        return Signal(code=str(it[0]), time=str(it[1])[:10], weight=it[2] or 0.0,
                      reason=str(it[3]) if len(it) > 3 else "",
                      meta=(it[4] if len(it) > 4 else {}) or {})
    if isinstance(it, pd.Series):             # DataFrame 逐行
        d = it.to_dict()
        return _coerce(d)
    raise TypeError(f"信号元素必须是 Signal / dict / tuple，实际是 {type(it).__name__}")


def _as_signals(raw) -> list[Signal]:
    """把策略返回值标准化为 list[Signal]，并做**组合权重**口径的硬校验。

    契约（由 LOAD 期冒烟测试保证）：策略返回 list[Signal]，每条带 code、
    weight ≥ 0，且**任一事件日 Σweight ≤ 1**。

    这里是**严格校验**，理由有二：
      1. 负权重 = 做空，A 股不支持；裁剪成 0 会让「想空仓」和「想做空」变得无法区分。
      2. Σweight > 1 若静默按比例归一化，策略的 bug 会被改写成「半仓运行」——
         结果既不可解释也极难排查（用户只会看到「莫名其妙只有一半仓位」）。
         故直接抛错，报错信息带上超额总量、Σ 实测值、贡献最大的前 5 只。
    空列表（窗口内无信号）属合法，不拦截。
    """
    if raw is None:
        return []
    if isinstance(raw, pd.DataFrame):
        raw = raw.to_dict("records")
    if not isinstance(raw, list):
        raise TypeError(f"策略返回值必须是 list[Signal]，实际是 {type(raw).__name__}")

    warn: list[str] = []
    seen: dict[tuple[str, str], Signal] = {}   # (code, time) 去重：后写覆盖先写
    for raw_it in raw:
        it = _coerce(raw_it)
        if not it.code:
            warn.append("丢弃一条不带 code 的信号")
            continue
        w = float(it.weight or 0.0)
        if w != w or math.isinf(w):             # NaN / inf 权重
            warn.append(f"{it.code} {it.time} 权重非法(NaN/inf)，按 0 处理")
            w = 0.0
        if w < 0.0:
            warn.append(f"{it.code} {it.time} 负权重 {w}（做空）已裁剪为 0")
            w = 0.0
        key = (it.code, str(it.time)[:10])
        if key in seen:
            warn.append(f"{key} 重复输出（同日同票两条目标权重），以后者为准")
        seen[key] = Signal(code=it.code, time=key[1], weight=w,
                           reason=str(it.reason or ""),
                           meta=_clean_meta(it.meta, warn))

    out = sorted(seen.values(), key=lambda s: (s.code, s.time))

    # ---- 组合权重硬校验：任一事件日 Σweight ≤ 1 ----
    by_day: dict[str, list[Signal]] = {}
    for s in out:
        by_day.setdefault(s.time, []).append(s)
    for d, ss in by_day.items():
        tot = sum(s.weight for s in ss)
        if tot > 1.0 + _SUM_EPS:
            top = sorted(ss, key=lambda s: -s.weight)[:5]
            detail = ", ".join(f"{s.code}={s.weight:.4f}" for s in top)
            raise ValueError(
                f"{d} 目标权重合计 {tot:.4f} > 1（超出 {tot - 1.0:.4f}）：{detail}。"
                f"组合权重口径下同一事件日 Σweight 必须 ≤ 1；"
                f"想提高总仓位请用 position 参数，别让单票权重相加超过满仓。")

    # ---- 持仓骤降守卫：漏发一条信号 = 静默清仓，这里让它变成可定位的告警 ----
    prev_n: int | None = None
    for d in sorted(by_day):
        n = sum(1 for s in by_day[d] if s.weight > 0.0)
        if prev_n and n < prev_n * (1.0 - _DROP_WARN):
            warnings.warn(
                f"{d} 持仓数从 {prev_n} 骤降到 {n}（>{_DROP_WARN:.0%}）。"
                f"若非刻意清仓，多半是漏发信号——引擎会把本调仓日未输出的 code 记为 0。",
                RuntimeWarning, stacklevel=2)
        prev_n = n

    for msg in warn:
        warnings.warn(f"[信号标准化] {msg}", RuntimeWarning, stacklevel=2)
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


# ---- 信号 → 目标权重序列 --------------------------------------------------

def rebalance_dates(signals: list[Signal]) -> list[str]:
    """调仓日 = 全部信号日期的并集（升序、去重）。

    这是「缺失即清零」的**唯一**依据，因此策略无法表达"沉默持有"——
    想继续持有某票就必须显式重发它（哪怕权重与上期完全相同）。
    代价换来的是：任一天都能直接读出完整持仓，幂等、可重放。
    """
    return sorted({s.time for s in (signals or [])})


def signals_to_positions(signals: list[Signal],
                         close_by_code: dict[str, pd.Series],
                         rb_dates: list[str] | None = None) -> dict[str, pd.Series]:
    """把扁平信号按标的展开成「目标权重序列」{code: Series(权重, 索引=行情日期)}。

    口径：
      * 事件日        设为该code 当日的目标权重；
      * 非调仓日      前向填充（持有不变）；
      * 调仓日未输出  **记 0**（即「不在目标组合里 → 卖出」）；
      * 首个事件之前  空仓（0）。

    ⚠️ 初值必须是 **NaN 而非 0.0**：ffill 只填 NaN，若用 0 填充则非事件日
    全是实数 0、前向填充失效，持仓被压成「只有信号当天有仓」。

    `close_by_code` 只用它的 `.index`（该票自身的行情日期），**从不碰价格**——
    所以 run_recommend 可以用只带 index 的伪行情把内存降到 O(只数×天数×字符串)。
    """
    sig = signals or []
    if rb_dates is None:
        rb_dates = rebalance_dates(sig)
    by_code: dict[str, dict[str, Signal]] = {}
    for s in sig:
        by_code.setdefault(s.code, {})[s.time] = s

    out: dict[str, pd.Series] = {}
    for code, close in (close_by_code or {}).items():
        idx = [str(d)[:10] for d in close.index]
        pos_of = {d: i for i, d in enumerate(idx)}
        arr = np.full(len(idx), np.nan, dtype=float)

        mine = by_code.get(code) or {}
        for d, s in mine.items():
            i = pos_of.get(d)
            if i is not None:
                arr[i] = float(s.weight)
        # 调仓日未输出 → 0（「不在目标组合里」= 卖出），必须在 ffill **之前**落定，
        # 否则会被前向填充把上一期的权重带过来，卖出信号直接消失。
        for d in rb_dates:
            i = pos_of.get(d)
            if i is not None and d not in mine:
                arr[i] = 0.0

        out[code] = pd.Series(arr, index=idx).ffill().fillna(0.0)
    return out


# ---- 目标权重 → 交易流水（买卖由引擎 diff 派生）--------------------------

def diff_trades(positions: dict[str, pd.Series],
                signal_meta: dict[tuple[str, str], tuple[str, dict]] | None = None,
                tol: float = _DELTA_EPS) -> dict[str, list[dict]]:
    """对相邻调仓日的权重向量做差分，派生 buy / sell / add / trim 流水。

    参数：
      positions    {code: Series(目标权重)}，来自 signals_to_positions
      signal_meta  {(code, time): (reason, meta)}，用于把归因信息挂到流水上；
                   **卖出来自「未输出」时查不到**，此时 reason 用引擎合成的文案。

    返回 {code: [{"time","prev_w","w","delta","kind","reason","meta"}, ...]}，
    金额 / 股数 / 可行性一律交给 stats —— base 只表达「权重怎么变的」。

    向量化：先用 numpy 一次比较定位「权重真的变了」的下标，只对这些下标做 Python 迭代。
    全市场 5559 只 × 700 天若逐日 Python 循环是 390 万次迭代，报告生成会卡到不可接受。
    """
    lut = signal_meta or {}
    out: dict[str, list[dict]] = {}
    for code, pos in (positions or {}).items():
        if pos is None or len(pos) < 2:
            continue
        w = pos.to_numpy(dtype=float, copy=False)
        prev = np.concatenate(([0.0], w[:-1]))          # 首日之前为空仓
        delta = w - prev
        idxs = np.flatnonzero(np.abs(delta) > tol)
        if idxs.size == 0:
            continue
        dates = [str(x)[:10] for x in pos.index]
        events: list[dict] = []
        for i in idxs:
            pw, cw, dl = float(prev[i]), float(w[i]), float(delta[i])
            if pw <= 0.0 < cw:
                kind = "buy"
            elif pw > 0.0 >= cw:
                kind = "sell"
            elif cw > pw:
                kind = "add"
            else:
                kind = "trim"
            reason, meta = lut.get((code, dates[i]), ("", {}))
            if kind == "sell" and not reason:
                reason = "跌出目标组合（本次调仓未输出）"
            events.append({
                "code": code, "time": dates[i],
                "prev_w": round(pw, 6), "w": round(cw, 6), "delta": round(dl, 6),
                "kind": kind, "reason": reason, "meta": dict(meta or {}),
            })
        if events:
            out[code] = events
    return out


def signal_meta_index(signals: list[Signal]) -> dict[tuple[str, str], tuple[str, dict]]:
    """建 (code, time) → (reason, meta) 索引，供 diff_trades 挂归因。"""
    return {(s.code, s.time): (s.reason, dict(s.meta or {})) for s in (signals or [])}