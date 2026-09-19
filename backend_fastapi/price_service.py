# -*- coding: utf-8 -*-
"""股价采集：把外部行情数据源的日 K 与复权参考数据写入 `price_store`。

与 `price_store` 的分工：
  - 本模块**只负责拉取、清洗与降级**（有网络、有重试）；
  - 复权计算、读写接口都在 `price_store`。
  这样回测等消费方只依赖 `price_store`，完全不碰网络。

数据源：
  - 日 K：东方财富 `stock_zh_a_hist`，失败降级腾讯 `stock_zh_a_hist_tx`
  - 复权因子：新浪 `stock_zh_a_daily(adjust="hfq-factor")`
  - 除权除息明细：新浪 `stock_history_dividend_detail`
"""
from __future__ import annotations

import datetime as dt
from typing import Any

import akshare as ak

import price_store
from market_service import _ak

# 同步落盘的三套口径（daily_bars 主键含 adjust，可同表并存）：
#   qfq 前复权：以最新日为基准，价格接近现价，供页面展示；
#   hfq 后复权：以上市首日为基准，**历史数据不随新的除权变化**，供策略回测（结果可复现）；
#   raw 不复权：原始成交价，用于准确还原当时的成交价与持仓成本。
BAR_ADJUSTS = ("qfq", "hfq", "raw")
_AK_ADJUST = {"qfq": "qfq", "hfq": "hfq", "raw": ""}     # akshare 用空串表示不复权
BACKFILL_DAYS = 730                                       # 首次同步的默认回看窗口（约 2 年）
OVERLAP_DAYS = 14                                         # 增量重叠窗口，覆盖数据源事后修订


def market_symbol(code: str) -> str:
    """6 位代码 → 带市场前缀（sh / sz / bj）的符号。"""
    if code.startswith(("6", "5")):
        return "sh" + code
    if code.startswith(("0", "1", "2", "3")):
        return "sz" + code
    return "bj" + code


def _number(value: Any) -> float | None:
    try:
        num = float(value)
        return None if num != num else num
    except (TypeError, ValueError):
        return None


def _date_text(value: Any) -> str:
    """日期归一成 YYYY-MM-DD；空值 / NaT / nan 一律返回空串（避免 "NaT" 这类字符串入库）。"""
    text = str(value or "")[:10].replace("/", "-")
    return "" if text in ("NaT", "nat", "None", "nan", "NaN") else text


def normalize_bars(frame) -> list[dict[str, Any]]:
    """akshare 日线 DataFrame → 统一字段的 bar 列表。

    兼容三类列名：东财（中文）、新浪 / 腾讯（英文）。
    """
    if frame is None or getattr(frame, "empty", True):
        return []
    result = []
    for _, row in frame.iterrows():
        day = _date_text(row.get("日期", row.get("date")))
        if not day:
            continue
        result.append({
            "date": day,
            "open": _number(row.get("开盘", row.get("open"))),
            "high": _number(row.get("最高", row.get("high"))),
            "low": _number(row.get("最低", row.get("low"))),
            "close": _number(row.get("收盘", row.get("close"))),
            "volume": _number(row.get("成交量", row.get("volume"))),
            "amount": _number(row.get("成交额", row.get("amount"))),
            "amplitude": _number(row.get("振幅")),
            "change_pct": _number(row.get("涨跌幅")),
            "change_amount": _number(row.get("涨跌额")),
            "turnover": _number(row.get("换手率", row.get("turnover"))),
        })
    return result


def sync_bars(code: str, adjust: str = "raw") -> dict[str, Any]:
    """同步单一口径（raw / hfq / qfq）的日 K，按已落盘的最新日期增量补齐。"""
    store_adjust = price_store._store_adjust(adjust)
    ak_adjust = _AK_ADJUST.get(store_adjust, store_adjust)
    today = dt.date.today()
    latest = price_store.latest_bar_date(code, store_adjust)
    if latest:
        start = dt.date.fromisoformat(latest) - dt.timedelta(days=OVERLAP_DAYS)
    else:
        start = today - dt.timedelta(days=BACKFILL_DAYS)
    source = "东方财富"
    frame = _ak(
        ak.stock_zh_a_hist,
        symbol=code,
        period="daily",
        start_date=start.strftime("%Y%m%d"),
        end_date=today.strftime("%Y%m%d"),
        adjust=ak_adjust,
        timeout=30,
    )
    if frame is None or getattr(frame, "empty", True):
        source = "腾讯证券"
        frame = _ak(
            ak.stock_zh_a_hist_tx,
            symbol=market_symbol(code),
            start_date=start.strftime("%Y%m%d"),
            end_date=today.strftime("%Y%m%d"),
            adjust=ak_adjust,
            timeout=35,
        )
    bars = normalize_bars(frame)
    if not bars:
        raise RuntimeError("行情数据源未返回日 K")
    count = price_store.upsert_bars(code, bars, store_adjust, source=source)
    return {"count": count, "start": bars[0]["date"], "end": bars[-1]["date"],
            "source": source, "adjust": store_adjust}


def sync_all_adjusts(code: str) -> dict[str, Any]:
    """一次同步全部口径（qfq / hfq / raw），单口径失败不影响其他口径。

    返回结构兼容单口径调用方的旧字段（count / start / end / source 取自 qfq），
    各口径明细见 adjusts，失败原因见 errors。
    """
    results: dict[str, Any] = {}
    errors: list[str] = []
    for adjust in BAR_ADJUSTS:
        try:
            results[adjust] = sync_bars(code, adjust)
        except Exception as exc:          # noqa: BLE001 - 单口径失败不影响其余口径
            results[adjust] = None
            errors.append(f"{adjust}：{exc}")
    primary = results.get("qfq") or {}
    return {
        "count": sum((item or {}).get("count") or 0 for item in results.values()),
        "start": primary.get("start"),
        "end": primary.get("end"),
        "source": primary.get("source"),
        "adjusts": results,
        "errors": errors,
    }


def sync_factors(code: str) -> dict[str, Any]:
    """采集后复权因子（新浪 hfq-factor）并落盘。

    **只采集 hfq_factor**：其基准是「上市首日」，历史值不随新的除权变化；
    qfq_factor 以「最新日」为基准、每天都在变，入库等于又引入了会变的数据。
    前复权价请在读取时现算（见 `price_store.load_bars`）。
    """
    frame = _ak(ak.stock_zh_a_daily, symbol=market_symbol(code), adjust="hfq-factor", timeout=30)
    if frame is None or getattr(frame, "empty", True):
        raise RuntimeError("复权因子数据源未返回数据")
    rows = []
    for _, row in frame.iterrows():
        ex_date = _date_text(row.get("date"))
        factor = _number(row.get("hfq_factor"))
        if ex_date and factor:
            rows.append({"ex_date": ex_date, "hfq_factor": factor})
    count = price_store.upsert_factors(code, rows, source="新浪")
    if not count:
        raise RuntimeError("复权因子解析后为空")
    return {"count": count, "source": "新浪"}


def sync_dividends(code: str) -> dict[str, Any]:
    """采集除权除息明细（分红送转）并落盘。

    用途：审计复权因子、与数据源对拍口径、计算税后真实持仓成本。
    送股 / 转增 / 派息均为「每 10 股」口径（与数据源一致）。
    """
    frame = _ak(ak.stock_history_dividend_detail, symbol=code, indicator="分红", timeout=30)
    if frame is None or getattr(frame, "empty", True):
        return {"count": 0, "source": "新浪", "note": "无分红送转记录"}
    rows = []
    for _, row in frame.iterrows():
        ex_date = _date_text(row.get("除权除息日"))
        if not ex_date:                    # 只有已实施（有除权日）的方案才能用于复权
            continue
        rows.append({
            "ex_date": ex_date,
            "announce_date": _date_text(row.get("公告日期")),
            "record_date": _date_text(row.get("股权登记日")),
            "bonus_per_10": _number(row.get("送股")),
            "transfer_per_10": _number(row.get("转增")),
            "cash_per_10": _number(row.get("派息")),
            "progress": str(row.get("进度") or ""),
        })
    count = price_store.upsert_dividends(code, rows, source="新浪")
    return {"count": count, "source": "新浪"}


def sync_reference(code: str, force: bool = False) -> dict[str, Any]:
    """采集「复权因子 + 除权明细」这两类参考数据。

    与日 K 不同，它们只在发生除权时才变化，因此默认**只在首次采集**（已有因子则跳过），
    避免每次导入股票池都为每只股票多发两次请求；需要补救时用 force=True。
    """
    factors = price_store.list_factors(code)
    dividends = price_store.list_dividends(code)
    if not force and factors:
        return {"cached": True, "factors": len(factors), "dividends": len(dividends)}
    errors: list[str] = []
    result: dict[str, Any] = {"cached": False}
    for name, producer in (("factors", sync_factors), ("dividends", sync_dividends)):
        try:
            result[name] = producer(code)
        except Exception as exc:          # noqa: BLE001 - 参考数据失败不影响日 K
            result[name] = None
            errors.append(f"{name}：{exc}")
    result["errors"] = errors
    return result
