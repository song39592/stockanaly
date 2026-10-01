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
import strategies.backtest as backtest
import strategies.data as sdata

router = APIRouter(prefix="/api/strategies", tags=["策略回测"])


@router.get("")
def list_strategies():
    """策略清单：每项含 id / name / category / description / params。"""
    return strategies.list_strategies()


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
        )
    except (KeyError, ValueError, RuntimeError) as exc:
        code = _CODE_ERR[type(exc)]
        return JSONResponse(status_code=400,
                            content={"ok": False, "error": {"code": code, "message": str(exc)}})
