# -*- coding: utf-8 -*-
"""「当日策略」三档以「上一次推荐」为基线的单元测试。

复现用户报的现象①：改日重跑后，上一次「买入」的票**凭空消失**
（三张表里都没有），且建仓日 / 成本价在两次运行之间漂移
（实测 002086 在 11-13 与 11-16 两次运行里建仓日分别是 10-30 / 11-12）。

根因：三档原来只看「策略信号相邻两日的差分」，描述的是**策略的仓位**，
不是**用户按建议建立的仓位**。于是：

  * 上次买入、本次已不在目标组合里的票 → 既不在持股也不在卖出（消失）；
  * 持股的建仓日由策略信号回溯得出 → 策略历史一重算就漂移；
  * 每只票拿**自己最后一根 K 线**比差分 → 停牌票落在不同日期上，直接错档。

本测试用合成信号构造（不依赖本地行情库），断言：
  1. 有基线：上次买入、本次不在目标组合里 → 落进**卖出**，不消失；
  2. 有基线：持股的建仓日 / 成本价**沿用上次**（不漂移）；
  3. 有基线：上次已持股、本次仍持有 → **不再**被当成「今天新建仓」；
  4. 无基线（首次运行）→ 退回「信号相邻两日差分」，与旧版行为一致；
  5. 停牌票（信号日没有 K 线）按「仍持有」处理，不因最后一行落在哪天而错档；
  6. 同一天重跑（有基线）→ 结果一致（幂等）。
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from strategies.core import backtest  # noqa: E402
from strategies.core import base as _base  # noqa: E402

Signal = _base.Signal
strategy = _base.strategy

DATES = ["2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"]
CODES = ["AAA", "BBB", "CCC", "DDD"]
PRICE = {"AAA": 10.0, "BBB": 20.0, "CCC": 30.0, "DDD": 40.0}
# DDD 在 11-13 停牌（没有那一根 K 线）——用来验「停牌按仍持有处理」
AVAIL = {
    "AAA": list(DATES),
    "BBB": list(DATES),
    "CCC": list(DATES),
    "DDD": DATES[:3],
}

SID = "reco_ledger_fake"


@strategy(id=SID, name="自检用假策略", category="test", description="仅供单元测试",
          inputs=["close"], outputs="")
def _fake(ctx, **kwargs):        # noqa: ARG001 - 装饰器登记用，本体不会被调用
    return []


def _bars(codes, start=None, end=None, fields=None, adjust="hfq", progress=None):
    """合成行情：每只票按自己的可用交易日给一段连续 K 线（索引用 DatetimeIndex，
    与真实行情一致 —— 逼调用方按「≤ 某日」而不是「恰好等于某日」取值）。"""
    out: dict[str, pd.DataFrame] = {}
    lo = str(start or DATES[0])[:10]
    hi = str(end or DATES[-1])[:10]
    for c in codes:
        idx = [d for d in AVAIL.get(c, DATES) if lo <= d <= hi]
        if not idx:
            continue
        out[c] = pd.DataFrame(
            {"close": [PRICE[c] + i for i in range(len(idx))]},
            index=pd.DatetimeIndex(idx),
        )
    return out


def _sig(rows):
    return [Signal(code=c, time=t, weight=w) for (t, c, w) in rows]


# 第 1 次运行（信号日 11-12）：AAA 建仓，BBB / CCC / DDD 已在持仓
SIG1 = _sig([
    ("2026-11-10", "BBB", 0.5), ("2026-11-10", "CCC", 0.5),
    ("2026-11-11", "BBB", 0.5), ("2026-11-11", "CCC", 0.5),
    ("2026-11-11", "DDD", 0.5),
    ("2026-11-12", "AAA", 0.5), ("2026-11-12", "BBB", 0.5),
    ("2026-11-12", "CCC", 0.5), ("2026-11-12", "DDD", 0.5),
])

# 第 2 次运行（信号日 11-13）：**策略历史被重算**——
#   AAA 直接没了、BBB 在 11-12 被清掉、11-13 又买回、CCC 在 11-13 被清掉。
# 这正是「同一天两次运行结果不同」的形态：只按信号差分，AAA 会凭空消失，
# BBB 还会被当成「今天新建仓」。
SIG2 = _sig([
    ("2026-11-10", "BBB", 0.5), ("2026-11-10", "CCC", 0.5),
    ("2026-11-11", "BBB", 0.5), ("2026-11-11", "CCC", 0.5),
    ("2026-11-11", "DDD", 0.5),
    ("2026-11-12", "CCC", 0.5), ("2026-11-12", "DDD", 0.5),
    ("2026-11-13", "BBB", 0.5),
])


def _next_trade_day(sig_day):
    """替掉交易日历（单测不该联网）：轴里没有下一根就按自然日 +1。"""
    import datetime as dt
    return (dt.date.fromisoformat(sig_day) + dt.timedelta(days=1)).isoformat()


def _run(signals, end, exec_date, baseline=None):
    with mock.patch.object(backtest.base, "run", return_value=signals), \
         mock.patch.object(backtest.data, "load_bars", side_effect=_bars), \
         mock.patch.object(backtest.data, "load_names", return_value={}), \
         mock.patch.object(backtest, "next_trading_day", _next_trade_day), \
         mock.patch.object(backtest, "_MIN_BARS", 2):     # 合成行情只有几根 K 线
        return backtest.run_recommend(
            SID, {}, list(CODES), DATES[0], end,
            execution_date=exec_date, signal_date=end, baseline=baseline,
        )


def _codes(rows):
    return {r["code"]: r for r in rows}


class TestRecommendLedger(unittest.TestCase):
    """三档口径：以上一次推荐为基线（持仓账本），而不是策略信号的相邻两日差分。"""

    def test_first_run_without_baseline_uses_signal_diff(self):
        """首次运行（没有基线）→ 退回信号差分：AAA 建仓，其余在持仓。"""
        out = _run(SIG1, "2026-11-12", "2026-11-13")
        self.assertEqual(set(_codes(out["buy"])), {"AAA"})
        self.assertEqual(set(_codes(out["hold"])), {"BBB", "CCC", "DDD"})
        self.assertEqual(out["sell"], [])
        self.assertEqual(out["baseline"]["source"], "signal_diff")
        # 建仓日 = **执行日**（用户实际下单那天），不是信号日
        self.assertEqual(_codes(out["buy"])["AAA"]["buy_date"], "2026-11-13")

    def test_baseline_keeps_previous_buy_from_vanishing(self):
        """有基线：上次买入、本次不在目标组合里 → 落进卖出，**不消失**。"""
        first = _run(SIG1, "2026-11-12", "2026-11-13")
        second = _run(SIG2, "2026-11-13", "2026-11-16", baseline=first)

        buy = _codes(second["buy"])
        sell = _codes(second["sell"])
        hold = _codes(second["hold"])
        self.assertEqual(second["baseline"]["source"], "previous_recommend")
        # AAA：上次买入、这次策略里没了 → 必须明确告诉用户「卖」
        self.assertIn("AAA", sell)
        self.assertNotIn("AAA", hold)
        self.assertNotIn("AAA", buy)
        # BBB：上次就持股 → 仍是持股，**不是**「今天新建仓」
        self.assertIn("BBB", hold)
        self.assertNotIn("BBB", buy)
        # CCC：上次持股、这次被清 → 卖出
        self.assertIn("CCC", sell)
        self.assertEqual(set(buy), set())

    def test_baseline_carries_buy_date_and_cost(self):
        """有基线：建仓日 / 成本价沿用上次（跨运行稳定，不再漂移）。"""
        first = _run(SIG1, "2026-11-12", "2026-11-13")
        second = _run(SIG2, "2026-11-13", "2026-11-16", baseline=first)
        hold = _codes(second["hold"])
        # BBB 的建仓信号在 11-10 → **执行日**是 11-11（用户当天下单）。
        # 上次的建仓日就是 11-11；这次策略历史被重算，按信号回溯会变成 11-13 ——
        # 有基线时必须保持 11-11。
        self.assertEqual(hold["BBB"]["buy_date"], "2026-11-11")
        self.assertAlmostEqual(hold["BBB"]["cost_price"], 21.0, places=2)

    def test_buy_date_is_execution_day_not_signal_day(self):
        """建仓日必须是**执行日**（用户下单那天），不是信号日 —— 否则「今天持股的
        成本价」与「昨天买入表里的价」差一天（实测现象①）。"""
        first = _run(SIG1, "2026-11-12", "2026-11-13")
        buy = _codes(first["buy"])
        # AAA 的建仓信号在 11-12，执行日 11-13 → 建仓日 = 11-13
        self.assertEqual(buy["AAA"]["buy_date"], "2026-11-13")
        # 持股的建仓日同样按执行日给（BBB：信号 11-10 → 执行 11-11）
        hold = _codes(first["hold"])
        self.assertEqual(hold["BBB"]["buy_date"], "2026-11-11")
        self.assertEqual(hold["DDD"]["buy_date"], "2026-11-12")

    def test_suspended_stock_is_treated_as_still_held(self):
        """停牌票（信号日没有 K 线）按「仍持有」处理，不因最后一行落在哪天而错档。"""
        first = _run(SIG1, "2026-11-12", "2026-11-13")
        second = _run(SIG2, "2026-11-13", "2026-11-16", baseline=first)
        hold = _codes(second["hold"])
        # DDD 11-13 停牌：权重沿用 11-12 的 → 仍是持股（不是「今天新建仓」）
        self.assertIn("DDD", hold)
        self.assertNotIn("DDD", _codes(second["buy"]))

    def test_same_day_rerun_is_idempotent(self):
        """同一天重跑（有基线）→ 结果一致，不会因为基线漂移而变。"""
        first = _run(SIG1, "2026-11-12", "2026-11-13")
        again = _run(SIG1, "2026-11-12", "2026-11-13", baseline=first)
        self.assertEqual(set(_codes(again["buy"])), {"AAA"})
        self.assertEqual(set(_codes(again["hold"])), {"BBB", "CCC", "DDD"})
        self.assertEqual(again["sell"], [])

    def test_future_baseline_is_rejected(self):
        """往回改日期重跑时，不能拿「未来那次」当持仓基线。"""
        later = _run(SIG2, "2026-11-13", "2026-11-16")
        back = _run(SIG1, "2026-11-12", "2026-11-13", baseline=later)
        self.assertEqual(back["baseline"]["source"], "signal_diff")


if __name__ == "__main__":
    unittest.main()
