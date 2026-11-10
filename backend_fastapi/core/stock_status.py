# -*- coding: utf-8 -*-
"""股票级状态标记：停牌 / 退市 / 正常。

标记来源是「下载时的失败模式」，**不是**权威数据源（避免联网拉 akshare 状态列表）：

  * 一只票**有正常历史**且**连续取数失败**（耗尽重试），而同期**全局错误率正常**
    （说明不是数据源整体故障）→ 判定为停牌 / 退市。标记后下载不再刷屏、不计失败、
    并集区间也不被它拖低。
  * 某次成功取到数据 → 自动清除标记（复牌 / 补数据后自愈），恢复正常下载。

单独一个小 sqlite 文件存储，不进主库（`state/stock_history.db`），避免触发
`db.py` 的完整性 HMAC（主库结构被改动会误报「库被外部工具改动」）。
"""
from __future__ import annotations

import datetime as dt
import sqlite3
import threading
from pathlib import Path

from . import config

_PATH = config.STATE_DIR / "stock_status.db"
_lock = threading.Lock()

STATUS_SUSPENDED = "suspended"
STATUS_DELISTED = "delisted"
STATUS_NORMAL = "normal"

_SQL_CREATE = (
    "CREATE TABLE IF NOT EXISTS stock_status ("
    "  code       TEXT PRIMARY KEY,"
    "  status     TEXT NOT NULL,"
    "  reason     TEXT NOT NULL DEFAULT '',"
    "  updated_at TEXT NOT NULL DEFAULT '')"
)


def _conn() -> sqlite3.Connection:
    Path(_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(_SQL_CREATE)
    return conn


def set_status(code: str, status: str, reason: str = "") -> None:
    """写入 / 覆盖一只票的状态标记。"""
    code = str(code).zfill(6)
    with _lock, _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO stock_status(code, status, reason, updated_at) "
            "VALUES(?, ?, ?, ?)",
            (code, status, reason[:500], dt.datetime.now(dt.timezone.utc).isoformat()))


def get_status(code: str) -> dict | None:
    """读取一只票的状态标记，无标记返回 None。"""
    code = str(code).zfill(6)
    with _lock, _conn() as conn:
        row = conn.execute(
            "SELECT code, status, reason, updated_at FROM stock_status WHERE code=?",
            (code,)).fetchone()
        return dict(row) if row else None


def clear_status(code: str) -> None:
    """清除一只票的标记（成功取到数据时的自愈）。"""
    code = str(code).zfill(6)
    with _lock, _conn() as conn:
        conn.execute("DELETE FROM stock_status WHERE code=?", (code,))


def is_suspended_like(code: str) -> bool:
    """是否已被标记为停牌 / 退市（供 `_stamp_span` 排除并集起点用）。"""
    s = get_status(code)
    return s is not None and s["status"] in (STATUS_SUSPENDED, STATUS_DELISTED)


def suspended_like_codes() -> set[str]:
    """一次取出**全部**被标记为停牌 / 退市的代码。

    给「全库扫描」类判定用（stale 口径、每日自动更新的排队列）：
    逐票调 `is_suspended_like` 是几千次小查询，而这里一次就取全。
    """
    with _lock, _conn() as conn:
        rows = conn.execute(
            "SELECT code FROM stock_status WHERE status IN (?, ?)",
            (STATUS_SUSPENDED, STATUS_DELISTED)).fetchall()
    return {str(r["code"]) for r in rows}
