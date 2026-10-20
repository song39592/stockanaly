# -*- coding: utf-8 -*-
"""筹码峰簇占比（PEAKB）· 最大选股策略。

逻辑：每 `rebalance` 个交易日，在回测池内按**当日 PEAKB（筹码分布主峰簇占比）**
降序排名，取前 `top_n` 只等权持有，跌出榜单即移出（`top_n` 默认 1 = 只买最大的那只）。

PEAKB = 每日筹码分布里**主峰簇**的占比（%），与 PEAK（只取最高单箱）不同：把峰值两侧
**连续衰减**的箱也归入同一座峰（遇回升 / 超出峰位 ±band_pct% 即停）。数值越大说明
筹码越向主峰簇集中。本策略与 K 线下方副图 **PEAKB 指标同一口径**（含 band_pct / tol 参数）。

**为什么能回测（关键）**：`chip_formulas` 的筹码矩阵是**因果**的 —— 第 i 帧只由
第 0..i 天的换手与价格推出（`chips *= (1-k)` 后再铺当日筹码），所以一次算完某只票就
得到**整段历史**的 PEAKB 序列，可以逐日做横截面排序，不存在未来函数。为让窗口首日
不退化成「只累积了一天」的尖刺，取数区间在 start 之前再往前多取约 1.5×window 个
自然日 —— 这些帧只养状态、不参与选股。

口径（与 PEAKB 指标一致）：
    - formula_id="tri_decay"，adjust="qfq"，offline=False（带锁仓修正）。
    - bins=80 与 PEAKB 指标默认一致；band_pct / tol 即 PEAKB 指标的「峰宽 / 衰减容差」。
    - 代价：offline=False 会在线取流通股本 / 锁仓比例；现已多线程并行取数（与周榜一致），
      全市场仍较重，建议用「股票池」跑，别直接挂全市场。
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
    id="peak_top",
    name="筹码峰簇占比(PEAKB) · 最大选股",
    category="筹码",
    description="每 N 个交易日在池内按 PEAKB（筹码分布主峰簇占比 %）降序排名，取前 K 只等权持有，"
                "跌出榜单即移出；PEAKB 越大筹码越向主峰簇集中。\n"
                "PEAKB 与下方副图 PEAKB 指标同口径（含峰宽 band_pct / 衰减容差 tol）：默认全历史，"
                "window>0 才切近期窗口；锁仓系数公式可选『带锁仓系数L』（默认）或『全部用1』。",
    inputs=["close"],
    outputs="目标权重向量(组合占比)",
    params=[
        ParamSpec("top_n", "int", 1, min=1, max=200, label="持仓数量(N)"),
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
def peak_top(ctx, top_n: int = 1, rebalance: int = 1, window: int = 0,
             bins: int = 80, position: float = 1.0, formula: str = "tri_decay",
             band_pct: float = 20.0, tol: float = 0.5) -> list:
    """按 PEAK 降序做横截面选股，返回完整目标组合的 list[Signal]。"""
    codes = [str(c).strip() for c in (ctx.codes or []) if str(c).strip()]
    if not codes:
        return []
    top_n = max(1, int(top_n))
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
    # 逐票算全历史筹码矩阵很重：用线程池并行（与 features/chip/rank_service.py 的批量口径一致，
    # 该服务已稳定用线程池跑同款 compute_matrix）。numpy 重算会释放 GIL；进度点仍在主线程
    # 逐只 checkpoint（支持暂停/停止）；被取消时 checkpoint 抛 Cancelled 中止整批。
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
                    s = s[s.index >= win_start]     # 预热段不参与选股
                s = s.dropna()
                if not s.empty:
                    cols[c] = s
                    lockups[c] = L
            checkpoint()                            # 暂停/停止点（未装控制器时为 no-op）
    except Cancelled:                               # 用户停止：取消尚未开始的票，避免继续算完整个池子
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
    held: set[str] = set()              # ★ 自己维护持仓集合

    for k in range(0, len(dates), rebalance):
        day = dates[k]
        row = panel.loc[day].dropna()            # 该日缺值的票不参与本次排名
        if row.empty:
            continue
        ranked = list(row.sort_values(ascending=False).index[:min(top_n, len(row))])
        picks = set(ranked)
        day_s = str(day)[:10]

        # 掉出的票显式 weight=0：不发的话这一天不会进入引擎的 rebalance_dates，
        # 「移出」根本没被登记，上一期持仓会继续持有。
        for code in sorted(held - picks):
            signals.append(Signal(code=code, time=day_s, weight=0.0,
                                  reason=f"跌出峰簇前{top_n}",
                                  meta={"dropped": 1, "lockup": round(lockups.get(code, 1.0), 3),
                                        "formula": formula_id}))

        # 完整目标组合：等权分配总仓位
        w = (pos / len(picks)) if picks else 0.0
        for code in sorted(picks):
            signals.append(Signal(
                code=code, time=day_s, weight=w,
                reason=f"峰簇占比(PEAKB) 排名前{top_n}",
                meta={"peakb": round(float(row[code]), 2), "rank": ranked.index(code) + 1,
                      "lockup": round(lockups.get(code, 1.0), 3),
                      "formula": formula_id},
            ))

        held = picks
    return signals
