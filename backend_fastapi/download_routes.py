"""历史数据遍历下载的 HTTP 接口。

任务在后端后台跑（worker 池 + 限速），前端只需轮询进度：
    POST /api/history/download/start            启动任务
    GET  /api/history/download/{id}             进度 + 失败清单
    POST /api/history/download/{id}/pause|resume|cancel|retry-failed
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import download_service

router = APIRouter(prefix="/api/history/download", tags=["历史数据下载"])


class StartRequest(BaseModel):
    scope: str = Field(default="all", pattern=r"^(all|pool)$")
    mode: str = Field(default="full", pattern=r"^(full|incremental)$")
    source: str = Field(default="online", pattern=r"^(online|tdx)$")
    start_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    concurrency: int = Field(default=4, ge=1, le=8)
    rate_limit: float = Field(default=3.0, gt=0, le=20)
    with_reference: bool = True
    force_reference: bool = False


class SettingsRequest(BaseModel):
    auto_update: bool | None = None
    idle_download: bool | None = None
    idle_concurrency: int | None = Field(default=None, ge=1, le=2)
    tdx_path: str | None = None


# 注意：下面两个固定路径必须注册在 `/{task_id}` 之前，否则会被当成 task_id 吃掉
@router.get("/universe")
def universe(scope: str = "all", mode: str = "full", source: str = "online",
             start_date: str | None = None):
    """预览某个范围会下载多少只、大约多大，供前端做容量提示（含磁盘可用空间）。"""
    try:
        return {"ok": True, **download_service.universe_view(scope, mode, source, start_date)}
    except Exception as exc:                      # noqa: BLE001 - 转成可读接口错误
        raise HTTPException(status_code=500, detail=f"股票清单获取失败：{exc}")


@router.get("/list")
def list_tasks(limit: int = 20):
    return {"ok": True, "tasks": download_service.list_tasks(limit)}


@router.post("/start")
def start(req: StartRequest):
    try:
        task_id = download_service.start_task(
            scope=req.scope, source=req.source, mode=req.mode, start_date=req.start_date,
            concurrency=req.concurrency, rate_limit=req.rate_limit,
            with_reference=req.with_reference, force_reference=req.force_reference,
        )
    except Exception as exc:                      # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"任务启动失败：{exc}")
    return {"ok": True, "task_id": task_id, "task": download_service.task_view(task_id)}


@router.get("/settings")
def get_settings():
    return {"ok": True, "settings": download_service.settings_view()}


@router.post("/settings")
def save_settings(req: SettingsRequest):
    """保存后台开关（每日自动更新 / 闲时补历史 / 闲时并发）。"""
    try:
        return {"ok": True, "settings": download_service.update_settings(
            auto_update=req.auto_update, idle_download=req.idle_download,
            idle_concurrency=req.idle_concurrency, tdx_path=req.tdx_path)}
    except Exception as exc:                      # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"设置保存失败：{exc}")


@router.get("/tdx/status")
def tdx_status(path: str | None = None):
    """通达信目录检测（设置页用）：是否有效、覆盖多少只、数据到哪天。"""
    try:
        return {"ok": True, **download_service.tdx_status(path)}
    except Exception as exc:                      # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"通达信目录检测失败：{exc}")


@router.get("/auto-state")
def auto_state(refresh: bool = False):
    """后台开关的可开启状态：已下载多少只、有多少只落后（决定是否允许勾选）。"""
    try:
        return {"ok": True, **download_service.auto_state(force=refresh)}
    except Exception as exc:                      # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"覆盖度检测失败：{exc}")


@router.get("/{task_id}")
def task_status(task_id: str):
    task = download_service.task_view(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="下载任务不存在")
    return {"ok": True, "task": task}


@router.post("/{task_id}/pause")
def pause(task_id: str):
    return _control(task_id, download_service.pause)


@router.post("/{task_id}/resume")
def resume(task_id: str):
    return _control(task_id, download_service.resume)


@router.post("/{task_id}/cancel")
def cancel(task_id: str):
    return _control(task_id, download_service.cancel)


@router.post("/{task_id}/retry-failed")
def retry_failed(task_id: str):
    return _control(task_id, download_service.retry_failed)


def _control(task_id: str, action):
    try:
        task = action(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="下载任务不存在")
    except Exception as exc:                      # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"操作失败：{exc}")
    return {"ok": True, "task": task}
