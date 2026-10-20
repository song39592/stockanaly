# -*- coding: utf-8 -*-
"""历史数据的遍历下载编排。

职责：把「一堆股票代码」变成「受控的后台下载任务」——
  - 代码逐条从游标队列领取（`download_store` 持久化，可断点续传）；
  - worker 池并发 + 令牌桶限速，既不慢成单线程，也不把数据源打挂；
  - 暂停 / 继续 / 取消 / 失败重试（指数退避）只改库里的一行，重启后依然有效。

真正的采集复用 `price_service`（日K / 复权因子 / 除权）与
`features.stock.profile`（锁仓系数），本模块只决定「下载谁、什么时候下载、
失败怎么办」，不碰数据源细节、不碰存储结构。

任务分两类（`kind`）：
  * `bars`（默认）—— 日 K，带「先近后远」两轮与参考数据；
  * `lockup` —— 只补锁仓系数（十大流通股东 → r / L），按报告期过滤队列，
    已是最新报告期的股票根本不排进来。
"""
from __future__ import annotations

import datetime as dt
import shutil
import threading
import time
import uuid
from functools import wraps
from typing import Any

import akshare as ak

from core import db
from features.download import store as download_store
from features.history import store as history_store
from core import price_service
from core import price_store
from core import tdx_reader
from core.fallback import first_ok      # bug-02：串行多源回退的总时间预算
from features.market.service import _ak
from features.stock import profile as stock_profile

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

KIND_BARS = "bars"                  # 任务类型：日 K（默认）
KIND_LOCKUP = "lockup"              # 任务类型：只补锁仓系数（十大流通股东 → r / L）

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
WEEKLY_OVERLAP_DAYS = 10       # 周度修订回收重拉的窗口（≈上周 5 个交易日 + 缓冲）；每日纯增量不再回拉重叠

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
_operations_lock = threading.RLock()


def _serialized(action):
    @wraps(action)
    def call(*args, **kwargs):
        with _operations_lock:
            return action(*args, **kwargs)
    return call

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
# 全市场代码表的取数预算（秒）。两个源原本各 timeout=90 且串行 -> 最坏 180s，
# 而本函数在下载接口的同步路径上。正常情况下首个源即命中（实测 1~2s），
# 预算给到 20s 已留 10 倍余量（bug-02）。
_UNIVERSE_BUDGET = 20.0
_UNIVERSE_PER_SOURCE = 90.0


def _universe_codes() -> list[str]:
    """取全市场 A 股代码（akshare；两个来源任一可用即可）。

    ⚠️ 两个源各自 `timeout=90` 且**串行** → 最坏 180s。本函数在
    `/api/history/download/*` 的**同步路径**上被调用（`_resolve_codes(scope="all")`），
    启动器一侧超时有限，故用公共件把整条链压进 `_UNIVERSE_BUDGET`（bug-02）。
    降级语义不变：两个源都取不到仍抛 RuntimeError，由调用方转成可读错误。
    """
    sources = []
    for name in ("stock_info_a_code_name", "stock_zh_a_spot_em"):
        func = getattr(ak, name, None)
        if func is not None:
            sources.append((name, lambda t, _fn=func: _ak(_fn, timeout=t)))
    _, frame = first_ok(sources, budget=_UNIVERSE_BUDGET, per_call=_UNIVERSE_PER_SOURCE,
                        is_ok=lambda df: df is not None and not getattr(df, "empty", True))
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
# 锁仓系数（十大流通股东 → r / L）的补采范围与状态
# --------------------------------------------------------------------------- #
def _lockup_universe(scope: str) -> list[str]:
    """锁仓补采的股票清单。

    `scope=all` 取**库里已下载日线的股票**（`code_latest_dates`）而不是 akshare
    全市场清单：锁仓系数只对「算得出筹码」的票有意义（没日线就算不出换手率），
    顺带省掉一次最坏 180 秒的 A 股代码列表请求。
    """
    if scope == "pool":
        return _normalize_codes(history_store.latest_snapshot_codes())
    if scope == "all":
        return sorted(price_store.code_latest_dates().keys())
    raise ValueError(f"未知的下载范围：{scope}")


def _lockup_pending(codes: list[str] | None = None) -> list[str]:
    """报告期过期或从未采集的股票（**需要补采**的那些）。

    只读一次 `lockup_cache` 的 GROUP BY（表就几千行），再和清单比对，
    不逐只点查 —— 全市场 5585 只逐只查要点 5585 次库。
    """
    wanted = codes if codes is not None else _lockup_universe("all")
    expected = stock_profile.expected_lockup_period()
    periods = price_store.lockup_period_map()
    return [code for code in wanted
            if str(periods.get(code) or "") < expected]


_lockup_state_cache: dict[str, Any] = {}
_lockup_state_at = 0.0


def lockup_state(force: bool = False) -> dict[str, Any]:
    """锁仓系数的落盘状态（下载页展示 + 前端决定要不要提示补采）。

    `total` 是**已下载日线的股票数**（锁仓系数的有效口径），`fresh` / `stale` /
    `missing` 在它内部互斥且相加等于 total。`cached_codes` 是缓存表的全部条数
    （可能含已下线的票），单独给出用于对账。

    扫全库 + 读一次缓存表不算便宜，结果按 `AUTO_STATE_TTL` 缓存；
    前端轮询时传 `force=true` 只在用户手动点「刷新」时用。
    """
    global _lockup_state_cache, _lockup_state_at
    now = time.time()
    if not force and _lockup_state_cache and now - _lockup_state_at < AUTO_STATE_TTL:
        snapshot = dict(_lockup_state_cache)
        snapshot["cached"] = True
        return snapshot

    universe = price_store.code_latest_dates()
    expected = stock_profile.expected_lockup_period()
    periods = price_store.lockup_period_map()
    fresh = stale = 0
    missing: list[str] = []
    for code in universe:
        period = str(periods.get(code) or "")
        if not period:
            missing.append(code)
        elif period >= expected:
            fresh += 1
        else:
            stale += 1
    overview = price_store.lockup_overview()
    state = {
        "expected": expected,
        "total": len(universe),
        "fresh": fresh,
        "stale": stale,
        "missing": len(missing),
        "missing_examples": missing[:10],
        "pending": len(missing) + stale,
        "cached_codes": overview["cached"],
        "updated_at": overview["updated_at"],
        "periods": overview["periods"],
        "checked_at": db.now_iso(),
        "cached": False,
    }
    _lockup_state_cache = dict(state)
    _lockup_state_at = now
    return dict(state)


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


def _is_suspended(code: str, expected: dt.date | None, *,
                  latest: str | None = None, earliest: str | None = None) -> bool:
    """长期无新交易日（停牌 / 退市）：库里有过正常历史、但最新日远落后于应有日。

    用来把这类票和「今天还没出 / 数据源延迟 / 新股上市晚」区分开：前者补也补不到，
    调用方应直接算完成、不记失败、不占限速额度。判定结合了「最新日落后幅度」
    （`SUSPENDED_GAP_DAYS`）与「是否曾有正常交易历史」（`SUSPENDED_HISTORY_MIN_DAYS`），
    后者用 earliest 排除上市不久的新股（新股由 `_no_older_history` 处理）。
    """
    if expected is None:
        return False
    if latest is None:
        latest = price_store.latest_bar_date(code, "raw")
    if not latest:
        return False
    try:
        last = dt.date.fromisoformat(str(latest)[:10])
    except (TypeError, ValueError):
        return False
    if last >= expected:
        return False
    if (expected - last).days < SUSPENDED_GAP_DAYS:
        return False
    if earliest is None:
        earliest = price_store.earliest_bar_date(code, "raw")
    if not earliest:
        return False
    try:
        first = dt.date.fromisoformat(str(earliest)[:10])
    except (TypeError, ValueError):
        return False
    return (expected - first).days >= SUSPENDED_HISTORY_MIN_DAYS


# --------------------------------------------------------------------------- #
# worker
# --------------------------------------------------------------------------- #
def _tdx_root(options: dict[str, Any]) -> str:
    """任务用的通达信目录：优先设置里配置的，没配就用自动检测到的。"""
    return download_store.get_setting("tdx_path", "") or tdx_reader.auto_detect() or ""


_tdx_valid_cache: dict[str, bool] = {}


def _tdx_is_valid(root: str) -> bool:
    """通达信目录是否可用（按 root 缓存，避免自动更新遍历全市场时每只都重扫目录）。"""
    if not root:
        return False
    cached = _tdx_valid_cache.get(root)
    if cached is None:
        cached = bool(tdx_reader.detect(root).get("valid"))
        _tdx_valid_cache[root] = cached
    return cached


def _import_tdx_bars(root: str, code: str,
                     begin: dt.date | str | None = None) -> None:
    """阶段 ①：读本机通达信日线并落盘。**不联网、不占限速额度**。

    `begin` 给定时只导入该日（含）之后的本地数据——增量补最新时用来避免把
    整只 .day（几年）全读一遍；`begin` 为 None（如「同步通达信历史」全量
    同步）则导入本地全部日线。
    """
    bars = tdx_reader.read_day_bars(root, code)
    if not bars:
        raise RuntimeError("本地通达信没有这只股票的日线文件")
    if begin is not None:
        if isinstance(begin, dt.date):
            begin = begin.isoformat()
        bars = [b for b in bars if str(b.get("date", ""))[:10] >= begin]
        if not bars:
            return                              # 本地这段没有比库内更新的，无需动作
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


def _download_lockup(task_id: str, runtime: _Runtime, item: dict[str, Any]) -> None:
    """锁仓系数补采：**不打日 K**，只把「十大流通股东 → r / L」取来落盘。

    与日 K 任务共用同一套队列 / 限速 / 暂停续传 / 失败退避，所以下载页上的
    暂停、继续、重试失败项对它同样有效。

    已是最新报告期的股票在 `start_task` 建队列时就被过滤掉，不会排进来；
    单只内部再由 `stock_profile.sync_lockup` 按报告期兜一次底（零请求返回）。
    """
    code = item["code"]
    seq = item["seq"]
    options = runtime.options
    try:
        if runtime.stop.is_set():
            return
        runtime.limiter.acquire()
        result = stock_profile.sync_lockup(
            code, force=bool(options.get("force_reference", False)))
        if not result.get("ok"):
            raise RuntimeError("；".join(result.get("errors") or ["锁仓系数取数失败"]))
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


def _download_one(task_id: str, runtime: _Runtime, item: dict[str, Any]) -> None:
    """下载一只股票的**某一个阶段**：日K（+ 可选复权因子 / 除权），失败退避重试。

    时间窗由 mode / phase 决定：
      完整历史 · 近期阶段：分界点 → 今天
      完整历史 · 更早阶段：起始日 → 分界点
      增量模式：交给 `sync_bars` 按库里最新日期 -14 天重叠，只补最新数据

    `kind=lockup` 时只补锁仓系数，不走下面的时间窗与日 K 逻辑。
    """
    code = item["code"]
    seq = item["seq"]
    phase = int(item.get("phase") or 1)
    options = runtime.options
    if (options.get("kind") or KIND_BARS) == KIND_LOCKUP:
        _download_lockup(task_id, runtime, item)
        return
    mode = options.get("mode") or MODE_FULL
    source = options.get("source") or SOURCE_ONLINE

    begin, stop = None, None
    no_early_gap = False
    overlap = int(options.get("overlap_days") or 0)
    if mode == MODE_FULL:
        split = _option_date(options, "split_date") or _split_date()
        oldest = _option_date(options, "start_date") or _default_start_date()
        if phase == 1:
            begin, stop = max(oldest, split), None
            # 近期阶段同样按库内已有数据裁剪。库内最新已到 2026-10 时，
            # 若仍固定「分界点(2021-10) → 今天」拉一遍，等于把 2021~今天 整段重下；
            # 用户把起始年份往前挪（如 2021→2020）就会触发，表现为「明明已有 21~26，
            # 却又从 20 下到 26」。这里把起点抬到「库内最新 − 重叠天数」，只补尾部增量；
            # 库内数据比用户起点还旧时由 max(oldest, …) 兜底为用户起点，避免漏掉
            # [库内最新, 用户起点] 之间的缺口段。
            have = price_store.latest_bar_date(code, "raw")
            if have:
                tail = dt.date.fromisoformat(have) - dt.timedelta(days=price_service.OVERLAP_DAYS)
                begin = max(oldest, tail)
        else:
            begin, stop = oldest, split        # 更早段：起始日 → 分界点
            # 增量补更早历史：库里已有更晚起步的数据时，**只补「更早缺口段」**。
            # 原实现固定拉 [起始日 → 分界点]，导致每次把起始日往前挪一格，
            # 库里已有的近段（如 2021+）就被整段重复下载一遍，浪费流量和时间。
            # 这里把 end 裁到「库内最早日期 − 1 天」，只下载库里还缺的那段；
            # 库内最早已不晚于起始日 → 没有更早缺口，本阶段直接算完成。
            have = price_store.earliest_bar_date(code, "raw")
            if have:
                gap_end = dt.date.fromisoformat(have) - dt.timedelta(days=1)
                if gap_end < begin:
                    no_early_gap = True
                else:
                    stop = gap_end
    elif mode == MODE_INCREMENTAL:
        # 增量起点：overlap=0（每日纯增量）只拉「库内最新日之后新增」(最新日+1→今天)；
        # overlap>0（周度修订回收）把 [最新日-overlap, 今天] 重拉一遍，兜回数据源对
        # 近段日线的事后修订——这是周检任务存在的目的。
        have = price_store.latest_bar_date(code, "raw")
        if have:
            base = dt.date.fromisoformat(have)
            begin = (base - dt.timedelta(days=overlap)) if overlap > 0 else (base + dt.timedelta(days=1))

    try:
        errors: list[str] = []
        if runtime.stop.is_set():
            return
        if no_early_gap:
            download_store.finish_code(task_id, seq, ok=True)   # 无更早缺口：算完成（不算失败）
            return
        # 库里已有足量历史 → 这只不动作（增量任务本身只拉最近几天，不需要这道判断）
        if mode != MODE_INCREMENTAL and _skip_if_covered(task_id, code, options, source):
            return
        # 纯增量(overlap=0)且库里已到最近应有交易日 → 零请求跳过；
        # 周度修订回收(overlap>0)要重拉最近窗口，即便已最新也要跑，故不跳过。
        used_tdx = False
        if mode == MODE_INCREMENTAL and overlap == 0 and source == SOURCE_ONLINE:
            expected = _expected_data_day()
            have_latest = price_store.latest_bar_date(code, "raw")
            if expected and have_latest and have_latest >= expected:
                download_store.finish_code(task_id, seq, ok=True)
                return
            if expected and have_latest and _is_suspended(code, expected):
                # 停牌 / 退市：补也补不到，直接算完成——不记失败、不占限速额度。
                download_store.finish_code(task_id, seq, ok=True)
                return
            # 优先本地通达信：本地比库内更新 → 直接读本地、不联网、不占限速额度。
            # 这样「有本地数据就不必联网」——自动更新 / 手动增量都受益，且本地缺的
            # 票（如新上市）会自然回落到下面的在线分支，不会漏数据。
            root = _tdx_root(options)
            if _tdx_is_valid(root):
                span = tdx_reader.day_range(root, code)
                if span and have_latest and span[1] >= str(have_latest)[:10]:
                    try:
                        _import_tdx_bars(
                            root, code,
                            dt.date.fromisoformat(str(have_latest)[:10]) + dt.timedelta(days=1))
                        used_tdx = True
                    except Exception:
                        used_tdx = False                  # 本地异常：回落在线
        if used_tdx:
            pass                                          # 日线已由本地导入，跳过在线拉取
        elif source == SOURCE_TDX:
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
            # 本地优先导入的票：复权参考同样优先本地 gbbq（解不出才回落在线）
            errors.extend(_sync_reference(runtime, options, code,
                                         SOURCE_TDX if used_tdx else source))

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
        if _is_suspended(code, _expected_data_day()):
            # 停牌 / 退市导致的失败：数据本来补不到，不计入失败、不刷屏。
            download_store.finish_code(task_id, seq, ok=True)
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
        # 「每日自动更新已跑过今天」只在**真的跑完时**登记（第 24 项）。
        # 原先在 `_kick_auto_update` 启动瞬间就写，于是进程一停就等于
        # 「今天已经跑过」，当天不会再起新任务；叠加任务随时会被停的正常工况，
        # 就出现了库里 17 个 auto 任务全部 paused 且进度停在半截的现象。
        if task.get("origin") == ORIGIN_AUTO:
            # 「今天跑过」按任务类型分别登记：日线的每日自动更新与锁仓系数的
            # 定期刷新各用一个键。共用一个键会让后跑完的那个把另一个的
            # 「已跑过」状态一起吃掉，另一个当天再也不被调度。
            # 「今天跑过」按任务类型分别登记：日线、锁仓、周度修订回收各用一个键，
            # 共用一个键会让后跑完的把另一个的「已跑过」状态一起吃掉。周度按 ISO 周
            # 登记，故用 `_iso_week()` 而非自然日。
            opts = task.get("options") or {}
            if opts.get("weekly_refresh"):
                download_store.set_setting("weekly_refresh_last_iso", _iso_week())
            elif opts.get("kind") == KIND_LOCKUP:
                download_store.set_setting("lockup_last_run", dt.date.today().isoformat())
            else:
                download_store.set_setting("auto_last_run", dt.date.today().isoformat())
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


def _expected_data_day(grace_days: int = 0) -> str | None:
    """**本地行情数据应有的最新日期**（交易日，不是今天）。

    这是「数据新不新鲜」的**唯一基准**，不能只看自然日：

    1. **收盘后才期望今天的数据**。盘中（<15:00）或非交易日都往回退一个交易日 ——
       否则每天上午都会被判成「全市场 5585 只数据陈旧」，把「今天本来就没数据」
       误报成缺口。`_market_has_closed_today()` 负责这个判断。
    2. 再用交易日历（拿不到就退化为「工作日」）往前找最近一个已收盘交易日。
    3. 兜底：库里已有的最大日期 —— 本地数据本身就是最准的日历。
    4. 全都拿不到才返回 None，由调用方退回自然日口径。
    """
    back = dt.date.today()
    if not _market_has_closed_today():
        back -= dt.timedelta(days=1)
    for _ in range(grace_days + 8):
        iso = back.isoformat()
        if _is_trade_day(back):
            return iso
        back -= dt.timedelta(days=1)
    latest = price_store.code_latest_dates()
    if latest:
        return max(str(day) for day in latest.values())
    return None


def _market_has_closed_today() -> bool:
    """今天是否已经收盘（非交易日直接算「已收盘」——今天本就没有新数据）。"""
    now = dt.datetime.now()
    try:
        from features.market.service import _a_share_is_trade_day
        if not _a_share_is_trade_day(now):
            return True
    except Exception:                           # noqa: BLE001 - 日历不可用则按时间判断
        pass
    return (now.hour, now.minute) >= (15, 0)


def _is_trade_day(day: dt.date) -> bool:
    """是否交易日：优先交易日历，取不到退化为「工作日」。"""
    try:
        from features.market.service import _a_share_trade_days
        days = _a_share_trade_days()
    except Exception:                           # noqa: BLE001
        days = set()
    if days:
        return day.isoformat() in days
    return day.weekday() < 5


def _median_day(days: list[str]) -> str | None:
    """一组 YYYY-MM-DD 的中位数（ISO 字符串排序即日期序，可直接排）。"""
    if not days:
        return None
    ordered = sorted(str(day) for day in days if day)
    if not ordered:
        return None
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return min(ordered[mid - 1], ordered[mid])


# 覆盖区间与「陈旧度」都要用全库聚合，同一轮刷新里复用同一份结果：
SPAN_CACHE_TTL = 60.0
_span_cache: dict[str, tuple[float, dict[str, str]]] = {}


def _span_dates(kind: str, loader) -> dict[str, str]:
    """`code_latest_dates` / `code_earliest_dates` 的带缓存包装。

    两者都是全表聚合（几千只 × 各年份分片），而「陈旧度」和「覆盖区间」
    在一次刷新里都要用到 latest。缓存 TTL 很短：只为消掉同一次刷新里的
    重复扫描，代价是下载过程中覆盖区间最多滞后 SPAN_CACHE_TTL 秒。
    """
    hit = _span_cache.get(kind)
    now = time.time()
    if hit and now - hit[0] < SPAN_CACHE_TTL:
        return hit[1]
    try:
        result = loader()
    except Exception:                             # noqa: BLE001 - 聚合失败按无数据处理
        return {}
    _span_cache[kind] = (now, result)
    return result


def _coverage_span() -> tuple[str | None, str | None]:
    """**多数股票都覆盖到的区间**（下载页展示用）。

    不用「库里最早 / 最晚」，那会被一只极端股票带偏：某只 2005 年上市的
    老票会把起点拉到 2005，看起来像「全库覆盖 20 年」，其实多数票没有。
    也不用交集（交集会被那一只最短的票压到很窄）。因此两端各取**中位数**：
    一半股票比它早、一半比它晚，代表「典型水平」的覆盖边界。
    """
    earliest = _span_dates("earliest", price_store.code_earliest_dates)
    latest = _span_dates("latest", price_store.code_latest_dates)
    shared = set(earliest) & set(latest)
    if not shared:
        return None, None
    return (_median_day([earliest[code] for code in shared]),
            _median_day([latest[code] for code in shared]))


def _downloaded_staleness() -> tuple[int, int, list[str], str | None]:
    """(已下载只数, 数据陈旧的只数, 陈旧示例, 库里最新的日K日期)。

    判定口径：**只看库里已经下过的股票** —— 没下过的股票不算缺口。

    ⭐ 「陈旧」的基准是**交易日**，不是自然日（第 24 项）。原先用
    `今天 - FRESHNESS_DAYS 个自然日` 当阈值，把周末与节假日全算了进去，
    最多让阈值偏松 4 天（长假后连续几个交易日都被判成「不陈旧」）。
    现改为按 `_expected_data_day()`给出的应有日期逐票判定。
    """
    latest = _span_dates("latest", price_store.code_latest_dates)
    if not latest:
        return 0, 0, [], None
    expected = _expected_data_day()
    if expected is None:
        # 交易日历与本地数据都给不出基准时，退回原先的自然日口径
        expected = (dt.date.today() - dt.timedelta(days=FRESHNESS_DAYS)).isoformat()
    expected_date = dt.date.fromisoformat(expected) if expected else None
    earliest = _span_dates("earliest", price_store.code_earliest_dates)
    # 停牌 / 退市票长期落后是「补也补不到」，不计入陈旧，避免拖垮自动更新资格。
    stale = sorted(
        code for code, day in latest.items()
        if str(day) < expected
        and not _is_suspended(code, expected_date, latest=str(day), earliest=earliest.get(code))
    )
    return len(latest), len(stale), stale[:10], max(str(day) for day in latest.values())


# 长期停牌 / 已无新数据的股票天然落后，不该因此锁死自动更新：
# 允许「落后超过 FRESHNESS_DAYS 天的只数」在 10 只或已下载总数的 1% 以内（取大者）。
STALE_TOLERANCE_MIN = 10
STALE_TOLERANCE_RATIO = 0.01

# 停牌 / 退市判定：库里曾有正常交易历史，但最新日长期落后于应有交易日。
# 用来避免把「补也补不到」的票记成失败（反复重试、刷屏、拖累自动更新资格）。
SUSPENDED_GAP_DAYS = 10             # 最新日落后超过此自然日数（≈一周多交易日）才算「长期」
SUSPENDED_HISTORY_MIN_DAYS = 60     # 且曾有至少这么长的交易历史（区别于上市不久的新股）


def _auto_eligible(downloaded: int, stale: int) -> bool:
    """能否开启「每天自动更新」：历史基本到位即算过（容忍少量停牌股）。"""
    if downloaded <= 0:
        return False
    return stale <= max(STALE_TOLERANCE_MIN, int(downloaded * STALE_TOLERANCE_RATIO))


def _options_for(mode: str, *, source: str = SOURCE_ONLINE, start_date: str | None = None,
                 concurrency: int = DEFAULT_CONCURRENCY,
                 rate_limit: float = DEFAULT_RATE_LIMIT,
                 with_reference: bool = True,
                 force_reference: bool = False,
                 kind: str = KIND_BARS,
                 overlap_days: int = 0,
                 weekly_refresh: bool = False) -> dict[str, Any]:
    """组装任务选项：手动 / 自动 / 闲时共用一套，分界点与分段数不重复实现。"""
    begin: dt.date | None = None
    if mode == MODE_FULL:
        begin = _option_date({"start_date": start_date}, "start_date") or _default_start_date()
    split = _split_date()
    # 锁仓补采没有时间窗概念（股东数据按季披露），固定跑一轮
    if kind == KIND_LOCKUP:
        phases = 1
    elif source == SOURCE_TDX:
        # 本地通达信只跑一轮：本机有几年的日线就同步几年，不再联网补更早的历史
        #（系统里最长的需求是 MA60 / RPS≈250 日，5 年绰绰有余）
        phases = 1
    else:
        phases = 2 if (mode == MODE_FULL and begin is not None and begin < split) else 1
    return {
        "mode": mode,
        "kind": kind,
        "source": source,
        "start_date": begin.isoformat() if begin else None,
        "split_date": split.isoformat(),
        "phases": phases,
        "concurrency": _clamp_int(concurrency, DEFAULT_CONCURRENCY, 1, MAX_CONCURRENCY),
        "rate_limit": max(0.1, min(20.0, float(rate_limit or DEFAULT_RATE_LIMIT))),
        "with_reference": bool(with_reference),
        "force_reference": bool(force_reference),
        "overlap_days": max(0, int(overlap_days or 0)),
        "weekly_refresh": bool(weekly_refresh),
    }


def freshness(codes: list[str] | None = None) -> dict[str, Any]:
    """行情数据新鲜度（第 24 项）：K 线页 / 策略页运行前的前置检查。

    `codes` 为空 = 统计全市场（策略页的用法）。**判定基准是交易日**
    （`_expected_data_day`），不是自然日 —— 否则盘中与长假后都会误报缺口。

    返回 `need_refresh` 给前端决定「是否要先补数据」。刻意**不在这里自动启动
    下载**：全市场一轮 60~80 分钟（实测吞吐 1.2~1.4 只/秒），同步等待不现实；
    该由自动更新提前把数据补齐，这里只负责如实报告 + 提供单只补齐入口。
    """
    wanted = [str(c).strip() for c in (codes or []) if str(c).strip()]
    latest = price_store.code_latest_dates()
    if not latest:
        return {"expected": None, "total": 0, "covered": 0, "stale": 0,
                "latest": None, "need_refresh": bool(wanted), "checked": []}
    expected = _expected_data_day()
    if wanted:
        checked = [c for c in wanted if c in latest]
        # 库里完全没有的票也算缺口（第一次打开的新股）
        missing = [c for c in wanted if c not in latest]
    else:
        checked = list(latest.keys())
        missing = []
    covered = sum(1 for c in checked if str(latest[c]) >= expected)
    stale_codes = sorted(c for c in checked if str(latest[c]) < expected)
    return {
        "expected": expected,
        "total": len(wanted) if wanted else len(latest),
        "checked": len(checked),
        "covered": covered,
        "stale": len(stale_codes),
        "missing": len(missing),
        "latest": max(str(d) for d in latest.values()),
        "need_refresh": bool(stale_codes or missing),
        "stale_examples": (stale_codes + missing)[:10],
        "market_closed": _market_has_closed_today(),
    }


def sync_one(code: str) -> dict[str, Any]:
    """**同步**补齐单只股票的日 K（第 24 项，K 线页用）。

    与遍历下载共用 `price_service`（同一套数据源与降级逻辑），但走单只同步：
    实测 1~3 秒，页面等得起。顺带把复权因子、流通股本一起同步 ——
    后者是筹码分布的前置，只补 K 线会让筹码页算出「因子缺失」的空值。
    """
    from core import price_service
    bars = price_service.sync_bars(code)
    detail: dict[str, Any] = {"bars": bars}
    for name, fn in (("factors", price_service.sync_factors),
                     ("share_capital", price_service.sync_share_capital),
                     # 锁仓系数一并补：K 线页进入就顺手把筹码峰的前置数据备齐，
                     # 用户切到筹码 / 策略页时不必再等一次东财取数。
                     ("lockup", stock_profile.sync_lockup)):
        try:
            detail[name] = fn(code)
        except Exception as exc:                   # noqa: BLE001 - 附加项失败不否决补齐
            detail[name] = {"ok": False, "error": str(exc)}
    expected = _expected_data_day()
    actual = None
    try:
        actual = price_store.latest_bar_date(code)
    except Exception:                             # noqa: BLE001
        actual = None
    return {"code": code, "expected": expected, "latest": actual,
            "fresh": bool(actual and expected and str(actual) >= expected),
            "detail": detail}


def _find_pending_auto_task() -> str | None:
    """今天还没跑完的「自动更新」任务 id（取最近的一个），没有则 None。

    只认 `origin=auto`：手动任务是用户自己安排的，闲时任务走
    `_ensure_idle_task`。候选里排除已取消的 —— 取消就该生效。
    """
    today = dt.date.today().isoformat()
    best: tuple[str, str] | None = None
    for task in download_store.list_tasks(200):
        if task.get("origin") != ORIGIN_AUTO or task["status"] not in ("paused",):
            continue
        if str(task.get("created_at") or "")[:10] != today:
            continue                      # 昨天没跑完的交给启动时的续传，不在这重复排
        if download_store.remaining(task["id"]) <= 0:
            continue
        stamp = str(task.get("created_at") or "")
        if best is None or stamp > best[1]:
            best = (task["id"], stamp)
    return best[0] if best else None


def _iso_week() -> str:
    """当前 ISO 周标签，如 '2026-W43'。用于「每周一次」去重——按 ISO 周而非死磕周五，
    节假日顺延到新周首次调度也不会漏跑或重复跑。"""
    return dt.date.today().strftime("%G-W%V")


def _kick_weekly_refresh() -> str | None:
    """**周度修订回收**：每个 ISO 周一次，把已下载股票的「最近 N 天」重拉一遍，
    兜回数据源（akshare/东财）对近段日线的事后修订——每日纯增量已不再回拉重叠，
    这类修订只能靠周期性重拉来回收。

    按 `weekly_refresh_last_iso`（ISO 周）去重：本周没跑过才起，跑完登记；
    周五碰上休市则顺延到新周首次调度。只排确有数据的股票；无待回收时登记一次，
    免得每天空扫。任务带 `overlap_days>0`，`_download_one` 据此把起点抬到最新日-N 天。
    """
    if _has_alive_task():
        return None
    latest = price_store.code_latest_dates()
    codes = sorted(c for c, d in latest.items() if d)      # 只排确有数据的
    if not codes:
        download_store.set_setting("weekly_refresh_last_iso", _iso_week())
        return None
    options = _options_for(MODE_INCREMENTAL, concurrency=_idle_concurrency(),
                           overlap_days=WEEKLY_OVERLAP_DAYS, weekly_refresh=True)
    # 展示窗口反映 overlap：起点=最早那只的 [最新日-N]；终点=今天
    _stamp_span(options, codes, latest)
    task_id = "wk-" + uuid.uuid4().hex
    download_store.create_task(task_id, "downloaded", options, codes, 1, origin=ORIGIN_AUTO)
    _spawn(task_id, options)
    return task_id


def _kick_auto_update() -> str | None:
    """给「库里已下载的股票」补最新数据（每日自动更新）。

    只排「库内最新 < 应有交易日」的股票：都已最新时**根本不建任务**。
    原先固定把全部已下载股票排进队列，于是每天都要把 5585 只全程巡检一遍
    （每只都发一次 14 天重叠请求），用户看到的就像「增量刚补完又开始重新下了」；
    而且只要上一个任务没跑完（重启/暂停），`auto_last_run` 就不会登记，重启后再排一遍。
    """
    latest = price_store.code_latest_dates()
    if not latest or _has_alive_task():
        return None
    expected = _expected_data_day()
    codes = sorted(code for code, day in latest.items()
                   if not expected or str(day) < expected)
    if not codes:
        return None
    _ensure_disk_space(len(codes), MODE_INCREMENTAL)
    options = _options_for(MODE_INCREMENTAL, concurrency=_idle_concurrency())
    # 增量窗口：每只从库内最新日 -14 天补到今天，这里记下并集供下载页展示
    _stamp_span(options, codes, latest)
    task_id = "dl-" + uuid.uuid4().hex
    download_store.create_task(task_id, "downloaded", options, codes, 1, origin=ORIGIN_AUTO)
    # `auto_last_run` **不在这里写**：它由 `_run` 在任务真正跑完时登记。
    # 启动即写等于「还没补完就先宣布今天跑过了」，进程一停当天就再也不会起新任务。
    _spawn(task_id, options)
    return task_id


def _kick_lockup_update() -> str | None:
    """锁仓系数的**定期刷新**：新一季十大流通股东出齐后自动补采。

    与日线的每日自动更新分开排队（`_scheduler_tick` 里串行调用），
    两者都慢，并行会互相抢带宽与东财配额。

    当天已跑过（`lockup_last_run`，由 `_run` 在任务真正跑完时登记）就不再起；
    没有待补采的股票时也登记一次，免得每天白扫一遍全表。
    """
    if _has_alive_task():
        return None
    pending = _lockup_pending()
    if not pending:
        download_store.set_setting("lockup_last_run", dt.date.today().isoformat())
        return None
    options = _options_for(MODE_INCREMENTAL, concurrency=_idle_concurrency(),
                           with_reference=False, kind=KIND_LOCKUP)
    task_id = "lk-" + uuid.uuid4().hex
    download_store.create_task(task_id, "downloaded", options, pending, 1, origin=ORIGIN_AUTO)
    _spawn(task_id, options)
    return task_id


def _resume_idle(task_id: str, task: dict[str, Any]) -> None:
    """把闲时任务拉起来，并把并发降到闲时档位。"""
    options = dict(task.get("options") or {})
    options["concurrency"] = _idle_concurrency()
    download_store.update_options(task_id, options)
    with _runtimes_lock:
        runtime = _runtimes.get(task_id)
        alive = runtime is not None and runtime.manager is not None and runtime.manager.is_alive()
    if not alive:
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
    covered = download_store.task_code_set(current) if current else set()
    missing = [code for code in _normalize_codes(_universe_codes()) if code not in covered]
    if not missing:
        download_store.set_setting("idle_refreshed_at", today)
        return None
    _ensure_disk_space(len(missing), MODE_FULL)
    options = _options_for(MODE_FULL, concurrency=_idle_concurrency())
    task_id = "dl-" + uuid.uuid4().hex
    download_store.create_task(task_id, "all", options, missing,
                               options["phases"], origin=ORIGIN_IDLE)
    download_store.set_setting("idle_task_id", task_id)
    _spawn(task_id, options)
    download_store.set_setting("idle_refreshed_at", today)
    return task_id


@_serialized
def _scheduler_tick() -> None:
    """一次调度：到点就补最新数据，空闲就接着补完整历史（两者不同时跑）。"""
    # 每日清理（过期日志 / 诊断包 / 可重建缓存 / 跨周周榜）：
    # **刻意放在最前面** —— 后面的 `if _has_alive_task(): return` 会让清理被长时间
    # 运行的下载任务「饿死」，而有任务在跑时恰恰最需要清日志。
    # `schedule_tick()` 内部按**日期**去重，60 秒一轮进来也只会真的清一次。
    try:
        import cleanup
        cleanup.schedule_tick()
    except Exception:                         # noqa: BLE001 - 清理失败绝不能影响下载调度
        pass

    # 老增量任务补算时间窗（只补一次；同样放在 alive 判断之前，运行中的任务也要补）
    try:
        _backfill_spans()
    except Exception:                         # noqa: BLE001 - 补展示不能影响调度
        pass

    if _has_alive_task():
        return
    stored = download_store.settings_map()
    today = dt.date.today().isoformat()
    if stored.get("auto_update") == "1":
        if stored.get("auto_last_run") != today and dt.datetime.now().hour >= AUTO_UPDATE_HOUR:
            # 今天还没跑完过（`auto_last_run` 现在只在任务真正跑完时登记）。
            # 若今天已有一个未完成的 auto 任务，**接着它跑**而不是另起一个 ——
            # 全市场一轮要 60~80 分钟，一个 60 秒一轮的调度器不能每轮都新建。
            # 这条同时也是断电续传的第二道保险：启动时 `_resume_auto_tasks`
            # 没接上的（例如任务被暂停过），这里会再试一次。
            pending = _find_pending_auto_task()
            if pending:
                _resume_idle(pending, download_store.get_task(pending))
            else:
                _kick_auto_update()
            return
        # 日线补完了（或今天不补），接着刷新锁仓系数。**串行**：两者都慢，
        # 并行会互相抢带宽与东财请求配额。报告期没更新时 `_kick_lockup_update`
        # 一个请求都不发，只会顺手登记「今天巡检过」。
        if stored.get("lockup_last_run") != today and dt.datetime.now().hour >= AUTO_UPDATE_HOUR:
            _kick_lockup_update()
        # 周度修订回收：每个 ISO 周一次，把已下载股票的近段重拉一遍回收修订。
        # 后台自动、无交互；本周没跑过才起，节假日顺延到新周首次调度。
        # 与日线/锁仓串行，避免三套慢任务同时抢带宽。
        if (stored.get("weekly_refresh_last_iso") != _iso_week()
                and dt.datetime.now().hour >= AUTO_UPDATE_HOUR):
            _kick_weekly_refresh()
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


@_serialized
def start_task(*, scope: str = "all", source: str = SOURCE_ONLINE,
               mode: str = MODE_FULL, start_date: str | None = None,
               concurrency: int = DEFAULT_CONCURRENCY,
               rate_limit: float = DEFAULT_RATE_LIMIT, with_reference: bool = True,
               force_reference: bool = False,
               kind: str = KIND_BARS) -> str:
    """启动一个遍历下载任务，返回 task_id。

    mode=full（默认）走「先近后远」两轮：全市场先补齐最近 `RECENT_YEARS` 年，
    再回头补更老的历史——中途被打断，也已经拿到了能用的近期数据。
    mode=incremental 只补最新数据（每只按库里最新日期 -14 天重叠），用于日常更新。

    source=tdx 时**只同步本机通达信已有的日线**（实测约 5 年），走一轮、不联网：
    系统里最长的需求是 MA60 / RPS≈250 个交易日，5 年绰绰有余，不再联网补更早的历史。

    kind=lockup 时**只补锁仓系数**（不打日 K）：范围取「已下载日线的股票」，
    且只排「报告期过期或从未采集」的那些，已是最新的直接不进队列。
    """
    if _has_alive_task():
        raise RuntimeError("已有任务正在运行或暂停中，请继续该任务，或取消并等待收尾后再新建")
    if mode not in (MODE_FULL, MODE_INCREMENTAL):
        raise ValueError(f"未知的下载模式：{mode}")
    if source not in (SOURCE_ONLINE, SOURCE_TDX):
        raise ValueError(f"未知的数据来源：{source}")
    if kind not in (KIND_BARS, KIND_LOCKUP):
        raise ValueError(f"未知的任务类型：{kind}")

    if kind == KIND_LOCKUP:
        return _start_lockup_task(
            scope=scope, concurrency=concurrency, rate_limit=rate_limit,
            force_reference=force_reference)

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
    # 增量任务没有统一时间窗，算一次并集写进 options，前端才能显示真实日期
    _stamp_span(options, resolved)
    task_id = "dl-" + uuid.uuid4().hex
    download_store.create_task(task_id, scope, options, resolved,
                               options["phases"], origin=ORIGIN_MANUAL)
    _spawn(task_id, options)
    return task_id


def _start_lockup_task(*, scope: str, concurrency: int,
                       rate_limit: float, force_reference: bool) -> str:
    """建一个「只补锁仓系数」的任务（手动入口，`lk-` 前缀便于在任务列表里认出来）。"""
    resolved = _lockup_universe(scope)
    if not resolved:
        raise RuntimeError("还没有已下载日线的股票，请先下载历史行情")
    pending = resolved if force_reference else _lockup_pending(resolved)
    if not pending:
        raise RuntimeError("所有股票的锁仓系数都已是最新报告期，无需补采")
    options = _options_for(MODE_INCREMENTAL, concurrency=concurrency,
                           rate_limit=rate_limit, with_reference=False,
                           force_reference=force_reference, kind=KIND_LOCKUP)
    task_id = "lk-" + uuid.uuid4().hex
    download_store.create_task(task_id, scope, options, pending, 1, origin=ORIGIN_MANUAL)
    _spawn(task_id, options)
    return task_id


def _stamp_span(options: dict[str, Any], codes: list[str],
                latest: dict[str, str] | None = None) -> None:
    """给**增量**任务记下这次真正请求的时间窗（下载页展示用）。

    增量是「逐票只拉库内最新日之后真正新增的交易日」（最新日 + 1 → 今天），
    没有统一区间，所以展示取这些窗口的**并集**：起点取最早那只的起点，终点取今天。
    创建任务时算一次写进 options，轮询时零成本；老任务没有这两个键就回退成
    「仅补最近」（前端已兼容）。

    完整历史任务不做任何事（它的窗口就是 options.start_date ~ 今天）。
    """
    if str(options.get("mode") or MODE_FULL) != MODE_INCREMENTAL:
        return
    overlap = int(options.get("overlap_days") or 0)
    days = latest if latest is not None else price_store.code_latest_dates()
    begin: dt.date | None = None
    for code in codes:
        day = str(days.get(code) or "")
        if not day:
            continue
        try:
            moment = dt.date.fromisoformat(day)
        except ValueError:
            continue
        # overlap>0（周度修订回收）：窗口是 [最新日-overlap, 今天]；
        # 否则（每日纯增量）：只拉新增 [最新日次日, 今天]。与 _download_one 口径一致。
        start = (moment - dt.timedelta(days=overlap)) if overlap > 0 else (moment + dt.timedelta(days=1))
        begin = start if begin is None or start < begin else begin
    if begin is None:                       # 全都没下过：就是从头拉，不编区间
        return
    options["span_begin"] = begin.isoformat()
    # 终点用「应有交易日」，不要用创建任务时的 dt.date.today() 写死：任务创建于
    # 昨日或创建时尚未收盘，都会让写死的 end 比实际下载目标（_expected_data_day）
    # 少一天，造成「请求区间停在 10-19 而非 10-20」的展示错误。
    options["span_end"] = _expected_data_day() or dt.date.today().isoformat()


def _backfill_spans() -> None:
    """给**早于 `_stamp_span` 之前建的**增量任务补算时间窗（只补一次）。

    老任务的 options 里没有 `span_begin`，下载页只能显示「仅补最近」。这里在
    调度器里补一次并写回 options，之后轮询就有真实日期了。
    """
    for task in download_store.list_tasks(20):
        options = task.get("options") or {}
        if str(options.get("mode") or MODE_FULL) != MODE_INCREMENTAL:
            continue
        if options.get("span_begin"):
            continue
        if task["status"] not in ("running", "queued", "paused"):
            continue
        try:
            codes = sorted(download_store.task_code_set(task["id"]))
        except Exception:                       # noqa: BLE001 - 补展示不能影响调度
            continue
        before = dict(options)
        _stamp_span(options, codes)
        if options != before:
            download_store.update_options(task["id"], options)


def _requested_span(task: dict[str, Any]) -> tuple[str | None, str | None]:
    """这个任务**请求下载的时间段**（下载页展示用），空值表示不限。

    完整历史：从 options 里的起始日到今天；
    增量：创建时由 `_stamp_span` 算好的并集窗口（逐票其实是从各自库内最新
    日期前推的重叠段，展示的是这些窗口的并集）。
    """
    options = task.get("options") or {}
    if str(options.get("kind") or KIND_BARS) == KIND_LOCKUP:
        return None, None                   # 锁仓补采没有时间窗，看报告期
    if str(options.get("mode") or MODE_FULL) != MODE_FULL:
        begin = str(options.get("span_begin") or "")
        if not begin:
            return None, None
        # 终点随「应有交易日」动态刷新，不用创建任务时写死的 span_end：
        # 任务创建于昨日 / 创建时尚未收盘都会让写死的 end 比实际下载目标少一天。
        end = _expected_data_day() or str(options.get("span_end") or "")
        return begin, end
    begin = _option_date(options, "start_date")
    if begin is None:
        return None, None
    return begin.isoformat(), dt.date.today().isoformat()


def _decorate(task: dict[str, Any]) -> dict[str, Any]:
    done = task["completed"] + task["failed"]
    task["percent"] = round(done * 100.0 / max(1, task["total"]), 1)
    task["remaining"] = download_store.remaining(task["id"])
    task["failures"] = download_store.failures(task["id"])
    task["kind"] = str((task.get("options") or {}).get("kind") or KIND_BARS)
    requested_begin, requested_end = _requested_span(task)
    task["requested_begin"] = requested_begin
    task["requested_end"] = requested_end
    # 增量任务的区间是「各票窗口的并集」，前端要据此换一种说法
    task["requested_incremental"] = (
        str((task.get("options") or {}).get("mode") or MODE_FULL) == MODE_INCREMENTAL
        and bool(requested_begin))
    # overlap_days>0 表示这是「周度修订回收」任务（重拉近段而非只补新增），前端据此区分文案
    task["overlap_days"] = int((task.get("options") or {}).get("overlap_days") or 0)
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
        "lockup_last_run": stored.get("lockup_last_run") or "",
        "tdx_path": stored.get("tdx_path") or "",
    }


def tdx_status(path: str | None = None) -> dict[str, Any]:
    """通达信目录检测结果（设置页 / 下载页用）：是否有效、有多少只、覆盖到哪天。

    没配置时**回退到自动检测**，与 `_resolve_codes` / `_tdx_root` 保持一致：
    配置存在数据库里，换数据目录后会是一个空库，若这里不回退，
    下载页的「同步通达信历史数据」会凭空空掉，看起来像「配置丢了」。
    """
    configured = download_store.get_setting("tdx_path", "")
    target = path or configured or tdx_reader.auto_detect() or ""
    if target:
        info = tdx_reader.detect(target)
    else:
        info = {"path": "", "exists": False, "valid": False,
                "markets": {}, "total_files": 0, "sample": "", "has_gbbq": False}
    info["configured"] = configured
    info["auto_detected"] = tdx_reader.auto_detect()
    info["codes"] = len(tdx_reader.list_codes(target)) if info.get("valid") else 0
    return info


@_serialized
def update_settings(*, auto_update: bool | None = None, idle_download: bool | None = None,
                    idle_concurrency: int | None = None,
                    tdx_path: str | None = None) -> dict[str, Any]:
    """保存后台开关。打开即时生效——不等下一个轮询周期。

    开启「自动更新」要求：已下载的股票里没有一只落后超过 FRESHNESS_DAYS 天。
    否则用户会以为它在保持最新，实际大片历史还空着。
    """
    global _auto_state_at
    values = {}
    if tdx_path is not None:
        text = str(tdx_path).strip()
        if text and not tdx_reader.detect(text)["valid"]:
            raise ValueError(f"该目录下没有通达信日线数据（vipdoc/*/lday）：{text}")
        values["tdx_path"] = text
    if idle_concurrency is not None:
        values["idle_concurrency"] = str(_clamp_int(idle_concurrency, IDLE_CONCURRENCY, 1, 2))
    if idle_download is not None:
        values["idle_download"] = "1" if idle_download else "0"
    if auto_update is not None:
        values["auto_update"] = "1" if auto_update else "0"
    if auto_update:
        downloaded, stale, _, _ = _downloaded_staleness()
        if not _auto_eligible(downloaded, stale):
            raise ValueError(
                f"暂时不能开启自动更新：已下载 {downloaded} 只，"
                f"其中 {stale} 只落后超过 {FRESHNESS_DAYS} 天（超出容忍范围）。"
                f"请先把历史补齐（可用「后台闲时下载」），再开启。")
    download_store.set_settings(values)
    _auto_state_at = 0.0
    # 关闭开关也要暂停对应的在途任务；已发出的单次请求允许完成。
    for task in download_store.list_tasks(1000):
        if ((auto_update is False and task.get("origin") == ORIGIN_AUTO)
                or (idle_download is False and task.get("origin") == ORIGIN_IDLE)):
            if task["status"] in ("queued", "running"):
                cancel(task["id"])
    warning = None
    try:
        if auto_update:
            _kick_auto_update()
        if idle_download:
            _revive_idle_task()
            if not _has_alive_task():
                _ensure_idle_task()
    except Exception as exc:
        warning = f"设置已保存，但本次启动下载失败：{exc}；后台将按调度重试"
    return {**settings_view(), "scheduling_warning": warning}


def auto_state(force: bool = False) -> dict[str, Any]:
    """后台开关的**可开启状态** + 当前设置。

    要靠 `code_latest_dates()` 扫一遍全库，成本不低，因此结果缓存 AUTO_STATE_TTL 秒。
    """
    global _auto_state, _auto_state_at
    now = time.time()
    if not force and _auto_state and now - _auto_state_at < AUTO_STATE_TTL:
        cached = dict(_auto_state)
        cached.update(settings_view())
        cached["cached"] = True
        return cached
    downloaded, stale, examples, latest_date = _downloaded_staleness()
    cover_begin, cover_end = _coverage_span()
    state = dict(settings_view())
    state.update({
        "downloaded": downloaded,
        "stale": stale,
        "stale_examples": examples,
        "latest_date": latest_date,
        "cover_begin": cover_begin,
        "cover_end": cover_end,
        "eligible": _auto_eligible(downloaded, stale),
        "freshness_days": FRESHNESS_DAYS,
        "checked_at": db.now_iso(),
        "cached": False,
    })
    _auto_state = dict(state)
    _auto_state_at = now
    return dict(state)


@_serialized
def pause(task_id: str) -> dict[str, Any]:
    task = download_store.get_task(task_id)
    if task is None:
        raise KeyError(task_id)
    if task["status"] not in ("completed", "cancelled"):
        download_store.update_task(task_id, status="paused")
    return task_view(task_id)


@_serialized
def resume(task_id: str) -> dict[str, Any]:
    task = download_store.get_task(task_id)
    if task is None:
        raise KeyError(task_id)
    if task["status"] == "cancelled":
        raise RuntimeError("任务已取消，不能继续（请重新「开始下载」）")
    if task["status"] != "paused":
        raise RuntimeError("只有已暂停的任务可以继续")
    with _runtimes_lock:
        runtime = _runtimes.get(task_id)
        alive = runtime is not None and runtime.manager is not None and runtime.manager.is_alive()
    if not alive:
        if _has_alive_task():
            raise RuntimeError("请先结束当前运行的任务")
        download_store.requeue_running(task_id)
    # 重新检查日期：把已完成（done）的项退回待下载，由 _download_one 实时重判
    # （已最新走 `have_latest >= expected` 秒跳过，滞后的补最新）。否则「暂停→跨天→继续」
    # 会漏掉已完成票在暂停期间新增的交易日（如 10-19 完成、10-20 数据出来后不再补）。
    download_store.requeue_done(task_id)
    download_store.update_task(task_id, status="running")
    _spawn(task_id, task["options"])
    return task_view(task_id)


@_serialized
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


@_serialized
def retry_failed(task_id: str) -> dict[str, Any]:
    """只重跑失败的那几只（成功的不动），用于收尾补漏。"""
    task = download_store.get_task(task_id)
    if task is None:
        raise KeyError(task_id)
    if _has_alive_task():
        raise RuntimeError("请先取消当前任务并等待在途请求结束，再重试失败项")
    count = download_store.reset_failed(task_id)
    if not count:
        return task_view(task_id)
    download_store.update_task(task_id, status="running")
    _spawn(task_id, task["options"], force=True)
    return task_view(task_id)


def _resume_auto_tasks() -> None:
    """启动时把「自动更新」留下的断点自动接上（断电/关机续传）。

    「随时会停」是这个模块的**正常工况**，不是异常：进程被关、机器断电、
    用户重启，内存里的 worker 全没了，但队列与断点都写在库里
    （`download_codes.status` / `retries`，`recover_stale` 已把 running 的
    代码退回 pending）。此前唯一的续跑入口是用户点「继续」，而自动更新
    没人会点 —— 于是每天 18:00 的任务跑到一半就`paused`，进度永远卡住
    （实测库里17 个 auto 任务全是paused，进度 2~3966 只不等）。

    这里在启动时自动恢复，且**只恢复 auto 来源**：手动任务是用户自己
    安排的，不该在他重启后被后台悄悄拉起；`idle` 任务由 `_ensure_idle_task`
    在调度里自行处理（见其注释「接着上次没下完的继续」），不重复处理。
    """
    for task in download_store.list_tasks(200):
        if task.get("origin") != ORIGIN_AUTO or task["status"] != "paused":
            continue
        if download_store.remaining(task["id"]) <= 0:
            continue                      # 已跑完，只是没落finished，交给调度重开
        try:
            resume(task["id"])
        except Exception:                 # noqa: BLE001 - 一个续不上不影响其它
            continue


def _start_background() -> None:
    """启动后台：先恢复上次没跑完的任务（断电续传），再起调度线程。"""
    try:
        download_store.recover_stale()
    except Exception:                             # noqa: BLE001 - 表还没建好时静默跳过
        pass
    try:
        _resume_auto_tasks()
    except Exception:                             # noqa: BLE001 - 同上，绝不让后台起不来
        pass
    threading.Thread(target=_scheduler_loop, daemon=True).start()


_start_background()
