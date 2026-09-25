# -*- coding: utf-8 -*-
"""历史数据遍历下载的任务持久化（主库 `stock_history.db`）。

与 `download_service` 的分工：
  - 本模块只负责「任务 + 待下载代码队列」的读写（纯存储：无网络、无线程）；
  - 编排（worker 池 / 限速 / 暂停续传 / 重试）在 `download_service`。

设计要点：
  队列按 (task_id, seq) **逐行存**，而不是把几千个代码塞进一个 JSON 字段。
  这样推进游标、标记失败、重试都只是单行 UPDATE，任务再大也不会整体重写整块数据；
  后端重启后队列仍在，配合 `recover_stale` 就能做到「暂停 → 重启 → 继续」。
"""
from __future__ import annotations

import json
from typing import Any

from db import connect, ensure_column, now_iso


def init_db() -> None:
    with connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS download_tasks (
          id           TEXT PRIMARY KEY,
          scope        TEXT NOT NULL,
          status       TEXT NOT NULL,
          total        INTEGER NOT NULL DEFAULT 0,
          completed    INTEGER NOT NULL DEFAULT 0,
          failed       INTEGER NOT NULL DEFAULT 0,
          cursor_seq   INTEGER NOT NULL DEFAULT 0,
          phase1_done  INTEGER NOT NULL DEFAULT 0,
          options_json TEXT NOT NULL DEFAULT '{}',
          errors_json  TEXT NOT NULL DEFAULT '[]',
          created_at   TEXT NOT NULL,
          updated_at   TEXT NOT NULL,
          finished_at  TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_download_tasks_created
          ON download_tasks(created_at DESC);
        CREATE TABLE IF NOT EXISTS download_settings (
          key        TEXT PRIMARY KEY,
          value      TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS download_codes (
          task_id TEXT NOT NULL,
          seq     INTEGER NOT NULL,
          code    TEXT NOT NULL,
          phase   INTEGER NOT NULL DEFAULT 1,
          status  TEXT NOT NULL DEFAULT 'pending',
          retries INTEGER NOT NULL DEFAULT 0,
          error   TEXT NOT NULL DEFAULT '',
          PRIMARY KEY(task_id, seq)
        );
        CREATE INDEX IF NOT EXISTS idx_download_codes_task
          ON download_codes(task_id, status, seq);
        """)
        # 幂等加列：phase / phase1_done 是「先近后远两轮遍历」后加的，
        # 早期已建的库没有这两列，这里补上（SQLite 的 ADD COLUMN 带默认值即可）。
        ensure_column(conn, "download_tasks", "phase1_done", "INTEGER NOT NULL DEFAULT 0")
        ensure_column(conn, "download_codes", "phase", "INTEGER NOT NULL DEFAULT 1")
        # skipped：库里历史已足量、整只跳过没下载的股票数
        ensure_column(conn, "download_tasks", "skipped", "INTEGER NOT NULL DEFAULT 0")
        # origin 区分「谁发起的」：manual 手动 / auto 每日自动更新 / idle 后台闲时
        ensure_column(conn, "download_tasks", "origin", "TEXT NOT NULL DEFAULT 'manual'")


def _loads(text: str | None, default: Any) -> Any:
    try:
        return json.loads(text or "") or default
    except (TypeError, ValueError):
        return default


def _decode(row) -> dict[str, Any]:
    item = dict(row)
    item["options"] = _loads(item.pop("options_json", None), {})
    item["errors"] = _loads(item.pop("errors_json", None), [])
    return item


# --------------------------------------------------------------------------- #
# 任务
# --------------------------------------------------------------------------- #
def create_task(task_id: str, scope: str, options: dict[str, Any], codes: list[str],
                phases: int = 1, origin: str = "manual") -> None:
    """建任务并铺开待下载队列（一次事务，5000 行也就几十毫秒）。

    `phases=2` 时每只股票排**两行**：前半段 seq 是「近期」阶段，后半段是「更早历史」。
    领取时按 seq 升序，于是天然形成「全市场先跑完近的、再回头补远的」两轮遍历——
    哪怕任务中途被打断，已跑完的那部分也是**最近几年**的数据，先可用起来。

    任务的 `total` 始终按**股票只数**记（不是行数），进度条才不会因分段而虚高。
    """
    stamp = now_iso()
    rows: list[tuple] = [(task_id, index, code, 1) for index, code in enumerate(codes)]
    if phases >= 2:
        offset = len(codes)
        rows.extend((task_id, offset + index, code, 2) for index, code in enumerate(codes))
    with connect() as conn:
        conn.execute(
            """INSERT INTO download_tasks(
                 id,scope,origin,status,total,completed,failed,cursor_seq,phase1_done,
                 options_json,errors_json,created_at,updated_at,finished_at)
               VALUES(?,?,?,?,?,0,0,0,0,?,'[]',?,?,NULL)""",
            (task_id, scope, origin, "queued", len(codes),
             json.dumps(options, ensure_ascii=False), stamp, stamp),
        )
        conn.executemany(
            "INSERT INTO download_codes(task_id,seq,code,phase,status,retries,error) "
            "VALUES(?,?,?,?,'pending',0,'')", rows)


def update_task(task_id: str, *, status: str | None = None, completed: int | None = None,
                failed: int | None = None, cursor_seq: int | None = None,
                finished: bool = False) -> None:
    fields: list[str] = []
    params: list[Any] = []
    for key, value in (("status", status), ("completed", completed),
                       ("failed", failed), ("cursor_seq", cursor_seq)):
        if value is not None:
            fields.append(f"{key}=?")
            params.append(value)
    fields.append("updated_at=?")
    params.append(now_iso())
    if finished:
        fields.append("finished_at=?")
        params.append(now_iso())
    params.append(task_id)
    with connect() as conn:
        conn.execute(f"UPDATE download_tasks SET {','.join(fields)} WHERE id=?", params)


def mark_running(task_id: str) -> bool:
    """把任务置为 running；已取消 / 已完成则不动（返回 False 表示没置上）。

    用**条件更新**而不是「先查后写」：线程开跑与用户点取消之间只差几毫秒，
    只有让数据库来判定，才不会把「取消」覆盖回 running。
    """
    with connect() as conn:
        cursor = conn.execute(
            "UPDATE download_tasks SET status='running', updated_at=? "
            "WHERE id=? AND status NOT IN ('cancelled','completed')", (now_iso(), task_id))
    return int(cursor.rowcount or 0) > 0


def get_task(task_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM download_tasks WHERE id=?", (task_id,)).fetchone()
    return _decode(row) if row else None


def list_tasks(limit: int = 20) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM download_tasks ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [_decode(row) for row in rows]


def record_error(task_id: str, code: str, message: str, limit: int = 200) -> None:
    """把一条失败追加到任务级错误清单（只保留最近 limit 条，避免无限膨胀）。"""
    with connect() as conn:
        row = conn.execute("SELECT errors_json FROM download_tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            return
        errors = _loads(row["errors_json"], [])
        errors.append({"code": code, "error": message})
        conn.execute("UPDATE download_tasks SET errors_json=?, updated_at=? WHERE id=?",
                     (json.dumps(errors[-limit:], ensure_ascii=False), now_iso(), task_id))


def recover_stale() -> int:
    """把「上次进程没跑完」的任务标成暂停，等用户点继续。

    后端重启后内存里的 worker 全没了，但库里仍写着 running/queued——不处理的话
    这些任务会永远卡在那里没人推进。这里统一转成 paused，并把领了却没做完的
    代码退回 pending，继续时自然从断点接着跑。
    """
    with connect() as conn:
        cursor = conn.execute(
            "UPDATE download_tasks SET status='paused', updated_at=? WHERE status IN ('running','queued')",
            (now_iso(),))
        conn.execute("UPDATE download_codes SET status='pending' WHERE status='running'")
    return int(cursor.rowcount or 0)


# --------------------------------------------------------------------------- #
# 后台开关（持久化：重启后端后依然生效）
# --------------------------------------------------------------------------- #
def get_setting(key: str, default: str = "") -> str:
    with connect() as conn:
        row = conn.execute("SELECT value FROM download_settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO download_settings(key,value,updated_at) VALUES(?,?,?)",
            (key, str(value), now_iso()))


def settings_map() -> dict[str, str]:
    with connect() as conn:
        rows = conn.execute("SELECT key,value FROM download_settings").fetchall()
    return {row["key"]: row["value"] for row in rows}


def task_code_set(task_id: str) -> set[str]:
    """该任务覆盖过的股票代码（去重）。用于「闲时下载」跳过已排过的股票。"""
    with connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT code FROM download_codes WHERE task_id=?", (task_id,)).fetchall()
    return {row["code"] for row in rows}


def update_options(task_id: str, options: dict[str, Any]) -> None:
    """整份替换任务选项（闲时下载降并发时用）。"""
    with connect() as conn:
        conn.execute("UPDATE download_tasks SET options_json=?, updated_at=? WHERE id=?",
                     (json.dumps(options, ensure_ascii=False), now_iso(), task_id))


# --------------------------------------------------------------------------- #
# 队列
# --------------------------------------------------------------------------- #
def claim_code(task_id: str) -> dict[str, Any] | None:
    """领取下一个待下载代码；没有剩余则返回 None。

    并发安全由调用方加锁保证（见 `download_service._claim_lock`）：
    SQLite 的 UPDATE...RETURNING 需要较新的版本，这里用「锁 + 查一条 + 改这条」
    更稳妥，而领取本身只是一次点查，持锁时间可以忽略。
    """
    with connect() as conn:
        row = conn.execute(
            "SELECT seq,code,phase,retries FROM download_codes "
            "WHERE task_id=? AND status='pending' ORDER BY seq LIMIT 1", (task_id,)).fetchone()
        if row is None:
            return None
        conn.execute("UPDATE download_codes SET status='running' WHERE task_id=? AND seq=?",
                     (task_id, row["seq"]))
        conn.execute("UPDATE download_tasks SET cursor_seq=?, updated_at=? WHERE id=?",
                     (row["seq"], now_iso(), task_id))
    return dict(row)


def finish_code(task_id: str, seq: int, ok: bool, error: str = "") -> None:
    """一只股票的**某一个阶段**结束；顺带维护「按股票计」的完成 / 失败数。

    计数按股票、不按阶段：只有这只股票的**全部阶段都落定**后才计一次 completed / failed，
    否则一只股票排两行会让进度虚高一倍。
    """
    with connect() as conn:
        row = conn.execute("SELECT code,phase FROM download_codes WHERE task_id=? AND seq=?",
                           (task_id, seq)).fetchone()
        if row is None:
            return
        code, phase = row["code"], row["phase"]
        if ok:
            conn.execute("UPDATE download_codes SET status='done',error='' WHERE task_id=? AND seq=?",
                         (task_id, seq))
            if phase == 1:
                # 「近期」阶段完成：这只股票最近几年的数据已经可用了
                conn.execute("UPDATE download_tasks SET phase1_done=phase1_done+1 WHERE id=?",
                             (task_id,))
        else:
            conn.execute("UPDATE download_codes SET status='failed',error=? WHERE task_id=? AND seq=?",
                         ((error or "")[:500], task_id, seq))
        left = conn.execute(
            "SELECT COUNT(*) AS n FROM download_codes "
            "WHERE task_id=? AND code=? AND status IN ('pending','running')", (task_id, code)).fetchone()["n"]
        if left == 0:
            broken = conn.execute(
                "SELECT COUNT(*) AS n FROM download_codes WHERE task_id=? AND code=? AND status='failed'",
                (task_id, code)).fetchone()["n"]
            if broken:
                conn.execute("UPDATE download_tasks SET failed=failed+1, updated_at=? WHERE id=?",
                             (now_iso(), task_id))
            else:
                conn.execute("UPDATE download_tasks SET completed=completed+1, updated_at=? WHERE id=?",
                             (now_iso(), task_id))
        else:
            conn.execute("UPDATE download_tasks SET updated_at=? WHERE id=?", (now_iso(), task_id))


def requeue_code(task_id: str, seq: int, error: str = "") -> None:
    """失败重试：退回 pending 并累加重试次数（不计入 failed，还没放弃）。"""
    with connect() as conn:
        conn.execute(
            "UPDATE download_codes SET status='pending', retries=retries+1, error=? "
            "WHERE task_id=? AND seq=?", ((error or "")[:500], task_id, seq))


def requeue_running(task_id: str) -> int:
    """把卡在 running 的代码退回待下载（上次进程中断 / 暂停时领了没做完的）。"""
    with connect() as conn:
        cursor = conn.execute(
            "UPDATE download_codes SET status='pending' WHERE task_id=? AND status='running'",
            (task_id,))
    return int(cursor.rowcount or 0)


def reset_failed(task_id: str) -> int:
    """「重试失败项」：失败的代码（含中断残留）全部退回待下载，并清零失败计数。"""
    with connect() as conn:
        cursor = conn.execute(
            "UPDATE download_codes SET status='pending', retries=0, error='' "
            "WHERE task_id=? AND status IN ('failed','running')", (task_id,))
        conn.execute(
            "UPDATE download_tasks SET failed=0, errors_json='[]', updated_at=?, finished_at=NULL "
            "WHERE id=?", (now_iso(), task_id))
    return int(cursor.rowcount or 0)


def skip_stock(task_id: str, code: str) -> int:
    """这只股票库里的数据已经够了，**整只跳过**：所有未完成阶段一并标完成。

    既不打数据源（省请求额度、也降低被限流的风险），也不算失败。
    计入 `completed`（数据确实在位）并单独计入 `skipped`（标明这次没真的下载）。
    """
    with connect() as conn:
        rows = conn.execute(
            "SELECT seq, phase FROM download_codes "
            "WHERE task_id=? AND code=? AND status IN ('pending','running')",
            (task_id, code)).fetchall()
        if not rows:
            return 0
        conn.executemany(
            "UPDATE download_codes SET status='done', error='' WHERE task_id=? AND seq=?",
            [(task_id, row["seq"]) for row in rows])
        stamp = now_iso()
        if any(row["phase"] == 1 for row in rows):
            conn.execute("UPDATE download_tasks SET phase1_done=phase1_done+1 WHERE id=?", (task_id,))
        conn.execute(
            "UPDATE download_tasks SET completed=completed+1, skipped=skipped+1, updated_at=? "
            "WHERE id=?", (stamp, task_id))
    return len(rows)


def remaining(task_id: str) -> int:
    """还没落定的**股票只数**（有待下载 / 正在下载的阶段即算一只）。

    按代码去重而不是数行：完整历史一只排两行，数行会让「剩余」看起来比总数还多。
    """
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(DISTINCT code) AS n FROM download_codes "
            "WHERE task_id=? AND status IN ('pending','running')", (task_id,)).fetchone()
    return int(row["n"] or 0)


def failures(task_id: str, limit: int = 200) -> list[dict[str, Any]]:
    """失败清单（代码 + 原因），供前端展示与「重试失败项」。

    按代码聚合：一只股票两个阶段都失败时只报一条，避免清单里出现重复代码。
    """
    with connect() as conn:
        rows = conn.execute(
            "SELECT code, MIN(seq) AS seq, MAX(error) AS error FROM download_codes "
            "WHERE task_id=? AND status='failed' GROUP BY code ORDER BY seq LIMIT ?",
            (task_id, limit)).fetchall()
    return [dict(row) for row in rows]
