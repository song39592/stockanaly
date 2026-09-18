"""股票池历史、K线与消息面的 HTTP 接口。"""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

import history_service
import history_store

router = APIRouter(prefix="/api/history", tags=["股票池历史与K线"])


class PoolStock(BaseModel):
    code: str
    name: str = ""
    industry: str = ""
    region: str = ""
    price: float | None = None
    change: float | None = None

    @field_validator("code")
    @classmethod
    def code_is_valid(cls, value: str) -> str:
        value = value.strip().zfill(6)
        if not re.fullmatch(r"\d{6}", value):
            raise ValueError("股票代码必须是6位数字")
        return value


class PoolImportRequest(BaseModel):
    snapshot_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    stocks: list[PoolStock]
    sync_market: bool = True


@router.post("/pool/import")
def import_pool(req: PoolImportRequest):
    stocks = [item.model_dump() for item in req.stocks]
    history_store.save_snapshot(req.snapshot_date, stocks)
    job_id = history_service.enqueue_snapshot_sync(req.snapshot_date, stocks) if req.sync_market and stocks else None
    return {"ok": True, "saved": len(stocks), "job_id": job_id}


@router.get("/sync/{job_id}")
def sync_status(job_id: str):
    job = history_store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="同步任务不存在")
    return {"ok": True, "job": job}


@router.get("/stock/{code}")
def get_stock_history(code: str, start: str | None = None, end: str | None = None,
                      refresh: bool = False):
    if not re.fullmatch(r"\d{6}", code):
        raise HTTPException(status_code=400, detail="股票代码必须是6位数字")
    try:
        return {"ok": True, **history_service.stock_detail(code, start, end, refresh)}
    except Exception as exc:  # noqa: BLE001 - 转换为可读接口错误
        raise HTTPException(status_code=500, detail=f"个股历史加载失败：{exc}")
