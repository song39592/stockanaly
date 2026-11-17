# -*- coding: utf-8 -*-
"""涨跌停口径 —— **全项目唯一实现**（统计层的可行性判定 + 策略层的炸板过滤共用）。

## 为什么放 core/

「今天这只票能不能按涨停价买到」这件事有**两处**要用：

  * **统计层**（`strategies/core/stats.py`）：严格成交模型的一字板判定
    （一字涨停买不进即空仓）、逐笔可行性判定（整手 / 涨跌停 / 停牌）；
  * **策略层**（`strategies/jwzz_cross.py` / `jwzz_hold.py`）：「涨停开盘但盘中炸板
    → 视为隔夜单未成交 → 这一笔不买」的过滤。

两边各写一份 → 同一口径两个数、**两边都不报错**（最难查的一类 bug）。
本模块是**市场规则常量 + 纯算术**，不属于任何业务语义 → 放依赖图底部最合适，
与 `core/bars_schema.py` 同一性质（跨包共用的口径收在一处）。

## 口径要点（逐条写死，避免歧义）

  * **涨跌停幅度按板块**：主板 10%、双创（30/301/688/689 开头）20%。
    ⚠️ 已知不精确（与 stats 侧历史口径完全一致，改这里 = 改那里）：
    ST 自 2025-04 起由 5% 改为 10%（现行口径一律按 10% 处理，跨 2025-04 的区间早期会偏松）、
    北交所 30% 未覆盖、本地行情无 ST 标识。
  * **涨停价 / 跌停价** = `round(昨收 × (1 ± 幅度), 2)`（交易所四舍五入到分）。
    按**价比对**比按「收盘涨跌幅 ≥ 幅度」准：昨收 10.00 的涨停价是 11.00，
    而 9.8% 的涨幅阈值对应 10.98，中间那 0.02 的缝隙会把
    「最低价 10.99（盘中开过板、买得到）」的票误判成一字板。
  * **一字涨停（买不进）** = 全天最低价 ≥ 涨停价（没有任何低于涨停价的成交机会）。
  * **一字跌停（卖不出）** = 全天最高价 ≤ 跌停价。
    ⚠️ **买卖方向各判各的**：一字跌停时买入是能成交的（板上是卖单），
    一字涨停时卖出是能成交的（板上是买单）。
  * **涨停开盘但炸板**（本模块新增，策略层用）：**开盘价 ≥ 涨停价**（涨停开盘）
    且**盘中最低价 < 开盘价**（跌破过开盘价 == 跌破过涨停价）—— 即「突破失败」。
    策略层据此实现「隔夜单开盘没成交就撤单 → 这只票不买」。
    ⚠️ **一字板不算炸板**：开盘 = 最低 = 涨停价意味着根本没开板，它**买不进**，
    由引擎的严格成交模型处理（建仓失败 → 该份额度当日为现金），**不重复拦截**。

## 为什么炸板判定带一个相对容差

交易所把涨停价四舍五入到**分**，而策略层拿到的是**复权序列**（hfq）——
复权后无法再复刻「到分」这一步（同一比例在不同价位上的分位误差不同）。
故「涨停开盘」这一半用**相对容差**吸收取整误差，「炸板」这一半**不含容差、
也不依赖涨停价**：

    涨停开盘：open ≥ 涨停价 × (1 − tol)      （tol = 0.2%）
    盘中炸板：low  < open × (1 − 1e-9)        （跌破自己的开盘价）

涨停开盘时开盘价就是涨停价，故「盘中跌破开盘价」==「盘中跌破涨停价」——
这一半因此既精确，又天然把**一字板排除在外**（一字板 low == open → 不算炸板），
也不会因涨停价取整方向不同而误判（涨停价取整向下时，拿涨停价比对会把
「全天封死」的票误判成炸板）。

`tol = 0.2%` 的含义：只有「开盘价距涨停价 0.2% 以内」的边界票会受影响
（低价股更敏感：raw 价 2.5 元以下时，交易所的分位取整误差可能超过 0.2%）。

⚠️ 复权不变性：同一天内 `open / 昨收` 的比例关系不受复权因子影响
（除权除息日 hfq 的「昨收」正好等于交易所的除权参考价 → 判定依然成立），
所以用 hfq 判定涨停开盘与交易所规则**口径一致**，不必为策略层再取一份未复权行情。

## ⚠️ 已知分歧：本模块**不含**北交所 30%

`features/stock/profile.py` 的「涨停连板统计」自带一份 `limit_pct`，
**含北交所（43/83/87/88/92 → 30%）**，本模块按主板 10% 处理 → 两份实现有差异。

  * 影响面：默认回测范围**已剔除北交所**（`universe.CATEGORIES` 含 `bse`）→
    回测**不受影响**；个股档案的涨停连板统计也不受影响（它用的是自己那份）。
  * 只在「手工把北交所票放进回测池」时才有差：那时一字板判定会按 10% 算（偏松）。
  * 统一需要单独决策（补北交所进本模块 = 改统计层对北交所的行为；
    或改 profile.py 用本模块 = 丢它的北交所 30%）→ 不在「跳过炸板」这件事的范围内，
    故本次**保持现状、只把差异写明**。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# 涨跌停阈值：主板 10%、双创 20%（见模块 docstring 的已知不精确项）
BOARD_LIMIT = 0.20        # 创业板(30/301)/科创板(688/689)
MAIN_LIMIT = 0.10         # 主板（ST 自 2025-04 起也是 10%）

# 炸板判定的相对容差（见模块 docstring）
BROKEN_TOL = 0.002        # 0.2%

# ---- 「涨停开盘应对」三档（全局生效；回测范围里切换）----
#   keep   不撤单：涨停开盘照常建仓（现状）
#   cancel 撤单：涨停开盘当日不建仓（该份额度当日为现金、不重分配）
#   reseal 涨停条件单：炸板票只有「收盘仍封住涨停价」才进，否则撤单
POLICY_KEEP = "keep"
POLICY_CANCEL = "cancel"
POLICY_RESEAL = "reseal"
POLICIES = (POLICY_KEEP, POLICY_CANCEL, POLICY_RESEAL)
POLICY_LABELS = {
    POLICY_KEEP: "不撤单（照常买入）",
    POLICY_CANCEL: "撤单（涨停开盘不买）",
    POLICY_RESEAL: "涨停条件单（炸板后回封才进）",
}

# limit_open_action 的结论
ACT_NA = "na"            # 当日不涉及涨停开盘 → 与本条件无关
ACT_BUY = "buy"          # 可以建仓
ACT_CANCEL = "cancel"    # 撤单 → 当日不建仓


def limit_pct(code: str) -> float:
    """该代码所属板块的涨跌停阈值（**现行口径**：主板 10%、双创 20%）。

    ⚠️ 已知不精确：ST 自 2025-04 起由 5% 改为 10%（现行口径一律按 10% 处理，
    跨 2025-04 的区间早期会偏松）、北交所 30% 未覆盖、本地行情无 ST 标识。
    """
    c = str(code)
    if c.startswith(("300", "301", "688", "689")):
        return BOARD_LIMIT
    return MAIN_LIMIT


def limit_prices(prev_close: float, thr: float) -> tuple[float, float]:
    """按昨收算涨停价 / 跌停价（四舍五入到分，与交易所口径一致）。

    交易所的涨跌停价 = round(昨收 × (1 ± 幅度), 2)。直接用「收盘涨跌幅 ≥ 幅度」
    判定不准：昨收 10.00 的涨停价是 11.00，而 9.8% 的涨幅阈值对应 10.98，
    中间那 0.02 的缝隙会把「最低价 10.99（盘中开过板、买得到）」的票误判成一字板。
    按**价比对**更贴实盘。
    """
    if prev_close <= 0:
        return (0.0, 0.0)
    return (round(prev_close * (1.0 + thr), 2), round(prev_close * (1.0 - thr), 2))


def is_limit_up(hi: float, lo: float, prev_close: float, thr: float) -> bool:
    """当天是否**一字涨停**（买不进）：`low >= 涨停价`。

    含义是「全天最低价都在涨停价」—— 没有任何低于涨停价的成交机会，
    挂涨停价单只能排队、买不到。

    ⚠️ 旧写法是「high == low 且 |收盘涨跌幅| ≥ 幅度」，买卖两侧共用**绝对值**涨跌幅，
    造成「一字跌停的买入被标成涨停」（跌停板上买单是能成交的，买得到）。
    """
    if prev_close <= 0:
        return False
    up, _ = limit_prices(prev_close, thr)
    return up > 0 and lo >= up


def is_limit_down(hi: float, lo: float, prev_close: float, thr: float) -> bool:
    """当天是否**一字跌停**（卖不出）：`high <= 跌停价`。

    含义是「全天最高价都在跌停价」—— 没有任何高于跌停价的成交机会。
    与 is_limit_up 对称：卖出侧只看跌停，涨停时卖出是完全能成交的。
    """
    if prev_close <= 0:
        return False
    _, dn = limit_prices(prev_close, thr)
    return dn > 0 and hi <= dn


def limit_up_open_broke(open_: float, low: float, prev_close: float,
                         thr: float, tol: float = BROKEN_TOL) -> bool:
    """当天是否**涨停开盘但盘中炸板**（突破失败）→ 策略层据此放弃这一笔买入。

    判定：
      * 涨停开盘：`open ≥ 涨停价 × (1 − tol)`（tol 吸收交易所涨停价取整到分的误差）
      * 盘中炸板：`low < open × (1 − 1e-9)`（盘中跌破过开盘价 == 跌破过涨停价）

    ⚠️ 一字板（开盘 = 最低 = 涨停价）**不算炸板** → 返回 False：
    它不是「没成交」，而是**根本买不进**，由引擎的严格成交模型处理，
    两处不重复拦截（否则会把「买不进」记成「策略放弃」，归因错位）。
    """
    if prev_close <= 0:
        return False
    up, _ = limit_prices(prev_close, thr)
    if up <= 0:
        return False
    if not (open_ >= up * (1.0 - tol)):
        return False               # 没涨停开盘
    return bool(low < open_ * (1.0 - 1e-9))


def limit_open_action(open_: float, high: float, low: float, close: float,
                      prev_close: float, thr: float,
                      policy: str = POLICY_KEEP,
                      tol: float = BROKEN_TOL) -> str:
    """「涨停开盘」当日的应对结论：`ACT_NA` / `ACT_BUY` / `ACT_CANCEL`。

    判定（raw 价，昨收 = 该票自己上一根 K 线的收盘）：
      * 涨停开盘：`open ≥ 涨停价 × (1 − tol)`
      * 炸板：`low < 涨停价`（盘中打开过涨停价）
      * 收盘仍封住：`close ≥ 涨停价`（涨停价取整到分，容差半分钱）

    三档（policy）：
      * `keep`（不撤单）：涨停开盘 → `ACT_BUY`（照常建仓；一字板仍由引擎的物理拦截处理）
      * `cancel`（撤单）：涨停开盘 → `ACT_CANCEL`（当日不建仓）
      * `reseal`（涨停条件单，一次回封就进）：炸板票只有**收盘仍封住涨停价**才 `ACT_BUY`，
        否则 `ACT_CANCEL`；一字板（没炸板）→ `ACT_BUY`（引擎物理拦截买不进）

    ⚠️ **日线口径的限制**：`涨停开盘` 的票 `high` **恒等于涨停价**（开盘价就是当日
    最高价之一）→ 日线**分不出**「炸板后回封过」与「炸板后再没回去」，更分不出
    「盘中第一次回封」的时刻（本项目无分钟级数据）。
    故 `reseal` 取**日线下唯一可判的近似**：收盘仍封住涨停价。
    此时引擎的**收盘成交价 = 涨停价**，与实盘「回封瞬间成交」的价格**一致**；
    代价是实盘里「回封过又打开、收盘没封住」的票会撤单（方向偏保守，已知近似误差）。

    ⚠️ 一字板（开盘 = 最低 = 涨停价）在本函数里返回 `ACT_BUY`：
    它是**物理买不进**（板上全是买单），由引擎的严格成交模型拦截，
    不该被记成「策略主动撤单」（归因要分开）。
    """
    if prev_close <= 0:
        return ACT_NA
    up, _ = limit_prices(prev_close, thr)
    if up <= 0:
        return ACT_NA
    if not (open_ >= up * (1.0 - tol)):
        return ACT_NA                      # 没涨停开盘 → 与本条件无关
    # ⚠️ 一字板优先于 policy 分支：全天封死 → 一律 ACT_BUY，
    #   由引擎的严格成交模型按**物理买不进**拦截（归因归 buy_blocked）。
    #   若让 cancel 档先返回 ACT_CANCEL，就会把「板上全是买单、根本买不到」
    #   记成「策略主动放弃」—— 两件事不同，报告口径不能混。
    if low >= up * (1.0 - 1e-9):
        return ACT_BUY                     # 全天封死（一字板）→ 引擎物理拦截
    if policy == POLICY_CANCEL:
        return ACT_CANCEL
    if policy == POLICY_KEEP:
        return ACT_BUY
    # reseal：炸板 → 只有收盘仍封住涨停价才进（= 日线下「回封」的可判近似）
    if close >= up - 0.005:
        return ACT_BUY
    return ACT_CANCEL


def broken_limit_up_panel(bars: dict[str, pd.DataFrame], codes: list[str],
                           days: list[str], tol: float = BROKEN_TOL) -> np.ndarray:
    """把 `{code: OHLC}` 算成「统一交易日轴」上的**炸板矩阵**（C×T bool）。

    与 `formulas/jwzz.py` 的 `jwzz_panel` 同构：行 = `codes` 的顺序，列 = `days`；
    某票停牌 / 无行情的那天为 False（**不沿用**上一个状态 —— 炸板是当日事件，
    没有行情就没有「当日炸板」这回事）。

    参数：
        bars   {code: DataFrame}，需含 open / low / close（缺列 → 该票整行 False）
        codes  行顺序（通常是策略面板的 `cols`）
        days   统一交易日轴（升序）

    返回：np.ndarray(bool, shape=(len(codes), len(days)))
    """
    out = np.zeros((len(codes), len(days)), dtype=bool)
    if not codes or not days:
        return out
    day_pos = {str(d)[:10]: i for i, d in enumerate(days)}
    for r, code in enumerate(codes):
        df = (bars or {}).get(code)
        if df is None or getattr(df, "empty", True):
            continue
        if any(c not in df.columns for c in ("open", "low", "close")):
            continue
        o = pd.to_numeric(df["open"], errors="coerce").to_numpy(dtype=float)
        lo = pd.to_numeric(df["low"], errors="coerce").to_numpy(dtype=float)
        cl = pd.to_numeric(df["close"], errors="coerce").to_numpy(dtype=float)
        if len(cl) < 2:
            continue
        # 昨收 = 该票**自己上一根 K 线**的收盘（停牌跨天时交易所也用最后一个收盘价）
        prev = np.concatenate(([np.nan], cl[:-1]))
        thr = limit_pct(code)
        up = prev * (1.0 + thr)
        ok = np.isfinite(up) & (up > 0) & np.isfinite(o) & np.isfinite(lo)
        opened = ok & (o >= up * (1.0 - tol))
        # 炸板 = 盘中跌破**自己的开盘价**（涨停开盘时开盘价就是涨停价）——
        # 不用涨停价比对：涨停价取整向下时会把「全天封死」的一字板误判成炸板。
        broke = ok & (lo < o * (1.0 - 1e-9))
        flags = opened & broke
        idx = np.fromiter((day_pos.get(str(d)[:10], -1) for d in df.index),
                          dtype=np.int64, count=len(df))
        sel = idx >= 0
        if sel.any():
            out[r, idx[sel]] = flags[sel]
    return out


__all__ = [
    "BOARD_LIMIT", "MAIN_LIMIT", "BROKEN_TOL",
    "POLICY_KEEP", "POLICY_CANCEL", "POLICY_RESEAL", "POLICIES", "POLICY_LABELS",
    "ACT_NA", "ACT_BUY", "ACT_CANCEL",
    "limit_pct", "limit_prices", "is_limit_up", "is_limit_down",
    "limit_up_open_broke", "limit_open_action", "broken_limit_up_panel",
]
