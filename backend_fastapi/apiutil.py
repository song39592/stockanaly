# -*- coding: utf-8 -*-
"""HTTP 响应与**错误体形状**的统一层（第 11 项）。

## 统一后的形状

```json
{"ok": false, "detail": "…", "error": {"code": "BAD_PARAM", "message": "…"}}
```

- `ok`：**业务**是否成功。
- `error.code` / `error.message`：**结构化**错误，供程序判断与展示。
- `detail`：**原样保留**的字符串消息。

## 为什么 `detail` 还要留着（不是冗余，是有意的）

前端有 8 处直接读 `detail` 取提示文案（下载页 3 处、系统页 2 处、个股页 2 处、
估值页 1 处），而它们走的都是 `raise HTTPException(detail=...)`。
既然本项承诺「前端行为不变」，`detail` 就必须留着 —— 它是**向后兼容锚点**，
不是重复字段。前端新的取值入口是 `J.ErrMsg()`（C#），一处兼容三种形状。

## 状态码不统一（踩坑点 3 的执行结果）

本项只统一**形状**，保留二分法：

- **HTTP 状态码** = 请求是否合法（400 / 404 / 500 …）
- **`ok` 字段** = 业务是否成功（周榜恒 200）

⚠️ `chip/rank` 的「恒 200 + ok=false」是**刻意设计**（前端据此轮询「先点刷新本周」），
压平成 400 会打断轮询。所以周榜走 `soft_fail()` 而不是 `fail()`。

## 覆盖范围

靠 `install(app)` 挂一个**全局 HTTPException 处理器**，
把 FastAPI 默认的 `{"detail": …}` 规范化 —— 90 余处 `raise HTTPException`
**一行都不用改**就拿到了新形状。路由侧只需要在自己想给更准的 `code` 时
显式调用 `fail()`。
"""
from __future__ import annotations

from typing import Any, Callable

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

# 异常类型 → 错误码。两份历史映射（indicators 的 UNKNOWN_INDICATOR 与
# strategies 的 UNKNOWN_STRATEGY）在这里合并成一个通用码：
# 前端只读 message、从不读 code（见 StrategyPage.StErrMsg），合并是安全的。
CODE_ERR: dict[type, str] = {
    KeyError: "UNKNOWN_KEY",
    ValueError: "BAD_PARAM",
    RuntimeError: "NO_DATA",
    FileNotFoundError: "NOT_FOUND",
    PermissionError: "FORBIDDEN",
}

# 各历史域用过的、更具体的码。保留下来以便个别路由沿用原值（不改变既有语义）。
CODE_UNKNOWN_INDICATOR = "UNKNOWN_INDICATOR"
CODE_UNKNOWN_STRATEGY = "UNKNOWN_STRATEGY"
CODE_EXPIRED = "EXPIRED"

DEFAULT_STATUS = 400


def code_of(exc: BaseException, default: str = "BAD_PARAM") -> str:
    """异常 → 错误码；未登记的类型给 `default`（不抛，避免二次故障）。"""
    return CODE_ERR.get(type(exc), default)


def ok(payload: dict | None = None, **extra: Any) -> dict:
    """成功响应：`{"ok": True, **payload, **extra}`。"""
    out: dict = {"ok": True}
    if payload:
        out.update(payload)
    out.update(extra)
    return out


def body(message: str, code: str) -> dict:
    """统一错误体（`detail` 与 `message` 同时给，见模块 docstring）。"""
    return {"ok": False, "detail": message,
            "error": {"code": code, "message": message}}


def fail(message: str, code: str = "BAD_PARAM",
         status: int = DEFAULT_STATUS) -> JSONResponse:
    """**HTTP 错误**（请求非法 / 资源不存在 / 服务端故障）。

    ⚠️ 返回的是 `JSONResponse` 而不是异常，所以调用点是 **`return apiutil.fail(...)`**，
    **不是 `raise`**。曾经写成 `raise apiutil.fail(...)` —— JSONResponse 不是 Exception，
    raise 它会让 FastAPI 落到兜底处理器，回 `500 Internal Server Error`（连错误体都没有）。
    要「抛异常」的写法请用 `raise apiutil.http_error(...)`。
    """
    return JSONResponse(status_code=status, content=body(message, code))


def http_error(message: str, code: str = "BAD_PARAM",
               status: int = DEFAULT_STATUS) -> HTTPException:
    """`fail` 的**抛异常**版本，供 `raise apiutil.http_error(...)` 使用。

    与 `fail` 等价（都经全局处理器得到同一形状），只是包成 HTTPException。
    路由里若习惯 `raise`，用这个。
    """
    return HTTPException(status_code=status, detail=body(message, code))


def soft_fail(message: str, code: str = "NO_DATA") -> dict:
    """**业务性失败**：200 + `ok=false`。

    给「用户能做点什么」的失败（本周没算、缓存损坏、行情不足）——
    这类不该用 4xx/5xx 表达，因为请求本身完全合法。
    """
    return body(message, code)


def guard(status: int = DEFAULT_STATUS,
          code: str | dict[type, str] | None = None) -> Callable:
    """装饰器：把「调 handler，异常转统一错误体」这件事消掉。

    ```python
    @router.post("/x")
    @guard()                       # 消掉 8 行 try/except
    def do_x(req: Req): ...
    ```

    `code` 可以是：

    - `None` —— 用 `CODE_ERR` 的通用码（`UNKNOWN_KEY` / `BAD_PARAM` / `NO_DATA`）；
    - **字符串** —— 所有异常统一用这个码；
    - **字典** —— 按异常类型分别给码，用于保留本域更有意义的码
      （如策略域的 `KeyError -> UNKNOWN_STRATEGY`，比通用 `UNKNOWN_KEY` 更可读）。
    """
    def deco(func: Callable) -> Callable:
        import functools

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except HTTPException:
                raise                      # 已经是 HTTP 错误，交给全局处理器
            except (KeyError, ValueError, RuntimeError, OSError) as exc:
                if isinstance(code, dict):
                    got = code.get(type(exc)) or code_of(exc)
                elif isinstance(code, str):
                    got = code
                else:
                    got = code_of(exc)
                return fail(str(exc), got, status)

        return wrapper

    return deco


def install(app: FastAPI) -> None:
    """把规范化处理器挂到 app 上（`main.py` 建 app 后调一次）。

    这是本项的**主要杠杆**：所有 `raise HTTPException(detail=...)` 无需改动
    即获得统一形状。两类容易漏掉的也一并接管：

    - **Starlette 的 404 / 405**（路由不存在、方法不对）——它抛的是
      `starlette.exceptions.HTTPException`，**不是** `fastapi.HTTPException`
      （后者是它的子类）。只注册 fastapi 那个，404 会漏出去仍是裸
      `{"detail":"Not Found"}`，于是又多一种形状。
    - **请求校验错误 422** ——原本是 `{"detail": [ {loc,msg,type}, … ]}`，
      前端读 detail 当字符串会拿到数组。
    """
    @app.exception_handler(StarletteHTTPException)
    async def _http_exc(_request, exc: StarletteHTTPException):
        detail = exc.detail
        if isinstance(detail, dict) and "error" in detail:
            # 已经是统一形状（例如 fail() / http_error() 产出的），补齐 detail 锚点即可
            msg = (detail.get("error") or {}).get("message") or str(detail)
            payload = dict(detail)
            payload.setdefault("ok", False)
            payload.setdefault("detail", msg)
        else:
            msg = detail if isinstance(detail, str) else str(detail)
            code = _code_from_status(exc.status_code)
            payload = body(msg, code)
        return JSONResponse(status_code=exc.status_code, content=payload,
                            headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def _validation(_request, exc: RequestValidationError):
        # 原本是 detail=[{loc,msg,type}, …]，前端读 detail 当字符串会拿到数组
        first = (exc.errors() or [{}])[0]
        loc = ".".join(str(x) for x in (first.get("loc") or [])[1:]) or "参数"
        return fail(f"{loc}：{first.get('msg', '参数不合法')}",
                    "BAD_PARAM", status=422)


def _code_from_status(status: int) -> str:
    """状态码 → 默认错误码（没显式给 code 时的兜底命名）。"""
    if status == 404:
        return "NOT_FOUND"
    if status in (401, 403):
        return "FORBIDDEN"
    if status >= 500:
        return "SERVER_ERROR"
    return "BAD_PARAM"