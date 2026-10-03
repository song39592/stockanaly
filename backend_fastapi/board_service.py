# -*- coding: utf-8 -*-
"""个股所属板块（行业 / 概念 / 地域）——通达信 HYBLOCK / GNBLOCK / DYBLOCK 的等价物。

数据源选择（实测 2026-10）：
  * 东财行业/概念板块（`stock_board_*_em`）在本机被墙（Connection aborted），不可用；
  * 同花顺只有板块**列表**（`stock_board_*_name_ths`），没有「个股属于哪些板块」的
    反查接口，遍历 375 个概念逐一拉成分股不现实；
  * **新浪板块体系可用**：`stock_sector_spot(indicator)` 给板块清单（带 label），
    `stock_sector_detail(label)` 给成分股，且 `code` 列就是 6 位股票代码。
    覆盖：新浪行业 49 + 行业(证监会) 84 + 概念 175 + 地域 31 ≈ 340 个板块。

因此采用**预计算索引**：把 340 个板块的成分股拉一遍（~100s），
落库成 `board_members`（股票代码 → 所属板块），个股查询只查库、零网络等待。
索引 >24h 过期，过期后个股查询会在后台触发重建（不阻塞当前请求）。

「最相关板块」：成分股越少的板块越「专属」（反之「沪深A股」这类人人都属于的
板块没有信息量），故每类内按成分股数量**升序**排，第一个即最相关。
"""
from __future__ import annotations

import datetime as dt
import threading
from typing import Any

import akshare as ak
import db as db_mod
from market_service import _ak

# (新浪 indicator, 类别)：行业两类都收（一个传统行业板块、一个证监会门类，互补）
_BOARD_SOURCES: list[tuple[str, str]] = [
    ("新浪行业", "industry"),
    ("行业", "industry"),
    ("概念", "concept"),
    ("地域", "region"),
]

_TTL_HOURS = 24
_sync_lock = threading.Lock()
_syncing = False

# 时间戳线程安全（python dict 读写有 GIL 保护，够用）
_last_sync_error: str | None = None

_MEMBERS_TABLE = """
CREATE TABLE IF NOT EXISTS board_members (
  code TEXT NOT NULL,             -- 6 位股票代码
  category TEXT NOT NULL,         -- industry / concept / region
  board TEXT NOT NULL,            -- 板块名（如 房地产开发、华为鸿蒙）
  label TEXT NOT NULL,            -- 新浪板块 label（重建索引用）
  member_count INTEGER NOT NULL,  -- 板块成分股数量（算「最相关」用）
  fetched_at TEXT NOT NULL,
  PRIMARY KEY(code, category, board)
) WITHOUT ROWID;
"""

_META_TABLE = """
CREATE TABLE IF NOT EXISTS board_meta (
  id INTEGER PRIMARY KEY CHECK(id = 1),
  fetched_at TEXT,
  boards INTEGER,
  members INTEGER
);
"""


def _ensure_tables(conn) -> None:
    conn.execute(_MEMBERS_TABLE)
    conn.execute(_META_TABLE)


def status() -> dict[str, Any]:
    """索引状态：是否建过、多旧、多少板块/成员、是否正在重建。"""
    with db_mod.connect(db_mod.FACTORS_DB) as conn:
        _ensure_tables(conn)
        row = conn.execute("SELECT * FROM board_meta WHERE id=1").fetchone()
    out: dict[str, Any] = {"syncing": _syncing, "error": _last_sync_error,
                           "ready": False, "age_hours": None}
    if row is not None and row["fetched_at"]:
        out["fetched_at"] = row["fetched_at"]
        out["boards"] = row["boards"]
        out["members"] = row["members"]
        try:
            # now_iso() 是 UTC 带时区；统一转 UTC naive 再相减，避免 offset 混算
            ts = dt.datetime.fromisoformat(row["fetched_at"])
            if ts.tzinfo is not None:
                ts = ts.astimezone(dt.timezone.utc).replace(tzinfo=None)
            age = (dt.datetime.utcnow() - ts).total_seconds() / 3600
        except ValueError:
            age = None
        out["age_hours"] = round(age, 1) if age is not None else None
        out["ready"] = age is not None and age < _TTL_HOURS * 30   # 只要建过就算可用
    return out


def boards_of(code: str) -> dict[str, Any]:
    """查某只股所属板块。库没建过 / 该股无记录时 ready=False（调用方提示重建索引）。"""
    code = str(code).zfill(6)
    with db_mod.connect(db_mod.FACTORS_DB) as conn:
        _ensure_tables(conn)
        rows = conn.execute(
            "SELECT category, board, member_count FROM board_members "
            "WHERE code=? ORDER BY member_count, board", (code,),
        ).fetchall()
    out: dict[str, list[dict[str, Any]]] = {"industry": [], "concept": [], "region": []}
    for r in rows:
        cat = r["category"]
        if cat in out:
            out[cat].append({"board": r["board"], "member_count": r["member_count"]})
    st = status()
    return {"ok": True, "code": code, "boards": out,
            "syncing": st["syncing"], "index_ready": st["ready"],
            "index_fetched_at": st.get("fetched_at")}


def _sync_worker() -> None:
    global _syncing, _last_sync_error
    try:
        all_rows: list[tuple] = []
        n_boards = 0
        fetched_at = db_mod.now_iso()
        for indicator, category in _BOARD_SOURCES:
            spots = _ak(ak.stock_sector_spot, indicator=indicator)
            if spots is None or getattr(spots, "empty", True):
                continue
            for _, srow in spots.iterrows():
                label, name = str(srow.get("label") or ""), str(srow.get("板块") or "")
                if not label or not name:
                    continue
                cons = _ak(ak.stock_sector_detail, sector=label)
                if cons is None or getattr(cons, "empty", True) or "code" not in cons:
                    continue
                codes = [str(c).zfill(6) for c in cons["code"].tolist()]
                n_boards += 1
                for c in codes:
                    all_rows.append((c, category, name, label, len(codes), fetched_at))
        if not all_rows:
            _last_sync_error = "板块数据源未返回任何成分"
            return
        with db_mod.connect(db_mod.FACTORS_DB) as conn:
            _ensure_tables(conn)
            # 全量重建：旧索引整表清掉，避免改名/退市的板块残留
            conn.execute("DELETE FROM board_members")
            conn.executemany(
                "INSERT OR REPLACE INTO board_members"
                "(code,category,board,label,member_count,fetched_at) VALUES(?,?,?,?,?,?)",
                all_rows)
            conn.execute(
                "INSERT OR REPLACE INTO board_meta(id,fetched_at,boards,members) "
                "VALUES(1,?,?,?)", (fetched_at, n_boards, len(all_rows)))
        _last_sync_error = None
    except Exception as exc:                       # noqa: BLE001 - 后台线程不抛
        _last_sync_error = f"{type(exc).__name__}: {exc}"
    finally:
        _syncing = False


def sync_boards(force: bool = False) -> dict[str, Any]:
    """重建板块索引（**后台线程**，立即返回；进度看 status()）。已在跑则不重复。

    force=True 无视 TTL 强制重建；缺省时索引未建或 >24h 才会真正开工。
    """
    global _syncing
    st = status()
    if _syncing:
        return {"ok": True, "syncing": True, "started": False}
    if not force and st["ready"] and (st["age_hours"] is None
                                      or st["age_hours"] < _TTL_HOURS):
        return {"ok": True, "syncing": False, "started": False}
    with _sync_lock:
        if _syncing:
            return {"ok": True, "syncing": True, "started": False}
        _syncing = True
    threading.Thread(target=_sync_worker, daemon=True,
                     name="board-index-sync").start()
    return {"ok": True, "syncing": True, "started": True}
