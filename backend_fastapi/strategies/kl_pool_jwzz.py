# -*- coding: utf-8 -*-
"""策略：**KL 入池 · JWZZ 上穿买入 / 下穿卖出**。

## 规则（用户口径，逐条对应实现）

1. **入池**：筹码博弈 `KL` **上穿**阈值（默认 80）→ 该票入池，并**记录入池价**
   （入池当天的收盘价）。
2. **出池**：价格 > 入池价 × `exit_ratio`（默认 1.5 = 150%）→ 出池。
   ⚠️ **出池只禁买、不强卖**：已持仓的仍按第 3 条（JWZZ 下穿）卖出。
3. **买卖**：**池内**的票，JWZZ **上穿**阈值（默认 95‰）→ 买入；
   JWZZ **下穿**同一阈值 → 卖出（清仓）。
4. **重新入池**：出池后，若 KL **再次上穿**阈值 → 重新入池，**以当时价格重记入池价**
   （150% 线随之上移，相当于新一轮）。

## 判定细节（口径钉死在这里 —— 改规则先改这段）

**入池 / 出池**

* 入池判定 = KL **上穿**（由 ≤阈值 升到 >阈值）；**首个有效值也算上穿**
  （与「JWZZ 上穿/下穿」策略同口径，否则强势标的凭空晚一步）。
* **值一直 > 阈值不重复入池**：已在池里就跳过（入池价只在「入池」那一刻记一次）。
* 出池判定 = **收盘价** > 入池价 × `exit_ratio`（用收盘价，与引擎「权重变化按当日
  收盘价成交」的口径一致）。
  ⚠️ **停牌日没有收盘价 → 不判定出池**（收盘价不前向填充，不拿旧价当今天的价格）。
* 出池 = **只禁买、不强卖**：出池当天**不发任何卖单**（持仓不动，等 JWZZ 下穿）。
* 重新入池 = KL **再次上穿** → 必须先跌回阈值以下再冲上去；
  入池价按**当天收盘价重记**（150% 线随之上移）。

**买入 / 卖出**

* **入池 ≠ 买入**：还得 JWZZ 上穿 95‰ 才建仓。
* **入池当天即可买**：同一天里先判入池、再判买入（入池价即当天收盘价）。
* JWZZ 下穿 = 当日 ≤ 阈值 且**前一日** > 阈值（首个有效值**不算**下穿）。
* 已持仓的票再次上穿 → **不重复建仓、权重不变**（它已经在场）。
* 停牌 / 无值日：KL 与 JWZZ 沿用上次有效值（状态不丢），但**不产生新的穿越事件**。

## 为什么「入池/出池」用**上穿事件**而不是「值大于阈值」

若按「值 > 80 就入池」，一只票连续多天 KL > 80 时会在出池当天**立刻自动重新入池**
（因为它的值仍 > 80）→ 出池形同虚设，150% 线被不断重记、永远追不上。
用**上穿事件**（由 ≤80 升到 >80）后，重新入池必须等它先跌回 80 以下再冲上去，
这才是「一轮」的语义，也和「出池后可以重新入池、价格重记」自洽。

## 权重口径（组合权重契约）

* 每次**新建仓**给固定权重 `buy_weight`（默认 10% = 占组合净值 10%），
  持有期间**不再改动**（避免"等权再分配"造成的无谓买卖与成本）。
* 同一日多只票上穿 → **全部买**（不设 top_n）；若可用仓位不够，则**等比例压缩**
  当日新买入各票的权重（而不是跳过几只）—— 事件驱动的信号只有一次，
  跳过就永远错过。
* 仓位**已满**（可用仓位 ≤ 0）→ **不再新建仓**，留现金，已持仓的不动。
* 总仓位上限 `position_cap`（默认 1.0）：**Σweight ≤ 1** 是引擎硬约束
  （超了直接抛 ValueError，不做静默归一化）。
* 卖出时发 **weight=0**（不发则该日不登记为调仓日、清仓不会发生）；
  每个调仓日输出**完整目标组合**（引擎把未出现的 code 记 0）。

## 引擎告警说明（别误判成 bug）

本策略常年**单票持仓**（KL>80 极罕见）→ 卖出时引擎会报
「持仓数从 1 骤降到 0（>50%）」。这是**刻意清仓**，不是漏发信号。

## 计算层复用（口径唯一来源）

* `KL` → `formulas/kl.py`（与指标「筹码博弈 KL」**同一份**计算，不另写一遍）
* `JWZZ` → `formulas/jwzz.py`（与策略「JWZZ 上穿/下穿」**同一份**）
* `KL = 100 × (获利盘(收盘) − 获利盘(开盘))`，量程 **±100**（阈值 ±80 = 接近极端）。
  实测：150 只票 × 2 年 = 72640 个交易日里 `KL>80` 只出现 **3 天** ——
  这是**极端事件**触发器，一年可能只有几次信号。

## 复权口径

筹码矩阵按 **qfq** 算（与指标 KL 一致），价格也取 **qfq** —— 筹码矩阵与价格
**必须同口径**，错位不报错、只会让数字看起来合理而已。
JWZZ 是比值（close 与成本线同因子缩放），用哪个复权口径结果相同。

## 性能（重要）

筹码矩阵约 0.25 s/只（`scr90` 实测），本策略每只票还要逐日算两次 WINNER →
**只能用「股票池」跑，不要挂全市场**（5599 只 ≈ 25 分钟起）。
"""
from __future__ import annotations

import datetime as dt

import numpy as np

import chipformulas as chip_formulas
from formulas.jwzz import DEFAULT_PERIOD, jwzz_series
from formulas.kl import kl_series

from .core import data
from .core.base import ParamSpec, Signal, checkpoint, strategy

# 筹码矩阵的预热天数：筹码分布要靠一段历史形成，起步就用「一天」会退化成尖刺
KL_WARMUP_DAYS = 250


@strategy(
    id="kl_pool_jwzz",
    name="KL入池 · JWZZ买卖",
    category="筹码",
    description="筹码博弈 KL 上穿阈值入池并记录入池价；池内票 JWZZ 上穿阈值买入、"
                "下穿阈值卖出；价格超过入池价 1.5 倍出池（只禁买，不强卖）；"
                "出池后 KL 再次上穿可重新入池并重记入池价。",
    inputs=["open", "close", "volume", "amount"],
    outputs="目标权重向量(组合占比) + 因子值(meta)",
    requires_pool=False,
    params=[
        ParamSpec("kl_threshold", "float", 80.0, min=-100.0, max=100.0,
                  label="入池阈值KL(上穿才入池)"),
        ParamSpec("exit_ratio", "float", 1.5, min=1.0, max=10.0,
                  label="出池倍数(入池价×)"),
        ParamSpec("jwzz_threshold", "float", 95.0, min=-500.0, max=2000.0,
                  label="JWZZ触发阈值(‰)"),
        ParamSpec("cost_period", "int", DEFAULT_PERIOD, min=2, max=250,
                  label="成本EMA周期"),
        ParamSpec("buy_weight", "float", 0.1, min=0.01, max=1.0,
                  label="单只建仓权重"),
        ParamSpec("position_cap", "float", 1.0, min=0.1, max=1.0,
                  label="总仓位上限"),
        ParamSpec("formula", "choice", "tri_decay", label="筹码公式"),
        ParamSpec("bins", "int", 80, min=20, max=200, label="价格分箱数"),
    ],
)
def kl_pool_jwzz(ctx, kl_threshold: float = 80.0, exit_ratio: float = 1.5,
                 jwzz_threshold: float = 95.0, cost_period: int = DEFAULT_PERIOD,
                 buy_weight: float = 0.1, position_cap: float = 1.0,
                 formula: str = "tri_decay", bins: int = 80) -> list:
    """返回完整目标组合的 list[Signal]（逐日推演见 `_simulate`）。"""
    codes = [str(c).strip() for c in (ctx.codes or []) if str(c).strip()]
    if not codes:
        return []
    kl_thr = float(kl_threshold)
    ex_ratio = max(1.0, float(exit_ratio))
    jw_thr = float(jwzz_threshold)
    period = max(2, int(cost_period))
    bw = min(1.0, max(0.0, float(buy_weight)))
    cap = min(1.0, max(0.0, float(position_cap)))
    nbin = max(20, int(bins))

    # ---- 取数：价格与筹码矩阵**同复权口径**（qfq），错位不报错但数字会失真 ----
    bars = data.load_bars(codes, ctx.start, ctx.end,
                          fields=["open", "close", "volume", "amount"],
                          adjust="qfq")
    end = str(ctx.end)[:10] if ctx.end else None
    win_start = str(ctx.start)[:10] if ctx.start else None
    fetch_start = win_start
    if win_start:
        try:
            fetch_start = (dt.date.fromisoformat(win_start)
                           - dt.timedelta(days=KL_WARMUP_DAYS)).isoformat()
        except ValueError:
            fetch_start = win_start

    # ---- 统一交易日轴（组合决策必须在同一天上做）----
    seen: set[str] = set()
    for df in bars.values():
        for d in df.index:
            seen.add(str(d)[:10])
    if not seen:
        return []
    day_index = sorted(seen)
    day_pos = {d: i for i, d in enumerate(day_index)}
    T = len(day_index)

    per_code: dict[str, dict] = {}
    for code in codes:
        df = bars.get(code)
        if df is None or len(df) < period + 2:
            continue
        # 逐标的检查点：本策略要逐票算筹码矩阵（最慢的一类策略），
        # 必须在这里查暂停/停止，否则「停止」按钮在这段完全无效。
        checkpoint()
        try:
            res = chip_formulas.compute_matrix(
                code, formula_id=formula, start=fetch_start, end=end,
                adjust="qfq", bins=nbin, offline=True)
        except Exception:                    # noqa: BLE001 - 单只失败不拖垮整批
            continue
        if res is None or not len(res.dates):
            continue

        idx_bar = {str(d)[:10]: i for i, d in enumerate(df.index)}
        opens = df["open"].astype(float).to_numpy()
        closes = df["close"].astype(float).to_numpy()

        # KL：筹码矩阵与开盘/收盘价**同口径**（qfq）才算得出来
        m_open = np.full(len(res.dates), np.nan)
        for i, day in enumerate(res.dates):
            j = idx_bar.get(str(day)[:10])
            if j is not None:
                m_open[i] = opens[j]
        kl_vals = kl_series(res.pct, res.centers, m_open, res.close)

        # JWZZ：与「JWZZ 上穿/下穿」策略同源（formulas/jwzz.py）
        jwzz_vals = jwzz_series(df, period).to_numpy(dtype="float64")

        # 映射到统一轴（预热段只养状态，不参与决策）
        row_kl = np.full(T, np.nan, dtype="float64")
        row_jw = np.full(T, np.nan, dtype="float64")
        row_close = np.full(T, np.nan, dtype="float64")
        idx = np.fromiter((day_pos.get(str(d)[:10], -1) for d in df.index),
                          dtype=np.int64, count=len(df.index))
        ok = idx >= 0
        row_close[idx[ok]] = closes[ok]
        for i, day in enumerate(res.dates):
            t = day_pos.get(str(day)[:10])
            if t is None:
                continue
            if win_start is not None and str(day)[:10] < win_start:
                continue                      # 预热段不参与决策
            row_kl[t] = kl_vals[i]
        jw_ok = np.isfinite(jwzz_vals)
        both = jw_ok & ok
        row_jw[idx[both]] = jwzz_vals[both]

        if not np.isfinite(row_kl).any() and not np.isfinite(row_jw).any():
            continue
        per_code[code] = {
            "kl": _ffill(row_kl),
            "jwzz": _ffill(row_jw),
            "close": row_close,          # 收盘价**不**前向填充：停牌日没有价格
        }

    if not per_code:
        return []

    return _simulate(day_index, per_code, kl_thr=kl_thr, exit_ratio=ex_ratio,
                     jwzz_threshold=jw_thr, buy_weight=bw, position_cap=cap)


def _ffill(arr: np.ndarray) -> np.ndarray:
    """前向填充 NaN（停牌日沿用上次有效值；首个有效值之前保持 NaN）。"""
    out = np.asarray(arr, dtype="float64").copy()
    valid = np.isfinite(out)
    if not valid.any():
        return out
    pos = np.where(valid, np.arange(out.size), 0)
    np.maximum.accumulate(pos, out=pos)
    filled = out[pos]
    first = int(np.argmax(valid))
    out[:first] = np.nan
    out[first:] = filled[first:]
    return out


def _cross_up(arr: np.ndarray, thr: float) -> np.ndarray:
    """上穿：当前 > 阈值 且（前值 ≤ 阈值 或 前值不可用=首个有效值）。

    首个有效值也算上穿 —— 与现有「JWZZ 上穿/下穿」策略同口径
    （否则强势新股要等到下一次真穿越才建仓，凭空晚一步）。
    """
    arr = np.asarray(arr, dtype="float64")
    prev = np.empty_like(arr)
    prev[1:] = arr[:-1]
    prev[0] = np.nan
    return (arr > thr) & ((prev <= thr) | ~np.isfinite(prev))


def _cross_down(arr: np.ndarray, thr: float) -> np.ndarray:
    """下穿：当前 ≤ 阈值 且 前值 > 阈值。"""
    arr = np.asarray(arr, dtype="float64")
    prev = np.empty_like(arr)
    prev[1:] = arr[:-1]
    prev[0] = np.nan
    return (arr <= thr) & (prev > thr)


def _round(value, nd: int = 4):
    """meta 用：NaN / inf → None（引擎会丢弃 NaN 字段并计 WARNING）。"""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(f):
        return None
    return round(f, nd)


def _simulate(day_index: list[str], per_code: dict[str, dict],
              kl_thr: float, exit_ratio: float, jwzz_threshold: float,
              buy_weight: float, position_cap: float) -> list[Signal]:
    """逐日推演：池子状态 + 目标权重 → 完整目标组合的信号列表。

    `per_code[code]` = {"kl": arr, "jwzz": arr, "close": arr}，
    三个数组同长（= len(day_index)），NaN 表示当日无值。
    """
    pool: dict[str, float] = {}          # code → 入池价
    held: dict[str, float] = {}          # code → 目标权重
    signals: list[Signal] = []

    # 事件表：day → [code]，避免逐日扫全池（全市场下那是 T×C 次 Python 循环）
    kl_up_by_day: dict[str, list[str]] = {}
    jw_up_by_day: dict[str, list[str]] = {}
    jw_dn_by_day: dict[str, list[str]] = {}
    for code, s in per_code.items():
        for day, ok in zip(day_index, _cross_up(s["kl"], kl_thr)):
            if ok:
                kl_up_by_day.setdefault(day, []).append(code)
        for day, ok in zip(day_index, _cross_up(s["jwzz"], jwzz_threshold)):
            if ok:
                jw_up_by_day.setdefault(day, []).append(code)
        for day, ok in zip(day_index, _cross_down(s["jwzz"], jwzz_threshold)):
            if ok:
                jw_dn_by_day.setdefault(day, []).append(code)

    for ti, day in enumerate(day_index):
        bought: list[str] = []
        sold: list[str] = []

        # ① 出池：价格 > 入池价 × exit_ratio（**只禁买，不强卖**）
        for code in list(pool.keys()):
            px = per_code[code]["close"][ti]
            entry = pool[code]
            if np.isfinite(px) and np.isfinite(entry) and entry > 0 \
                    and px > entry * exit_ratio:
                del pool[code]

        # ② 入池：KL 上穿 → 以当天收盘价重记入池价
        for code in kl_up_by_day.get(day, []):
            if code in pool:
                continue                     # 已在池里（重复上穿不算新一轮）
            px = per_code[code]["close"][ti]
            if not np.isfinite(px) or px <= 0:
                continue
            pool[code] = float(px)

        # ③ 买入：池内 + JWZZ 上穿（已持仓的再上穿 → 权重不变，无需动作）
        cands = [c for c in jw_up_by_day.get(day, [])
                 if c in pool and c not in held]
        if cands:
            used = sum(held.values())
            avail = position_cap - used
            if avail > 1e-9:
                w_each = min(buy_weight, avail / len(cands))
                for code in sorted(cands):
                    held[code] = w_each
                    bought.append(code)

        # ④ 卖出：持有 + JWZZ 下穿
        for code in list(held.keys()):
            if code in jw_dn_by_day.get(day, []):
                del held[code]
                sold.append(code)

        # ⑤ 有变化才发信号，且**发完整目标组合**（引擎把未出现的 code 记 0）
        if not bought and not sold:
            continue
        for code in sorted(sold):
            signals.append(Signal(
                code=code, time=day, weight=0.0, reason="JWZZ 下穿阈值",
                meta={"jwzz": _round(per_code[code]["jwzz"][ti]),
                      "entry_price": _round(pool.get(code))}))
        for code, w in sorted(held.items()):
            signals.append(Signal(
                code=code, time=day, weight=w,
                reason="池内 JWZZ 上穿阈值" if code in bought else "KL池持有",
                meta={"kl": _round(per_code[code]["kl"][ti]),
                      "jwzz": _round(per_code[code]["jwzz"][ti]),
                      "entry_price": _round(pool.get(code))}))
    return signals
