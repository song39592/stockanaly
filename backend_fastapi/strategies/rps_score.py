# -*- coding: utf-8 -*-
"""RPS 股票池评分策略（移植自前端 analysis-engine.js 的评分引擎）。

算法（与前端一致，0-100 综合评分 → 按评分决定操作）：
  对每一个股票池快照日，在**当日全池**内计算：
    · 连续在池天数   min(days,7)/7*35           最多 35
    · 行业入池数     min(indCount,8)/8*20        最多 20
    · 行业是否前列   top-N 行业 +10
    · 是否新入池     新 +5 / 老 +10
    · 当日涨幅       clamp(7.5+change*1.5,0,15)，缺则 +4
    · 惩罚           days < minContinuousDays 则 -10
  仅对「在池中且属于回测范围」的票评分；score>=观察阈值且排名前 top_n 的建仓，
  跌出则卖出。可选市场温度（risk_off）全局空仓。

移植要点（区别于前端纯展示）：
    · 信号须经框架「次日生效 + 等权资金带」口径：weight=1.0 表示「该带满仓」，
      与 scr90 一致；统计由 stats 算净值，无需移植前端的「未来第 N 日收益采样」。
    · 池快照日未必是交易日 —— 必须把信号对齐到「≤快照日的最近交易日」，否则
      signals_to_positions 会静默丢弃（见 base.signals_to_positions）。
    · streak / 行业计数依赖**全池成员时序**，故取数走 data.load_pool_members；
      回测起点前多取 pool_warmup 天养连续性。

数据依赖：features.history.store.pool_members。无历史 / 未覆盖回测区间时，
本函数返回 []（注册冒烟安全）；真正的「请先导入历史数据」提示由编排层预检抛出。

评分可见性（预留接口）：当前只把 score/标签/连续天数/行业塞进 Signal.reason；
后续如需在前端重现前端的「候选评分表」，可在 recommend 结果里单独输出评分卡。
"""
from __future__ import annotations

import datetime as dt

from .core.base import strategy, ParamSpec, Signal
from .core import data


@strategy(
    id="rps_score",
    name="RPS 股票池评分",
    category="选股",
    description="移植自前端评分引擎：按「连续在池天数 / 行业集中度 / 涨幅 / 是否新入池」合成 "
                "0-100 评分，score≥观察阈值且排名前 N 的建仓，跌出则卖出；可选市场温度 "
                "(risk_off) 全局空仓。依赖股票池历史（pool_members）。",
    inputs=["close"],
    outputs="buy/sell 信号 + 仓位系数(weight)",
    requires_pool=True,
    pool_warmup=30,
    params=[
        ParamSpec("watch_score", "int", 50, min=0, max=100, label="观察阈值(>=即候选)"),
        ParamSpec("focus_score", "int", 70, min=0, max=100, label="重点关注阈值"),
        ParamSpec("top_n", "int", 10, min=1, max=200, label="持仓数量上限"),
        ParamSpec("rebalance", "int", 1, min=1, max=60, label="调仓间隔(数据日)"),
        ParamSpec("min_continuous_days", "int", 3, min=1, max=30, label="连续在池最少天数"),
        ParamSpec("sector_top_n", "int", 3, min=1, max=20, label="行业前列N"),
        ParamSpec("position", "float", 1.0, min=0.1, max=1.0, label="建仓系数"),
        ParamSpec("use_regime", "choice", "on", label="市场温度开关", choices=["on", "off"]),
    ],
)
def rps_score(ctx, watch_score: int = 50, focus_score: int = 70, top_n: int = 10,
              rebalance: int = 1, min_continuous_days: int = 3, sector_top_n: int = 3,
              position: float = 1.0, use_regime: str = "on") -> list:
    codes = [str(c).strip() for c in (ctx.codes or []) if str(c).strip()]
    if not codes:
        return []
    watch_score = int(watch_score)
    focus_score = int(focus_score)
    top_n = max(1, int(top_n))
    rebalance = max(1, int(rebalance))
    min_continuous_days = max(1, int(min_continuous_days))
    sector_top_n = max(1, int(sector_top_n))
    pos = float(position)
    universe = set(codes)

    # 交易日历（用于把快照日对齐到交易日，避免信号被静默丢弃）
    bars = data.load_bars(codes, start=ctx.start, end=ctx.end,
                          fields=["close"], adjust=ctx.adjust)
    if not bars:
        return []
    all_days = sorted({str(d)[:10] for df in bars.values() for d in df.index})
    if not all_days:
        return []

    # 股票池成员（含 warmup 前移）；空则直接返回 []（注册冒烟安全）
    lo = None
    if ctx.start:
        try:
            lo = (dt.date.fromisoformat(str(ctx.start)[:10])
                  - dt.timedelta(days=30)).isoformat()
        except ValueError:
            lo = None
    pool = data.load_pool_members(start=lo, end=ctx.end)
    if not pool:
        return []

    snap_dates = sorted(pool.keys())

    # 预计算每只票在哪些快照下标出现，便于 O(streak) 回溯连续性
    present: dict[str, set[int]] = {c: set() for c in universe}
    for i, d in enumerate(snap_dates):
        for code in pool[d]:
            if code in present:
                present[code].add(i)

    def streak_at(code: str, i: int) -> int:
        if i not in present[code]:
            return 0
        n = 0
        while i in present[code]:
            n += 1
            i -= 1
        return n

    def trading_day(d: str) -> str | None:
        t = None
        for x in all_days:
            if x <= d:
                t = x
            else:
                break
        return t

    def in_range(d: str) -> bool:
        if ctx.start and d < str(ctx.start)[:10]:
            return False
        if ctx.end and d > str(ctx.end)[:10]:
            return False
        return True

    signals: list[Signal] = []
    held: set | None = None
    rb_counter = 0

    for i, d in enumerate(snap_dates):
        if not in_range(d):
            continue
        rb_counter += 1
        if (rb_counter - 1) % rebalance != 0:
            continue

        today = pool[d]
        prev = pool[snap_dates[i - 1]] if i > 0 else {}

        # 全池行业计数 + 前列行业（评分上下文用全池口径）
        sector: dict[str, int] = {}
        for row in today.values():
            ind = str(row.get("industry") or "未分类")
            sector[ind] = sector.get(ind, 0) + 1
        top_sectors = {ind for ind, _ in
                       sorted(sector.items(), key=lambda kv: (-kv[1], kv[0]))[:sector_top_n]}

        # 市场温度（risk_off 时全局空仓）
        regime = "neutral"
        if use_regime == "on":
            tlen, plen = len(today), len(prev)
            growth = (tlen - plen) / plen if plen else 0.0
            changes = [float(r["import_change"]) for r in today.values()
                       if r.get("import_change") is not None]
            avg_change = sum(changes) / len(changes) if changes else None
            positive = sum(1 for c in changes if c > 0)
            rs = 50.0
            rs += max(-15.0, min(15.0, growth * 100.0))
            if avg_change is not None:
                rs += max(-15.0, min(15.0, avg_change * 2.0))
            if changes:
                rs += ((positive / len(changes)) - 0.5) * 20.0
            rs = max(0.0, min(100.0, rs))
            regime = "risk_on" if rs >= 65.0 else ("risk_off" if rs <= 35.0 else "neutral")

        # 仅对「在池中且属于回测范围」的票评分
        info: dict[str, tuple[float, int, str, str]] = {}
        for code in (set(today.keys()) & universe):
            row = today[code]
            ind = str(row.get("industry") or "未分类")
            ind_count = sector.get(ind, 0)
            days = streak_at(code, i)
            is_new = code not in prev
            chg = row.get("import_change")
            chg = float(chg) if chg is not None else None

            score = 0.0
            score += min(days, 7) / 7.0 * 35.0
            score += min(ind_count, 8) / 8.0 * 20.0
            score += 10.0 if ind in top_sectors else 0.0
            score += 5.0 if is_new else 10.0
            if chg is not None:
                score += max(0.0, min(15.0, 7.5 + chg * 1.5))
            else:
                score += 4.0
            if days < min_continuous_days:
                score -= 10.0
            score = max(0.0, min(100.0, round(score)))
            label = ("重点关注" if score >= focus_score
                     else ("观察" if score >= watch_score else "暂不关注"))
            info[code] = (score, days, ind, label)

        picks = [c for c, v in sorted(info.items(),
                   key=lambda kv: (-kv[1][0], -kv[1][1], kv[0]))
                 if v[0] >= watch_score][:top_n]
        picks_set = set(picks)

        tday = trading_day(d)
        if tday is None:
            continue

        if use_regime == "on" and regime == "risk_off":
            if held:
                for code in sorted(held - picks_set):
                    signals.append(Signal(code=code, time=tday, action="sell",
                                          weight=0.0, reason="市场温度 risk_off（空仓）"))
            held = set()
            continue

        if held is None:
            for code in picks:
                sc, days, ind, label = info[code]
                signals.append(Signal(code=code, time=tday, action="buy", weight=pos,
                                      reason=f"RPS评分{sc}·{label}·连续{days}日·{ind}"))
            held = picks_set
        else:
            for code in sorted(picks_set - held):
                sc, days, ind, label = info[code]
                signals.append(Signal(code=code, time=tday, action="buy", weight=pos,
                                      reason=f"RPS评分{sc}·{label}·连续{days}日·{ind}"))
            for code in sorted(held - picks_set):
                signals.append(Signal(code=code, time=tday, action="sell",
                                      weight=0.0, reason="跌出 RPS 候选"))
            held = picks_set

    return signals
