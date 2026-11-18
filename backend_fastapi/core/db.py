# -*- coding: utf-8 -*-
"""本地 SQLite 基础设施：连接、建表调度、元信息与完整性钩子。

库文件布局（根为 `config.DATA_DIR`，库本身位于其 `state/` 分区）：

    state/stock_history.db  主库：股票池快照、消息面、同步任务（体积小）
    bars/factors.db     复权因子与除权除息明细（全量，行数少）
    bars/bars_YYYY.db   日 K 原始价，**按年分片**（单文件可控，便于备份与归档）
    bars/indices.db     **指数**日 K（独立库：指数不是股票，不能混进个股日 K 表）

行情（`price_store`）与股票池历史（`history_store`）共用一条连接实现，
因此连接参数、PRAGMA 调优与完整性钩子集中在这里。

**完整性钩子**：每个库都有一张 `_meta` 表，记录「结构版本 / 应用最后写入时间」，
并对内容做 HMAC 签名。每次**写事务提交后自动更新**（无需各 store 关心），
供 `integrity` 模块判断库是否被外部工具改动过。
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from . import config
from . import crypto

DATA_DIR = config.DATA_DIR
DB_PATH = config.STATE_DIR / "stock_history.db"      # 持久化状态统一放 state/
BARS_DIR = DATA_DIR / "bars"
FACTORS_DB = BARS_DIR / "factors.db"
# 指数行情：**独立库**。指数不是股票 —— code 命名空间（sh000001）与个股重叠
# （000001 既是上证指数也是平安银行），且指数没有复权/换手概念，
# 混进 daily_bars 会撞主键、还会被当成股票进回测池。详见 price_store 的指数段。
INDEX_DB = BARS_DIR / "indices.db"

# 入库格式版本：任何影响表结构或字段语义的改动都应递增，启动时比对并提示迁移
SCHEMA_VERSION = "1"

# 元信息键
META_SCHEMA = "schema_version"
META_LAST_WRITE = "last_write_at"

# 读性能相关：内存映射 256 MB、页缓存 64 MB，显著减少大表扫描的系统调用
MMAP_SIZE = 256 * 1024 * 1024
CACHE_SIZE_KB = 64 * 1024

_META_SQL = """
CREATE TABLE IF NOT EXISTS _meta (
  key        TEXT NOT NULL PRIMARY KEY,
  value      TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  hmac       TEXT NOT NULL
) WITHOUT ROWID;
"""


def bars_db(year: int) -> Path:
    """某一年的日 K 分片库路径。"""
    return BARS_DIR / f"bars_{year}.db"


# 分片探测缓存（见 is_bars_shard）：key = (路径, mtime_ns, 大小)。
# 只缓存「有效分片」；空壳每次重探（WAL 下写库不改主库 mtime/size，
# 缓存否定结论会把刚写好的分片误判成空壳）。
_SHARD_PROBE: dict[tuple[str, int, int], bool] = {}
_SHARD_PROBE_MAX = 512
# 小于这个大小才值得探测：sqlite 空壳 8KB，真分片含 daily_bars 表至少几页。
_SHARD_MIN_SIZE = 16 * 1024


def is_bars_shard(path: Path) -> bool:
    """该文件是否是**有效的日 K 分片**（含 `daily_bars` 表）。

    为什么需要它：`connect()` 对不存在的路径会**顺手创建一个空 sqlite 文件**
    （8KB、只有 `_meta`、没有 `daily_bars`）。若枚举分片时只按文件名 glob，
    这种空壳会被当成真分片 → 读路径查 `daily_bars` 直接报「no such table」
    （实测偶发，且**同一个目录里另一个分片被正常读取时**才暴露）。

    探测**只读**打开（不改文件、不写 `_meta`）：探测动作本身若改动文件，
    会被完整性校验判成「外部工具改动」。

    探测不了（文件被锁 / 格式异常 / 只读模式读不了 WAL）→ **保守当作有效分片**：
    宁可让读路径报「数据不可信」，也不要静默漏掉一整年数据。

    ⚠️ **只探测「小到不可能含表」的文件**：空壳只有 8KB（sqlite 头 + 一页空
    schema），而真分片是几十上百 MB。因此大于 `_SHARD_MIN_SIZE` 的文件
    **直接放行、不开句柄** —— 探测要开文件句柄，在 Windows 上会与并发写抢锁
    （实测偶发 WinError 32：临时目录删不掉 / 指纹写不上）。
    """
    try:
        st = path.stat()
    except OSError:
        return True
    if st.st_size >= _SHARD_MIN_SIZE:
        return True
    key = (str(path), st.st_mtime_ns, st.st_size)
    hit = _SHARD_PROBE.get(key)
    if hit is not None:
        return hit
    ok = _probe_bars_shard(path)
    if ok:
        if len(_SHARD_PROBE) >= _SHARD_PROBE_MAX:
            _SHARD_PROBE.clear()
        _SHARD_PROBE[key] = True
    return ok


def _probe_bars_shard(path: Path) -> bool:
    """实际探测（只读打开 → 查 sqlite_master 里有没有 daily_bars 表）。"""
    try:
        conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    except (sqlite3.Error, ValueError):
        return True
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='daily_bars'"
        ).fetchone()
        return row is not None
    except sqlite3.Error:
        return True
    finally:
        conn.close()


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# 元信息（带签名）
# --------------------------------------------------------------------------- #
def ensure_meta(conn) -> None:
    conn.execute(_META_SQL)


def meta_all(conn) -> dict[str, dict]:
    """读取全部元信息；表不存在时返回空。"""
    try:
        rows = conn.execute("SELECT * FROM _meta").fetchall()
    except sqlite3.OperationalError:
        return {}
    return {row["key"]: dict(row) for row in rows}


def meta_get(conn, key: str, default: str | None = None) -> str | None:
    item = meta_all(conn).get(key)
    return item["value"] if item else default


def meta_set(conn, key: str, value: str) -> None:
    """写入元信息并签名。签名覆盖 key + value + updated_at 三者。"""
    ensure_meta(conn)
    stamp = now_iso()
    signature = crypto.sign(f"{key}|{value}|{stamp}")
    conn.execute(
        "INSERT OR REPLACE INTO _meta(key,value,updated_at,hmac) VALUES(?,?,?,?)",
        (key, str(value), stamp, signature),
    )


def touch(conn) -> None:
    """记录「应用最后一次写入」的时间与结构版本。

    由 `connect()` 在**写事务提交前**自动调用，因此各 store 不必关心；
    文件系统的 mtime 若明显晚于这里记录的时间，说明库被外部工具改动过。
    """
    meta_set(conn, META_LAST_WRITE, now_iso())
    meta_set(conn, META_SCHEMA, SCHEMA_VERSION)


@contextmanager
def connect(path: Path | str | None = None, touch_meta: bool = True,
            force_touch: bool = False):
    """连接指定库文件（缺省为主库），自动建目录、应用 PRAGMA，并在写入后记录元信息。

    `force_touch=True` 用于**会改动文件但不产生 DML** 的操作（典型是 `VACUUM`），
    否则文件时间会被刷新而记录时间不更新，导致完整性校验误报。
    """
    target = Path(path) if path else DB_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, timeout=20)
    conn.row_factory = sqlite3.Row
    # 只在还不是 WAL 时切换：已是 WAL 的库再执行该 PRAGMA 仍会改写文件头，
    # 于是文件时间变化却不产生 DML（不触发元信息刷新），会造成完整性校验误报。
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        if str(mode).lower() != "wal":
            conn.execute("PRAGMA journal_mode=WAL")   # 读写并发：写不阻塞读
    except sqlite3.OperationalError:
        pass
    conn.execute("PRAGMA synchronous=NORMAL")   # WAL 下依然安全，写入更快
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        conn.execute(f"PRAGMA mmap_size={MMAP_SIZE}")
        conn.execute(f"PRAGMA cache_size=-{CACHE_SIZE_KB}")
    except sqlite3.OperationalError:
        pass                                    # 个别环境不支持 mmap，忽略
    try:
        yield conn
        # 本次连接产生过写入（INSERT/UPDATE/DELETE，DDL/VACUUM 不计）时刷新元信息
        if touch_meta and (force_touch or conn.total_changes):
            touch(conn)
        conn.commit()
    finally:
        conn.close()


def ensure_column(conn, table: str, column: str, ddl: str) -> bool:
    """幂等加列：SQLite 没有 ADD COLUMN IF NOT EXISTS，重复执行会报错，故先查表结构。"""
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column in existing:
        return False
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    return True


def init_db() -> None:
    """建齐所有表。重复执行安全（IF NOT EXISTS + 幂等加列），供服务启动时调用。"""
    # 函数内导入：避免与 db 形成模块级循环依赖
    import download_store
    import history_store
    import price_store
    price_store.init_db()
    history_store.init_db()
    download_store.init_db()
