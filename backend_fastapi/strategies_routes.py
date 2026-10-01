# -*- coding: utf-8 -*-
"""策略回测 HTTP 接口。

路由前缀 /api/strategies：
    GET  ""              策略清单（含 description / params 规格）
    GET  /codes          本地已下载股票代码列表（供「回测范围」选股票池）
    POST /backtest       运行回测：{"strategy_id","params","codes","use_all",
                                    "start","end","initial_capital","commission","benchmark"}

错误统一返回 {"ok": False, "error": {"code", "message"}}（HTTP 400）。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import strategies
import strategies.core.backtest as backtest
import strategies.core.data as sdata
import strategies.core.registry as registry

router = APIRouter(prefix="/api/strategies", tags=["策略回测"])


@router.get("")
def list_strategies():
    """策略清单：仅返回**通过接口校验**的策略（每项含 id / name / category / description / params）。"""
    return strategies.list_strategies()


@router.get("/validation")
def validation():
    """校验报告：全部策略文件的校验状态（含未通过文件的错误与最后修改时间）。"""
    return registry.validation_report()


@router.post("/refresh")
def refresh():
    """强制重新扫描并校验全部策略文件（文件改动后无需重启即可生效）。"""
    registry.scan(force=True)
    return registry.validation_report()


@router.get("/codes")
def list_codes():
    """本地已下载（有日 K）的股票代码列表。"""
    return {"codes": sdata.list_universe_codes()}


class BacktestRequest(BaseModel):
    strategy_id: str
    params: dict = {}
    codes: list[str] = []        # 自定义代码；为空且 use_all=True 时取全部本地
    use_all: bool = False        # True → 股票池 = 本地全部已下载股票
    start: Optional[str] = None
    end: Optional[str] = None
    initial_capital: float = 100000.0
    commission: float = 0.0003   # 单边佣金比例（如 0.0003 = 万三）
    benchmark: Optional[str] = None


_CODE_ERR = {
    KeyError: "UNKNOWN_STRATEGY",
    ValueError: "BAD_PARAM",
    RuntimeError: "NO_DATA",
}


@router.post("/backtest")
def do_backtest(req: BacktestRequest):
    codes = [str(c).strip() for c in (req.codes or []) if str(c).strip()]
    if req.use_all:
        codes = sdata.list_universe_codes()
    try:
        return backtest.run_backtest(
            req.strategy_id, req.params, codes, req.start, req.end,
            req.initial_capital, req.commission, req.benchmark,
            allow_sample=req.use_all,   # 「全部本地」超上限时自动抽样，不直接报错
        )
    except (KeyError, ValueError, RuntimeError) as exc:
        code = _CODE_ERR[type(exc)]
        return JSONResponse(status_code=400,
                            content={"ok": False, "error": {"code": code, "message": str(exc)}})
