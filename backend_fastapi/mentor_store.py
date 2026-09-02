# -*- coding: utf-8 -*-
"""大佬策略实验室：本地 SQLite 存储、PDF/文本解析与版本化流程。"""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from pypdf import PdfReader


DATA_DIR = Path(__file__).resolve().parent / "data"
DB_PATH = DATA_DIR / "mentor_lab.db"
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TEXT_CHARS = 300_000


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


@contextmanager
def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS mentors (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS materials (
          id TEXT PRIMARY KEY, mentor_id TEXT NOT NULL REFERENCES mentors(id), file_name TEXT NOT NULL,
          file_type TEXT NOT NULL, sha256 TEXT NOT NULL, raw_text TEXT NOT NULL, segments_json TEXT NOT NULL,
          parse_status TEXT NOT NULL, parse_notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
          UNIQUE(mentor_id, sha256)
        );
        CREATE TABLE IF NOT EXISTS extractions (
          id TEXT PRIMARY KEY, material_id TEXT NOT NULL REFERENCES materials(id), model TEXT NOT NULL,
          structured_json TEXT NOT NULL, raw_output TEXT NOT NULL, status TEXT NOT NULL,
          reviewer_note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, reviewed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS daily_views (
          id TEXT PRIMARY KEY, mentor_id TEXT NOT NULL REFERENCES mentors(id), view_date TEXT NOT NULL,
          data_as_of TEXT NOT NULL, raw_text TEXT NOT NULL, structured_json TEXT NOT NULL,
          status TEXT NOT NULL, created_at TEXT NOT NULL, reviewed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS skill_versions (
          id TEXT PRIMARY KEY, mentor_id TEXT NOT NULL REFERENCES mentors(id), version INTEGER NOT NULL,
          status TEXT NOT NULL, parent_version INTEGER, skill_json TEXT NOT NULL,
          change_reason TEXT NOT NULL, created_at TEXT NOT NULL, promoted_at TEXT,
          UNIQUE(mentor_id, version)
        );
        CREATE TABLE IF NOT EXISTS evaluations (
          id TEXT PRIMARY KEY, mentor_id TEXT NOT NULL REFERENCES mentors(id), skill_version INTEGER NOT NULL,
          result_json TEXT NOT NULL, reviewer_decision TEXT NOT NULL DEFAULT 'pending',
          reviewer_note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, reviewed_at TEXT
        );
        """)


def row_dict(row: sqlite3.Row | None, json_fields=()) -> dict[str, Any] | None:
    if row is None:
        return None
    out = dict(row)
    for field in json_fields:
        if field in out:
            try:
                out[field] = json.loads(out[field])
            except Exception:
                out[field] = None
    return out


def create_mentor(name: str, description: str = "") -> dict[str, Any]:
    mentor = {"id": new_id("mentor"), "name": name.strip(), "description": description.strip(), "created_at": utc_now()}
    with connect() as db:
        db.execute("INSERT INTO mentors(id,name,description,created_at) VALUES(:id,:name,:description,:created_at)", mentor)
    return mentor


def list_mentors() -> list[dict[str, Any]]:
    with connect() as db:
        return [dict(r) for r in db.execute("SELECT * FROM mentors ORDER BY created_at DESC")]


def decode_text(content: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "gb18030", "utf-16"):
        try:
            return content.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace"), "utf-8-replace"


def parse_material(file_name: str, content: bytes) -> dict[str, Any]:
    if not content or len(content) > MAX_FILE_BYTES:
        raise ValueError("文件为空或超过 20MB")
    suffix = Path(file_name).suffix.lower()
    if suffix == ".pdf":
        reader = PdfReader(io.BytesIO(content))
        segments = []
        for index, page in enumerate(reader.pages, 1):
            text = re.sub(r"[ \t]+", " ", page.extract_text() or "").strip()
            segments.append({"source_ref": f"p.{index}", "page": index, "text": text})
        raw_text = "\n\n".join(f"[p.{s['page']}]\n{s['text']}" for s in segments if s["text"])
        nonempty = sum(1 for s in segments if len(s["text"]) >= 20)
        coverage = nonempty / max(1, len(segments))
        status = "ok" if raw_text else "needs_ocr"
        notes = f"PDF 共 {len(segments)} 页，文字页覆盖率 {coverage:.0%}"
        if coverage < 0.5:
            notes += "；较多页面没有可提取文字，建议 OCR 或人工补录"
        file_type = "pdf"
    elif suffix in {".txt", ".md", ".markdown"}:
        raw_text, encoding = decode_text(content)
        segments = [{"source_ref": "text", "page": None, "text": raw_text}]
        status, notes, file_type = "ok", f"文本编码：{encoding}", "text"
    else:
        raise ValueError("只支持 PDF、TXT、MD 文件")
    if len(raw_text) > MAX_TEXT_CHARS:
        raw_text = raw_text[:MAX_TEXT_CHARS]
        notes += f"；正文超过 {MAX_TEXT_CHARS} 字符，已截断"
    return {"file_type": file_type, "raw_text": raw_text, "segments": segments, "parse_status": status, "parse_notes": notes}


def add_material(mentor_id: str, file_name: str, content: bytes) -> dict[str, Any]:
    parsed = parse_material(file_name, content)
    item = {
        "id": new_id("material"), "mentor_id": mentor_id, "file_name": Path(file_name).name,
        "sha256": hashlib.sha256(content).hexdigest(), "created_at": utc_now(),
        **{k: parsed[k] for k in ("file_type", "raw_text", "parse_status", "parse_notes")},
        "segments_json": json.dumps(parsed["segments"], ensure_ascii=False),
    }
    try:
        with connect() as db:
            db.execute("""INSERT INTO materials
              (id,mentor_id,file_name,file_type,sha256,raw_text,segments_json,parse_status,parse_notes,created_at)
              VALUES(:id,:mentor_id,:file_name,:file_type,:sha256,:raw_text,:segments_json,:parse_status,:parse_notes,:created_at)""", item)
    except sqlite3.IntegrityError as exc:
        raise ValueError("该资料已经上传过") from exc
    return {k: item[k] for k in item if k not in {"raw_text", "segments_json"}}


def get_material(material_id: str) -> dict[str, Any] | None:
    with connect() as db:
        return row_dict(db.execute("SELECT * FROM materials WHERE id=?", (material_id,)).fetchone(), ("segments_json",))


def list_materials(mentor_id: str) -> list[dict[str, Any]]:
    with connect() as db:
        rows = db.execute("""SELECT m.id,m.mentor_id,m.file_name,m.file_type,m.parse_status,m.parse_notes,m.created_at,
          (SELECT e.status FROM extractions e WHERE e.material_id=m.id ORDER BY e.created_at DESC LIMIT 1) extraction_status,
          (SELECT e.id FROM extractions e WHERE e.material_id=m.id ORDER BY e.created_at DESC LIMIT 1) extraction_id
          FROM materials m WHERE mentor_id=? ORDER BY created_at DESC""", (mentor_id,))
        return [dict(r) for r in rows]


def parse_json_output(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip(), flags=re.I)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("模型没有返回有效 JSON")
        value = json.loads(cleaned[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("模型提炼结果必须是 JSON 对象")
    return value


def save_extraction(material_id: str, model: str, structured: dict[str, Any], raw_output: str) -> dict[str, Any]:
    item = {"id": new_id("extract"), "material_id": material_id, "model": model,
            "structured_json": json.dumps(structured, ensure_ascii=False), "raw_output": raw_output,
            "status": "pending_review", "created_at": utc_now()}
    with connect() as db:
        db.execute("""INSERT INTO extractions(id,material_id,model,structured_json,raw_output,status,created_at)
          VALUES(:id,:material_id,:model,:structured_json,:raw_output,:status,:created_at)""", item)
    return {**item, "structured_json": structured}


def get_extraction(extraction_id: str) -> dict[str, Any] | None:
    with connect() as db:
        return row_dict(db.execute("SELECT * FROM extractions WHERE id=?", (extraction_id,)).fetchone(), ("structured_json",))


def review_extraction(extraction_id: str, structured: dict[str, Any], status: str, note: str) -> dict[str, Any] | None:
    with connect() as db:
        db.execute("UPDATE extractions SET structured_json=?,status=?,reviewer_note=?,reviewed_at=? WHERE id=?",
                   (json.dumps(structured, ensure_ascii=False), status, note, utc_now(), extraction_id))
    return get_extraction(extraction_id)


def add_daily_view(mentor_id: str, view_date: str, data_as_of: str, raw_text: str,
                   structured: dict[str, Any]) -> dict[str, Any]:
    item = {"id": new_id("view"), "mentor_id": mentor_id, "view_date": view_date, "data_as_of": data_as_of,
            "raw_text": raw_text, "structured_json": json.dumps(structured, ensure_ascii=False),
            "status": "pending_review", "created_at": utc_now()}
    with connect() as db:
        db.execute("""INSERT INTO daily_views(id,mentor_id,view_date,data_as_of,raw_text,structured_json,status,created_at)
          VALUES(:id,:mentor_id,:view_date,:data_as_of,:raw_text,:structured_json,:status,:created_at)""", item)
    return {**item, "structured_json": structured}


def review_daily_view(view_id: str, structured: dict[str, Any], status: str) -> None:
    with connect() as db:
        changed = db.execute("UPDATE daily_views SET structured_json=?,status=?,reviewed_at=? WHERE id=?",
                             (json.dumps(structured, ensure_ascii=False), status, utc_now(), view_id)).rowcount
        if not changed:
            raise ValueError("每日观点不存在")


def lab_state(mentor_id: str) -> dict[str, Any]:
    with connect() as db:
        views = [row_dict(r, ("structured_json",)) for r in db.execute(
            "SELECT * FROM daily_views WHERE mentor_id=? ORDER BY view_date DESC,created_at DESC LIMIT 100", (mentor_id,))]
        skills = [row_dict(r, ("skill_json",)) for r in db.execute(
            "SELECT * FROM skill_versions WHERE mentor_id=? ORDER BY version DESC", (mentor_id,))]
        evaluations = [row_dict(r, ("result_json",)) for r in db.execute(
            "SELECT * FROM evaluations WHERE mentor_id=? ORDER BY created_at DESC LIMIT 100", (mentor_id,))]
    return {"materials": list_materials(mentor_id), "daily_views": views, "skill_versions": skills, "evaluations": evaluations}


def approved_knowledge(mentor_id: str) -> list[dict[str, Any]]:
    with connect() as db:
        rows = db.execute("""SELECT e.structured_json,m.file_name FROM extractions e JOIN materials m ON m.id=e.material_id
          WHERE m.mentor_id=? AND e.status='approved' ORDER BY e.reviewed_at""", (mentor_id,))
        return [{"file_name": r["file_name"], "content": json.loads(r["structured_json"])} for r in rows]


def approved_views(mentor_id: str, limit: int = 60) -> list[dict[str, Any]]:
    with connect() as db:
        rows = db.execute("""SELECT id,view_date,data_as_of,structured_json FROM daily_views
          WHERE mentor_id=? AND status='approved' ORDER BY view_date DESC,created_at DESC LIMIT ?""", (mentor_id, limit))
        return [{"id": r["id"], "view_date": r["view_date"], "data_as_of": r["data_as_of"],
                 "content": json.loads(r["structured_json"])} for r in rows]


def next_skill_version(mentor_id: str) -> tuple[int, int | None]:
    with connect() as db:
        row = db.execute("SELECT MAX(version) max_version FROM skill_versions WHERE mentor_id=?", (mentor_id,)).fetchone()
    parent = row["max_version"] if row and row["max_version"] is not None else None
    return (parent or 0) + 1, parent


def save_skill_version(mentor_id: str, skill: dict[str, Any], reason: str) -> dict[str, Any]:
    version, parent = next_skill_version(mentor_id)
    item = {"id": new_id("skillv"), "mentor_id": mentor_id, "version": version, "status": "candidate",
            "parent_version": parent, "skill_json": json.dumps(skill, ensure_ascii=False),
            "change_reason": reason, "created_at": utc_now()}
    with connect() as db:
        db.execute("""INSERT INTO skill_versions(id,mentor_id,version,status,parent_version,skill_json,change_reason,created_at)
          VALUES(:id,:mentor_id,:version,:status,:parent_version,:skill_json,:change_reason,:created_at)""", item)
    return {**item, "skill_json": skill}


def review_skill_version(mentor_id: str, version: int, skill: dict[str, Any], note: str) -> None:
    with connect() as db:
        changed = db.execute("""UPDATE skill_versions SET skill_json=?,change_reason=?
          WHERE mentor_id=? AND version=? AND status='candidate'""",
          (json.dumps(skill, ensure_ascii=False), note, mentor_id, version)).rowcount
        if not changed:
            raise ValueError("只有候选 Skill 可以编辑")


def promote_skill(mentor_id: str, version: int) -> None:
    with connect() as db:
        approved = db.execute("""SELECT 1 FROM evaluations WHERE mentor_id=? AND skill_version=?
          AND reviewer_decision='approved' LIMIT 1""", (mentor_id, version)).fetchone()
        if not approved:
            raise ValueError("请先运行回测并人工批准至少一份评估")
        db.execute("UPDATE skill_versions SET status='archived' WHERE mentor_id=? AND status='active'", (mentor_id,))
        changed = db.execute("UPDATE skill_versions SET status='active',promoted_at=? WHERE mentor_id=? AND version=?",
                             (utc_now(), mentor_id, version)).rowcount
        if not changed:
            raise ValueError("Skill 版本不存在")


def save_evaluation(mentor_id: str, version: int, result: dict[str, Any]) -> dict[str, Any]:
    item = {"id": new_id("eval"), "mentor_id": mentor_id, "skill_version": version,
            "result_json": json.dumps(result, ensure_ascii=False), "created_at": utc_now()}
    with connect() as db:
        db.execute("INSERT INTO evaluations(id,mentor_id,skill_version,result_json,created_at) VALUES(:id,:mentor_id,:skill_version,:result_json,:created_at)", item)
    return {**item, "result_json": result}


def review_evaluation(evaluation_id: str, decision: str, note: str) -> None:
    with connect() as db:
        changed = db.execute("UPDATE evaluations SET reviewer_decision=?,reviewer_note=?,reviewed_at=? WHERE id=?",
                             (decision, note, utc_now(), evaluation_id)).rowcount
        if not changed:
            raise ValueError("回测评估不存在")
