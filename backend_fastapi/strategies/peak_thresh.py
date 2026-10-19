# -*- coding: utf-8 -*-
"""筹码峰占比（PEAK）· 阈值选股策略。

逻辑：每 `rebalance` 个交易日，在回测池内选出**当日 PEAK > threshold** 的全部标的，
等权持有；次日重新判定，不再满足阈值即移出。`threshold` 默认 80（单一价格分箱即
容纳 80% 以上筹码，属极端集中），可调。

PEAK 口径同 PEAK 指标 / peak_top：tri_decay、qfq、带锁仓修正、bins 同参数。
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

import chipformulas as chip_formulas
from .core.base import strategy, ParamSpec, Signal, checkpoint


def _peak_series(code: str, fetch_start, end, bins: int) -> "pd.Series | None":
    """取单只票的 PEAK 时间序列（每帧最高箱占比 %）；取不到返回 None。

    ⚠️ `compute_matrix` 的**价格轴（分箱边界）取决于取数范围的最大/最小价**，所以
    必须和 PEAK 指标取同一份范围才能数值完全一致：这里固定传 `end=None`（铺满
    「上市→最新」全价格轴，与指标 `chip_rows_of` 同口径）。`end` 仅用于裁掉回测终点
    之后的帧，不参与价格轴计算。
    """
    try:
        res = chip_formulas.compute_matrix(
            code, formula_id="tri_decay", start=fetch_start, end=None,
            adjust="qfq", bins=bins, offline=False)
    except Exception:                        # noqa: BLE001 - 单只失败不拖垮整批
        return None
    peak = np.nanmax(res.pct, axis=1)        # 每帧峰值占比（%）
    s = pd.Series(peak, index=list(res.dates))
    if end:
        s = s[s.index <= end]
    return s


@strategy(
    id="peak_thresh",
    name="筹码峰占比 · 阈值选股",
    category="筹码",
    description="每 N 个交易日在池内选出当日 PEAK（筹码分布最高峰占比）"
                "大于阈值的全部标的等权持有；跌回阈值下即移出。"
                "PEAK 默认用全历史（与副图 PEAK 指标完全同口径）；window>0 才切近期窗口。",
    inputs=["close"],
    outputs="目标权重向量(组合占比)",
    params=[
        ParamSpec("threshold", "float", 80.0, min=0.0, max=100.0, label="PEAK 阈值（大于此值入选）"),
        ParamSpec("rebalance", "int", 1, min=1, max=60, label="调仓间隔（交易日）"),
        ParamSpec("window", "int", 0, min=0, max=1000, label="筹码窗口（0=全历史，与 PEAK 指标同口径）"),
        ParamSpec("bins", "int", 80, min=20, max=200, label="价格分箱数"),
        ParamSpec("position", "float", 1.0, min=0.1, max=1.0, label="总仓位系数"),
    ],
)
def peak_thresh(ctx, threshold: float = 80.0, rebalance: int = 1,
                window: int = 250, bins: int = 80, position: float = 1.0) -> list:
    """选出当日 PEAK 超阈值的全部标的，返回完整目标组合的 list[Signal]。"""
    codes = [str(c).strip() for c in (ctx.codes or []) if str(c).strip()]
    if not codes:
        return []
    threshold = float(threshold)
    rebalance = max(1, int(rebalance))
    window = int(window)                        # 0 = 全历史（与 PEAK 指标同口径）
    bins = max(20, int(bins))
    pos = min(1.0, max(0.0, float(position)))

    end = str(ctx.end)[:10] if ctx.end else None
    win_start = str(ctx.start)[:10] if ctx.start else None

    # 默认 window=0 → 用全历史（从上市起）算筹码，与 PEAK 指标完全同口径。
    # 仅当显式 window>0 时，才截断到「回测起点前 1.5×window 天」做近期窗口（另一种口径）。
    if window and window > 0 and win_start:
        try:
            fetch_start = (dt.date.fromisoformat(win_start)
                           - dt.timedelta(days=int(window * 1.5))).isoformat()
        except ValueError:
            fetch_start = None
    else:
        fetch_start = None                      # 全历史：compute_matrix 内部 win=n_all、warm=0

    cols: dict[str, pd.Series] = {}
    for code in codes:
        checkpoint()
        s = _peak_series(code, fetch_start, end, bins)
        if s is None:
            continue
        if win_start:
            s = s[s.index >= win_start]
        s = s.dropna()
        if s.empty:
            continue
        cols[code] = s
    if not cols:
        return []

    panel = pd.DataFrame(cols).sort_index()
    dates = list(panel.index)
    signals: list[Signal] = []
    held: set[str] = set()

    for k in range(0, len(dates), rebalance):
        day = dates[k]
        row = panel.loc[day].dropna()
        if row.empty:
            continue
        picks = set(row[row > threshold].index)
        day_s = str(day)[:10]

        for code in sorted(held - picks):
            signals.append(Signal(code=code, time=day_s, weight=0.0,
                                  reason=f"PEAK 跌破 {threshold:.0f}", meta={"dropped": 1}))

        w = (pos / len(picks)) if picks else 0.0
        for code in sorted(picks):
            signals.append(Signal(
                code=code, time=day_s, weight=w,
                reason=f"PEAK>{threshold:.0f}",
                meta={"peak": round(float(row[code]), 2)},
            ))

        held = picks
    return signals
