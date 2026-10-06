# -*- coding: utf-8 -*-
"""筹码体系 · SCR90 周榜的 HTTP 接口（独立模块，不放在 main.py 里）。

本模块只负责 HTTP 层：参数校验与响应封装；批量计算在 `chip_rank_service.py`。
main.py 只需在 ROUTE_MODULES 里登记一行。

路由前缀 /api/chip/rank：
    GET  /status                 本周计算状态（前端轮询进度）
    GET  ?limit=100              本周分析结果（含三档；未算过返回 ok=false）
    POST /refresh                触发本周计算（已在跑则直接返回进度）

与 /api/chip/dist（单只票分布图）的分工：
    那边交互式、按只算、会做锁仓修正；这里是批量全市场、按周缓存、**不做锁仓修正**
    （几千只逐一取股东数据会拖垮/限流，结果的 `lockup=false` 如实标注）。
"""
from fastapi import APIRouter, Query

from features.chip import rank_service as svc

router = APIRouter(prefix="/api/chip/rank", tags=["筹码体系 · SCR90 周榜"])


@router.get("/status")
def chip_rank_status():
    """本周的计算状态：idle / running（含 percent 进度）/ ready / error。"""
    return {"ok": True, **svc.status()}


@router.get("")
def chip_rank_result(limit: int = Query(svc.DEFAULT_TOP, ge=1, le=2000,
                                        description="最新一期榜单返回前 N 只")):
    """本周分析结果。

    返回 `current`（最新一期 SCR90 升序前 N）与 `tiers` 三档：
    stay=连续全勤在榜 / up=上涨离榜（启动型）/ down=下跌离榜（破位）。
    """
    body = svc.result(limit)
    body["ok"] = body.get("ok", True)
    return body


@router.post("/names")
def chip_rank_fill_names():
    """给本周已算好的结果**单独补齐名称**（一次外部查询，不必重跑重型计算）。

    榜单计算时名称取不到（网络抖动等）会留空，用这个接口补一次即可。
    """
    body = svc.fill_names()
    body["ok"] = body.get("ok", True)
    return body


@router.post("/refresh")
def chip_rank_refresh(
        force: bool = False,
        top_n: int = Query(svc.DEFAULT_TOP, ge=1, le=1000,
                           description="每期取 SCR90 最小的 N 只（=最集中）"),
        weeks: int = Query(svc.DEFAULT_WEEKS, ge=2, le=12,
                           description="回看几期（连续全勤的判定长度）"),
        window: int = Query(svc.DEFAULT_DAYS, ge=60, le=1000,
                            description="筹码窗口（交易日）"),
        bins: int = Query(svc.DEFAULT_BINS, ge=20, le=200, description="价格分箱数"),
        chg_days: int = Query(svc.DEFAULT_CHG_DAYS, ge=5, le=120,
                              description="离榜时看多少交易日的涨幅"),
        launch: float = Query(svc.DEFAULT_LAUNCH, ge=0.0, le=100.0,
                              description="涨幅≥该值判为上涨离榜（%）")):
    """触发（或复用）本周计算，立即返回状态——全量十几分钟，跑在后台线程。

    force=true 忽略已有结果重算；跨周会自动重算，不必 force。
    """
    return {"ok": True, **svc.ensure(
        force=force, top_n=top_n, weeks=weeks, window=window, bins=bins,
        chg_days=chg_days, launch=launch)}
