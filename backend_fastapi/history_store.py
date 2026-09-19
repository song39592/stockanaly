"""股票池快照与消息事件的本地 SQLite 存储（股价行情见 `price_store`）。

连接、库路径与建表调度统一由 `db` 模块负责；本模块只定义自己的表。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import db
# 兼容既有调用方与测试的导入习惯（连接与工具函数实际定义在 db 模块）
from db import DATA_DIR, DB_PATH, connect, now_iso        # noqa: F401


def init_db() -> None:
    with connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS pool_snapshots (
          snapshot_date TEXT PRIMARY KEY,
          stock_count INTEGER NOT NULL,
          imported_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS pool_members (
          snapshot_date TEXT NOT NULL,
          code TEXT NOT NULL,
          name TEXT NOT NULL DEFAULT '',
          industry TEXT NOT NULL DEFAULT '',
          region TEXT NOT NULL DEFAULT '',
          import_price REAL,
          import_change REAL,
          PRIMARY KEY(snapshot_date, code),
          FOREIGN KEY(snapshot_date) REFERENCES pool_snapshots(snapshot_date) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_pool_members_code ON pool_members(code, snapshot_date);
        CREATE TABLE IF NOT EXISTS stock_events (
          id TEXT PRIMARY KEY,
          code TEXT NOT NULL,
          kind TEXT NOT NULL,
          title TEXT NOT NULL,
          published_at TEXT,
          source TEXT,
          source_url TEXT,
          summary TEXT,
          fetched_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_stock_events_code_date ON stock_events(code, published_at DESC);
        CREATE TABLE IF NOT EXISTS event_sync_state (
          code TEXT PRIMARY KEY,
          fetched_at TEXT NOT NULL,
          note TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS sync_jobs (
          id TEXT PRIMARY KEY,
          snapshot_date TEXT NOT NULL,
          status TEXT NOT NULL,
          total INTEGER NOT NULL,
          completed INTEGER NOT NULL DEFAULT 0,
          failed INTEGER NOT NULL DEFAULT 0,
          errors_json TEXT NOT NULL DEFAULT '[]',
          created_at TEXT NOT NULL,
          finished_at TEXT
        );
        """)


def save_snapshot(snapshot_date: str, stocks: list[dict[str, Any]]) -> None:
    clean: dict[str, dict[str, Any]] = {}
    for stock in stocks:
        code = str(stock.get("code", "")).strip().zfill(6)
        if len(code) != 6 or not code.isdigit():
            continue
        clean[code] = {**stock, "code": code}
    with connect() as db:
        db.execute(
            "INSERT OR REPLACE INTO pool_snapshots(snapshot_date,stock_count,imported_at) VALUES(?,?,?)",
            (snapshot_date, len(clean), now_iso()),
        )
        db.execute("DELETE FROM pool_members WHERE snapshot_date=?", (snapshot_date,))
        db.executemany(
            """INSERT INTO pool_members(snapshot_date,code,name,industry,region,import_price,import_change)
               VALUES(?,?,?,?,?,?,?)""",
            [(
                snapshot_date, code, str(s.get("name") or ""), str(s.get("industry") or ""),
                str(s.get("region") or ""), s.get("price"), s.get("change"),
            ) for code, s in clean.items()],
        )


def event_id(code: str, kind: str, title: str, published_at: str, url: str) -> str:
    raw = "|".join((code, kind, title.strip(), published_at or "", url or ""))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def upsert_events(code: str, events: list[dict[str, Any]], note: str = "") -> int:
    fetched_at = now_iso()
    rows = []
    for item in events:
        kind = str(item.get("kind") or "news")
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        published_at = str(item.get("published_at") or item.get("date") or "")[:10]
        url = str(item.get("source_url") or "")
        rows.append((
            event_id(code, kind, title, published_at, url), code, kind, title, published_at,
            str(item.get("source") or ""), url, str(item.get("summary") or item.get("content") or "")[:800], fetched_at,
        ))
    with connect() as db:
        if rows:
            db.executemany("""INSERT OR REPLACE INTO stock_events(
              id,code,kind,title,published_at,source,source_url,summary,fetched_at)
              VALUES(?,?,?,?,?,?,?,?,?)""", rows)
        db.execute("INSERT OR REPLACE INTO event_sync_state(code,fetched_at,note) VALUES(?,?,?)",
                   (code, fetched_at, note[:1000]))
    return len(rows)


def list_events(code: str, limit: int = 100) -> list[dict[str, Any]]:
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM stock_events WHERE code=? ORDER BY published_at DESC,kind LIMIT ?", (code, limit)
        ).fetchall()
    return [dict(row) for row in rows]


def event_state(code: str) -> dict[str, Any] | None:
    with connect() as db:
        row = db.execute("SELECT * FROM event_sync_state WHERE code=?", (code,)).fetchone()
    return dict(row) if row else None


def pool_history(code: str) -> dict[str, Any]:
    with connect() as db:
        all_dates = [r["snapshot_date"] for r in db.execute(
            "SELECT snapshot_date FROM pool_snapshots ORDER BY snapshot_date"
        )]
        members = [dict(r) for r in db.execute(
            "SELECT * FROM pool_members WHERE code=? ORDER BY snapshot_date", (code,)
        )]
    member_map = {x["snapshot_date"]: x for x in members}
    spans, start_index = [], None
    for index, day in enumerate(all_dates):
        inside = day in member_map
        if inside and start_index is None:
            start_index = index
        if not inside and start_index is not None:
            spans.append({"start": all_dates[start_index], "end": all_dates[index - 1],
                          "days": index - start_index, "open": False})
            start_index = None
    if start_index is not None:
        spans.append({"start": all_dates[start_index], "end": all_dates[-1],
                      "days": len(all_dates) - start_index, "open": True})
    return {"members": members, "spans": spans, "snapshot_dates": all_dates}


def create_job(job_id: str, snapshot_date: str, total: int) -> None:
    with connect() as db:
        db.execute("""INSERT OR REPLACE INTO sync_jobs(
          id,snapshot_date,status,total,completed,failed,errors_json,created_at,finished_at)
          VALUES(?,?,?, ?,0,0,'[]',?,NULL)""", (job_id, snapshot_date, "queued", total, now_iso()))


def update_job(job_id: str, *, status: str | None = None, completed: int | None = None,
               failed: int | None = None, errors: list[str] | None = None, finished: bool = False) -> None:
    fields, params = [], []
    for key, value in (("status", status), ("completed", completed), ("failed", failed)):
        if value is not None:
            fields.append(f"{key}=?")
            params.append(value)
    if errors is not None:
        fields.append("errors_json=?")
        params.append(json.dumps(errors, ensure_ascii=False))
    if finished:
        fields.append("finished_at=?")
        params.append(now_iso())
    if not fields:
        return
    params.append(job_id)
    with connect() as db:
        db.execute(f"UPDATE sync_jobs SET {','.join(fields)} WHERE id=?", params)


def get_job(job_id: str) -> dict[str, Any] | None:
    with connect() as db:
        row = db.execute("SELECT * FROM sync_jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        return None
    item = dict(row)
    item["errors"] = json.loads(item.pop("errors_json") or "[]")
    return item
