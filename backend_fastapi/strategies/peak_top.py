# -*- coding: utf-8 -*-
"""筹码峰占比（PEAK）· 最大选股策略。

逻辑：每 `rebalance` 个交易日，在回测池内按**当日 PEAK（筹码分布最高峰占比）**
降序排名，取前 `top_n` 只等权持有，跌出榜单即移出（`top_n` 默认 1 = 只买最大的那只）。

PEAK = 每日筹码分布里**最高峰（最集中价格分箱）**的占比（max(每行分布)，合计 100）。
数值越大说明筹码越向单一价位集中。本策略与 K 线副图 PEAK 指标**同一口径**：
`tri_decay` 公式、`qfq`、带锁仓修正、`bins` 同参数（`get_chip_frames` 的默认）。

**为什么能回测（关键）**：`chip_formulas` 的筹码矩阵是**因果**的 —— 第 i 帧只由
第 0..i 天的换手与价格推出（`chips *= (1-k)` 后再铺当日筹码），所以一次算完某只票就
得到**整段历史**的 PEAK 序列，可以逐日做横截面排序，不存在未来函数。为让窗口首日
不退化成「只累积了一天」的尖刺，取数区间在 start 之前再往前多取约 1.5×window 个
自然日 —— 这些帧只养状态、不参与选股。

口径（与 PEAK 指标一致，非 SCR90 周榜口径）：
    - formula_id="tri_decay"，adjust="qfq"，offline=False（带锁仓修正）。
    - 代价：offline=False 会在线取流通股本 / 锁仓比例，池子一大（尤其全市场）
      可能偏慢或被限流；建议用「股票池」跑，别直接挂全市场。
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
    id="peak_top",
    name="筹码峰占比 · 最大选股",
    category="筹码",
    description="每 N 个交易日在池内按 PEAK（筹码分布最高峰占比）降序排名，"
                "取最大的前 K 只等权持有，跌出榜单即移出；PEAK 越大筹码越集中。"
                "PEAK 默认用全历史（与副图 PEAK 指标完全同口径）；window>0 才切近期窗口。",
    inputs=["close"],
    outputs="目标权重向量(组合占比)",
    params=[
        ParamSpec("top_n", "int", 1, min=1, max=200, label="持仓数量（PEAK 最大前 N）"),
        ParamSpec("rebalance", "int", 1, min=1, max=60, label="调仓间隔（交易日）"),
        ParamSpec("window", "int", 0, min=0, max=1000, label="筹码窗口（0=全历史，与 PEAK 指标同口径）"),
        ParamSpec("bins", "int", 80, min=20, max=200, label="价格分箱数"),
        ParamSpec("position", "float", 1.0, min=0.1, max=1.0, label="总仓位系数"),
    ],
)
def peak_top(ctx, top_n: int = 1, rebalance: int = 1, window: int = 250,
             bins: int = 80, position: float = 1.0) -> list:
    """按 PEAK 降序做横截面选股，返回完整目标组合的 list[Signal]。"""
    codes = [str(c).strip() for c in (ctx.codes or []) if str(c).strip()]
    if not codes:
        return []
    top_n = max(1, int(top_n))
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
        # 逐标的检查点：本策略逐只算筹码矩阵，池子一大最慢，**必须**在这里查暂停/停止，
        # 否则「停止」按钮在这段完全无效。未安装控制器时（如注册冒烟、单测）这里是 no-op。
        checkpoint()
        s = _peak_series(code, fetch_start, end, bins)
        if s is None:
            continue
        if win_start:
            s = s[s.index >= win_start]         # 预热段不参与选股
        s = s.dropna()
        if s.empty:
            continue
        cols[code] = s
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
                                  reason=f"跌出 PEAK 前{top_n}", meta={"dropped": 1}))

        # 完整目标组合：等权分配总仓位
        w = (pos / len(picks)) if picks else 0.0
        for code in sorted(picks):
            signals.append(Signal(
                code=code, time=day_s, weight=w,
                reason=f"PEAK 排名前{top_n}",
                meta={"peak": round(float(row[code]), 2), "rank": ranked.index(code) + 1},
            ))

        held = picks
    return signals
