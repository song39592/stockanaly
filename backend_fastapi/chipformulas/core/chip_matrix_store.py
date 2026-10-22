# -*- coding: utf-8 -*-
"""筹码矩阵的**增量持久化**存储（SQLite，复用价格库的数据库格式约定）。

与 `matrix_cache` 的**内存热缓存**不同，这里是**跨进程 / 跨天**的持久层，也是
「每日增量落盘」的基础：每只票存的是 **全历史矩阵 + 末日筹码状态(seed) + 末日日期**。

· 同日 / 同口径再次请求 → 直接命中（含跨进程、隔天同口径），无需重建；
· 数据推进了若干天 → 只从 seed 续算「新增的天」，不再重算全历史；
· key 与 `compute_matrix` 的矩阵身份一致（code/formula/bins/adjust/offline/period/params），
  但不含 start/end/window —— 存的是全历史，窗口由调用方按需切。

数据库位置：`<data>/cache/chip_matrix.db`（数据盘，仓库外，不进 git）。
"""
from __future__ import annotations

import io
import json
import sqlite3
from datetime import datetime
from pathlib import Path

import numpy as np

try:                                     # 数据盘根（仓库外，天然不进 git）
    from config import CACHE_DIR
except Exception:                        # 降级：backend_fastapi/cache/
    CACHE_DIR = Path(__file__).resolve().parents[2] / "cache"

DB_PATH = CACHE_DIR / "chip_matrix.db"


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS matrix (
          code     TEXT    NOT NULL,
          formula  TEXT    NOT NULL,
          bins     INTEGER NOT NULL,
          adjust   TEXT    NOT NULL,
          offline  INTEGER NOT NULL,
          period   TEXT    NOT NULL,
          params   TEXT    NOT NULL,
          last_date TEXT   NOT NULL,
          blob     BLOB,
          dates    TEXT,
          meta     TEXT,
          updated  TEXT    NOT NULL,
          PRIMARY KEY (code, formula, bins, adjust, offline, period, params)
        )
        """)


def _connect():
    return sqlite3.connect(str(DB_PATH))


def _pack(arrays: dict) -> bytes:
    buf = io.BytesIO()
    np.savez_compressed(buf, **{k: v for k, v in arrays.items() if v is not None})
    return buf.getvalue()


def _unpack(blob: bytes) -> dict:
    with np.load(io.BytesIO(blob), allow_pickle=True) as f:
        return {k: f[k] for k in f.files}


def save(code, formula, bins, adjust, offline, period, params_key,
         res, terminal_chips, last_date) -> None:
    """落盘：全历史矩阵 + 末日 seed + 元信息（失败静默，缓存只是加速手段）。"""
    try:
        init_db()
        arrays = {
            "pct": np.asarray(res.pct, dtype=np.float32),
            "close": np.asarray(res.close, dtype=np.float32),
            "turnover": np.asarray(res.turnover, dtype=np.float32),
            "centers": np.asarray(res.centers, dtype=np.float32),
            "edges": np.asarray(res.edges, dtype=np.float32),
            "terminal_chips": (np.asarray(terminal_chips, dtype=np.float64)
                               if terminal_chips is not None else None),
            "gray": (np.asarray(res.gray, dtype=np.float32)
                     if res.gray is not None else None),
            "red": (np.asarray(res.red, dtype=np.float32)
                    if res.red is not None else None),
        }
        blob = _pack(arrays)
        dates = json.dumps(list(res.dates), ensure_ascii=False)
        meta = json.dumps({
            "float_shares": float(res.float_shares), "warm": int(res.warm),
            "adjust": res.adjust, "period": res.period, "formula_id": res.formula_id,
            "formula_name": res.formula_name, "params": res.params,
            "lockup_ratio": float(res.lockup_ratio), "lockup_factor": float(res.lockup_factor),
        }, ensure_ascii=False)
        with _connect() as conn:
            conn.execute(
                """INSERT INTO matrix(code,formula,bins,adjust,offline,period,params,
                                      last_date,blob,dates,meta,updated)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(code,formula,bins,adjust,offline,period,params)
                   DO UPDATE SET last_date=excluded.last_date, blob=excluded.blob,
                                 dates=excluded.dates, meta=excluded.meta,
                                 updated=excluded.updated""",
                (code, formula, int(bins), adjust, int(bool(offline)), period, params_key,
                 last_date, blob, dates, meta, datetime.now().isoformat()),
            )
    except Exception:                        # noqa: BLE001 - 写不上不能影响主流程
        return


def load(code, formula, bins, adjust, offline, period, params_key):
    """读出持久化的全历史矩阵 + seed；未命中返回 None。"""
    try:
        init_db()
        with _connect() as conn:
            row = conn.execute(
                "SELECT last_date,blob,dates,meta FROM matrix WHERE "
                "code=? AND formula=? AND bins=? AND adjust=? AND offline=? "
                "AND period=? AND params=?",
                (code, formula, int(bins), adjust, int(bool(offline)), period, params_key),
            ).fetchone()
        if not row or row[1] is None:
            return None
        last_date, blob, dates, meta = row
        a = _unpack(blob)
        meta_d = json.loads(meta)
        return {
            "last_date": last_date,
            "dates": json.loads(dates),
            "pct": np.asarray(a["pct"], dtype=np.float64),
            "close": np.asarray(a["close"], dtype=np.float64),
            "turnover": np.asarray(a["turnover"], dtype=np.float64),
            "centers": np.asarray(a["centers"], dtype=np.float64),
            "edges": np.asarray(a["edges"], dtype=np.float64),
            "gray": (np.asarray(a["gray"], dtype=np.float64)
                     if "gray" in a and a["gray"] is not None else None),
            "red": (np.asarray(a["red"], dtype=np.float64)
                    if "red" in a and a["red"] is not None else None),
            "terminal_chips": (np.asarray(a["terminal_chips"], dtype=np.float64)
                               if "terminal_chips" in a and a["terminal_chips"] is not None else None),
            "meta": meta_d,
        }
    except Exception:                        # noqa: BLE001 - 坏缓存当未命中
        return None


def latest_date(code, formula, bins, adjust, offline, period, params_key):
    rec = load(code, formula, bins, adjust, offline, period, params_key)
    return rec["last_date"] if rec else None


def clear() -> None:
    """清空磁盘持久层。"""
    try:
        init_db()
        with _connect() as conn:
            conn.execute("DELETE FROM matrix")
    except Exception:                        # noqa: BLE001
        return
