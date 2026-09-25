# -*- coding: utf-8 -*-
"""本地 SQLite 基础设施：连接、建表调度、元信息与完整性钩子。

库文件布局（均位于 `config.DATA_DIR` 下）：

    stock_history.db    主库：股票池快照、消息面、同步任务（体积小）
    bars/factors.db     复权因子与除权除息明细（全量，行数少）
    bars/bars_YYYY.db   日 K 原始价，**按年分片**（单文件可控，便于备份与归档）

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

import config
import crypto

DATA_DIR = config.DATA_DIR
DB_PATH = DATA_DIR / "stock_history.db"
BARS_DIR = DATA_DIR / "bars"
FACTORS_DB = BARS_DIR / "factors.db"

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
