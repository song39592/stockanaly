# -*- coding: utf-8 -*-
"""RPS 强势股评分策略（从全市场日线自算 RPS，与 RPS 页面同源）。

算法（0-100 综合评分 → 按评分决定目标组合）：
  对每一个交易日，先用**全市场日线**算出 RPS（max(RPS50,RPS120,RPS250)，与 RPS 页面
  完全同一套 `indicators.rps_data.market_rps`），取「RPS≥阈值」的票构成当日强势池；
  然后在**当日全池**内评分：
    · 连续在池天数   min(days,7)/7*35           最多 35
    · 行业入池数     min(indCount,8)/8*20        最多 20
    · 行业是否前列   top-N 行业 +10
    · 是否新入池     新 +5 / 老 +10
    · 当日涨幅       clamp(7.5+change*1.5,0,15)，缺则 +4
    · 惩罚           days < minContinuousDays 则 -10
  仅对「在强势池中且属于回测范围」的票评分；score≥观察阈值且排名前 top_n 的入选，
  跌出则移出。可选市场温度（risk_off）全局空仓。

【组合权重契约】本文件是「全量重发」的样板，改其它策略时照这个模式写：
  1. **自己维护 held 集合**（当前持有哪些票）。引擎把「调仓日未出现的 code」
     记为 0，所以少发一条 = 静默清仓 —— 漏发不是「少买一只」，是「全组合对倒」。
  2. **每个调仓日输出完整目标组合**，而不是只输出变化的那几条：
       入选的票 → weight = position / len(held)
       掉出的票 → **必须显式发 weight=0**
     第 2 条的「必须」有个坑：若某调仓日发出**零条**信号（空仓日），
     该日不会进入引擎的 rebalance_dates（= 全部信号日期的并集），
     于是「清仓」这件事根本没被登记，上一期的持仓会**继续持有**。
     所以清仓票也要发信号 —— weight=0 不影响 Σweight，却把这一天登记成调仓日。
  3. weight 是**占组合净值的比例**，同一事件日 Σweight ≤ 1（引擎会硬校验）。
     现金 = 1 − position，由 position 参数显式产生。

与旧版的区别：
  · 依赖手动导入的股票池快照（pool_members）已彻底移除，强势池从日线现算；
  · RPS 计算与 RPS 页面同一引擎，消除「页面一套、策略一套」的口径分歧；
  · 信号语义从「buy/sell + 带内仓位系数」改为「目标权重」，
    买卖由引擎对权重向量做差分派生（旧口径下 N=回测池股票数，
    全市场 5559 只时每只资金带仅 17.99 元、资金利用率 1.72%，metrics 全部失真）。

移植要点：
  · 取数经 data.load_strong_pool（策略包唯一取数入口），内部复用 indicators.rps_data；
    策略本体不直接读库 / 调网络。
  · 连续性（streak）依赖**全池成员时序**，故起点前多取 warmup_days 天养连续性
    （在 load_strong_pool 内完成，策略只拿到 [起点前 warmup, 结束] 的池序列）。
  · 无日线 / RPS 横截面不可用时，load_strong_pool 返回空 + error，策略返回 []
    （注册冒烟安全）；真正的「请先下载日线」提示由取数层 error 透出。

归因可见性：score / 连续天数 / 行业标签进 `reason`（人读）；
评分与排名等数值进 `meta`（机器读，供 IC / 分层分析）。
"""
from __future__ import annotations

from .core.base import strategy, ParamSpec, Signal, checkpoint
from .core import data


@strategy(
    id="rps_score",
    name="RPS 强势股评分",
    category="选股",
    description="从全市场日线自行计算 RPS（与 RPS 页面同源：取 max(RPS50,RPS120,RPS250)≥阈值的"
                "强势股为每日池），按「连续在池天数 / 行业集中度 / 涨幅 / 是否新入池」合成 "
                "0-100 评分，score≥观察阈值且排名前 N 的入选，跌出则移出；可选市场温度 "
                "(risk_off) 全局空仓。不再依赖手动导入的股票池快照。",
    inputs=["close"],
    outputs="目标权重向量(组合占比) + 评分因子(meta)",
    requires_pool=False,
    params=[
        ParamSpec("rps_threshold", "int", 90, min=50, max=100, label="RPS强势阈值(>=即强势)"),
        ParamSpec("watch_score", "int", 50, min=0, max=100, label="观察阈值(>=即候选)"),
        ParamSpec("focus_score", "int", 70, min=0, max=100, label="重点关注阈值"),
        ParamSpec("top_n", "int", 10, min=1, max=200, label="持仓数量上限"),
        ParamSpec("rebalance", "int", 1, min=1, max=60, label="调仓间隔(数据日)"),
        ParamSpec("min_continuous_days", "int", 3, min=1, max=30, label="连续在池最少天数"),
        ParamSpec("sector_top_n", "int", 3, min=1, max=20, label="行业前列N"),
        ParamSpec("position", "float", 1.0, min=0.1, max=1.0, label="总仓位系数"),
        ParamSpec("use_regime", "choice", "on", label="市场温度开关", choices=["on", "off"]),
    ],
)
def rps_score(ctx, rps_threshold: int = 90, watch_score: int = 50, focus_score: int = 70,
              top_n: int = 10, rebalance: int = 1, min_continuous_days: int = 3,
              sector_top_n: int = 3, position: float = 1.0, use_regime: str = "on") -> list:
    codes = [str(c).strip() for c in (ctx.codes or []) if str(c).strip()]
    if not codes:
        return []

    rps_threshold = max(50, min(100, int(rps_threshold)))
    watch_score = int(watch_score)
    focus_score = int(focus_score)
    top_n = max(1, int(top_n))
    rebalance = max(1, int(rebalance))
    min_continuous_days = max(1, int(min_continuous_days))
    sector_top_n = max(1, int(sector_top_n))
    pos = min(1.0, max(0.0, float(position)))
    universe = set(codes)

    # 从全市场日线自算强势池（含起点前 warmup 天养连续性）；无数据则空结果
    # （注册冒烟时被 registry stub 成空池 + error，直接早退）
    pool, change, err = data.load_strong_pool(
        ctx.start, ctx.end, threshold=rps_threshold,
        periods=(50, 120, 250), warmup_days=30)
    if err or not pool:
        return []

    dates = sorted(pool.keys())          # 面板交易日（含 warmup 前缀）

    # 行业（本地 board_index 单条 IN 查询；缺失则留空，不影响评分契约）
    all_codes = sorted({c for row in pool.values() for c in row})
    try:
        from features.stock import board_index
        ind_map = board_index.industries_of(all_codes) or {}
    except Exception:                      # noqa: BLE001 - 行业缺失可降级
        ind_map = {}

    # 预计算每只票在哪些下标出现，便于 O(streak) 回溯连续性
    present: dict[str, set[int]] = {c: set() for c in universe}
    for i, d in enumerate(dates):
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

    def in_range(d: str) -> bool:
        if ctx.start and d < str(ctx.start)[:10]:
            return False
        if ctx.end and d > str(ctx.end)[:10]:
            return False
        return True

    signals: list[Signal] = []
    held: set[str] = set()          # ★ 组合权重契约：自己维护持仓集合
    rb_counter = 0

    for i, d in enumerate(dates):
        if not in_range(d):
            continue
        rb_counter += 1
        if (rb_counter - 1) % rebalance != 0:
            continue

        # 逐交易日检查点：RPS 要对全池逐日算横截面（3 年约 725 次），
        # 每次都遍历强势池。不查这个点，这段就停不下来。
        checkpoint()
        today = pool[d]
        prev = pool[dates[i - 1]] if i > 0 else {}

        # 全池行业计数 + 前列行业（评分上下文用全池口径）
        sector: dict[str, int] = {}
        for code in today:
            ind = str(ind_map.get(code) or "未分类")
            sector[ind] = sector.get(ind, 0) + 1
        top_sectors = {ind for ind, _ in
                       sorted(sector.items(), key=lambda kv: (-kv[1], kv[0]))[:sector_top_n]}

        # 市场温度（risk_off 时全局空仓）
        regime = "neutral"
        if use_regime == "on":
            tlen, plen = len(today), len(prev)
            growth = (tlen - plen) / plen if plen else 0.0
            chg_today = change.get(d, {})
            changes = [v for v in (chg_today.get(c) for c in today) if v is not None]
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

        # 仅对「在强势池中且属于回测范围」的票评分
        info: dict[str, tuple[float, int, str, str]] = {}
        for code in (set(today.keys()) & universe):
            ind = str(ind_map.get(code) or "未分类")
            ind_count = sector.get(ind, 0)
            days = streak_at(code, i)
            is_new = code not in prev
            chg = change.get(d, {}).get(code)
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

        ranked = [c for c, v in sorted(info.items(),
                   key=lambda kv: (-kv[1][0], -kv[1][1], kv[0]))
                 if v[0] >= watch_score][:top_n]

        # ---- 组装本期**完整目标组合**（risk_off 时为空）----
        if use_regime == "on" and regime == "risk_off":
            target: list[str] = []
            drop_reason = "市场温度 risk_off（空仓）"
        else:
            target = ranked
            drop_reason = "跌出 RPS 候选"

        target_set = set(target)
        dropped = held - target_set

        # 掉出的票：**显式发 weight=0**。
        # 不发的话这一天不会被登记为调仓日（rebalance_dates = 信号日期并集），
        # 于是「清仓」没发生，上一期持仓会继续持有 —— 策略想空仓却没空掉。
        for code in sorted(dropped):
            signals.append(Signal(code=code, time=d, weight=0.0, reason=drop_reason,
                                  meta={"dropped": 1}))

        # 入选的票：等权分配总仓位 Σw = position，现金 = 1 − position
        w = (pos / len(target_set)) if target_set else 0.0
        for code in sorted(target_set):
            sc, days, ind, label = info[code]
            signals.append(Signal(
                code=code, time=d, weight=w,
                reason=f"RPS评分{sc}·{label}·连续{days}日·{ind}",
                meta={"score": sc, "streak": days, "rank": ranked.index(code) + 1,
                      "industry": ind, "regime": regime},
            ))

        held = target_set

    return signals