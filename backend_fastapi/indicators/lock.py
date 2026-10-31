# -*- coding: utf-8 -*-
"""锁仓信号（主图箭头指标）：低换手沉淀成立 / 强换手解除。

判定口径与 EMA 锁仓筹码公式同源：
  · 自然换手率 B = 1/N，N 由换手率序列反推「100% 换手周期」；
  · 锁仓成立（+1，红上箭头）：换手率首次跌破 M_low × B（低换手沉淀开始）；
  · 锁仓解除（-1，绿下箭头）：处于锁仓态时换手率升破 M_high × B（筹码松动）。

输出 kind="marker"：+1 / -1 / 0（0 与 NaN 前端不画）。只在状态切换点出箭头，
不是每天都标 —— 与「主图稀疏上下箭头」的视觉预期一致。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import data
from .base import ParamSpec, indicator, series_marker


def _turnover_to_n(turnover: np.ndarray) -> np.ndarray:
    """由换手率序列反推每日的『100% 换手周期』N[t]（与筹码公式同口径）。"""
    n = len(turnover)
    pref = np.concatenate(([0.0], np.cumsum(turnover)))
    n_est = (n / pref[n]) if pref[n] > 1e-12 else float(n)
    out = np.empty(n, dtype=float)
    for t in range(n):
        total = pref[t + 1]
        if total < 1.0:
            out[t] = n_est
            continue
        j = int(np.searchsorted(pref, total - 1.0, side="right")) - 1
        out[t] = max(1, t - j + 1)
    return out


@indicator(
    id="LOCK",
    name="锁仓",
    category="chip",
    panel="main",
    params=[
        ParamSpec("M_low", "float", 0.5, min=0.1, max=3.0, label="锁仓成立阈值(×B)"),
        ParamSpec("M_high", "float", 2.0, min=0.5, max=5.0, label="锁仓解除阈值(×B)"),
    ],
)
def lock(df: pd.DataFrame, M_low: float = 0.5, M_high: float = 2.0) -> dict:
    code = data.code_of(df)
    period = str(df.attrs.get("period") or "day")
    turnover = data.get_turnover(code, period=period)
    aligned = turnover.reindex(df.index)
    to = np.asarray(aligned.values, dtype=float)
    # 索引类型不一致导致 reindex 全空时，长度相同则退回原序列（同源取数，顺序一致）
    if np.all(~np.isfinite(to)) and len(turnover) == len(df):
        to = np.asarray(turnover.values, dtype=float)
    if len(to) != len(df):
        raise RuntimeError(f"换手率长度与 K 线不一致: {len(to)} != {len(df)}")
    b = 1.0 / _turnover_to_n(np.nan_to_num(to, nan=0.0))

    sig = np.zeros(len(df), dtype=float)
    state = 0                       # 0 = 未锁仓，1 = 锁仓中
    for t in range(len(df)):
        if not np.isfinite(to[t]):
            continue
        if state == 0 and to[t] < M_low * b[t]:
            sig[t] = 1.0            # 图标序号 1：上箭头（锁仓成立）
            state = 1
        elif state == 1 and to[t] > M_high * b[t]:
            sig[t] = 2.0            # 图标序号 2：下箭头（锁仓解除）
            state = 0
    return {"series": [series_marker("锁仓", pd.Series(sig, index=df.index))]}
