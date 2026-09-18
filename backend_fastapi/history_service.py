"""池内股票的行情与消息增量同步。"""

from __future__ import annotations

import datetime as dt
import threading
import time
import uuid
from typing import Any

import akshare as ak

import history_store
from collectors import get_announcements, get_news
from market_service import _ak

EVENT_TTL_HOURS = 12
_jobs_lock = threading.Lock()
_active_dates: set[str] = set()


def _number(value: Any) -> float | None:
    try:
        num = float(value)
        return None if num != num else num
    except (TypeError, ValueError):
        return None


def _date_text(value: Any) -> str:
    text = str(value or "")[:10]
    return text.replace("/", "-")


def normalize_bars(frame) -> list[dict[str, Any]]:
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


def market_symbol(code: str) -> str:
    if code.startswith(("6", "5")):
        return "sh" + code
    if code.startswith(("0", "1", "2", "3")):
        return "sz" + code
    return "bj" + code


def sync_bars(code: str, adjust: str = "qfq") -> dict[str, Any]:
    today = dt.date.today()
    latest = history_store.latest_bar_date(code, adjust)
    if latest:
        start = dt.date.fromisoformat(latest) - dt.timedelta(days=14)
    else:
        start = today - dt.timedelta(days=730)
    source = "东方财富"
    frame = _ak(
        ak.stock_zh_a_hist,
        symbol=code,
        period="daily",
        start_date=start.strftime("%Y%m%d"),
        end_date=today.strftime("%Y%m%d"),
        adjust=adjust,
        timeout=30,
    )
    if frame is None or getattr(frame, "empty", True):
        source = "腾讯证券"
        frame = _ak(
            ak.stock_zh_a_hist_tx,
            symbol=market_symbol(code),
            start_date=start.strftime("%Y%m%d"),
            end_date=today.strftime("%Y%m%d"),
            adjust=adjust,
            timeout=35,
        )
    bars = normalize_bars(frame)
    count = history_store.upsert_bars(code, bars, adjust)
    if not bars:
        raise RuntimeError("行情数据源未返回日 K")
    return {"count": count, "start": bars[0]["date"], "end": bars[-1]["date"], "source": source}


def events_are_fresh(code: str) -> bool:
    state = history_store.event_state(code)
    if not state:
        return False
    try:
        fetched = dt.datetime.fromisoformat(state["fetched_at"])
        return dt.datetime.now(dt.timezone.utc) - fetched < dt.timedelta(hours=EVENT_TTL_HOURS)
    except (TypeError, ValueError):
        return False


def sync_events(code: str, force: bool = False) -> dict[str, Any]:
    if not force and events_are_fresh(code):
        return {"cached": True, "count": len(history_store.list_events(code))}
    announcements, ann_note = get_announcements(code, days=180)
    news, news_note = get_news(code, days=45)
    events = [{
        "kind": "announcement",
        "title": item.get("title"),
        "published_at": item.get("date"),
        "source": item.get("source"),
        "source_url": item.get("source_url"),
        "summary": item.get("type") or "公司公告",
    } for item in announcements]
    events.extend({
        "kind": "news",
        "title": item.get("title"),
        "published_at": item.get("date"),
        "source": item.get("source") or "东方财富",
        "source_url": item.get("source_url"),
        "summary": item.get("content") or "",
    } for item in news)
    notes = "；".join(x for x in (ann_note, news_note) if x)
    count = history_store.upsert_events(code, events, notes)
    return {"cached": False, "count": count, "note": notes}


def sync_stock(code: str, include_events: bool = False, force_events: bool = False) -> dict[str, Any]:
    errors = []
    bar_result = None
    event_result = None
    try:
        bar_result = sync_bars(code)
    except Exception as exc:  # noqa: BLE001 - 单股失败不影响整个任务
        errors.append(f"K线：{exc}")
    if include_events:
        try:
            event_result = sync_events(code, force_events)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"消息：{exc}")
    return {"bars": bar_result, "events": event_result, "errors": errors}


def _run_job(job_id: str, snapshot_date: str, stocks: list[dict[str, Any]]) -> None:
    completed, failed, errors = 0, 0, []
    history_store.update_job(job_id, status="running")
    try:
        for stock in stocks:
            code = str(stock.get("code") or "").zfill(6)
            result = sync_stock(code, include_events=False)
            completed += 1
            if result["errors"]:
                failed += 1
                errors.append(f"{code}：{'；'.join(result['errors'])}")
            history_store.update_job(job_id, completed=completed, failed=failed, errors=errors[-30:])
            time.sleep(0.25)
        history_store.update_job(job_id, status="completed", completed=completed, failed=failed,
                                 errors=errors[-30:], finished=True)
    finally:
        with _jobs_lock:
            _active_dates.discard(snapshot_date)


def enqueue_snapshot_sync(snapshot_date: str, stocks: list[dict[str, Any]]) -> str | None:
    with _jobs_lock:
        if snapshot_date in _active_dates:
            return None
        _active_dates.add(snapshot_date)
    job_id = "sync-" + uuid.uuid4().hex
    history_store.create_job(job_id, snapshot_date, len(stocks))
    thread = threading.Thread(target=_run_job, args=(job_id, snapshot_date, stocks), daemon=True)
    thread.start()
    return job_id


def stock_detail(code: str, start: str | None = None, end: str | None = None,
                 refresh: bool = False) -> dict[str, Any]:
    bars = history_store.list_bars(code, start, end)
    sync_result = None
    if refresh or not bars:
        sync_result = sync_stock(code, include_events=True, force_events=refresh)
        bars = history_store.list_bars(code, start, end)
    elif not events_are_fresh(code):
        # 有 K 线时仍按需刷新消息；失败只写进 errors，不影响已有图表。
        try:
            sync_result = {"bars": None, "events": sync_events(code), "errors": []}
        except Exception as exc:  # noqa: BLE001
            sync_result = {"bars": None, "events": None, "errors": [f"消息：{exc}"]}
    return {
        "code": code,
        "adjust": "qfq",
        "bars": bars,
        "events": history_store.list_events(code),
        "pool": history_store.pool_history(code),
        "sync": sync_result,
    }
