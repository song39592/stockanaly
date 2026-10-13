"""股票池历史、K线与消息面的 HTTP 接口。"""

from __future__ import annotations

import math
import re
import threading
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from features.history import service as history_service
from features.history import store as history_store
from core.periods import normalize

import indicators.rps_data as rps_data

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
                      refresh: bool = False, period: str = "day"):
    if not re.fullmatch(r"\d{6}", code):
        raise HTTPException(status_code=400, detail="股票代码必须是6位数字")
    try:
        period = normalize(period)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        return {"ok": True, **history_service.stock_detail(code, start, end, refresh,
                                                           period=period)}
    except Exception as exc:  # noqa: BLE001 - 转换为可读接口错误
        raise HTTPException(status_code=500, detail=f"个股历史加载失败：{exc}")


@router.get("/pool/rps")
def pool_rps():
    """RPS 标注的股票池：最新快照成员带 RPS50/120/250，并对比上一快照得出
    「入池」（新增）与「出池」（移除）。

    口径：
      · 在池 = 最新一期快照的全部成员，按 RPS120 降序（缺值沉底）；
      · 入池 = 最新一期有、上一期没有的成员（带最新一期的 RPS）；
      · 出池 = 上一期有、最新一期没有的成员（带上一期的 RPS）。
    RPS 首次计算需预热全市场日 K 面板（约 15 秒），算完进程内缓存复用；
    面板不可用时通过 `rps_error` 提示，三个列表仍正常返回（RPS 列为空）。
    """
    try:
        dates = history_store.snapshot_dates()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"读取股票池失败：{exc}")
    if not dates:
        return {"ok": True, "latest": None, "prev": None,
                "in_pool": [], "entered": [], "exited": [],
                "rps_error": "尚未导入任何股票池快照"}
    d1 = dates[-1]
    d0 = dates[-2] if len(dates) >= 2 else None
    try:
        members = (history_store.load_pool_members(start=d0, end=d1)
                   if d0 else history_store.load_pool_members(end=d1))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"读取股票池成员失败：{exc}")
    m1 = members.get(d1, {})
    m0 = members.get(d0, {}) if d0 else {}
    codes1 = set(m1)
    codes0 = set(m0)
    entered = sorted(codes1 - codes0)
    exited = sorted(codes0 - codes1)

    rps_error = None
    rps = None
    try:
        rps = rps_data.market_rps([d1] if not d0 else [d1, d0], [50, 120, 250])
    except Exception as exc:  # noqa: BLE001 - 面板不可用不应让接口整体失败
        rps_error = str(exc)

    def cell(df, date, code):
        if df is None:
            return None
        try:
            v = df.at[date, code]
        except Exception:
            return None
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return None
        return round(float(v), 1)

    def make_item(code, info, date):
        return {
            "code": code,
            "name": info.get("name") or "",
            "industry": info.get("industry") or "",
            "rps50": cell(rps[50] if rps else None, date, code),
            "rps120": cell(rps[120] if rps else None, date, code),
            "rps250": cell(rps[250] if rps else None, date, code),
        }

    def by_rps(items):
        items.sort(key=lambda it: (it["rps120"] is None, -(it["rps120"] or 0)))
        return items

    in_pool = by_rps([make_item(c, m1[c], d1) for c in sorted(codes1)])
    entered_items = by_rps([make_item(c, m1[c], d1) for c in entered])
    exited_items = by_rps([make_item(c, m0[c], d0) for c in exited]) if d0 else []

    return {
        "ok": True,
        "latest": d1,
        "prev": d0,
        "in_pool": in_pool,
        "entered": entered_items,
        "exited": exited_items,
        "rps_error": rps_error,
    }


class RpsPoolRefreshRequest(BaseModel):
    threshold: int = Field(90, ge=0, le=100)
    window: int = Field(20, ge=1, le=120)
    max_days: int = Field(20, ge=1, le=250)


@router.post("/rps-pool/refresh")
def rps_pool_refresh(req: RpsPoolRefreshRequest):
    """触发后台计算：把最近 window 个交易日的全市场 RPS 强势池（RPS50/120/250 任一
    ≥ threshold）落库，并截断到最近 max_days 天。返回 job_id，前端轮询进度。

    设计：每日增量 —— 已落库的最新交易日会被幂等覆盖，新增的交易日会追加；
    面板构建一次后进程内缓存，所以回填 window 天与算 1 天成本相近（~15s 预热 + 极快计算）。
    """
    try:
        job_id = history_service.enqueue_rps_pool_refresh(
            threshold=req.threshold, window=req.window, max_days=req.max_days)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"启动 RPS 池刷新失败：{exc}")
    return {"ok": True, "job_id": job_id}


@router.get("/rps-pool/job/{job_id}")
def rps_pool_job(job_id: str):
    job = history_store.get_rps_pool_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return {"ok": True, "job": job}


@router.get("/rps-pool")
def rps_pool():
    """读取已落库的每日 RPS 强势池，并对比最新与上一交易日得出「在池 / 入池 / 出池」。

    口径：
      · 在池 = 最新交易日命中的全部强股（按 RPS120 降序）；
      · 入池 = 最新有、上一交易日没有的强股；
      · 出池 = 上一交易日有、最新没有的强股。
    `dates` 为最近交易日序列（降序），`retention_days` 为当前保留天数。
    """
    try:
        dates = history_store.rps_pool_dates()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"读取 RPS 池失败：{exc}")
    if not dates:
        return {"ok": True, "latest": None, "prev": None, "dates": [],
                "in_pool": [], "entered": [], "exited": [],
                "retention_days": 0,
                "note": "尚未生成 RPS 强势池，点「刷新」触发全市场 RPS 计算"}
    data = history_store.load_rps_pool()
    latest, prev = dates[0], (dates[1] if len(dates) >= 2 else None)
    in_codes = {x["code"] for x in data.get(latest, [])}
    prev_map = {x["code"]: x for x in data.get(prev, [])} if prev else {}
    entered = [x for x in data.get(latest, []) if x["code"] not in prev_map]
    exited = [x for x in data.get(prev, []) if x["code"] not in in_codes]
    return {
        "ok": True,
        "latest": latest,
        "prev": prev,
        "dates": dates,
        "retention_days": len(dates),
        "in_pool": data.get(latest, []),
        "entered": entered,
        "exited": exited,
        "note": (f"数据截至 {latest}"
                 + (f" · 上一交易日 {prev}" if prev else " · 暂无上一交易日")
                 + f" · 保留最近 {len(dates)} 个交易日"),
    }
