# -*- coding: utf-8 -*-
"""JSON 出口清洗（第 09 项）。

**为什么必须有这一层**：`json.dumps` 默认放行 NaN（产出非法 JSON），而 FastAPI 的
`JSONResponse` 是严格模式（`allow_nan=False`），遇到 NaN 直接抛
`ValueError: Out of range float values are not JSON compliant`，
Starlette 兜底返回**纯文本 500**，前端只看到「无效的 JSON 基元: Internal」——
错误信息完全指不到真正出问题的那个字段。

## 两个函数的分工（别混用）

- **`json_safe(v)`**：单值。清洗 NaN / ±Inf → `None`，numpy 标量 → 原生类型。
  原本只有 `stock_profile` 在用（它逐字段调用），语义**保持原样、一行未改**。
- **`json_safe_deep(body)`**：整块响应体。**递归**清洗嵌套的 dict / list。
  ⚠️ `json_safe` 遇到 dict / list 是**原样返回**（`float(dict)` 抛 TypeError），
  所以**拿它洗整块响应体等于什么都没做**（`out is body` 为 True，嵌套 NaN 全留着）。
  要在 HTTP 出口清洗，**必须用 deep 版**。
"""

from __future__ import annotations

from typing import Any


def json_safe(v: Any) -> Any:
    """把 akshare / pandas 带来的值洗成 JSON 安全类型。

    关键是 **NaN / Inf → None**；顺带把 numpy 标量（int64 / float64 / bool_）
    转成 Python 原生类型。

    ⚠️ **对 `dict` / `list` 是恒等映射**（原样返回）—— 它是单值函数，
    要清洗整块响应体请用 `json_safe_deep`。
    """
    if v is None or isinstance(v, (str, bool)):
        return v
    if isinstance(v, (int,)) or (hasattr(v, "item") and isinstance(v.item(), int)):
        # int 分支必须在 float 之前：numpy bool_ 的 .item() 是 bool（isinstance int 为 True），
        # 先命中这里返回 int(1) —— 顺序反了会走到 float 分支变成 1.0。
        try:
            return int(v)
        except (ValueError, OverflowError, TypeError):
            return None
    f = None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v if isinstance(v, (list, dict)) else None
    if f != f or f in (float("inf"), float("-inf")):     # NaN / ±Inf
        return None
    return f


def json_safe_deep(body: Any) -> Any:
    """递归清洗整块响应体（HTTP 出口用）。

    - `dict`：逐个清洗**值**（key 不动 —— JSON 会自动把 int/float/bool/None 的 key 转成字符串，
      改 key 反而可能与调用方的字典查找不一致）；
    - `list` / `tuple`：逐个清洗元素，并**保持原容器类型**；
    - 其余：交给 `json_safe`。

    对**已经 JSON 安全的响应体是恒等映射**（含 str / int / float / bool / None /
    dict / list 的正常结构逐字段不变），所以正常路径的返回值不受影响 ——
    这是本项风险极低的根本原因。真正会变的只有 NaN / ±Inf / numpy 标量，
    而那三种在改动前会直接让接口 500。
    """
    if isinstance(body, dict):
        return {k: json_safe_deep(v) for k, v in body.items()}
    if isinstance(body, (list, tuple)):
        cleaned = [json_safe_deep(v) for v in body]
        return tuple(cleaned) if isinstance(body, tuple) else cleaned
    return json_safe(body)