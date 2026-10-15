# -*- coding: utf-8 -*-
"""筹码集中度 90（SCR90）**升序选股**策略。

逻辑：每 `rebalance` 个交易日，在回测池内按 SCR90 **升序**排名，取前 `top_n` 只持有，
跌出榜单则移出。「筹码越集中 → 越可能已完成吸筹」是使用它的前提。

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

【组合权重契约】weight 是**占组合净值的比例**，等权分配，总仓位 = `position`，
现金 = 1 − position。本策略天然是「按调仓日做横截面排名」，所以每个调仓日
直接输出**完整目标组合**即可：入选的给 `position/len(picks)`，
掉出的**必须显式给 weight=0**（不发的话该日不会被登记为调仓日，清仓不会发生）。

成本（本机实测）：约 0.25 s/只（窗口 250 日 + 预热）。池子 100 只约 25 秒；
全市场 5585 只约 25 分钟 —— 建议用「股票池」跑，别直接挂全市场。
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from features.chip import formulas as chip_formulas
from features.chip.formulas.core import scr
from .core.base import strategy, ParamSpec, Signal, checkpoint


@strategy(
    id="scr90",
    name="筹码集中度90 · 升序选股",
    category="筹码",
    description="每 N 个交易日在池内按 SCR90（集中度90）升序排名，取最集中的前 K 只持有，"
                "跌出榜单即移出；SCR90 越小代表筹码越集中。",
    inputs=["close"],
    outputs="目标权重向量(组合占比)",
    params=[
        ParamSpec("top_n", "int", 100, min=1, max=200, label="持仓数量（SCR90 最小前 N）"),
        ParamSpec("rebalance", "int", 5, min=1, max=60, label="调仓间隔（交易日）"),
        ParamSpec("window", "int", 250, min=60, max=1000, label="筹码窗口（交易日）"),
        ParamSpec("bins", "int", 80, min=20, max=200, label="价格分箱数"),
        ParamSpec("position", "float", 1.0, min=0.1, max=1.0, label="总仓位系数"),
    ],
)
def scr90(ctx, top_n: int = 100, rebalance: int = 5, window: int = 250,
          bins: int = 80, position: float = 1.0) -> list:
    """按 SCR90 升序做横截面选股，返回完整目标组合的 list[Signal]。"""
    codes = [str(c).strip() for c in (ctx.codes or []) if str(c).strip()]
    if not codes:
        return []
    top_n = max(1, int(top_n))
    rebalance = max(1, int(rebalance))
    window = max(30, int(window))
    bins = max(20, int(bins))
    pos = min(1.0, max(0.0, float(position)))

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
        # 逐标的检查点：本策略全市场实测约 0.25 s/只、合计 25 分钟，
        # 是所有策略里最慢的。**必须**在这里查暂停/停止，
        # 否则「停止」按钮在这段完全无效（框架侧没有别的检查点）。
        # 未安装控制器时（如注册冒烟、单测）这里是 no-op，不增加任何开销。
        checkpoint()
        try:
            res = chip_formulas.compute_matrix(
                code, formula_id="tri_decay", start=fetch_start, end=end,
                adjust=ctx.adjust, bins=bins, offline=True)
        except Exception:                        # noqa: BLE001 - 单只失败不拖垮整批
            continue
        s = scr.scr_series_pd(res)
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
        picks = set(row.sort_values().index[:min(top_n, len(row))])
        day_s = str(day)[:10]

        # 掉出的票显式 weight=0：不发的话这一天不会进入引擎的 rebalance_dates，
        # 「移出」根本没被登记，上一期持仓会继续持有。
        for code in sorted(held - picks):
            signals.append(Signal(code=code, time=day_s, weight=0.0,
                                  reason=f"跌出 SCR90 前{top_n}", meta={"dropped": 1}))

        # 完整目标组合：等权分配总仓位
        w = (pos / len(picks)) if picks else 0.0
        ranked = list(row.sort_values().index[:min(top_n, len(row))])
        for code in sorted(picks):
            signals.append(Signal(
                code=code, time=day_s, weight=w,
                reason=f"SCR90 排名前{top_n}",
                meta={"scr90": round(float(row[code]), 4), "rank": ranked.index(code) + 1},
            ))

        held = picks
    return signals