"""盘面及板块分析的五类数据接口（独立模块，不放在 main.py 里）。

数据采集与降级逻辑都在 market_service.py，本模块只负责路由与缓存清理，
新增盘面相关接口时只改这里，不必再动 main.py。

路由前缀 /api/market：
    GET /global     ① 外围环境（美股 / 港股 / 大宗商品 / 费城半导体）
    GET /capital    ② 大盘资金（两市成交 / 涨跌家数 / 主力净流向 / 特大单方向）
    GET /sectors    ③ 板块β（行业与概念资金流 Top10 / 申万一级行业涨跌）
    GET /limit-up   ④ 连板梯队（连板结构 + 晋级率）
    GET /big-loss   ⑤ 大面股（炸板池 + 跌停池）

均支持 ?date=YYYYMMDD 指定交易日（①②③ 仅部分口径有历史数据源，② 恒为实时快照）
与 ?force=1 跳过进程内缓存。
"""

from fastapi import APIRouter

import market_service

router = APIRouter(prefix="/api/market", tags=["盘面及板块分析"])


@router.get("/global")
def market_global(date: str = "", force: bool = False):
    """① 外围环境：美股 / 港股 / 大宗商品 / 费城半导体。date 为空时返回实时行情。"""
    if force:
        market_service.clear_cache("global_market")
    return market_service.global_market(date or None)


@router.get("/capital")
def market_capital(date: str = "", force: bool = False):
    """② 大盘资金：两市成交、涨跌家数、主力净流向、特大单方向。"""
    if force:
        market_service.clear_cache("capital_flow")
    return market_service.capital_flow(date or None)


@router.get("/sectors")
def market_sectors(date: str = "", force: bool = False):
    """③ 板块β：行业 / 概念板块资金流 Top10、申万一级行业涨跌。date 为空时返回实时。"""
    if force:
        market_service.clear_cache("sector_beta")
    return market_service.sector_beta(date or None)


@router.get("/limit-up")
def market_limit_up(date: str = "", force: bool = False):
    """④ 连板梯队：连板结构与晋级率。date 为空时自动取最近交易日。"""
    if force:
        market_service.clear_cache("limit_up")
    return market_service.limit_up_ladder(date or None)


@router.get("/big-loss")
def market_big_loss(date: str = "", force: bool = False):
    """⑤ 大面股：炸板池 + 跌停池。date 为空时自动取最近交易日。"""
    if force:
        market_service.clear_cache("big_loss")
    return market_service.big_loss(date or None)
