"""股票池快照与消息事件的本地 SQLite 存储（股价行情见 `price_store`）。

连接、库路径与建表调度统一由 `db` 模块负责；本模块只定义自己的表。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from core import db
# 兼容既有调用方与测试的导入习惯（连接与工具函数实际定义在 db 模块）
from core.db import DATA_DIR, DB_PATH, connect, now_iso        # noqa: F401


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
        CREATE TABLE IF NOT EXISTS rps_pool_daily (
          trade_date TEXT NOT NULL,
          code TEXT NOT NULL,
          name TEXT NOT NULL DEFAULT '',
          industry TEXT NOT NULL DEFAULT '',
          rps50 REAL,
          rps120 REAL,
          rps250 REAL,
          PRIMARY KEY(trade_date, code)
        );
        CREATE INDEX IF NOT EXISTS idx_rps_pool_daily_date ON rps_pool_daily(trade_date);
        CREATE TABLE IF NOT EXISTS rps_pool_jobs (
          id TEXT PRIMARY KEY,
          status TEXT NOT NULL,
          progress INTEGER NOT NULL DEFAULT 0,
          total INTEGER NOT NULL DEFAULT 0,
          note TEXT NOT NULL DEFAULT '',
          errors_json TEXT NOT NULL DEFAULT '[]',
          created_at TEXT NOT NULL,
          finished_at TEXT
        );
        CREATE TABLE IF NOT EXISTS stock_names (
          code TEXT PRIMARY KEY,
          name TEXT NOT NULL DEFAULT ''
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


def latest_snapshot_codes() -> list[str]:
    """最新一期股票池快照里的代码（按代码升序）；没有快照时返回空列表。"""
    with connect() as db:
        row = db.execute(
            "SELECT snapshot_date FROM pool_snapshots ORDER BY snapshot_date DESC LIMIT 1").fetchone()
        if not row:
            return []
        rows = db.execute(
            "SELECT code FROM pool_members WHERE snapshot_date=? ORDER BY code",
            (row["snapshot_date"],)).fetchall()
    return [str(item["code"]) for item in rows]


def load_pool_members(start: str | None = None,
                       end: str | None = None) -> dict[str, dict[str, dict]]:
    """按日期范围批量取股票池成员，供 RPS 等依赖股票池历史的策略使用。

    返回 {snapshot_date: {code: {"name","industry","region",
    "import_price","import_change"}}}；空库 / 异常优雅返回 {}（不抛错，
    让上层策略在「无历史」时安全地返回空信号）。
    """
    where, params = [], []
    if start:
        where.append("snapshot_date >= ?")
        params.append(start)
    if end:
        where.append("snapshot_date <= ?")
        params.append(end)
    sql = ("SELECT snapshot_date, code, name, industry, region, "
           "import_price, import_change FROM pool_members")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY snapshot_date"
    try:
        with connect() as db:
            rows = db.execute(sql, tuple(params)).fetchall()
    except Exception:
        return {}
    out: dict[str, dict[str, dict]] = {}
    for r in rows:
        d = r["snapshot_date"]
        out.setdefault(d, {})[r["code"]] = {
            "name": r["name"], "industry": r["industry"], "region": r["region"],
            "import_price": r["import_price"], "import_change": r["import_change"],
        }
    return out


def snapshot_dates() -> list[str]:
    """所有快照日（升序）；无数据 / 异常返回空列表。

    供 RPS 标注池等需要「最新一期 + 上一期」对比的接口取日期序列。
    """
    try:
        with connect() as db:
            rows = db.execute(
                "SELECT snapshot_date FROM pool_snapshots ORDER BY snapshot_date").fetchall()
    except Exception:
        return []
    return [str(r["snapshot_date"]) for r in rows]


def pool_snapshot_range() -> tuple[str | None, str | None]:
    """返回 (最早快照日, 最晚快照日)；无数据 / 异常返回 (None, None)。

    供策略编排层判断「股票池历史是否覆盖回测区间」。
    """
    try:
        with connect() as db:
            row = db.execute(
                "SELECT MIN(snapshot_date) AS mn, MAX(snapshot_date) AS mx "
                "FROM pool_snapshots").fetchone()
    except Exception:
        return (None, None)
    if not row or row["mn"] is None:
        return (None, None)
    return (row["mn"], row["mx"])


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


# --------------------------------------------------------------------------- #
# 每日 RPS 强势池（全市场 RPS≥阈值，按交易日落库；滚动窗口截断）
# --------------------------------------------------------------------------- #
def upsert_rps_pool_day(trade_date: str, rows: list[dict[str, Any]]) -> None:
    """覆盖写入某交易日的 RPS 强势池成员（先删后插，幂等）。

    rows: [{code, name, industry, rps50, rps120, rps250}]；空列表等价于清空该日。
    """
    trade_date = str(trade_date)[:10]
    with connect() as db:
        db.execute("DELETE FROM rps_pool_daily WHERE trade_date=?", (trade_date,))
        if rows:
            db.executemany(
                """INSERT OR REPLACE INTO rps_pool_daily
                   (trade_date, code, name, industry, rps50, rps120, rps250)
                   VALUES(?,?,?,?,?,?,?)""",
                [(trade_date, str(r["code"]).strip().zfill(6),
                  str(r.get("name") or ""), str(r.get("industry") or ""),
                  r.get("rps50"), r.get("rps120"), r.get("rps250"))
                 for r in rows],
            )


def rps_pool_dates() -> list[str]:
    """已落库的 RPS 池交易日（降序）；无数据返回 []。"""
    with connect() as db:
        rows = db.execute(
            "SELECT DISTINCT trade_date FROM rps_pool_daily ORDER BY trade_date DESC"
        ).fetchall()
    return [str(r["trade_date"]) for r in rows]


def load_rps_pool() -> dict[str, list[dict[str, Any]]]:
    """读全部 RPS 池，按交易日降序、当日按 rps120 降序；{trade_date: [item...]}。"""
    with connect() as db:
        rows = db.execute(
            "SELECT trade_date, code, name, industry, rps50, rps120, rps250 "
            "FROM rps_pool_daily ORDER BY trade_date DESC, rps120 DESC"
        ).fetchall()
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(r["trade_date"], []).append({
            "code": r["code"], "name": r["name"], "industry": r["industry"],
            "rps50": r["rps50"], "rps120": r["rps120"], "rps250": r["rps250"],
        })
    return out


def truncate_rps_pool(max_days: int) -> int:
    """只保留最近 max_days 个交易日，删除更早的；返回删除的日期数。"""
    dates = rps_pool_dates()
    if len(dates) <= max_days:
        return 0
    cutoff = dates[max_days - 1]          # 第 max_days 新的那个日期（含）
    with connect() as db:
        cur = db.execute(
            "DELETE FROM rps_pool_daily WHERE trade_date < ?", (cutoff,))
        return cur.rowcount


def pool_industry_map() -> dict[str, str]:
    """最新一期股票池快照的行业映射 {code: industry}，用于给强股补行业（没有则空）。"""
    with connect() as db:
        row = db.execute(
            "SELECT snapshot_date FROM pool_snapshots ORDER BY snapshot_date DESC LIMIT 1"
        ).fetchone()
        if not row:
            return {}
        rows = db.execute(
            "SELECT code, industry FROM pool_members WHERE snapshot_date=?",
            (row["snapshot_date"],)).fetchall()
    return {r["code"]: r["industry"] for r in rows}


def set_names(mapping: dict[str, str]) -> None:
    if not mapping:
        return
    with connect() as db:
        db.executemany(
            "INSERT OR REPLACE INTO stock_names(code, name) VALUES(?,?)",
            [(str(c).zfill(6), str(n)) for c, n in mapping.items()],
        )


def load_names() -> dict[str, str]:
    with connect() as db:
        rows = db.execute("SELECT code, name FROM stock_names").fetchall()
    return {r["code"]: r["name"] for r in rows}


def create_rps_pool_job(job_id: str, total: int) -> None:
    with connect() as db:
        db.execute(
            """INSERT OR REPLACE INTO rps_pool_jobs
               (id, status, progress, total, note, errors_json, created_at, finished_at)
               VALUES(?,?,?,?,?,?,?,NULL)""",
            (job_id, "queued", 0, total, "", "[]", now_iso()))


def update_rps_pool_job(job_id: str, *, status: str | None = None,
                        progress: int | None = None, note: str | None = None,
                        errors: list[str] | None = None, finished: bool = False) -> None:
    fields, params = [], []
    for key, value in (("status", status), ("progress", progress), ("note", note)):
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
        db.execute(f"UPDATE rps_pool_jobs SET {','.join(fields)} WHERE id=?", params)


def get_rps_pool_job(job_id: str) -> dict[str, Any] | None:
    with connect() as db:
        row = db.execute("SELECT * FROM rps_pool_jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        return None
    item = dict(row)
    item["errors"] = json.loads(item.pop("errors_json") or "[]")
    return item
