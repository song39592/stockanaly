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
import tdx_reader
from market_service import _ak

# 默认下载窗口：**最近 5 年**（界面「起始日期」可改）。
# 系统里最长的需求是 MA60 / RPS ≈ 250 个交易日，5 年绰绰有余；
# 想拿上市至今，把起始日填成 `FULL_HISTORY_START_DATE` 即可（数据源会自动截断到上市首日）。
DEFAULT_HISTORY_YEARS = 5
FULL_HISTORY_START_DATE = dt.date(1990, 1, 1)

MAX_CONCURRENCY = 8
DEFAULT_CONCURRENCY = 4
DEFAULT_RATE_LIMIT = 3.0        # 每秒最多发起多少次数据源请求
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = (2, 6, 15)
IDLE_SLEEP = 0.4                # 暂停时空转的轮询间隔

MODE_FULL = "full"                  # 完整历史：先近后远两轮
MODE_INCREMENTAL = "incremental"    # 增量：只补最新数据（日常更新用）

SOURCE_ONLINE = "online"            # 数据来源：在线 akshare（限速、能拿全历史）
SOURCE_TDX = "tdx"                  # 数据来源：本机通达信日线（秒级，但只有最近几年）
RECENT_YEARS = 3                    # 「近期」窗口：全市场先跑完这一轮，再回头补更早历史

# 容量提示：按现有注释「5000 只 × 完整 raw 日K ≈ 2.5 GB」折算，约 0.5 MB/只；
# 增量一次只补几天，量级小得多。
ESTIMATED_BYTES_PER_STOCK = 500 * 1024
ESTIMATED_BYTES_PER_STOCK_INCREMENTAL = 20 * 1024
# 本地通达信只有最近几年（实测约 1250 个交易日 ≈ 5 年），量级比全历史小一个数量级
ESTIMATED_BYTES_PER_STOCK_TDX = 200 * 1024
DISK_RESERVE_BYTES = 1024 * 1024 * 1024      # 数据目录所在磁盘至少再留 1 GB

# ---- 后台自动化 ----
AUTO_UPDATE_HOUR = 18          # 每日自动更新的触发时刻（收盘后）
FRESHNESS_DAYS = 10            # 「只缺最近 10 天」的判定窗口：超过即视为历史未补齐
SCHEDULER_INTERVAL = 60        # 调度线程轮询间隔（秒）
AUTO_STATE_TTL = 300           # 覆盖度检测结果的缓存时间（秒）：全库扫描不便宜
IDLE_CONCURRENCY = 2           # 闲时并发默认值（只能 1 或 2）

ORIGIN_MANUAL = "manual"       # 任务来源：手动 / 每日自动更新 / 后台闲时
ORIGIN_AUTO = "auto"
ORIGIN_IDLE = "idle"


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


def _resolve_codes(scope: str, source: str = SOURCE_ONLINE) -> list[str]:
    """按范围解析出待下载代码（只支持「全市场」与「当前股票池」两种）。

    `source=tdx` 时以**本地通达信实际覆盖的股票**为准——本地没有文件的代码
    排进去也只是白白失败一次。
    """
    if source == SOURCE_TDX:
        root = download_store.get_setting("tdx_path", "") or tdx_reader.auto_detect() or ""
        if not root or not tdx_reader.detect(root)["valid"]:
            raise RuntimeError("通达信目录未配置或不可用，请先到设置页配置路径")
        local = set(tdx_reader.list_codes(root))
        if scope == "pool":
            return sorted(local & set(_normalize_codes(history_store.latest_snapshot_codes())))
        return sorted(local)
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


def _default_start_date() -> dt.date:
    """默认起始日：DEFAULT_HISTORY_YEARS 年前的今天（用户可在界面改）。

    按「今天」算而不是写死常量：服务常年不重启时也不会把窗口越拖越短。
    """
    today = dt.date.today()
    try:
        return today.replace(year=today.year - DEFAULT_HISTORY_YEARS)
    except ValueError:                            # 2 月 29 日这类闰日
        return today.replace(year=today.year - DEFAULT_HISTORY_YEARS, day=28)


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


def _years_of(start_date: dt.date | None) -> float:
    """从起始日算起大约要下载多少年（容量预估用）。"""
    begin = start_date or _default_start_date()
    return max(1.0, (dt.date.today() - begin).days / 365.0)


def _per_stock_bytes(source: str, mode: str, start_date: dt.date | None) -> float:
    """每只股票大约占多少字节——容量提示与磁盘检查共用同一套口径。

    基准 `ESTIMATED_BYTES_PER_STOCK`（500 KB）对应约 **10 年**，按实际请求的年数缩放：
    默认改成「最近 5 年」后若还按 10 年估，提示与磁盘检查都会虚高一倍。
    """
    if source == SOURCE_TDX:
        return float(ESTIMATED_BYTES_PER_STOCK_TDX)
    if mode == MODE_INCREMENTAL:
        return float(ESTIMATED_BYTES_PER_STOCK_INCREMENTAL)
    return ESTIMATED_BYTES_PER_STOCK * _years_of(start_date) / 10.0


def _ensure_disk_space(count: int, mode: str, source: str = SOURCE_ONLINE,
                       start_date: dt.date | None = None) -> None:
    """启动前检查：数据目录放不下就直接拒绝，别跑到一半才发现磁盘满。"""
    estimated = count * _per_stock_bytes(source, mode, start_date)
    free = _disk_free_bytes()
    if free < 0:
        return
    if free < estimated + DISK_RESERVE_BYTES:
        raise RuntimeError(
            f"磁盘空间不足：预计需要约 {_fmt_bytes(estimated)}，"
            f"数据目录（{db.DATA_DIR}）当前可用 {_fmt_bytes(free)}，"
            f"另需保留 {_fmt_bytes(DISK_RESERVE_BYTES)}。"
            f"请清理磁盘，或改用「当前股票池」等更小的范围（也可把起始日期改晚一点）。")


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
def _tdx_root(options: dict[str, Any]) -> str:
    """任务用的通达信目录：优先设置里配置的，没配就用自动检测到的。"""
    return download_store.get_setting("tdx_path", "") or tdx_reader.auto_detect() or ""


def _import_tdx_bars(root: str, code: str) -> None:
    """阶段 ①：读本机通达信日线并落盘。**不联网、不占限速额度**。"""
    bars = tdx_reader.read_day_bars(root, code)
    if not bars:
        raise RuntimeError("本地通达信没有这只股票的日线文件")
    price_service.fill_missing_turnover(code, bars)      # 本地算不出换手率，先补回已有的
    price_service.import_bars(code, bars, source="通达信本地")


def _sync_reference(runtime: _Runtime, options: dict[str, Any], code: str,
                    source: str) -> list[str]:
    """复权参考数据：**优先本地 gbbq**，解不出就回落到在线源。

    实测 gbbq 是加密的（`tdx_reader.read_xdxr` 返回 None），
    所以目前实际走的都是在线那条路；本地一旦可解会自动优先用本地。
    """
    if source == SOURCE_TDX and tdx_reader.read_xdxr(_tdx_root(options), code):
        return []                       # 本地除权可用，无需联网
    runtime.limiter.acquire()
    reference = price_service.sync_reference(
        code, force=bool(options.get("force_reference", False)))
    return list(reference.get("errors") or [])


def _window_covered(code: str, begin: str | None, end: str | None) -> bool:
    """库里是否已经有 [begin, end] 这段数据（只比首尾，不逐日核对）。

    逐日核对要读全表、成本太高；首尾够用：日线是连续序列，
    起点早于 begin 且终点不早于 end，中间就不会有整段空洞
    （零星缺失由增量同步的 14 天重叠窗口负责补齐）。
    """
    if begin:
        earliest = price_store.earliest_bar_date(code, "raw")
        if not earliest or str(earliest)[:10] > begin:
            return False
    if end:
        latest = price_store.latest_bar_date(code, "raw")
        if not latest or str(latest)[:10] < end:
            return False
    return True


def _skip_if_covered(task_id: str, code: str, options: dict[str, Any],
                     source: str) -> bool:
    """库里历史已足量 → **整只跳过，不动作**。

    「设定值」就是这次任务要的窗口，用户可改：
      在线完整历史 = [起始日, 今天]（且尾部要在 FRESHNESS_DAYS 内，否则最近一段还缺）
      本地通达信   = 本地文件自己的首末日期
    已覆盖还去下载，等于白白消耗数据源额度并抬高被限流的概率。
    返回 True 表示已跳过（调用方直接返回即可）。
    """
    if source == SOURCE_TDX:
        span = tdx_reader.day_range(_tdx_root(options), code)
        if not span:
            return False
        begin, end = span
    else:
        begin = (_option_date(options, "start_date") or _default_start_date()).isoformat()
        end = (dt.date.today() - dt.timedelta(days=FRESHNESS_DAYS)).isoformat()
    if not _window_covered(code, begin, end):
        return False
    download_store.skip_stock(task_id, code)
    return True


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
    source = options.get("source") or SOURCE_ONLINE

    begin, stop = None, None
    if mode == MODE_FULL:
        split = _option_date(options, "split_date") or _split_date()
        oldest = _option_date(options, "start_date") or _default_start_date()
        if phase == 1:
            begin, stop = split, None          # 近：分界点 → 今天
        else:
            begin, stop = oldest, split        # 远：起始日 → 分界点

    try:
        errors: list[str] = []
        if runtime.stop.is_set():
            return
        # 库里已有足量历史 → 这只不动作（增量任务本身只拉最近几天，不需要这道判断）
        if mode != MODE_INCREMENTAL and _skip_if_covered(task_id, code, options, source):
            return
        if source == SOURCE_TDX:
            # 本机通达信是唯一来源：有几年的日线就同步几年，不联网补更早的历史
            _import_tdx_bars(_tdx_root(options), code)
        else:
            # 上市晚于分界点的新股：这一阶段本来就没有数据，直接算完成（不算失败）
            if phase == 2 and stop is not None and _no_older_history(code, stop):
                download_store.finish_code(task_id, seq, ok=True)
                return
            runtime.limiter.acquire()
            bars = price_service.sync_all_adjusts(code, start=begin, end=stop)
            errors.extend(bars.get("errors") or [])

        if options.get("with_reference", True):
            errors.extend(_sync_reference(runtime, options, code, source))

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
# 后台自动化：每日自动更新 + 闲时补历史
# --------------------------------------------------------------------------- #
_auto_state: dict[str, Any] = {}
_auto_state_at = 0.0


def _idle_concurrency() -> int:
    """闲时并发档位，只允许 1 / 2（后台任务不与前台抢资源）。"""
    return _clamp_int(download_store.get_setting("idle_concurrency", IDLE_CONCURRENCY),
                      IDLE_CONCURRENCY, 1, 2)


def _has_alive_task() -> bool:
    """此刻是否有任务真的在跑（有就不起新任务，避免两套 worker 互相抢带宽）。"""
    with _runtimes_lock:
        return any(runtime.manager is not None and runtime.manager.is_alive()
                   for runtime in _runtimes.values())


def _downloaded_staleness() -> tuple[int, int, list[str]]:
    """(已下载只数, 落后超过 FRESHNESS_DAYS 天的只数, 落后示例)。

    判定口径：**只看库里已经下过的股票**——没下过的股票不算缺口。
    因此「该补的历史都已到位、只是还差最近几天」时，stale 为 0。
    """
    latest = price_store.code_latest_dates()
    if not latest:
        return 0, 0, []
    threshold = (dt.date.today() - dt.timedelta(days=FRESHNESS_DAYS)).isoformat()
    stale = sorted(code for code, day in latest.items() if str(day) < threshold)
    return len(latest), len(stale), stale[:10]


def _options_for(mode: str, *, source: str = SOURCE_ONLINE, start_date: str | None = None,
                 concurrency: int = DEFAULT_CONCURRENCY,
                 rate_limit: float = DEFAULT_RATE_LIMIT,
                 with_reference: bool = True,
                 force_reference: bool = False) -> dict[str, Any]:
    """组装任务选项：手动 / 自动 / 闲时共用一套，分界点与分段数不重复实现。"""
    begin: dt.date | None = None
    if mode == MODE_FULL:
        begin = _option_date({"start_date": start_date}, "start_date") or _default_start_date()
    split = _split_date()
    # 本地通达信只跑一轮：本机有几年的日线就同步几年，不再联网补更早的历史
    #（系统里最长的需求是 MA60 / RPS≈250 日，5 年绰绰有余）
    if source == SOURCE_TDX:
        phases = 1
    else:
        phases = 2 if (mode == MODE_FULL and begin is not None and begin < split) else 1
    return {
        "mode": mode,
        "source": source,
        "start_date": begin.isoformat() if begin else None,
        "split_date": split.isoformat(),
        "phases": phases,
        "concurrency": _clamp_int(concurrency, DEFAULT_CONCURRENCY, 1, MAX_CONCURRENCY),
        "rate_limit": max(0.1, min(20.0, float(rate_limit or DEFAULT_RATE_LIMIT))),
        "with_reference": bool(with_reference),
        "force_reference": bool(force_reference),
    }


def _kick_auto_update() -> str | None:
    """给「库里已下载的股票」补最新数据（每日自动更新）。"""
    codes = sorted(price_store.code_latest_dates())
    if not codes or _has_alive_task():
        return None
    _ensure_disk_space(len(codes), MODE_INCREMENTAL)
    options = _options_for(MODE_INCREMENTAL, concurrency=_idle_concurrency())
    task_id = "dl-" + uuid.uuid4().hex
    download_store.create_task(task_id, "downloaded", options, codes, 1, origin=ORIGIN_AUTO)
    download_store.set_setting("auto_last_run", dt.date.today().isoformat())
    _spawn(task_id, options)
    return task_id


def _resume_idle(task_id: str, task: dict[str, Any]) -> None:
    """把闲时任务拉起来，并把并发降到闲时档位。"""
    options = dict(task.get("options") or {})
    options["concurrency"] = _idle_concurrency()
    download_store.update_options(task_id, options)
    download_store.requeue_running(task_id)
    download_store.update_task(task_id, status="running")
    _spawn(task_id, options)


def _revive_idle_task() -> None:
    """重新勾选「闲时下载」时，把上一次被取消的闲时任务恢复成可续跑。

    取消本身依然有效（后台不会自动拉起它），只有用户重新勾选开关才算解除。
    """
    task_id = download_store.get_setting("idle_task_id", "")
    if not task_id:
        return
    task = download_store.get_task(task_id)
    if task is not None and task["status"] == "cancelled":
        download_store.update_task(task_id, status="paused")


def _ensure_idle_task() -> str | None:
    """保证有一个「后台闲时任务」在低并发跑着。

    - 已有任务且还有没跑完的股票 → 接着跑（这就是「接着上次没下完的继续」）；
    - 没有任务 / 已跑完 → 只对**还没排过的股票**建新任务（例如新上市股），
      已经跑过的不再重排，否则每次开启都等于把全市场重下一遍。
    """
    current = download_store.get_setting("idle_task_id", "")
    if current:
        task = download_store.get_task(current)
        # 用户主动取消过的任务**不自动拉起**：取消就该生效，
        # 否则后台会把用户刚停掉的任务复活，表现为「取消不了」。
        if (task is not None and task["status"] != "cancelled"
                and download_store.remaining(current) > 0):
            _resume_idle(current, task)
            return current
    today = dt.date.today().isoformat()
    if download_store.get_setting("idle_refreshed_at", "") == today:
        return None                               # 今天已排过一轮，不再重复拉清单
    download_store.set_setting("idle_refreshed_at", today)
    covered = download_store.task_code_set(current) if current else set()
    missing = [code for code in _normalize_codes(_universe_codes()) if code not in covered]
    if not missing:
        return None
    _ensure_disk_space(len(missing), MODE_FULL)
    options = _options_for(MODE_FULL, concurrency=_idle_concurrency())
    task_id = "dl-" + uuid.uuid4().hex
    download_store.create_task(task_id, "all", options, missing,
                               options["phases"], origin=ORIGIN_IDLE)
    download_store.set_setting("idle_task_id", task_id)
    _spawn(task_id, options)
    return task_id


def _scheduler_tick() -> None:
    """一次调度：到点就补最新数据，空闲就接着补完整历史（两者不同时跑）。"""
    if _has_alive_task():
        return
    stored = download_store.settings_map()
    if stored.get("auto_update") == "1":
        today = dt.date.today().isoformat()
        if stored.get("auto_last_run") != today and dt.datetime.now().hour >= AUTO_UPDATE_HOUR:
            _kick_auto_update()
            return
    if stored.get("idle_download") == "1":
        _ensure_idle_task()


def _scheduler_loop() -> None:
    """后台调度线程。任何一次异常都吞掉，否则线程死了就再也不会自动更新。"""
    while True:
        try:
            _scheduler_tick()
        except Exception:                         # noqa: BLE001
            pass
        time.sleep(SCHEDULER_INTERVAL)


# --------------------------------------------------------------------------- #
# 对外接口
# --------------------------------------------------------------------------- #
def universe_view(scope: str = "all", mode: str = MODE_FULL,
                  source: str = SOURCE_ONLINE, start_date: str | None = None) -> dict[str, Any]:
    """预览某个范围会下载多少只、大约占多大（用于容量提示，真正开始时会再取一次）。"""
    resolved = _resolve_codes(scope, source)
    wanted = _option_date({"start_date": start_date}, "start_date")
    estimated = len(resolved) * _per_stock_bytes(source, mode, wanted)
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


def start_task(*, scope: str = "all", source: str = SOURCE_ONLINE,
               mode: str = MODE_FULL, start_date: str | None = None,
               concurrency: int = DEFAULT_CONCURRENCY,
               rate_limit: float = DEFAULT_RATE_LIMIT, with_reference: bool = True,
               force_reference: bool = False) -> str:
    """启动一个遍历下载任务，返回 task_id。

    mode=full（默认）走「先近后远」两轮：全市场先补齐最近 `RECENT_YEARS` 年，
    再回头补更老的历史——中途被打断，也已经拿到了能用的近期数据。
    mode=incremental 只补最新数据（每只按库里最新日期 -14 天重叠），用于日常更新。

    source=tdx 时**只同步本机通达信已有的日线**（实测约 5 年），走一轮、不联网：
    系统里最长的需求是 MA60 / RPS≈250 个交易日，5 年绰绰有余，不再联网补更早的历史。
    """
    if mode not in (MODE_FULL, MODE_INCREMENTAL):
        raise ValueError(f"未知的下载模式：{mode}")
    if source not in (SOURCE_ONLINE, SOURCE_TDX):
        raise ValueError(f"未知的数据来源：{source}")
    if source == SOURCE_TDX:
        mode = MODE_FULL                # 本地同步的语义就是「把完整历史攒齐」
    resolved = _resolve_codes(scope, source)
    if not resolved:
        raise RuntimeError("没有可下载的股票代码")
    # 放不下就别开始，跑到一半才发现更糟
    _ensure_disk_space(len(resolved), mode, source,
                       _option_date({"start_date": start_date}, "start_date"))

    # 分界点与分段数统一由 `_options_for` 决定（手动 / 自动 / 闲时任务走同一套）
    options = _options_for(mode, source=source, start_date=start_date,
                           concurrency=concurrency, rate_limit=rate_limit,
                           with_reference=with_reference,
                           force_reference=force_reference)
    task_id = "dl-" + uuid.uuid4().hex
    download_store.create_task(task_id, scope, options, resolved,
                               options["phases"], origin=ORIGIN_MANUAL)
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


def settings_view() -> dict[str, Any]:
    """后台开关的当前值。"""
    stored = download_store.settings_map()
    return {
        "auto_update": stored.get("auto_update") == "1",
        "idle_download": stored.get("idle_download") == "1",
        "idle_concurrency": _idle_concurrency(),
        "auto_update_hour": AUTO_UPDATE_HOUR,
        "idle_task_id": stored.get("idle_task_id") or "",
        "auto_last_run": stored.get("auto_last_run") or "",
        "tdx_path": stored.get("tdx_path") or "",
    }


def tdx_status(path: str | None = None) -> dict[str, Any]:
    """通达信目录检测结果（设置页用）：是否有效、有多少只、覆盖到哪天。"""
    configured = download_store.get_setting("tdx_path", "")
    target = path or configured
    if target:
        info = tdx_reader.detect(target)
    else:
        info = {"path": "", "exists": False, "valid": False,
                "markets": {}, "total_files": 0, "sample": "", "has_gbbq": False}
    info["configured"] = configured
    info["auto_detected"] = tdx_reader.auto_detect()
    info["codes"] = len(tdx_reader.list_codes(target)) if info.get("valid") else 0
    return info


def update_settings(*, auto_update: bool | None = None, idle_download: bool | None = None,
                    idle_concurrency: int | None = None,
                    tdx_path: str | None = None) -> dict[str, Any]:
    """保存后台开关。打开即时生效——不等下一个轮询周期。

    开启「自动更新」要求：已下载的股票里没有一只落后超过 FRESHNESS_DAYS 天。
    否则用户会以为它在保持最新，实际大片历史还空着。
    """
    if tdx_path is not None:
        text = str(tdx_path).strip()
        if text and not tdx_reader.detect(text)["valid"]:
            raise ValueError(f"该目录下没有通达信日线数据（vipdoc/*/lday）：{text}")
        download_store.set_setting("tdx_path", text)
    if idle_concurrency is not None:
        download_store.set_setting(
            "idle_concurrency", str(_clamp_int(idle_concurrency, IDLE_CONCURRENCY, 1, 2)))
    if idle_download is not None:
        download_store.set_setting("idle_download", "1" if idle_download else "0")
    if auto_update is not None:
        download_store.set_setting("auto_update", "1" if auto_update else "0")
    if auto_update:
        downloaded, stale, _ = _downloaded_staleness()
        if downloaded == 0 or stale > 0:
            raise ValueError(
                f"暂时不能开启自动更新：已下载 {downloaded} 只，"
                f"其中 {stale} 只落后超过 {FRESHNESS_DAYS} 天。"
                f"请先把历史补齐（可用「后台闲时下载」），再开启。")
        _kick_auto_update()
    if idle_download:
        _revive_idle_task()          # 重新勾选 = 解除上一次的取消
        _ensure_idle_task()
    return settings_view()


def auto_state(force: bool = False) -> dict[str, Any]:
    """后台开关的**可开启状态** + 当前设置。

    要靠 `code_latest_dates()` 扫一遍全库，成本不低，因此结果缓存 AUTO_STATE_TTL 秒。
    """
    global _auto_state, _auto_state_at
    now = time.time()
    if not force and _auto_state and now - _auto_state_at < AUTO_STATE_TTL:
        cached = dict(_auto_state)
        cached["cached"] = True
        return cached
    downloaded, stale, examples = _downloaded_staleness()
    state = dict(settings_view())
    state.update({
        "downloaded": downloaded,
        "stale": stale,
        "stale_examples": examples,
        "eligible": downloaded > 0 and stale == 0,
        "freshness_days": FRESHNESS_DAYS,
        "checked_at": db.now_iso(),
        "cached": False,
    })
    _auto_state = dict(state)
    _auto_state_at = now
    return dict(state)


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


def _start_background() -> None:
    """启动后台：先把上次没跑完的任务标成暂停（等调度或用户续跑），再起调度线程。"""
    try:
        download_store.recover_stale()
    except Exception:                             # noqa: BLE001 - 表还没建好时静默跳过
        pass
    threading.Thread(target=_scheduler_loop, daemon=True).start()


_start_background()
