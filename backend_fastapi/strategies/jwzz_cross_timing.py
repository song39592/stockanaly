# -*- coding: utf-8 -*-
"""策略：JWZZ 选股 + **大盘状态择时**（`jwzz_cross` 的择时变体）。

## 与 `jwzz_cross` 的关系

**选股口径完全同源**（同一个 `formulas/jwzz.py` 面板、同一套阈值 / 排名 / 卖出规则），
**只多一层「仓位开关」**：

> 大盘处于「向上」→ 按 `position` / `top_n` 正常持仓；
> 大盘处于「向下」→ 按 `down_position` / `down_top_n` 降仓或空仓。

为什么**新建策略**而不是给 `jwzz_cross` 加个参数：
① 改动的是**算法**（仓位随市场状态变），不是调参 → 独立 id 才能与基线并排对照、
   基线 `jwzz_cross` 一行不动；
② 项目已有同款先例：`jwzz_cross_safe` = `jwzz_cross` + 一条买入限制，做法同样是
   **新建策略文件**；
③ `strategy.version` 不同 → 报告里能看出「这是两套算法」，不会拿两套口径的数字直接比。

## 「大盘状态」的口径（逐条写死）

| 项 | 规则 |
|---|---|
| 状态判定 | `state[t] = close[t-1] > MA(ma_period)[t-1]`（**无前视**：决策日 t 只用 ≤ t-1 的信息） |
| MA 窗口 | `MA(period)[t-1] = mean(close[t-period .. t-1])`（**含** t-1，不含 t） |
| 序列来源 | `market_code`：`sh000001` / `sh000300` / `sh000852` / `sh000985` / `pool` |
| `pool` | 回测池等权指数（**内生**、随池子变 → 只作对照；实盘与横向对比用真指数） |

⚠️ **写成 `close[t] > MA[t]` 就是前视**：用当天收盘判当天要下的仓位，回测里会
凭空多出一段 alpha，实盘则是当天根本拿不到的信号。判定口径在
`formulas/market.py::trend_state`（中立内核；统计层的默认基准也用同一份「等权池」定义）。

## 仓位开关的落点（必须写死，否则两边算的不是一件事）

* 权重 = `目标总仓位 / 名额数`，**名额数 = 当期实际选出的票数**（不是 top_n 上限）。
* 「切空仓」= 给当期全部持仓发 `weight=0` → 引擎 diff 出**卖出** → 真的付换手成本。
  ⚠️ 不发 0 的话，引擎会把权重**前向填充**（`signals_to_positions`）→ 仓位根本没降，
  「空仓」变成「减仓」，是另一种东西。
* 状态切换的当天**必须重发整组**（哪怕成分没变）：权重变了才算得出买卖。
* 成分没变**且**目标仓位没变 → 不发信号（引擎 ffill 维持，避免无谓的权重抖动）。
* 「下跌时降低持仓上限」用 `down_top_n`（0 = 与 `top_n` 相同）。

## 数据依赖

* 选股：`data.load_bars(ctx.codes, ["close","volume","amount"])`（与 `jwzz_cross` 同源）。
* 大盘序列：
  * 真指数 → `data.load_index(market_code, ...)`（独立表 `index_bars`）；
  * `pool` → 由本策略用**同一批**收盘价算等权池指数（口径见 `formulas/market.py`）。
  ⚠️ 指数**没落盘**时**直接抛错**，不静默降级 —— 否则「择时没生效」会被读成
  「择时没用」。落盘用 `python scripts/download_indices.py`。
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from .core.base import strategy, ParamSpec, Signal
from .core import data
from formulas.jwzz import DEFAULT_PERIOD, jwzz_panel
from formulas import market

# 可选的大盘序列（`pool` = 回测池等权指数；其余是本地已落盘的真指数）
MARKET_SOURCES = ("sh000001", "sh000300", "sh000852", "sh000985", "pool")


@strategy(
    id="jwzz_cross_timing",
    name="JWZZ 上穿/下穿（大盘择时）",
    category="趋势",
    description="与「JWZZ 上穿/下穿」同一套选股口径，只多一层仓位开关："
                "大盘在 MA 之上按正常仓位/持仓上限持有，大盘跌破 MA 则按"
                "「下跌仓位 / 下跌持仓上限」降仓或空仓（空仓=真清仓，付换手成本）。"
                "状态判定无前视（决策日只用前一日收盘及更早）。",
    inputs=["close", "volume", "amount"],
    outputs="目标权重向量(组合占比) + 因子值(meta)",
    requires_pool=False,
    version="1",
    params=[
        ParamSpec("cost_period", "int", DEFAULT_PERIOD, min=2, max=250,
                   label="成本EMA周期"),
        ParamSpec("threshold", "float", 95.0, min=-500.0, max=2000.0,
                   label="触发阈值(‰)"),
        ParamSpec("smooth_period", "int", 0, min=0, max=250,
                   label="平滑周期(0=原始JWZZ；>0需把阈值调低约5倍)"),
        ParamSpec("top_n", "int", 10, min=1, max=200, label="上涨时持仓数量上限"),
        ParamSpec("position", "float", 1.0, min=0.0, max=1.0,
                   label="上涨时总仓位系数(1=满仓)"),
        ParamSpec("market_code", "choice", "sh000001", choices=list(MARKET_SOURCES),
                   label="大盘序列(sh000001=上证/sh000300=沪深300/"
                         "sh000852=中证1000/sh000985=中证全指/pool=池等权)"),
        ParamSpec("ma_period", "int", 20, min=2, max=250, label="大盘趋势MA周期"),
        ParamSpec("down_position", "float", 0.0, min=0.0, max=1.0,
                   label="下跌时总仓位系数(0=空仓,0.5=半仓)"),
        ParamSpec("down_top_n", "int", 0, min=0, max=200,
                   label="下跌时持仓数量上限(0=与上涨时相同)"),
    ],
)
def jwzz_cross_timing(ctx, cost_period: int = DEFAULT_PERIOD, threshold: float = 95.0,
                      smooth_period: int = 0, top_n: int = 10, position: float = 1.0,
                      market_code: str = "sh000001", ma_period: int = 20,
                      down_position: float = 0.0, down_top_n: int = 0) -> list:
    codes = [str(c).strip() for c in (ctx.codes or []) if str(c).strip()]
    if not codes:
        return []
    period = max(2, int(cost_period))
    thr = float(threshold)
    smooth_n = max(0, int(smooth_period))
    up_n = max(1, int(top_n))
    up_pos = min(1.0, max(0.0, float(position)))
    down_pos = min(1.0, max(0.0, float(down_position)))
    down_n = max(0, int(down_top_n))
    ma_p = max(2, int(ma_period))
    mkt = str(market_code or "sh000001").strip()

    # 主动拉取所需字段（inputs=["close","volume","amount"]）；缓存命中，无额外 I/O
    bars = data.load_bars(ctx.codes, ctx.start, ctx.end,
                          fields=["close", "volume", "amount"], adjust=ctx.adjust)

    # ---- 统一交易日轴 + 强势状态（**口径在 formulas/jwzz.py**，与 jwzz_cross 同源）----
    panel = jwzz_panel(bars, period=period, threshold=thr, smooth_period=smooth_n)
    if not panel.cols:
        return []
    day_index = panel.days
    cols = panel.cols
    bull_m = panel.bull                             # C × T，bool（每票一行）
    strength_m = panel.val                           # C × T，float32

    # ---- 大盘序列 + 无前视趋势状态（口径在 formulas/market.py）----
    up_state, valid_state = _market_state(mkt, bars, day_index, ma_p, ctx)

    signals: list[Signal] = []
    held: set[str] = set()                          # ★ 自己维护持仓集合
    last_pos = -1.0                                 # 上一期目标总仓位（-1 = 从未设过）
    last_n = -1

    for t, day in enumerate(day_index):
        # 判不出来的预热期（窗口不足 / 数据缺失）→ **退回基线行为**（按「向上」处理），
        # 不能当成「向下」→ 否则序列开头会白白空仓几天。
        is_up = bool(up_state[t]) if valid_state[t] else True
        tgt_pos = up_pos if is_up else down_pos
        tgt_n = up_n if is_up else (down_n if down_n > 0 else up_n)

        day_bull = np.flatnonzero(bull_m[:, t])
        if day_bull.size:
            # 偏离度降序；同值保持列序（stable），保证结果可复现、不依赖 dict 顺序
            order = day_bull[np.argsort(-strength_m[day_bull, t], kind="stable")]
            picks = {cols[i] for i in order[:tgt_n]}
        else:
            picks = set()

        # 成分没变 **且** 目标仓位/持仓上限没变 → 不发信号（引擎 ffill 维持）
        if picks == held and abs(tgt_pos - last_pos) < 1e-12 and tgt_n == last_n:
            continue

        # 掉出的票显式 weight=0：不发的话这一天不会进入引擎的 rebalance_dates，
        # 「移出」根本没被登记，上一期持仓会继续持有。
        for code in sorted(held - picks):
            signals.append(Signal(code=code, time=day, weight=0.0,
                                  reason="JWZZ 离开强势区/跌出前N",
                                  meta={"dropped": 1, "market_up": int(is_up),
                                        "market": mkt, "ma_period": ma_p}))

        if picks:
            # ⚠️ 权重分母是**当期实际选出的票数**；仓位为 0 时权重就是 0
            #    （= 真清仓，引擎 diff 出卖出 → 付换手成本，与「减仓」是两件事）。
            w = tgt_pos / len(picks)
            value_of = {cols[i]: float(strength_m[i, t]) for i in range(len(cols))}
            reason = "JWZZ 上穿阈值" if is_up else "JWZZ 上穿阈值（大盘向下·降仓）"
            for rank, code in enumerate(sorted(picks), start=1):
                signals.append(Signal(
                    code=code, time=day, weight=w, reason=reason,
                    meta={"value": round(value_of.get(code, 0.0), 3), "rank": rank,
                          "market_up": int(is_up), "market": mkt,
                          "ma_period": ma_p, "target_position": round(tgt_pos, 4)},
                ))
        held = picks
        last_pos = tgt_pos
        last_n = tgt_n

    return signals


def _market_state(market_code: str, bars: dict, days: list[str],
                  ma_period: int, ctx) -> tuple[np.ndarray, np.ndarray]:
    """大盘趋势状态：返回 `(state, valid)`。

    `state`：True = 在 MA 之上（**无前视**：`close[t-1] > MA(ma_period)[t-1]`）；
    `valid`：该点是否**判得出来**（预热期 / 数据缺失 → False）。

    ⚠️ 「判不出来」≠「判为向下」：调用方必须把 `valid=False` 退回**基线行为**，
    不能当成下跌 → 否则序列开头会白白空仓几天。

    实现见 `formulas/market.py::trend_state_detail`（中立内核）。
    """
    if market_code == "pool":
        # 回测池等权指数：由**同一批**收盘价现算，口径与统计层默认基准同源
        # （`formulas/market.py`）。⚠️ 它是**内生**的（池子涨 → 看多 → 买这池子里的票），
        # 且**随回测池变化** → 只适合做对照，不能当实盘信号。
        closes = pd.DataFrame({code: df["close"] for code, df in bars.items()
                                if df is not None and "close" in df.columns})
        closes = closes.reindex(days)
        series = market.pool_index_series(closes)
        if series is None or series.empty:
            raise RuntimeError("池等权指数算不出来（回测池没有收盘价）")
        return market.trend_state_detail(series, ma_period, lag=1)

    # 真指数：必须**已落盘**（scripts/download_indices.py）。
    # ⚠️ 取不到就抛错，不静默降级 —— 静默降级会让「择时没生效」被读成「择时没用」。
    #
    # ⚠️ 取数窗口要**往前多拉一段**：MA(period) 需要 t-1 之前就有 period 个点，
    # 否则回测区间的前几天没有状态 → 那几天的仓位是「猜」出来的。
    lookback_days = ma_period * 3 + 15          # 交易日 ≈ 0.7 × 自然日，取 3 倍留余量
    begin = _shift_days(ctx.start, -lookback_days)
    frame = data.load_index(market_code, begin, ctx.end, fields=["close"])
    if frame is None or frame.empty:
        raise RuntimeError(
            "大盘指数 %s 没有本地数据（区间 %s ~ %s）。"
            "请先运行：python scripts/download_indices.py --codes %s"
            % (market_code, ctx.start, ctx.end, market_code))
    series = market.align_to_days(frame["close"].astype(float), days)
    # 覆盖不足要**显式失败**：指数起点晚于回测区间起点时，那段区间没有状态，
    # 静默按「向下」处理会让整段回测变成另一个东西。
    # 判据只看**回测区间内**（区间之前允许缺 —— 那是预热窗口，本来就没状态）。
    in_range = [i for i, d in enumerate(days) if not ctx.start or str(d)[:10] >= str(ctx.start)[:10]]
    missing = [days[i] for i in in_range if series.isna().to_numpy()[i]]
    if missing:
        raise RuntimeError(
            "大盘指数 %s 的数据覆盖不了回测区间（区间内首个缺失日 %s，区间 %s ~ %s）。"
            "换一条覆盖更全的指数，或缩短回测区间。"
            % (market_code, missing[0], ctx.start, ctx.end))
    return market.trend_state_detail(series, ma_period, lag=1)


def _shift_days(day: str | None, delta: int) -> str | None:
    """日期字符串 ± 自然日（用于把取数窗口往前拉）。"""
    if not day:
        return None
    try:
        base = dt.date.fromisoformat(str(day)[:10])
    except ValueError:
        return None
    return (base + dt.timedelta(days=delta)).isoformat()
