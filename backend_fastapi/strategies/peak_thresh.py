# -*- coding: utf-8 -*-
"""筹码峰簇占比（PEAKB）· 阈值选股策略。

逻辑：每 `rebalance` 个交易日，在回测池内选出**当日 PEAKB > threshold** 的全部标的，
等权持有；次日重新判定，不再满足阈值即移出。`threshold` 默认 80（主峰簇容纳 80% 以上
筹码，属极端集中），可调。

PEAKB 口径同下方副图 PEAKB 指标 / peak_top：tri_decay、qfq、带锁仓修正、bins 同参数，
并含峰宽 band_pct / 衰减容差 tol（与 PEAKB 指标一致）。
"""
from __future__ import annotations

import datetime as dt
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

import chipformulas as chip_formulas
from chipformulas.core.peak import peak_band_series
from .core.base import strategy, ParamSpec, Signal, checkpoint, Cancelled

# 锁仓系数公式下拉：中文标签 → 实际筹码公式 id（"全部用1" = 锁仓系数强制为 1，不修正）
_LOCKUP_FORMULA = {
    "带锁仓系数L": "tri_decay",
    "全部用1": "tri_decay_nolock",
}


def _peak_series(code: str, fetch_start, end, bins: int, formula: str,
                 band_pct: float = 20.0, tol: float = 0.5) -> "tuple[pd.Series | None, float]":
    """取单只票的 **PEAKB（主峰簇占比 %）** 时间序列，并一并返回该票锁仓系数 L。

    ⚠️ `compute_matrix` 的**价格轴（分箱边界）取决于取数范围的最大/最小价**，所以
    固定传 `end=None`（铺满「上市→最新」全价格轴，与 PEAKB 指标 `chip_rows_of` 同口径），
    `end` 仅用于裁掉回测终点之后的帧，不参与价格轴计算。

    PEAKB 与 PEAK（只取最高单箱）不同：沿主峰两侧累加「连续衰减」的箱，遇回升或超出
    峰位 ±band_pct% 即停；`band_pct` / `tol` 即 PEAKB 指标的「峰宽 / 衰减容差」。

    锁仓系数 L = 1/(1-r) 直接取自 `compute_matrix` 的返回（chipformulas 已算过，不重复取数），
    供策略把该票的锁仓系数写进信号 meta；公式选择（含是否带 L 修正）由 `formula` 决定。
    """
    try:
        res = chip_formulas.compute_matrix(
            code, formula_id=formula, start=fetch_start, end=None,
            adjust="qfq", bins=bins, offline=False)
    except Exception:                        # noqa: BLE001 - 单只失败不拖垮整批
        return None, 1.0
    # 计算层（峰簇占比）在 chipformulas.core.peak，与 PEAKB 副图指标同一份实现，
    # 这里直接用矩阵结果逐帧算，无需再走指标展示层。
    s = pd.Series(peak_band_series(res.pct, res.centers, band_pct, tol),
                  index=list(res.dates))
    if end:
        s = s[s.index <= end]
    # 锁仓系数 L：tri_decay_nolock 内部把 lockup 强制为 1（不做锁仓放大），compute_matrix
    # 回写的 lockup_factor 仍是真实锁仓，故这里按公式口径取：nolock→1.0，其余→真实 L。
    L = 1.0 if formula == "tri_decay_nolock" else float(getattr(res, "lockup_factor", 1.0))
    return s, L


@strategy(
    id="peak_thresh",
    name="筹码峰簇占比(PEAKB) · 阈值选股",
    category="筹码",
    description="每 N 个交易日在池内选出当日 PEAKB（筹码分布主峰簇占比 %）大于阈值的标的等权持有；"
                "跌回阈值下即移出。\n"
                "PEAKB 与下方副图 PEAKB 指标同口径（含峰宽 band_pct / 衰减容差 tol）：默认全历史，"
                "window>0 才切近期窗口；锁仓系数公式可选『带锁仓系数L』（默认）或『全部用1』。",
    inputs=["close"],
    outputs="目标权重向量(组合占比)",
    params=[
        ParamSpec("threshold", "float", 80.0, min=0.0, max=100.0, label="PEAKB 阈值"),
        ParamSpec("rebalance", "int", 1, min=1, max=60, label="调仓间隔(日)"),
        ParamSpec("window", "int", 0, min=0, max=1000, label="筹码窗口(0=全历史)"),
        ParamSpec("formula", "choice", "带锁仓系数L",
                  choices=["带锁仓系数L", "全部用1"], label="锁仓系数公式"),
        ParamSpec("bins", "int", 80, min=20, max=200, label="价格分箱数"),
        ParamSpec("band_pct", "float", 20.0, min=1.0, max=100.0, label="峰宽(价格±%)"),
        ParamSpec("tol", "float", 0.5, min=0.0, max=5.0, label="衰减容差%"),
        ParamSpec("position", "float", 1.0, min=0.1, max=1.0, label="总仓位系数"),
    ],
)
def peak_thresh(ctx, threshold: float = 80.0, rebalance: int = 1,
                window: int = 0, bins: int = 80, position: float = 1.0,
                formula: str = "tri_decay", band_pct: float = 20.0,
                tol: float = 0.5) -> list:
    """选出当日 PEAK 超阈值的全部标的，返回完整目标组合的 list[Signal]。"""
    codes = [str(c).strip() for c in (ctx.codes or []) if str(c).strip()]
    if not codes:
        return []
    threshold = float(threshold)
    rebalance = max(1, int(rebalance))
    window = int(window)                        # 0 = 全历史（与 PEAK 指标同口径）
    bins = max(20, int(bins))
    pos = min(1.0, max(0.0, float(position)))
    formula = str(formula)                      # 下拉：带锁仓系数L / 全部用1
    formula_id = _LOCKUP_FORMULA.get(formula, "tri_decay")   # 解析到实际公式 id

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
    lockups: dict[str, float] = {}
    # 逐票算全历史筹码矩阵很重：用线程池并行（与 features/chip/rank_service.py 批量口径一致）。
    # 主线程逐只 checkpoint 支持暂停/停止；被取消时取消尚未开始的票。
    def _work(code):
        return (code,) + _peak_series(code, fetch_start, end, bins, formula_id, band_pct, tol)

    nworkers = max(1, min(os.cpu_count() or 4, 12))
    _ex = ThreadPoolExecutor(max_workers=nworkers)
    futs = {_ex.submit(_work, c): c for c in codes}
    try:
        for _fut in as_completed(futs):
            c = futs[_fut]
            try:
                c, s, L = _fut.result()
            except Exception:                        # noqa: BLE001 - 单只异常不拖垮整批
                s, L = None, 1.0
            if s is not None:
                if win_start:
                    s = s[s.index >= win_start]
                s = s.dropna()
                if not s.empty:
                    cols[c] = s
                    lockups[c] = L
            checkpoint()                            # 暂停/停止点（未装控制器时为 no-op）
    except Cancelled:                               # 用户停止：取消尚未开始的票
        for _f in futs:
            _f.cancel()
        raise
    finally:
        _ex.shutdown(wait=True)
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
                                  reason=f"峰簇占比(PEAKB) 跌破 {threshold:.0f}",
                                  meta={"dropped": 1, "lockup": round(lockups.get(code, 1.0), 3),
                                        "formula": formula_id}))

        w = (pos / len(picks)) if picks else 0.0
        for code in sorted(picks):
            signals.append(Signal(
                code=code, time=day_s, weight=w,
                reason=f"峰簇占比(PEAKB)>{threshold:.0f}",
                meta={"peakb": round(float(row[code]), 2),
                      "lockup": round(lockups.get(code, 1.0), 3),
                      "formula": formula_id},
            ))

        held = picks
    return signals
