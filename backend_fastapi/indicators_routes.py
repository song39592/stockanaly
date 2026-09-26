# -*- coding: utf-8 -*-
"""技术指标 HTTP 接口（标准化输出，详见 indicators/ARCHITECTURE.md §6）。

路由前缀 /api/indicators：
    GET  ""          指标清单（含 panel / params 规格）
    GET  /compute    单指标计算：?code=&id=&params=（params 为 JSON 字符串）
    POST /batch      批量计算：{"code","items":[{"id","params"}]}

错误统一返回 {"error":{"code","message"}}（单指标计算用对应 HTTP 状态码；
批量接口单个失败只在该项内返回 error，不影响其余项）。
"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import indicators

router = APIRouter(prefix="/api/indicators", tags=["技术指标"])

# 指标计算可能抛出的异常 → (错误码, HTTP 状态码)
_CODE_ERR = {
    KeyError: ("UNKNOWN_INDICATOR", 400),
    ValueError: ("BAD_PARAM", 400),
    RuntimeError: ("NO_DATA", 404),
}


def _err(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def _compute_or_error(code: str, id: str, params) -> dict | JSONResponse:
    try:
        return indicators.compute(code, id, params or {})
    except (KeyError, ValueError, RuntimeError) as exc:
        err_code, status = _CODE_ERR[type(exc)]
        return _err(err_code, str(exc), status)


def _safe_compute(code: str, id: str, params) -> dict:
    """批量用：单个失败不中断，返回带 error 的项。"""
    try:
        return indicators.compute(code, id, params or {})
    except (KeyError, ValueError, RuntimeError) as exc:
        err_code, _ = _CODE_ERR[type(exc)]
        return {"id": id, "error": {"code": err_code, "message": str(exc)}}


@router.get("")
def list_indicators():
    """指标清单：每项含 id / name / category / panel / params。"""
    return indicators.list_indicators()


@router.get("/compute")
def compute_indicator(code: str, id: str, params: Optional[str] = None):
    try:
        p = json.loads(params) if params else {}
        if not isinstance(p, dict):
            return _err("BAD_PARAM", "params 必须是 JSON 对象", 400)
    except json.JSONDecodeError:
        return _err("BAD_PARAM", "params 不是合法 JSON", 400)
    return _compute_or_error(code, id, p)


class BatchItem(BaseModel):
    id: str
    params: dict = {}


class BatchRequest(BaseModel):
    code: str
    items: list[BatchItem]


@router.post("/batch")
def batch(req: BatchRequest):
    """批量计算：一次拿多指标，避免前端多次请求。"""
    return {"items": [_safe_compute(req.code, it.id, it.params) for it in req.items]}
