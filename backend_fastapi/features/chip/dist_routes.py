# -*- coding: utf-8 -*-
"""筹码分布（筹码峰）的 HTTP 接口（独立模块，不放在 main.py 里）。

本模块只负责 HTTP 层：参数校验与响应封装；算法在 `chip_formulas/` 包里
（一个文件一个公式、文件名即 id、LOAD 时校验，详见该包 ARCHITECTURE.md）。
main.py 只需 import 本模块并 include_router。

路由前缀 /api/chip/dist：
    GET /formulas                        可用公式清单（供前端下拉）
    GET /?code=&formula=&params=&...     筹码分布（按价位的直方图 + 逐日快照）
    GET /float/{code}?force=             单独查流通股本（展示 / 排障）

与 SCR 选股（chip_routes.py，/api/chip/scr）同属筹码体系但彼此独立：
前者是「按日期的三档分类表」，这里是「按价位的分布图」，数据结构不通用。
"""
import json

from fastapi import APIRouter, HTTPException, Query

from features.chip import formulas as chip_formulas
from core.periods import normalize

router = APIRouter(prefix="/api/chip/dist", tags=["筹码体系 · 筹码分布"])


@router.get("/formulas")
def chip_dist_formulas():
    """可用筹码公式清单（LOAD 校验通过者）；default 供前端默认选中。"""
    return {
        "items": chip_formulas.list_formulas(),
        "default": chip_formulas.base.DEFAULT_FORMULA_ID,
    }


@router.get("")
def chip_dist(code: str = Query(..., min_length=6, max_length=6,
                                description="6 位股票代码"),
              formula: str | None = None,
              params: str | None = None,
              start: str | None = None,
              end: str | None = None,
              adjust: str = "qfq",
              days: int = 0,
              bins: int = chip_formulas.base.DEFAULT_BINS,
              period: str = "day"):
    """筹码分布：按价格分箱的占比（合计 100）+ 逐日快照 + 成本统计。

    params 为 JSON 对象字符串（公式参数，如 {"decay": 1.5}），缺省用公式默认值。
    period 与 K 线周期一致（周线时换手率即周换手率）。
    数据不足 / 缺流通股本时返回 200 + ok=false（便于前端在窗口内直接显示原因）。
    """
    if adjust not in ("qfq", "hfq", "raw"):
        raise HTTPException(status_code=400, detail=f"不支持的复权口径：{adjust}")
    try:
        period = normalize(period)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    parsed = None
    if params:
        try:
            parsed = json.loads(params)
        except ValueError:
            raise HTTPException(status_code=400, detail="params 不是合法的 JSON 对象")
        if not isinstance(parsed, dict):
            raise HTTPException(status_code=400, detail="params 必须是 JSON 对象")
    try:
        return chip_formulas.compute(
            code, formula_id=formula, params=parsed, start=start, end=end,
            adjust=adjust, days=days or None, bins=bins, period=period)
    except KeyError as exc:                     # 公式 id 不存在
        raise HTTPException(status_code=400, detail=str(exc).strip("'"))
    except RuntimeError as exc:                 # 行情不足 / 缺流通股本：业务性失败
        return {"ok": False, "code": code, "error": str(exc)}
    except Exception as exc:                    # noqa: BLE001 - 其余按服务端错误暴露
        raise HTTPException(status_code=500, detail=f"筹码分布计算失败：{exc}")


@router.get("/float/{code}")
def chip_float_shares(code: str, force: bool = False):
    """查询 / 强制刷新某只股票的流通股本（单位股）。"""
    result = chip_formulas.float_shares_info(code, force=force)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("error"))
    return result
