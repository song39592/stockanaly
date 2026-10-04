# -*- coding: utf-8 -*-
"""筹码峰公式的注册机制与统一计算入口。

每个筹码公式是一个**普通函数**，用 `@chip_formula(...)` 装饰即完成登记，
文件名即公式 id（与 strategies/ 一致）。

公式契约：
    def xxx(ctx: ChipContext, <参数...>) -> np.ndarray
  * 入参 `ctx` 由 core 装配好（行情 / 换手率 / 均价 / 价格轴 / 流通股本），公式**不自行取数**；
  * 返回 **二维数组 (交易日数, 分箱数)**，元素是**相对**筹码量（非负即可，不必归一化——
    core 会按行归一化到 100%）；
  * 每个公式文件只放一个公式；id 必须唯一，重复在导入时即报错。

校验全部在 **LOAD 时**（registry.scan，见 ARCHITECTURE.md §4）：导入 / 唯一性 /
元数据 / 签名 / 冒烟五步，不过者直接剔出 FORMULAS，运行期不再做冗余兜底。
"""
from __future__ import annotations

import dataclasses as dc
import datetime as dt
import inspect
from typing import Any, Callable

import numpy as np

from . import data

FORMULAS: dict[str, "ChipFormulaMeta"] = {}
DEFAULT_FORMULA_ID = "tri_decay"        # 未指定公式时用它；被删改则由调用方显式指定
DEFAULT_BINS = 80


@dc.dataclass
class ParamSpec:
    """公式参数规格：供前端渲染控件 + 后端填充默认值 / 校验范围。"""
    name: str
    type: str                 # "int" | "float" | "choice"
    default: Any
    min: float | None = None
    max: float | None = None
    choices: list | None = None
    label: str = ""


@dc.dataclass
class ChipContext:
    """公式输入：一段行情（含预热）+ 换手率 + 价格轴 + 流通股本。"""
    dates: list[str]
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray        # 手
    turnover: np.ndarray      # 换手率（小数 0~1）
    vwap: np.ndarray          # 当日成交均价（元）
    float_shares: float       # 流通股本（股）
    edges: np.ndarray         # 分箱边界，长度 bins+1
    centers: np.ndarray       # 分箱中心，长度 bins
    p_lo: float               # 价格轴下界（= edges[0]）
    p_hi: float               # 价格轴上界（= edges[-1]）
    adjust: str = "qfq"
    lockup_ratio: float = 0.0     # 锁仓占流通股比例 r（前十大流通股东 占流通股比例 > 5%）
    lockup_factor: float = 1.0    # 换手放大系数 1/(1-r)；公式按需使用，1.0 = 未修正

    @property
    def days(self) -> int:
        return len(self.close)

    @property
    def bins(self) -> int:
        return len(self.centers)


@dc.dataclass
class ChipFormulaMeta:
    id: str
    name: str
    category: str
    description: str
    params: list[ParamSpec]
    func: Callable


def chip_formula(id: str, name: str, category: str = "chip", description: str = "",
                 params: list[ParamSpec] | None = None):
    """装饰器：把筹码公式登记进 FORMULAS。id 必须唯一，重复会抛 ValueError。"""
    if id in FORMULAS:
        raise ValueError(f"筹码公式 id 重复: {id}")

    def deco(func: Callable) -> Callable:
        FORMULAS[id] = ChipFormulaMeta(
            id=id, name=name, category=category, description=description,
            params=params or [], func=func,
        )
        return func
    return deco


# --------------------------------------------------------------------------- #
# 参数填充与返回值校验
# --------------------------------------------------------------------------- #
def _fill_params(meta: ChipFormulaMeta, params: dict | None) -> dict:
    """按 ParamSpec 填默认值；未声明的参数不传（避免出现意外的 kwargs）。"""
    params = params or {}
    out: dict[str, Any] = {}
    for spec in meta.params:
        value = params.get(spec.name, spec.default)
        if value is None:
            value = spec.default
        if spec.type in ("int", "float") and value is not None:
            value = spec.type == "int" and int(value) or float(value)
            if spec.min is not None:
                value = max(value, spec.min)
            if spec.max is not None:
                value = min(value, spec.max)
        out[spec.name] = value
    return out


def _as_matrix(raw, days: int, bins: int) -> np.ndarray:
    """**严格**校验公式返回值：(交易日数, 分箱数) 的有限非负二维数组。

    不做任何静默纠正——形状或数值有问题就抛，由 LOAD 时冒烟拦下（见 registry）；
    正常路径上公式已通过校验，这里实际不会触发。
    """
    if not isinstance(raw, np.ndarray):
        raise TypeError(f"公式返回值必须是 numpy 二维数组，实际是 {type(raw).__name__}")
    if raw.ndim != 2 or raw.shape != (days, bins):
        raise ValueError(f"公式返回形状必须是 (交易日数, 分箱数)=({days}, {bins})，"
                         f"实际是 {raw.shape}")
    if not np.all(np.isfinite(raw)):
        raise ValueError("公式返回值含 NaN / Inf")
    if np.any(raw < 0):
        raise ValueError("公式返回值含负数")
    return raw


def _normalize_rows(matrix: np.ndarray) -> np.ndarray:
    """按行归一化到 100（契约：公式返回**相对**分布，core 统一转成百分比）。

    全部为 0 的行（尚无筹码）保持全 0，不做伪填充。
    """
    sums = matrix.sum(axis=1)
    safe = np.where(sums > 0, sums, 1.0)
    return matrix / safe[:, None] * 100.0


def _percentile(centers: np.ndarray, pct: np.ndarray, q: float) -> float | None:
    """按累计筹码取价格分位（线性插值）。"""
    total = float(pct.sum())
    if total <= 0:
        return None
    cum = np.cumsum(pct) / total
    return float(np.interp(q / 100.0, cum, centers))


def _stats_of(pct: np.ndarray, centers: np.ndarray, close_i: float) -> dict:
    """某一帧的统计（供光标回溯；字段精简以控响应体积）。

    `close` 是该日收盘价——获利比例要以**当天的**价格为界，不能统一用最新收盘价。
    """
    total = float(pct.sum())
    if total <= 0:
        return {"close": round(close_i, 4), "avg_cost": None, "peak_price": None,
                "peak_pct": None, "profit_ratio": None, "scr90": None}
    idx = int(np.argmax(pct))
    p5 = _percentile(centers, pct, 5)
    p95 = _percentile(centers, pct, 95)
    scr90 = None
    if p5 and p95 and (p95 + p5) != 0:
        scr90 = round((p95 - p5) / (p95 + p5), 6)
    return {
        "close": round(close_i, 4),
        "avg_cost": round(float((centers * pct).sum() / 100.0), 4),
        "peak_price": round(float(centers[idx]), 4),
        "peak_pct": round(float(pct[idx]), 4),
        "profit_ratio": round(float(pct[centers < close_i].sum()), 4),
        "scr90": scr90,
    }


# --------------------------------------------------------------------------- #
# 统一计算入口
# --------------------------------------------------------------------------- #
@dc.dataclass
class ChipResult:
    """公式计算结果（结构化，供 HTTP 响应与指标侧复用）。"""
    dates: list[str]        # 窗口内日期（已剔除预热段）
    close: np.ndarray       # 窗口内收盘价
    centers: np.ndarray     # 分箱中心（价格轴）
    edges: np.ndarray       # 分箱边界
    pct: np.ndarray         # (窗口帧数, 分箱数)，每行合计 100
    turnover: np.ndarray    # 窗口内换手率（小数）
    float_shares: float
    warm: int
    adjust: str
    period: str
    formula_id: str
    formula_name: str
    params: dict
    lockup_ratio: float = 0.0
    lockup_factor: float = 1.0


def compute_matrix(code: str, formula_id: str | None = None, params: dict | None = None,
                   start: str | None = None, end: str | None = None,
                   adjust: str = "qfq", days: int | None = None,
                   bins: int = DEFAULT_BINS, period: str = "day",
                   offline: bool = False) -> ChipResult:
    """按指定公式算出**筹码矩阵**（不含 HTTP 响应包装）。

    指标侧的延伸量（获利比例 / 平均成本 / 集中度）复用这一份结果，
    避免同一只票被重复计算多遍。

    `offline=True`：透传给 `data.load_input`，批量场景不联网（见那里的说明）。

    异常：
      KeyError      公式 id 不存在（调用方应转成 400）
      RuntimeError  行情不足 / 缺流通股本
    """
    fid = formula_id or DEFAULT_FORMULA_ID
    meta = FORMULAS.get(fid)
    if meta is None:
        raise KeyError(f"未知筹码公式: {fid}（可用：{', '.join(sorted(FORMULAS))}）")

    bins = int(bins) if bins and int(bins) > 1 else DEFAULT_BINS
    kwargs = _fill_params(meta, params)
    inp = data.load_input(code, start, end, adjust, days, bins, period=period,
                          offline=offline)
    ctx = ChipContext(
        dates=inp.dates, open=inp.open, high=inp.high, low=inp.low, close=inp.close,
        volume=inp.volume, turnover=inp.turnover, vwap=inp.vwap,
        float_shares=inp.float_shares, edges=inp.edges, centers=inp.centers,
        p_lo=float(inp.edges[0]), p_hi=float(inp.edges[-1]), adjust=inp.adjust,
        lockup_ratio=float(getattr(inp, "lockup_ratio", 0.0)),
        lockup_factor=float(getattr(inp, "lockup_factor", 1.0)),
    )
    matrix = _normalize_rows(_as_matrix(meta.func(ctx, **kwargs), ctx.days, ctx.bins))

    win = inp.win                                   # 只输出窗口内的帧（预热段不输出）
    return ChipResult(
        dates=inp.dates[-win:], close=ctx.close[-win:], centers=ctx.centers,
        edges=ctx.edges, pct=matrix[-win:], turnover=ctx.turnover[-win:],
        float_shares=inp.float_shares, warm=inp.warm, adjust=inp.adjust,
        period=inp.period,
        formula_id=meta.id, formula_name=meta.name, params=kwargs,
        lockup_ratio=ctx.lockup_ratio, lockup_factor=ctx.lockup_factor,
    )


def compute(code: str, formula_id: str | None = None, params: dict | None = None,
            start: str | None = None, end: str | None = None,
            adjust: str = "qfq", days: int | None = None,
            bins: int = DEFAULT_BINS, period: str = "day") -> dict:
    """按指定公式计算筹码分布，返回**标准化响应**（字段见 ARCHITECTURE.md §5）。"""
    res = compute_matrix(code, formula_id, params, start, end, adjust, days, bins,
                         period=period)
    pct = res.pct[-1]
    win = len(res.dates)
    frame_stats = [_stats_of(res.pct[i], res.centers, float(res.close[i]))
                   for i in range(win)]
    last_close = float(res.close[-1])
    centers = res.centers
    p5 = _percentile(centers, pct, 5)
    p15 = _percentile(centers, pct, 15)
    p50 = _percentile(centers, pct, 50)
    p85 = _percentile(centers, pct, 85)
    p95 = _percentile(centers, pct, 95)

    def scr(a: float | None, b: float | None) -> float | None:
        if not a or not b or (a + b) == 0:
            return None
        return round((b - a) / (b + a), 6)

    return {
        "ok": True,
        "code": code,
        "formula": {"id": res.formula_id, "name": res.formula_name, "params": res.params},
        "adjust": res.adjust,
        "period": res.period,
        "as_of": res.dates[-1],
        "start": res.dates[0],
        "end": res.dates[-1],
        "bars": int(win),                       # 输出帧数（= K 线可见根数）
        "warmup_bars": int(res.warm),           # 只用于养熟筹码状态、不输出的更早期 K 线
        "bins_count": int(len(res.centers)),
        "lockup_applied": abs(float(res.lockup_factor) - 1.0) > 1e-9,
        "lockup_ratio": round(float(res.lockup_ratio), 6),      # 锁仓占流通股比例（小数）
        "lockup_factor": round(float(res.lockup_factor), 6),    # 换手放大系数 1/(1-r)
        "float_shares": res.float_shares,
        "avg_turnover": round(float(np.mean(res.turnover)), 6),
        "last_close": round(last_close, 4),
        "bins": [
            {"lo": round(float(res.edges[i]), 4), "hi": round(float(res.edges[i + 1]), 4),
             "price": round(float(centers[i]), 4), "pct": round(float(pct[i]), 4)}
            for i in range(len(res.centers))
        ],
        "stats": frame_stats[-1] if frame_stats else {},
        # 逐日快照（光标左右移动时前端按日期本地切片，不再回服务端）：
        #   dates[i] / pct[i] / stats[i] 一一对应，pct[i] 与 bins 共用同一价格轴。
        "frames": {
            "dates": res.dates,
            "bins": int(len(res.centers)),
            "pct": np.round(res.pct, 2).tolist(),
            "stats": frame_stats,
        },
        "computed_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def list_formulas() -> list[dict]:
    """公式清单（供前端下拉选择）。"""
    return [
        {
            "id": m.id,
            "name": m.name,
            "category": m.category,
            "description": m.description,
            "params": [dc.asdict(p) for p in m.params],
        }
        for m in sorted(FORMULAS.values(), key=lambda x: x.id)
    ]


def signature_problems(meta: ChipFormulaMeta) -> list[str]:
    """签名检查（LOAD 时）：首参必须是 ctx，其余参数都要有默认值且与 ParamSpec 对齐。"""
    problems: list[str] = []
    try:
        sig = inspect.signature(meta.func)
    except (TypeError, ValueError) as exc:
        return [f"无法解析签名：{exc}"]
    names = [p.name for p in sig.parameters.values()
             if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)]
    if not names or names[0] != "ctx":
        problems.append(f"首参必须是 ctx（ChipContext），实际是 {names[0] if names else '无参数'}")
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
