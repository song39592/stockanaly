# -*- coding: utf-8 -*-
"""策略回测 HTTP 接口。

路由前缀 /api/strategies：
    GET  ""              策略清单（含 description / params 规格）
    GET  /codes          本地已下载股票代码列表（供「回测范围」选股票池）
    POST /backtest       运行回测：{"strategy_id","params","codes","use_all",
                                    "start","end","initial_capital","commission","benchmark"}
    GET  /backtest/trades  逐笔明细：?rid=<result_id>&code=<代码>（回测后按需展开）

错误统一返回 {"ok": false, "detail": …, "error": {"code", "message"}}（HTTP 400，
形状由 apiutil 统一；见第 11 项）。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import apiutil
import strategies
import strategies.core.backtest as backtest
import strategies.core.data as sdata
import strategies.core.registry as registry
from strategies import report

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
    window: Optional[int] = None   # 最近 N 个交易日（给了则覆盖 start/end，用于「近期成果」）
    initial_capital: float = 100000.0
    commission: float = 0.0003   # 单边佣金比例（如 0.0003 = 万三）
    benchmark: Optional[str] = None


_CODE_ERR = {
    KeyError: apiutil.CODE_UNKNOWN_STRATEGY,
    ValueError: "BAD_PARAM",
    RuntimeError: "NO_DATA",
}

_guard = apiutil.guard


@router.post("/backtest")
@_guard(code=_CODE_ERR)
def do_backtest(req: BacktestRequest):
    codes = [str(c).strip() for c in (req.codes or []) if str(c).strip()]
    if req.use_all:
        codes = sdata.list_universe_codes()
    # window 交给编排层处理（取数会额外向前预热，只统计最近 N 日）
    result = backtest.run_backtest(
        req.strategy_id, req.params, codes, req.start, req.end,
        req.initial_capital, req.commission, req.benchmark, req.window,
    )
    # 回测跑完**自动落盘一份报告**（策略 + 覆盖范围 + 指标 + 逐票汇总），
    # 便于事后对比不同策略 / 不同区间 —— 原来结果只在一次响应里，关掉就没了。
    # ⚠️ 写盘失败绝不影响这次回测的结果返回（report.save_report 内部全 try）。
    saved = report.save_report(result)
    result["report"] = {"saved": bool(saved.get("ok")),
                        "path": saved.get("path"),
                        "error": saved.get("error")}
    return result


@router.post("/backtest/start")
@_guard(code=_CODE_ERR)
def do_backtest_start(req: BacktestRequest):
    """**启动后台回测并立即返回**（跑全市场请用这个）。

    为什么要有这个：全市场 5000+ 只冷缓存读库要几分钟，而 `/backtest` 是一次
    同步请求 + 前端固定超时 —— 必然「请求被中止：操作超时」。本接口立即返回，
    由前端轮询 `/backtest/progress` 看**已测只数 / 总数**，完成后用
    `/backtest/result` 取结果。

    同一时间只允许一个任务（两个全市场并行会互相拖慢）。小股票池仍可用同步
    的 `/backtest`，更快。
    """
    codes = [str(c).strip() for c in (req.codes or []) if str(c).strip()]
    if req.use_all:
        codes = sdata.list_universe_codes()
    if not codes:
        raise ValueError("股票池为空，请先选择回测范围")
    out = backtest.start_task(
        req.strategy_id, req.params, codes, req.start, req.end,
        req.initial_capital, req.commission, req.benchmark, req.window,
    )
    if not out.get("ok"):
        return JSONResponse(status_code=409,
                            content={"ok": False,
                                     "error": {"code": "BUSY",
                                               "message": out.get("error") or "已有回测在运行"}})
    return out


@router.get("/backtest/progress")
def do_backtest_progress():
    """轮询回测进度：阶段 + 已测/总数 + 耗时。

    `phase=loading` 时 done/total 是**准确的已测只数**（取数逐票回调）；
    `phase=running` 是策略计算阶段 —— 策略函数自己遍历标的、外部无法细分，
    此时 done 保持 total，**不给假的百分比**。
    """
    return {"ok": True, "state": backtest.task_state()}


@router.get("/backtest/result")
def do_backtest_result():
    """取最近一次**已完成**的回测结果；还在跑 / 从未跑过则 ok=false。"""
    out = backtest.task_result()
    if out is None:
        return {"ok": False, "state": backtest.task_state(),
                "error": {"code": "NOT_READY", "message": "还没有已完成的回测结果"}}
    return out


@router.get("/backtest/reports")
def list_backtest_reports(limit: int = 50):
    """已保存的回测报告索引（新到旧）：横向对比历史回测用。"""
    return report.list_reports(limit)


@router.post("/recommend")
@_guard(code=_CODE_ERR)
def do_recommend(req: BacktestRequest):
    """当前策略推荐：按最新一日信号给出买入 / 卖出 / 持股三档建议。

    入参与 /backtest 一致（window 除外，本接口不用于短窗口回测）。
    """
    codes = [str(c).strip() for c in (req.codes or []) if str(c).strip()]
    if req.use_all:
        codes = sdata.list_universe_codes()
    return backtest.run_recommend(
        req.strategy_id, req.params, codes, req.start, req.end,
        req.initial_capital, req.commission,
    )


@router.get("/backtest/trades")
@_guard(code=apiutil.CODE_EXPIRED)
def do_backtest_trades(rid: str, code: str):
    """单只股票的逐笔交易明细（前端展开个股行时按需调用）。

    价格为原始收盘（未复权）、收益为后复权口径；rid 对应最近一次回测，
    过期返回 400（前端提示重跑）。
    """
    return {"ok": True, "code": code, "trades": backtest.trades_for(rid, code)}
