# -*- coding: utf-8 -*-
"""历史数据的遍历下载编排。

职责：把「一堆股票代码」变成「受控的后台下载任务」——
  - 代码逐条从游标队列领取（`download_store` 持久化，可断点续传）；
  - worker 池并发 + 令牌桶限速，既不慢成单线程，也不把数据源打挂；
  - 暂停 / 继续 / 取消 / 失败重试（指数退避）只改库里的一行，重启后依然有效。

真正的采集复用 `price_service`（日K / 复权因子 / 除权），
本模块只决定「下载谁、什么时候下载、失败怎么办」，不碰数据源细节、不碰存储结构。
"""
from __future__ import annotations

import datetime as dt
import shutil
import threading
import time
import uuid
from typing import Any

import akshare as ak

import db
import download_store
import history_store
import price_service
import price_store
from market_service import _ak

# 遍历下载默认从上市日之前开始：传一个足够早的日期，让数据源自行截断到上市首日。
DEFAULT_START_DATE = dt.date(1990, 1, 1)

MAX_CONCURRENCY = 8
DEFAULT_CONCURRENCY = 4
DEFAULT_RATE_LIMIT = 3.0        # 每秒最多发起多少次数据源请求
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = (2, 6, 15)
IDLE_SLEEP = 0.4                # 暂停时空转的轮询间隔

MODE_FULL = "full"                  # 完整历史：先近后远两轮
MODE_INCREMENTAL = "incremental"    # 增量：只补最新数据（日常更新用）
RECENT_YEARS = 3                    # 「近期」窗口：全市场先跑完这一轮，再回头补更早历史

# 容量提示：按现有注释「5000 只 × 完整 raw 日K ≈ 2.5 GB」折算，约 0.5 MB/只；
# 增量一次只补几天，量级小得多。
ESTIMATED_BYTES_PER_STOCK = 500 * 1024
ESTIMATED_BYTES_PER_STOCK_INCREMENTAL = 20 * 1024
DISK_RESERVE_BYTES = 1024 * 1024 * 1024      # 数据目录所在磁盘至少再留 1 GB


class _RateLimiter:
    """令牌桶：限制「每秒最多发起多少次数据源请求」。

    全市场遍历会打出上万次请求，不限速很容易触发数据源限流，
    表现为大面积超时、进而被误判成「数据缺失」。
    桶容量取一秒的量：允许小突发，之后自然回落，不会把并发线程一起饿死。
    """

    def __init__(self, rate: float) -> None:
        self._rate = max(0.1, float(rate))
        self._tokens = self._rate
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(self._rate, self._tokens + (now - self._last) * self._rate)
                self._last = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait = (1.0 - self._tokens) / self._rate
            time.sleep(min(wait, 1.0))


class _Runtime:
    """一个任务在内存里的运行态。

    DB 里的 status 是权威（重启后靠它恢复），这里只是让线程能快速停 / 等 / 限速。
    """

    def __init__(self, options: dict[str, Any]) -> None:
        self.options = options
        self.stop = threading.Event()
        self.limiter = _RateLimiter(options.get("rate_limit") or DEFAULT_RATE_LIMIT)
        self.workers: list[threading.Thread] = []
        self.manager: threading.Thread | None = None


# 运行中的任务：task_id → 运行态
_runtimes: dict[str, _Runtime] = {}
_runtimes_lock = threading.Lock()
_claim_lock = threading.Lock()        # 领取代码：保证同一条不会被两个 worker 领走


def _clamp_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _normalize_code(value: Any) -> str | None:
    """任意写法（600000 / sh600000 / 600000.SH）→ 6 位代码；不是就返回 None。"""
    text = str(value or "").strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) < 6:
        return None
    return digits[-6:]


def _normalize_codes(raw: Any) -> list[str]:
    codes: list[str] = []
    seen: set[str] = set()
    for item in raw or []:
        code = _normalize_code(item)
        if code and code not in seen:
            seen.add(code)
            codes.append(code)
    return codes


# --------------------------------------------------------------------------- #
# 下载范围
# --------------------------------------------------------------------------- #
def _universe_codes() -> list[str]:
    """取全市场 A 股代码（akshare；两个来源任一可用即可）。"""
    frame = None
    for name in ("stock_info_a_code_name", "stock_zh_a_spot_em"):
        func = getattr(ak, name, None)
        if func is None:
            continue
        frame = _ak(func, timeout=90)
        if frame is not None and not getattr(frame, "empty", True):
            break
        frame = None
    if frame is None:
        raise RuntimeError("A 股代码列表获取失败（数据源无响应，或 akshare 未提供该接口）")
    columns = {str(column).strip(): column for column in frame.columns}
    code_column = next(
        (columns[key] for key in ("code", "代码", "证券代码", "symbol") if key in columns), None)
    if code_column is None:
        raise RuntimeError("A 股代码列表里找不到代码列")
    return _normalize_codes(list(frame[code_column]))


def _resolve_codes(scope: str) -> list[str]:
    """按范围解析出待下载代码（只支持「全市场」与「当前股票池」两种）。"""
    if scope == "pool":
        resolved = _normalize_codes(history_store.latest_snapshot_codes())
        if not resolved:
            raise RuntimeError("本地还没有股票池快照，请先导入一次股票池")
        return resolved
    if scope == "all":
        return _universe_codes()
    raise ValueError(f"未知的下载范围：{scope}")


# --------------------------------------------------------------------------- #
# 时间窗与磁盘：「先近后远」的分段，以及启动前的容量检查
# --------------------------------------------------------------------------- #
def _option_date(options: dict[str, Any], key: str) -> dt.date | None:
    """从任务选项里取日期；为空或格式不对都返回 None。"""
    try:
        return dt.date.fromisoformat(str(options.get(key) or ""))
    except (TypeError, ValueError):
        return None


def _split_date() -> dt.date:
    """「近期」与「更早历史」的分界点（RECENT_YEARS 年前的今天）。"""
    today = dt.date.today()
    try:
        return today.replace(year=today.year - RECENT_YEARS)
    except ValueError:                            # 2 月 29 日这类闰日
        return today.replace(year=today.year - RECENT_YEARS, day=28)


def _fmt_bytes(size: float) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def _disk_free_bytes() -> int:
    """数据目录所在磁盘的可用空间；取不到返回 -1（不因此拦住下载）。"""
    try:
        return shutil.disk_usage(str(db.DATA_DIR)).free
    except Exception:                             # noqa: BLE001
        return -1


def _ensure_disk_space(count: int, mode: str) -> None:
    """启动前检查：数据目录放不下就直接拒绝，别跑到一半才发现磁盘满。"""
    estimated = count * (ESTIMATED_BYTES_PER_STOCK_INCREMENTAL if mode == MODE_INCREMENTAL
                         else ESTIMATED_BYTES_PER_STOCK)
    free = _disk_free_bytes()
    if free < 0:
        return
    if free < estimated + DISK_RESERVE_BYTES:
        raise RuntimeError(
            f"磁盘空间不足：预计需要约 {_fmt_bytes(estimated)}，"
            f"数据目录（{db.DATA_DIR}）当前可用 {_fmt_bytes(free)}，"
            f"另需保留 {_fmt_bytes(DISK_RESERVE_BYTES)}。"
            f"请清理磁盘，或改用「当前股票池 / 自定义代码」等更小的范围。")


def _no_older_history(code: str, split: dt.date) -> bool:
    """这只股票在分界点之前**本来就没有**数据（上市晚于分界点的新股）。

    用库里「最早一条」来判断，而不是看「数据源返回空」——后者跟网络超时长得一模一样，
    误判会把**抓漏的历史**当成「本来就没有」，属于静默的数据缺口，不可接受。
    """
    try:
        earliest = price_store.earliest_bar_date(code, "raw")
    except Exception:                             # noqa: BLE001
        return False                              # 读不到就按「有历史」处理，照常抓
    if not earliest:
        return False                              # 库里没数据：无从判断，照常抓
    try:
        return dt.date.fromisoformat(str(earliest)[:10]) > split
    except (TypeError, ValueError):
        return False


# --------------------------------------------------------------------------- #
# worker
# --------------------------------------------------------------------------- #
def _download_one(task_id: str, runtime: _Runtime, item: dict[str, Any]) -> None:
    """下载一只股票的**某一个阶段**：日K（+ 可选复权因子 / 除权），失败退避重试。

    时间窗由 mode / phase 决定：
      完整历史 · 近期阶段：分界点 → 今天
      完整历史 · 更早阶段：起始日 → 分界点
      增量模式：交给 `sync_bars` 按库里最新日期 -14 天重叠，只补最新数据
    """
    code = item["code"]
    seq = item["seq"]
    phase = int(item.get("phase") or 1)
    options = runtime.options
    mode = options.get("mode") or MODE_FULL

    begin, stop = None, None
    if mode == MODE_FULL:
        split = _option_date(options, "split_date") or _split_date()
        oldest = _option_date(options, "start_date") or DEFAULT_START_DATE
        if phase == 1:
            begin, stop = split, None          # 近：分界点 → 今天
        else:
            begin, stop = oldest, split        # 远：起始日 → 分界点

    try:
        errors: list[str] = []
        if runtime.stop.is_set():
            return
        # 上市晚于分界点的新股：这一阶段本来就没有数据，直接算完成（不算失败）
        if phase == 2 and stop is not None and _no_older_history(code, stop):
            download_store.finish_code(task_id, seq, ok=True)
            return
        runtime.limiter.acquire()
        bars = price_service.sync_all_adjusts(code, start=begin, end=stop)
        errors.extend(bars.get("errors") or [])

        if options.get("with_reference", True):
            runtime.limiter.acquire()
            reference = price_service.sync_reference(
                code, force=bool(options.get("force_reference", False)))
            errors.extend(reference.get("errors") or [])

        if errors:
            raise RuntimeError("；".join(errors))
    except Exception as exc:                      # noqa: BLE001 - 单只失败不影响整个任务
        if runtime.stop.is_set():
            return
        message = f"{type(exc).__name__}: {exc}"
        retries = int(item.get("retries") or 0)
        if retries < MAX_RETRIES:
            backoff = RETRY_BACKOFF_SECONDS[min(retries, len(RETRY_BACKOFF_SECONDS) - 1)]
            download_store.requeue_code(task_id, seq, f"{message}（{backoff}s 后重试）")
            time.sleep(backoff)
            return
        download_store.finish_code(task_id, seq, ok=False, error=message)
        download_store.record_error(task_id, code, message)
        return

    download_store.finish_code(task_id, seq, ok=True)


def _worker(task_id: str) -> None:
    runtime = _runtimes.get(task_id)
    if runtime is None:
        return
    while not runtime.stop.is_set():
        task = download_store.get_task(task_id)
        if task is None or task["status"] == "cancelled":
            return
        if task["status"] == "paused":
            # 暂停时线程留着空转：点「继续」立刻恢复，不需要重建线程池
            time.sleep(IDLE_SLEEP)
            continue
        with _claim_lock:
            item = download_store.claim_code(task_id)
        if item is None:                          # 队列空了，这个 worker 收工
            return
        _download_one(task_id, runtime, item)


def _run(task_id: str, concurrency: int) -> None:
    """管理线程：拉起 worker 池 → 等它们收工 → 给任务一个最终状态。"""
    runtime = _runtimes.get(task_id)
    if runtime is None:
        return
    # 竞态：spawn 之后、本线程真正开跑之前，任务可能已被取消（或本来就跑完了）。
    # `mark_running` 是条件更新——若此刻已是 cancelled / completed 就不置位，
    # 否则会把用户的「取消」覆盖回 running，表现为取消不了。
    if not download_store.mark_running(task_id):
        return
    for _ in range(max(1, concurrency)):
        thread = threading.Thread(target=_worker, args=(task_id,), daemon=True)
        thread.start()
        runtime.workers.append(thread)
    for thread in runtime.workers:
        thread.join()

    task = download_store.get_task(task_id)
    if task and task["status"] == "running":
        left = download_store.remaining(task_id)
        download_store.update_task(task_id, status="completed" if left == 0 else "paused",
                                   finished=(left == 0))
    with _runtimes_lock:
        _runtimes.pop(task_id, None)


def _spawn(task_id: str, options: dict[str, Any], force: bool = False) -> None:
    """起 / 续一个任务的管理线程。

    `force=True` 用于「重试失败项」：即使旧的线程还没退出也重建运行态，
    因为取消后的旧 worker 已收到停止信号，不会再来领代码。
    """
    with _runtimes_lock:
        runtime = _runtimes.get(task_id)
        if not force and runtime is not None and runtime.manager is not None \
                and runtime.manager.is_alive():
            return                                # 已在跑，不需要再来一套
        if runtime is not None:
            runtime.stop.set()                    # 让旧线程尽快退出，避免两套并存
        runtime = _Runtime(options)
        _runtimes[task_id] = runtime
        concurrency = _clamp_int(options.get("concurrency"), DEFAULT_CONCURRENCY, 1, MAX_CONCURRENCY)
        manager = threading.Thread(target=_run, args=(task_id, concurrency), daemon=True)
        runtime.manager = manager
    manager.start()


# --------------------------------------------------------------------------- #
# 对外接口
# --------------------------------------------------------------------------- #
def universe_view(scope: str = "all", mode: str = MODE_FULL) -> dict[str, Any]:
    """预览某个范围会下载多少只、大约占多大（用于容量提示，真正开始时会再取一次）。"""
    resolved = _resolve_codes(scope)
    estimated = len(resolved) * (
        ESTIMATED_BYTES_PER_STOCK_INCREMENTAL if mode == MODE_INCREMENTAL
        else ESTIMATED_BYTES_PER_STOCK)
    free = _disk_free_bytes()
    return {
        "scope": scope,
        "mode": mode,
        "count": len(resolved),
        "codes": resolved[:20],
        "estimated_bytes": estimated,
        "free_bytes": free,
        "disk_ok": free < 0 or free >= estimated + DISK_RESERVE_BYTES,
    }


def start_task(*, scope: str = "all", mode: str = MODE_FULL, start_date: str | None = None,
               concurrency: int = DEFAULT_CONCURRENCY,
               rate_limit: float = DEFAULT_RATE_LIMIT, with_reference: bool = True,
               force_reference: bool = False) -> str:
    """启动一个遍历下载任务，返回 task_id。

    mode=full（默认）走「先近后远」两轮：全市场先补齐最近 `RECENT_YEARS` 年，
    再回头补更老的历史——中途被打断，也已经拿到了能用的近期数据。
    mode=incremental 只补最新数据（每只按库里最新日期 -14 天重叠），用于日常更新。
    """
    if mode not in (MODE_FULL, MODE_INCREMENTAL):
        raise ValueError(f"未知的下载模式：{mode}")
    resolved = _resolve_codes(scope)
    if not resolved:
        raise RuntimeError("没有可下载的股票代码")
    _ensure_disk_space(len(resolved), mode)       # 放不下就别开始，跑到一半才发现更糟

    begin: dt.date | None = None
    if mode == MODE_FULL:
        begin = _option_date({"start_date": start_date}, "start_date") or DEFAULT_START_DATE
    split = _split_date()
    # 起始日已晚于分界点时没有「更早的历史」可补，只排一轮
    phases = 2 if (mode == MODE_FULL and begin is not None and begin < split) else 1
    options = {
        "mode": mode,
        "start_date": begin.isoformat() if begin else None,
        "split_date": split.isoformat(),
        "phases": phases,
        "concurrency": _clamp_int(concurrency, DEFAULT_CONCURRENCY, 1, MAX_CONCURRENCY),
        "rate_limit": max(0.1, min(20.0, float(rate_limit or DEFAULT_RATE_LIMIT))),
        "with_reference": bool(with_reference),
        "force_reference": bool(force_reference),
    }
    task_id = "dl-" + uuid.uuid4().hex
    download_store.create_task(task_id, scope, options, resolved, phases)
    _spawn(task_id, options)
    return task_id


def _decorate(task: dict[str, Any]) -> dict[str, Any]:
    done = task["completed"] + task["failed"]
    task["percent"] = round(done * 100.0 / max(1, task["total"]), 1)
    task["remaining"] = download_store.remaining(task["id"])
    task["failures"] = download_store.failures(task["id"])
    with _runtimes_lock:
        runtime = _runtimes.get(task["id"])
    task["alive"] = bool(runtime is not None and runtime.manager is not None
                         and runtime.manager.is_alive())
    return task


def task_view(task_id: str) -> dict[str, Any] | None:
    task = download_store.get_task(task_id)
    return _decorate(task) if task else None


def list_tasks(limit: int = 20) -> list[dict[str, Any]]:
    return [_decorate(task) for task in download_store.list_tasks(limit)]


def pause(task_id: str) -> dict[str, Any]:
    task = download_store.get_task(task_id)
    if task is None:
        raise KeyError(task_id)
    if task["status"] not in ("completed", "cancelled"):
        download_store.update_task(task_id, status="paused")
    return task_view(task_id)


def resume(task_id: str) -> dict[str, Any]:
    task = download_store.get_task(task_id)
    if task is None:
        raise KeyError(task_id)
    if task["status"] == "cancelled":
        raise RuntimeError("任务已取消，不能继续（请重新「开始下载」）")
    download_store.requeue_running(task_id)       # 上次领了没做完的，退回队列重跑
    download_store.update_task(task_id, status="running")
    _spawn(task_id, task["options"])
    return task_view(task_id)


def cancel(task_id: str) -> dict[str, Any]:
    task = download_store.get_task(task_id)
    if task is None:
        raise KeyError(task_id)
    with _runtimes_lock:
        runtime = _runtimes.get(task_id)
    if runtime is not None:
        runtime.stop.set()
    download_store.update_task(task_id, status="cancelled", finished=True)
    return task_view(task_id)


def retry_failed(task_id: str) -> dict[str, Any]:
    """只重跑失败的那几只（成功的不动），用于收尾补漏。"""
    task = download_store.get_task(task_id)
    if task is None:
        raise KeyError(task_id)
    count = download_store.reset_failed(task_id)
    if not count:
        return task_view(task_id)
    download_store.update_task(task_id, status="running")
    _spawn(task_id, task["options"], force=True)
    return task_view(task_id)


def _recover_on_startup() -> None:
    """启动时把上次没跑完的任务标成暂停，等用户点继续。"""
    try:
        download_store.recover_stale()
    except Exception:                             # noqa: BLE001 - 表还没建好时静默跳过
        pass


_recover_on_startup()
