# -*- coding: utf-8 -*-
"""筹码矩阵的**磁盘 + 内存**两级缓存。

`tri_decay` 建矩阵是全市场扫描 / 多次改参数回测的瓶颈（Python 逐帧循环 + 全历史取数，
约 0.1s+/只）。但矩阵只由 (code, formula, bins, start/window, offline) 决定——
`band_pct/tol/top_n/threshold` 这些**选股**参数根本不动矩阵，只是矩阵的后处理。

因此把矩阵落盘后：
- 单次全市场扫描：首次构建并落盘，之后同口径再扫直接命中（含跨进程、跨天同口径）；
- 多次改选股参数回测：矩阵不动，只重跑 4.4ms/只的 peak_band_series，从 ~20 分钟降到分钟级。

⚠️ 缓存键已含 `offline` 与 `start/end/window`：有无锁仓修正是业务口径差异，绝不能混；
`end=None`（取最新）的键额外带上「当日日期」令牌，按天自失效，避免拿到过期的「最新」矩阵。
"""
from __future__ import annotations

import hashlib
import json
import threading
from functools import lru_cache
from pathlib import Path

import numpy as np

try:                                     # 数据盘根（仓库外，天然不进 git）
    from config import CACHE_DIR
except Exception:                        # 降级：落到 backend_fastapi/cache/（被 .gitignore 的 cache/ 忽略）
    CACHE_DIR = Path(__file__).resolve().parents[2] / "cache"

CACHE_VERSION = 1
_MATRIX_DIR = CACHE_DIR / "matrices"
_MEM_MAX = 512                           # 进程内热缓存（仅工作集；全量由磁盘兜底，避免 ~4.5GB 驻留）
_lock = threading.Lock()                 # 预留：批量写盘时如需串行化可在此加锁


def _key_hash(key: tuple) -> str:
    return f"v{CACHE_VERSION}_" + hashlib.sha256(repr(key).encode("utf-8")).hexdigest()


def _path(key: tuple) -> Path:
    return _MATRIX_DIR / (_key_hash(key) + ".npz")


def serialize(res) -> dict:
    """ChipResult -> 可 npz 存储的字典（浮点降 float32 省磁盘；日期/params 走对象/字符串）。"""
    d = {
        "dates": np.array(res.dates, dtype=object),
        "close": np.asarray(res.close, dtype=np.float32),
        "centers": np.asarray(res.centers, dtype=np.float32),
        "edges": np.asarray(res.edges, dtype=np.float32),
        "pct": np.asarray(res.pct, dtype=np.float32),
        "turnover": np.asarray(res.turnover, dtype=np.float32),
        "float_shares": float(res.float_shares),
        "warm": int(res.warm),
        "adjust": str(res.adjust),
        "period": str(res.period),
        "formula_id": str(res.formula_id),
        "formula_name": str(res.formula_name),
        "params": json.dumps(res.params, ensure_ascii=False),
        "lockup_ratio": float(res.lockup_ratio),
        "lockup_factor": float(res.lockup_factor),
    }
    if res.gray is not None:
        d["gray"] = np.asarray(res.gray, dtype=np.float32)
    if res.red is not None:
        d["red"] = np.asarray(res.red, dtype=np.float32)
    return d


def deserialize(d: dict):
    """字典 -> ChipResult（浮点升回 float64，与运行时口径一致）。"""
    from .base import ChipResult        # 延迟导入：避免 base <-> matrix_cache 循环依赖
    gray = d.get("gray")
    red = d.get("red")
    return ChipResult(
        dates=[str(x) for x in d["dates"]],
        close=np.asarray(d["close"], dtype=np.float64),
        centers=np.asarray(d["centers"], dtype=np.float64),
        edges=np.asarray(d["edges"], dtype=np.float64),
        pct=np.asarray(d["pct"], dtype=np.float64),
        turnover=np.asarray(d["turnover"], dtype=np.float64),
        float_shares=float(d["float_shares"]),
        warm=int(d["warm"]),
        adjust=str(d["adjust"]),
        period=str(d["period"]),
        formula_id=str(d["formula_id"]),
        formula_name=str(d["formula_name"]),
        params=json.loads(str(d["params"])),
        lockup_ratio=float(d["lockup_ratio"]),
        lockup_factor=float(d["lockup_factor"]),
        gray=(np.asarray(gray, dtype=np.float64) if gray is not None else None),
        red=(np.asarray(red, dtype=np.float64) if red is not None else None),
    )


@lru_cache(maxsize=_MEM_MAX)
def _mem(key: tuple):
    """内存热缓存；同时兼做磁盘未命中的 None 记忆（避免反复读盘）。"""
    p = _path(key)
    if not p.exists():
        return None
    try:
        with np.load(p, allow_pickle=True) as f:
            return deserialize({k: f[k] for k in f.files})
    except Exception:                    # noqa: BLE001 - 坏缓存直接当未命中
        return None


def load(key: tuple):
    """返回缓存的 ChipResult，未命中返回 None。"""
    return _mem(key)


def save(key: tuple, res) -> None:
    """落盘（压缩）；失败静默——缓存只是加速手段，写不上不能影响主流程。

    写完后预热内存热缓存，覆盖可能已缓存的 None（同一 key 先未命中后落盘的情况）。
    """
    try:
        _MATRIX_DIR.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(_path(key), **serialize(res))
    except Exception:                    # noqa: BLE001
        return
    _mem(key)


def clear() -> None:
    """清空内存热缓存 + 磁盘缓存目录。"""
    _mem.cache_clear()
    try:
        if _MATRIX_DIR.exists():
            for fp in _MATRIX_DIR.glob("*.npz"):
                fp.unlink()
    except Exception:                    # noqa: BLE001
        return
