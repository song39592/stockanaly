# -*- coding: utf-8 -*-
"""筹码集中度 90（SCR90）**升序选股**策略。

逻辑：每 `rebalance` 个交易日，在回测池内按 SCR90 **升序**排名，取前 `top_n` 只持有，
跌出榜单则卖出。「筹码越集中 → 越可能已完成吸筹」是使用它的前提。

**为什么能回测（关键）**：`chip_formulas` 的筹码矩阵是**因果**的 ——
第 i 帧只由第 0..i 天的换手与价格推出（`chips *= (1-k)` 后再铺当日筹码），
所以一次算完某只票就得到**整段历史**的 SCR90 序列，可以逐日做横截面排序，
不存在未来函数。为让窗口首日不退化成「只累积了一天」的尖刺，
取数区间在 start 之前再往前多取约 1.5×window 个自然日 —— 这些帧只养状态、不参与选股。

口径：
    SCR90 = (P95 - P5) / (P95 + P5)，P5 / P95 按累计筹码取价格分位（值越小越集中）。
    批量一律 `offline=True`（不联网）：否则锁仓比例走 akshare、缺流通股本走腾讯行情现采，
    池子一大就会被拖垮 / 触发限流。
    **代价**：本策略的 SCR90 **未做锁仓修正**（与 /api/chip/rank 周榜同口径），
    要看带修正的单票值请用 K 线右侧的筹码分布。

仓位：统计引擎按「各标的等权分带」处理（每只分到 initial_capital / N），
故 weight=1.0 表示「该带满仓」，同时持 100 只不会变成 100 倍杠杆。

成本（本机实测）：约 0.25 s/只（窗口 250 日 + 预热）。池子 100 只约 25 秒；
全市场 5585 只约 25 分钟 —— 建议用「股票池」跑，别直接挂全市场。
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

import chip_formulas
from .core.base import strategy, ParamSpec, Signal


def _scr90_series(res) -> pd.Series:
    """把筹码矩阵逐帧换算成 SCR90 序列（索引 = 交易日）。

    逐帧线性插值取 P5 / P95；某帧筹码全空时留 NaN（该日不参与排名）。
    """
    pct = res.pct
    centers = res.centers
    out = np.full(pct.shape[0], np.nan)
    for i in range(pct.shape[0]):
        row = pct[i]
        total = float(row.sum())
        if total <= 0:
            continue
        cum = np.cumsum(row) / total
        p5 = float(np.interp(0.05, cum, centers))
        p95 = float(np.interp(0.95, cum, centers))
        den = p95 + p5
        if den == 0:
            continue
        out[i] = (p95 - p5) / den
    return pd.Series(out, index=list(res.dates))


@strategy(
    id="scr90",
    name="筹码集中度90 · 升序选股",
    category="筹码",
    description="每 N 个交易日在池内按 SCR90（集中度90）升序排名，取最集中的前 K 只持有，"
                "跌出榜单即卖出；SCR90 越小代表筹码越集中。",
    inputs=["close"],
    outputs="buy/sell 信号 + 仓位系数(weight)",
    params=[
        ParamSpec("top_n", "int", 100, min=1, max=200, label="持仓数量（SCR90 最小前 N）"),
        ParamSpec("rebalance", "int", 5, min=1, max=60, label="调仓间隔（交易日）"),
        ParamSpec("window", "int", 250, min=60, max=1000, label="筹码窗口（交易日）"),
        ParamSpec("bins", "int", 80, min=20, max=200, label="价格分箱数"),
        ParamSpec("position", "float", 1.0, min=0.1, max=1.0, label="建仓系数"),
    ],
)
def scr90(ctx, top_n: int = 100, rebalance: int = 5, window: int = 250,
          bins: int = 80, position: float = 1.0) -> list:
    """按 SCR90 升序做横截面选股，返回 list[Signal]。"""
    codes = [str(c).strip() for c in (ctx.codes or []) if str(c).strip()]
    if not codes:
        return []
    top_n = max(1, int(top_n))
    rebalance = max(1, int(rebalance))
    window = max(30, int(window))
    bins = max(20, int(bins))
    pos = float(position)

    end = str(ctx.end)[:10] if ctx.end else None
    win_start = str(ctx.start)[:10] if ctx.start else None

    # 取数区间往前多取一段养状态（不参与选股，只为了让窗口首帧不退化成尖刺）
    fetch_start = win_start
    if win_start:
        try:
            fetch_start = (dt.date.fromisoformat(win_start)
                           - dt.timedelta(days=int(window * 1.5))).isoformat()
        except ValueError:
            fetch_start = win_start

    cols: dict[str, pd.Series] = {}
    for code in codes:
        try:
            res = chip_formulas.compute_matrix(
                code, formula_id="tri_decay", start=fetch_start, end=end,
                adjust=ctx.adjust, bins=bins, offline=True)
        except Exception:                        # noqa: BLE001 - 单只失败不拖垮整批
            continue
        s = _scr90_series(res)
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
    held: set[str] | None = None

    for k in range(0, len(dates), rebalance):
        day = dates[k]
        row = panel.loc[day].dropna()            # 该日缺值的票不参与本次排名
        if row.empty:
            continue
        picks = set(row.sort_values().index[:min(top_n, len(row))])
        if held is None:
            entered = picks
        else:
            entered = picks - held
            for code in sorted(held - picks):
                signals.append(Signal(code=code, time=day, action="sell", weight=0.0,
                                      reason=f"跌出 SCR90 前{top_n}"))
        for code in sorted(entered):
            signals.append(Signal(code=code, time=day, action="buy", weight=pos,
                                  reason=f"SCR90 排名前{top_n}（{row[code]:.4f}）"))
        held = picks
    return signals
