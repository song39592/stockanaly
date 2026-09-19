# -*- coding: utf-8 -*-
"""本地 SQLite 基础设施：连接、建表调度与时间工具。

行情（`price_store`）与股票池历史 / 消息面（`history_store`）共用同一个库文件，
因此连接逻辑集中在这里；各存储模块只负责自己的表结构与读写。
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
DB_PATH = DATA_DIR / "stock_history.db"


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


@contextmanager
def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=20)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    try:
        yield db
        db.commit()
    finally:
        db.close()


def ensure_column(db, table: str, column: str, ddl: str) -> bool:
    """幂等加列：SQLite 没有 ADD COLUMN IF NOT EXISTS，重复执行会报错，故先查表结构。"""
    existing = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
    if column in existing:
        return False
    db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    return True


def init_db() -> None:
    """建齐所有表。重复执行安全（IF NOT EXISTS + 幂等加列），供服务启动时调用。"""
    # 函数内导入：避免与 db 形成模块级循环依赖
    import history_store
    import price_store
    price_store.init_db()
    history_store.init_db()
