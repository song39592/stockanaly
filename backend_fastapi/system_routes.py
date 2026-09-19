# -*- coding: utf-8 -*-
"""系统设置接口：数据目录的查询与修改、数据完整性校验。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import config
import crypto
import integrity
import storage

router = APIRouter(prefix="/api/system", tags=["系统设置"])


class DataDirRequest(BaseModel):
    data_dir: str = Field(min_length=1, max_length=260)


@router.get("/storage")
def storage_info():
    """当前数据目录、各项占用与是否使用默认位置（供首页展示）。"""
    return {"ok": True, **storage.snapshot()}


@router.post("/storage")
def storage_update(req: DataDirRequest):
    """修改数据目录：写入 backend_fastapi/.env，重启后端后生效。

    只改配置、不搬数据；重启时会把**项目内旧位置**的数据自动补到新目录。
    """
    result = storage.update_data_dir(req.data_dir)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error"))
    return result


@router.get("/integrity")
def integrity_check():
    """重新校验全部数据库的完整性（签名有效性 / 写入时间吻合 / 结构版本）。

    **只读操作**，不会修改任何数据；发现问题时只报告，是否重建由使用者决定。
    """
    return integrity.check_all()


@router.post("/integrity/resign")
def integrity_resign():
    """把当前状态重新记为可信基线。

    ⚠️ 这会**接受当前的数据状态**——仅当你明确知道变动来源时使用
    （例如刚做过维护操作或数据迁移），不要用它来「消掉报警」。
    """
    return integrity.resign()


@router.get("/integrity/deep")
def integrity_deep(code: str = ""):
    """深校验：逐股重算指纹并比对，返回不可信清单（可比对全部，也可只查一只）。

    比结构级校验更彻底，能定位到具体哪只股票的日K被改动过；耗时随数据量增长。
    """
    codes = [code.strip()] if code.strip() else None
    return integrity.deep_check(codes)


@router.post("/integrity/seal")
def integrity_seal():
    """把校验密钥改为**本机 DPAPI 密封**存放（绑定本机 + 当前用户）。

    密封后拷到其他机器将无法解开，按既定原则重新抓取即可。
    """
    result = crypto.seal_now()
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error"))
    return result


@router.get("/integrity/seal")
def integrity_seal_state():
    """查看密钥存放状态（是否已绑定本机、能否正常解开）。"""
    return {"ok": True, **crypto.seal_state()}


@router.get("/llm-key")
def llm_key_state():
    """查看 LLM 密钥的存放状态（是否明文、能否解开）——**不返回密钥本身**。"""
    return {"ok": True, **config.llm_key_state()}


@router.post("/llm-key/seal")
def llm_key_seal():
    """把 .env 里的明文 `LLM_API_KEY` 改为**本机 DPAPI 密封**存放。

    与其他密钥统一口径：不留明文。密封值绑定本机（换机器 / 换用户后解不开，
    需重新填写），因此只在你显式调用时执行，程序不会自动改写配置文件。
    明文读取始终兼容，转换后手工填写依旧有效。
    """
    result = config.seal_llm_key()
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error"))
    return result


class RebuildRequest(BaseModel):
    code: str = Field(min_length=1, max_length=10)


@router.post("/rebuild")
def rebuild(req: RebuildRequest):
    """按代码**全量重新抓取**日K并整体替换（用于数据被判定为不可信时恢复）。

    用 `refetch_bars` 而非增量 `sync_bars`：增量只补最新一段，
    改动若发生在历史区间就永远修不好（这正是本接口此前失效的原因）。
    与 `resign` 不同，本接口用**数据源的真实数据**覆盖，而不是接受当前状态。
    抓取失败时抛错并不动库中数据。
    """
    import price_service

    code = req.code.strip().zfill(6)
    if not code.isdigit() or len(code) != 6:
        raise HTTPException(status_code=400, detail="股票代码必须是 6 位数字")
    try:
        result = price_service.refetch_bars(code)
    except Exception as exc:                    # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"重新抓取失败：{exc}")
    return {"ok": True, **result}
