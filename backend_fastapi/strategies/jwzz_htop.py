# -*- coding: utf-8 -*-
"""策略：JWZZ 强势区选股买入 / 「JWZZ 下穿 或 最低价下破 HTOP」清仓。

与 `jwzz_cross` 的关键差别（**持有语义不同**）：
  * jwzz_cross：每日把「强势票按 JWZZ 降序前 top_n」当**完整目标组合**，
    当日跌出前 N / 下穿阈值 → 立刻移出（持仓随排名轮动）。
  * 本策略：**买入**才看排名（强势区内按 JWZZ 降序取前 top_n 中新进入的票），
    **持有**不因跌出前 N 而卖 —— 只有两个**清仓事件**：
      ① JWZZ 收盘下穿阈值（默认 95‰）；
      ② 当日**最低价下破 HTOP**（筹码密集上沿）。
    因此持仓数可以累积、超过 top_n，单股仓位固定 5%（1/20）时最多约 20 只满仓。

## 仓位口径
每只**固定** `M`（默认 0.05 = 1/20），不是 `jwzz_cross` 的「总仓位均分」。
Σw ≤ 1 由引擎约束；持仓数 × M 超过 1 时**只补足到不超额**为止。
回测范围页的「单股仓位上限」若更严，仍由引擎取更严者（两层独立，见 stats.py）。

## HTOP 口径（🔴 必须同复权，否则价位错位）
HTOP = 筹码密集上沿（位置最高的筹码峰峰顶价），计算内核在 `formulas.peak`，
与副图指标 `HTOP` 同一份实现（禁止在策略里重写一遍）。

筹码矩阵必须走 `chipformulas.compute_matrix(adjust=ctx.adjust)` —— 回测默认
**hfq**，指标展示默认 **qfq**。若拿 qfq 算出的 HTOP 去比 hfq 的 low，
除权除息日之后两条价格轴**错位**，会凭空触发「下破」。本策略统一用 ctx.adjust。

「下破」取**当日最低价 < HTOP**（`low` 必须放进 inputs）。
触发条件用最低价；**成交仍按引擎的收盘价**（组合权重模型）：
实盘可用条件单在 HTOP 价位挂单，回测与实盘成交价的差异属已知口径说明，
写进文档，不静默当成「按 HTOP 成交」。

两种清仓事件同日触发时，归因为 `dn`（`meta.trigger`），只发一条 weight=0，
不重复发两次。

⚠️ 参数名是 **`M`**（单股固定仓位），不是 `position`（总仓位系数）——两套语义不能混。
它与回测范围页的「单股仓位上限」是**两层独立约束**，引擎取更严者。

## HTOP 固定参数（不暴露为策略参数）
与 HTOP 指标默认值一致：`tri_decay` / `min_ratio=10%` / `min_pct=0` / `bins=80`。
算不出 HTOP（NaN，如早期无峰）→ 该票**不买**（买入需 HTOP 可判，否则没有卖出线）。
"""
from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

import chipformulas as chip_formulas
from formulas.jwzz import DEFAULT_PERIOD, jwzz_panel
from formulas.peak import top_price_peak_price_series

from .core.base import strategy, ParamSpec, Signal, checkpoint, Cancelled
from .core import data

# HTOP 固定口径（与 indicators/chip_htop.py 参数默认值一致，不暴露为策略参数）
_HTOP_FORMULA = "tri_decay"
_HTOP_BINS = 80
_HTOP_MIN_RATIO = 0.10       # 占主峰比例 10%
_HTOP_MIN_PCT = 0.0
_HTOP_WARM_DAYS = 400        # 筹码养状态：回测起点前多取的自然日（约 1.5×250）


def _htop_series(code: str, start: str | None, end: str | None,
                 adjust: str) -> pd.Series | None:
    """取单票的 HTOP（筹码密集上沿）序列，日期对齐到筹码矩阵的 dates。

    ⚠️ `end=None`：让 compute_matrix 铺满「上市→最新」的完整价格轴，
    与 HTOP 指标 `chip_rows_of` 同口径（固定 end 会改变分箱边界 → 价位漂移）。
    `start` 往前推 _HTOP_WARM_DAYS 自然日养状态；调用方按回测区间切片。
    ⚠️ adjust 必须 = ctx.adjust（回测 hfq），否则价位与 low 不在同一量纲。
    """
    fetch_start = None
    if start:
        try:
            fetch_start = (dt.date.fromisoformat(start[:10])
                           - dt.timedelta(days=_HTOP_WARM_DAYS)).isoformat()
        except ValueError:
            fetch_start = None
    try:
        res = chip_formulas.compute_matrix(
            code, formula_id=_HTOP_FORMULA, start=fetch_start, end=None,
            adjust=adjust, bins=_HTOP_BINS, offline=False)
    except Exception:                            # noqa: BLE001 - 单只失败不拖垮整批
        return None
    prices = top_price_peak_price_series(
        res.pct, res.centers, _HTOP_MIN_PCT, _HTOP_MIN_RATIO)
    s = pd.Series(prices, index=[str(d)[:10] for d in res.dates])
    if end:
        s = s[s.index <= end[:10]]
    return s


@strategy(
    id="jwzz_htop",
    name="JWZZ强势买入/HTOP破位清仓",
    category="趋势",
    description="JWZZ 处于强势区时按偏离度降序取前 N 只新买入（每只固定 5%），"
                "持有期间不因跌出前 N 卖出；只在 JWZZ 收盘下穿阈值（默认 95‰）"
                "或当日最低价下破 HTOP（筹码密集上沿）时清仓。HTOP 与回测同复权口径。",
    inputs=["close", "low", "volume", "amount"],
    outputs="目标权重向量(组合占比) + 因子值(meta)",
    params=[
        ParamSpec("cost_period", "int", DEFAULT_PERIOD, min=2, max=250,
                   label="成本EMA周期"),
        ParamSpec("threshold", "float", 95.0, min=-500.0, max=2000.0,
                   label="JWZZ下穿阈值(‰)"),
        ParamSpec("smooth_period", "int", 0, min=0, max=250,
                   label="平滑周期(0=原始JWZZ)"),
        ParamSpec("top_n", "int", 10, min=1, max=200, label="买入排名上限(N)"),
        ParamSpec("M", "float", 0.05, min=0.01, max=0.2,
                   label="单股仓位 M(0.05=1/20)"),
    ],
    version="1",
)
def jwzz_htop(ctx, cost_period: int = DEFAULT_PERIOD, threshold: float = 95.0,
              smooth_period: int = 0, top_n: int = 10,
              M: float = 0.05) -> list:
    codes = [str(c).strip() for c in (ctx.codes or []) if str(c).strip()]
    if not codes:
        return []
    period = max(2, int(cost_period))
    thr = float(threshold)
    smooth_n = max(0, int(smooth_period))
    top_n = max(1, int(top_n))
    w = min(0.2, max(0.01, float(M)))
    adjust = str(getattr(ctx, "adjust", "hfq") or "hfq")
    start = str(ctx.start)[:10] if ctx.start else None
    end = str(ctx.end)[:10] if ctx.end else None

    # ---- JWZZ 面板（formulas 内核，与副图/其他策略同源）----
    bars = data.load_bars(codes, start, end,
                          fields=["close", "low", "volume", "amount"],
                          adjust=adjust)
    panel = jwzz_panel(bars, period=period, threshold=thr, smooth_period=smooth_n)
    if not panel.cols:
        return []
    days = panel.days
    cols = panel.cols
    bull_m = panel.bull                         # C × T 强势状态
    dn_m = panel.dn                             # C × T 下穿事件（收盘下穿阈值）
    val_m = panel.val
    bar_m = panel.bar                           # C × T 当日是否有行情（停牌不买）

    # ---- HTOP 面板：逐票算筹码矩阵（线程池并行，与 peak_top 同款），再对齐到统一轴 ----
    htop: dict[str, pd.Series] = {}
    _ex = ThreadPoolExecutor(max_workers=max(1, min((__import__("os").cpu_count() or 4), 12)))
    futs = {_ex.submit(_htop_series, c, start, end, adjust): c for c in cols}
    try:
        for fut in as_completed(futs):
            c = futs[fut]
            try:
                s = fut.result()
            except Cancelled:
                raise
            except Exception:                    # noqa: BLE001 - 单只失败当无 HTOP
                s = None
            if s is not None and not s.empty:
                htop[c] = s
            checkpoint()
    except Cancelled:
        for f in futs:
            f.cancel()
        raise
    finally:
        _ex.shutdown(wait=True)

    # 对齐到统一交易日轴：每个 code 一行 NaN 数组，按日期填 HTOP
    htop_m = np.full((len(cols), len(days)), np.nan, dtype=float)
    day_pos = {d: i for i, d in enumerate(days)}
    low_m = np.full((len(cols), len(days)), np.nan, dtype=float)
    for i, c in enumerate(cols):
        s = htop.get(c)
        if s is not None:
            for d, v in s.items():
                j = day_pos.get(d)
                if j is not None and np.isfinite(v):
                    htop_m[i, j] = float(v)
        df = bars.get(c)
        if df is not None:
            for d, v in zip(df.index, df["low"] if "low" in df.columns else []):
                j = day_pos.get(str(d)[:10])
                if j is not None:
                    low_m[i, j] = float(v)

    signals: list[Signal] = []
    held: set[str] = set()
    total_weight = 0.0

    for t, day in enumerate(days):
        # ① 清仓事件：JWZZ 收盘下穿阈值 或 当日最低价下破 HTOP
        exits: dict[str, str] = {}
        for i, c in enumerate(cols):
            if c not in held:
                continue
            if bool(dn_m[i, t]):
                exits[c] = "dn"
                continue
            h = htop_m[i, t]
            lo = low_m[i, t]
            if np.isfinite(h) and np.isfinite(lo) and lo < h:
                exits[c] = "htop"
        for c in sorted(exits):
            signals.append(Signal(code=c, time=day, weight=0.0,
                                  reason="JWZZ下穿阈值/最低价下破HTOP清仓",
                                  meta={"exit": 1, "trigger": exits[c]}))
            held.discard(c)
            total_weight = max(0.0, total_weight - w)

        # ② 买入：强势区内按偏离度降序取**前 top_n**（只看这 N 名，不向后补位），
        #    对其中尚未持仓、有行情、HTOP 可判的票建仓。
        day_bull = np.flatnonzero(bull_m[:, t])
        if day_bull.size:
            order = day_bull[np.argsort(-val_m[day_bull, t], kind="stable")][:top_n]
            room = max(0, int(np.floor((1.0 - total_weight + 1e-9) / w)))
            for rank, i in enumerate(order, start=1):
                c = cols[i]
                if c in held or room <= 0:
                    continue
                if not bool(bar_m[i, t]) or not np.isfinite(htop_m[i, t]):
                    continue                                # 停牌 / 无 HTOP → 不买
                held.add(c)
                total_weight = round(total_weight + w, 6)
                room -= 1
                signals.append(Signal(
                    code=c, time=day, weight=w,
                    reason="JWZZ强势区排名前N买入",
                    meta={"rank": rank, "value": round(float(val_m[i, t]), 3),
                          "htop": round(float(htop_m[i, t]), 4), "M": w},
                ))

    return signals
