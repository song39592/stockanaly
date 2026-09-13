"""筹码体系 · SCR 选股的 HTTP 接口（独立模块，不放在 main.py 里）。

本模块只负责 HTTP 层：参数校验与响应封装；具体逻辑在 chip_service.py。
main.py 只需 `from chip_routes import router as chip_router` 并 `app.include_router(chip_router)`，
新增筹码体系相关接口时只改本文件，不必再动 main.py。

路由前缀 /api/chip/scr：
    POST /upload   导入「临时条件股YYYYMMDD.xls」（文件名解析默认日期）
    GET  /files    已导入文件列表（含日期与是否手动修改）
    POST /date     手动修改某个文件的所属日期
    POST /delete   删除某个已导入文件
    POST /analyze  刷新计算：最近 5 周三档分类（结果写入 chip_data/processed/）
"""

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

import chip_service

router = APIRouter(prefix="/api/chip/scr", tags=["筹码体系 · SCR 选股"])


class ChipFileRequest(BaseModel):
    file: str


class ChipDateRequest(BaseModel):
    file: str
    date: str = ""


class ChipAnalyzeRequest(BaseModel):
    full_weeks: int | None = None
    min_weeks_on: int | None = None
    min_market: float | None = None
    max_market: float | None = None
    launch_threshold: float | None = None


@router.post("/upload")
async def chip_scr_upload(file: UploadFile = File(...)):
    """导入 SCR 导出文件；文件名中的 YYYYMMDD 解析为默认日期（可在页面手动修改）。"""
    try:
        content = await file.read()
        result = chip_service.save_upload(file.filename or "", content)
    except Exception as exc:                    # noqa: BLE001 - 统一转成可读错误
        raise HTTPException(status_code=500, detail=f"导入失败：{exc}")
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error"))
    return {**result, "files": chip_service.list_files()}


@router.get("/files")
def chip_scr_files():
    """已导入文件列表（含解析日期、以及该日期是否被手动改过）。"""
    return {"ok": True, "files": chip_service.list_files()}


@router.post("/date")
def chip_scr_set_date(req: ChipDateRequest):
    """手动修改某个文件的所属日期（覆盖文件名解析出的默认值）。"""
    result = chip_service.set_file_date(req.file, req.date)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error"))
    return {**result, "files": chip_service.list_files()}


@router.post("/delete")
def chip_scr_delete(req: ChipFileRequest):
    """删除已导入的数据文件。"""
    result = chip_service.delete_file(req.file)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error"))
    return {**result, "files": chip_service.list_files()}


@router.post("/analyze")
def chip_scr_analyze(req: ChipAnalyzeRequest | None = None):
    """刷新计算：合并最近 5 周数据输出三档分类，结果同时写入 chip_data/processed/。

    数据不足最近 5 周时返回 ok=False 与缺口说明（HTTP 仍为 200，便于前端渲染缺口表）。
    """
    params = req.model_dump() if req else {}
    try:
        result = chip_service.analyze(params)
    except Exception as exc:                    # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"计算失败：{exc}")
    return {**result, "files": chip_service.list_files()}
