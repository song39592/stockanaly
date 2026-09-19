# -*- coding: utf-8 -*-
"""池内股票的同步编排：股票池快照、消息面与后台任务。

股价行情已拆分到独立模块，本模块不再直接接触行情数据源：
  - 采集（网络 / 降级）：`price_service`
  - 存储与复权计算：`price_store`
这里只负责「按股票池调度这些同步」，以及消息面（公告 / 新闻）的采集与落库。
"""
from __future__ import annotations

import datetime as dt
import threading
import time
import uuid
from typing import Any

import history_store
import price_service
import price_store
from collectors import get_announcements, get_news

EVENT_TTL_HOURS = 12
_jobs_lock = threading.Lock()
_active_dates: set[str] = set()

# 同一只股票「自动重抓」的冷却：重抓要打数据源（东财不可用时还会降级腾讯，一次几秒），
# 若不冷却，反复刷新或批量导入会把请求放大成几百次抓取。用户显式触发时可跳过（force）。
REFETCH_COOLDOWN_SECONDS = 60
_refetch_lock = threading.Lock()
_refetch_at: dict[str, float] = {}


def events_are_fresh(code: str) -> bool:
    """消息面是否仍在缓存有效期内。"""
    state = history_store.event_state(code)
    if not state:
        return False
    try:
        fetched = dt.datetime.fromisoformat(state["fetched_at"])
        return dt.datetime.now(dt.timezone.utc) - fetched < dt.timedelta(hours=EVENT_TTL_HOURS)
    except (TypeError, ValueError):
        return False


def sync_events(code: str, force: bool = False) -> dict[str, Any]:
    """采集公告与新闻（合并为消息时间轴）并落库。"""
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
    """同步单只股票：日 K（三口径）+ 复权参考数据（+ 可选消息面）。

    三块互相独立，任一块失败都只记录到 errors，不影响其余部分。
    """
    errors: list[str] = []
    bar_result = None
    reference_result = None
    event_result = None

    try:
        bar_result = price_service.sync_all_adjusts(code)
        errors.extend(bar_result.get("errors") or [])       # 单口径失败也要上报
    except Exception as exc:                                # noqa: BLE001 - 单股失败不影响整个任务
        errors.append(f"K线：{exc}")

    try:
        reference_result = price_service.sync_reference(code)   # 因子 / 除权：默认只首次采集
        errors.extend(reference_result.get("errors") or [])
    except Exception as exc:                                # noqa: BLE001 - 参考数据失败不影响日 K
        errors.append(f"参考数据：{exc}")

    if include_events:
        try:
            event_result = sync_events(code, force_events)
        except Exception as exc:                            # noqa: BLE001
            errors.append(f"消息：{exc}")

    return {"bars": bar_result, "events": event_result,
            "reference": reference_result, "errors": errors}


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
    """为某个交易日的股票池启动后台行情同步；同一天已有任务在跑时返回 None。"""
    with _jobs_lock:
        if snapshot_date in _active_dates:
            return None
        _active_dates.add(snapshot_date)
    job_id = "sync-" + uuid.uuid4().hex
    history_store.create_job(job_id, snapshot_date, len(stocks))
    thread = threading.Thread(target=_run_job, args=(job_id, snapshot_date, stocks), daemon=True)
    thread.start()
    return job_id


def _refetch_allowed(code: str, force: bool) -> bool:
    """该股此刻是否允许自动重抓（force 表示用户显式要求，跳过冷却）。"""
    if force:
        return True
    with _refetch_lock:
        last = _refetch_at.get(code)
        return last is None or (time.time() - last) >= REFETCH_COOLDOWN_SECONDS


def _mark_refetch(code: str) -> None:
    """记录本次重抓时间——不论成败都记，避免紧接着再打一次数据源。"""
    with _refetch_lock:
        _refetch_at[code] = time.time()


def load_trusted_bars(code: str, start: str | None = None, end: str | None = None,
                      force: bool = False
                      ) -> tuple[list[dict[str, Any]], str | None, dict[str, Any] | None]:
    """读取前复权 K 线；指纹校验失败时**全量重抓**后重读。

    发现数据被改动后不再停下来等确认：重抓以数据源为准**整体替换**，
    能还原任意位置（含历史区间）的改动，成功后重算指纹即恢复可信、对用户无感。
    重抓失败则**保留库中原数据**，并如实返回不可信原因——不假装成功，也不丢数据。

    **冷却**：自动重抓会打数据源，同一只股票在 `REFETCH_COOLDOWN_SECONDS` 内不重复执行，
    避免反复刷新 / 批量场景把请求放大成几百次抓取。
    `force=True`（用户点了「重新抓取」）时跳过冷却立即执行。

    返回 (bars, 不可信原因, 修复信息)；不可信原因非空时 bars 为空。
    """
    try:
        return price_store.load_bars(code, "qfq", start, end), None, None
    except price_store.UntrustedDataError as exc:
        reason = str(exc)
    if not _refetch_allowed(code, force):
        return ([], f"{reason}；刚执行过重抓，{REFETCH_COOLDOWN_SECONDS} 秒内不重复"
                    f"（库中数据已保留，可稍后重试或手动触发）", None)
    _mark_refetch(code)                                    # 冷却从本次尝试起算
    try:
        repair = price_service.refetch_bars(code)          # 先抓取，成功才写库
    except Exception as exc:              # noqa: BLE001 - 重抓失败时库中数据保持不动
        return [], f"{reason}；自动重抓失败，已保留库中数据：{exc}", None
    try:
        return price_store.load_bars(code, "qfq", start, end), None, repair
    except price_store.UntrustedDataError as exc:
        return [], f"{reason}；自动重抓后仍不一致：{exc}", repair


def stock_detail(code: str, start: str | None = None, end: str | None = None,
                 refresh: bool = False) -> dict[str, Any]:
    """个股详情：前复权 K 线（现算）+ 消息面 + 入池轨迹，必要时先同步。

    K 线读取前会比对按股指纹；一旦发现数据被改动过，**直接全量重抓该股并整体替换**
    （见 `load_trusted_bars`），成功即恢复可信、用户无感，`repaired` 字段记录修复详情；
    只有重抓也失败时才退回「不展示该股 K 线」，绝不展示不可信的数据。
    """
    bars, untrusted, repaired = load_trusted_bars(code, start, end, force=refresh)

    sync_result = None
    if untrusted is None:
        if refresh or not bars:
            sync_result = sync_stock(code, include_events=True, force_events=refresh)
            bars, untrusted, extra = load_trusted_bars(code, start, end, force=refresh)
            repaired = repaired or extra
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
        "untrusted": bool(untrusted),
        "untrusted_reason": untrusted,
        "repaired": repaired,
        "events": history_store.list_events(code),
        "pool": history_store.pool_history(code),
        "sync": sync_result,
    }
