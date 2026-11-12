# -*- coding: utf-8 -*-
"""严格成交模型（一字板买不进即空仓 / 跌停卖不出即延后清仓）的单元测试。

覆盖四件事：
  1. 买入侧：一字涨停 → 当天建仓失败 → 该份额度是现金（不产生收益、不重分配）；
     且**连续多天一字板会被连续拦截**（判定基于实际持仓，不是"目标权重是否跳升"）；
  2. 卖出侧：一字跌停 → 清零延后 → 继续持有（而不是当作已卖出）；
  3. 方向对称：一字跌停时**买入是能成交的**、一字涨停时**卖出是能成交的**
     （旧写法买卖两侧共用绝对值涨跌幅，把这两类判反了）；
  4. 涨停价按 `round(prev_close×(1+幅度), 2)` 精确比对，不用「涨幅 ≥ 幅度」的近似。
"""
from __future__ import annotations

import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from strategies.core import stats  # noqa: E402

DATES = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08"]
CODE = "000001"


def _mk(closes, raws):
    """closes: hfq 收盘价序列；raws: [(high, low, close), ...]（未复权）。"""
    idx = pd.Index(DATES)
    close = pd.Series(closes, index=idx, dtype=float)
    raw = pd.DataFrame(
        {
            "open": [r[2] for r in raws],
            "high": [r[0] for r in raws],
            "low": [r[1] for r in raws],
            "close": [r[2] for r in raws],
            "volume": [1000.0] * len(DATES),
        },
        index=idx,
    )
    return close, raw


def _run(positions, close, raw):
    return stats.compute(
        {CODE: pd.Series(positions, index=pd.Index(DATES), dtype=float)},
        {CODE: close},
        initial_capital=100000.0,
        commission=0.0, stamp_duty=0.0, slippage=0.0, min_commission=0.0,
        raw_by_code={CODE: raw},
    )


class TestStrictFillModel(unittest.TestCase):
    # ---------- 买入侧：一字涨停 → 空仓 ----------
    def test_consecutive_limit_up_keeps_position_empty(self):
        """d1、d2 连着两个一字涨停 → 两次建仓都没成交 → d3 才买到。

        昨收 10.00 → 涨停价 11.00；昨收 11.00 → 涨停价 12.10；
        d3 最低价 13.20 < 涨停价 13.31（开过板）→ 买得到。
        """
        close, raw = _mk(
            [10.00, 11.00, 12.10, 13.31, 14.00],
            [(10.00, 10.00, 10.00), (11.00, 11.00, 11.00),
             (12.10, 12.10, 12.10), (13.31, 13.20, 13.31),
             (14.00, 13.90, 14.00)],
        )
        out = _run([0.0, 0.5, 0.5, 0.5, 0.5], close, raw)
        # 两天都没买进 → d3 建仓 → 只吃 d4 的涨幅 13.31→14.00
        expect = 100000.0 * (1 + 0.5 * (14.00 / 13.31 - 1.0))
        self.assertAlmostEqual(out["equity"][-1], expect, places=2)
        self.assertEqual(out["fill_blocked"]["buy_blocked"], 2)
        # 利用率：d1~d3 都是 0，d4 才 0.5
        self.assertAlmostEqual(out["metrics"]["capital_utilization"], 0.125, places=6)

    def test_same_setup_without_limit_up_earns_return(self):
        """对照组：d1 不是一字板 → 正常建仓、正常吃涨幅。"""
        close, raw = _mk(
            [10.0, 10.0, 11.0, 12.0, 12.0],
            [(10.0, 10.0, 10.0), (10.5, 10.2, 10.0),
             (11.0, 11.0, 11.0), (12.0, 12.0, 12.0), (12.0, 12.0, 12.0)],
        )
        out = _run([0.0, 0.5, 0.5, 0.5, 0.5], close, raw)
        self.assertGreater(out["equity"][-1], 109000.0)
        self.assertEqual(out["fill_blocked"]["buy_blocked"], 0)

    # ---------- 方向对称：一字跌停的买入是能成交的 ----------
    def test_limit_down_buy_is_not_blocked(self):
        """跌停板上全是卖单 → 买单能成交 → 亏损照计（不被当成"买不进"）。"""
        close, raw = _mk(
            [10.0, 10.0, 9.0, 9.0, 9.0],
            [(10.0, 10.0, 10.0), (9.0, 9.0, 9.0),
             (9.0, 9.0, 9.0), (9.0, 9.0, 9.0), (9.0, 9.0, 9.0)],
        )
        out = _run([0.0, 0.5, 0.5, 0.5, 0.5], close, raw)
        # d2 一字跌停 10→9 → 权重 0.5 → 净值 -5%
        self.assertAlmostEqual(out["equity"][-1], 95000.0, places=2)
        self.assertEqual(out["fill_blocked"]["buy_blocked"], 0)

    def test_limit_up_sell_is_not_blocked(self):
        """涨停板上全是买单 → 卖单能成交 → 不该被当成"卖不出"。"""
        close, raw = _mk(
            [10.0, 10.0, 11.0, 11.0, 11.0],
            [(10.0, 10.0, 10.0), (11.0, 11.0, 11.0),
             (11.0, 11.0, 11.0), (11.0, 11.0, 11.0), (11.0, 11.0, 11.0)],
        )
        out = _run([0.5, 0.5, 0.0, 0.0, 0.0], close, raw)
        self.assertEqual(out["fill_blocked"]["sell_deferred"], 0)
        self.assertAlmostEqual(out["equity"][-1], 105000.0, places=2)

    # ---------- 卖出侧：一字跌停 → 清零延后 ----------
    def test_limit_down_sell_is_deferred(self):
        """d2 一字跌停卖不掉 → 继续持有到 d3（能卖的那天才清零）→ 继续承担 d3 的跌幅。"""
        close, raw = _mk(
            [10.0, 10.0, 9.0, 8.3, 8.3],
            [(10.0, 10.0, 10.0), (10.0, 10.0, 10.0),
             (9.0, 9.0, 9.0), (8.5, 8.2, 8.3), (8.4, 8.3, 8.3)],
        )
        out = _run([0.5, 0.5, 0.0, 0.0, 0.0], close, raw)
        self.assertEqual(out["fill_blocked"]["sell_deferred"], 1)
        # 延后期间仍持仓 → d3 的 9.00→8.30 的跌幅照计
        expect = 100000.0 * (1 + 0.5 * (9.0 / 10.0 - 1.0)) * (1 + 0.5 * (8.3 / 9.0 - 1.0))
        self.assertAlmostEqual(out["equity"][-1], expect, places=2)
        # 若能立即卖出（没有这条约束），就不会承担 d3 的跌幅 → 净值更高
        self.assertLess(out["equity"][-1], 95000.0)

    # ---------- 涨停价精度 ----------
    def test_limit_price_precision(self):
        """昨收 10.00 → 涨停价 11.00。最低价 10.99（开过板、买得到）不算一字板。"""
        self.assertEqual(stats._limit_prices(10.00, 0.10), (11.00, 9.00))
        self.assertFalse(stats._is_limit_up(11.00, 10.99, 10.00, 0.10))
        self.assertTrue(stats._is_limit_up(11.00, 11.00, 10.00, 0.10))
        # 跌停侧对称
        self.assertFalse(stats._is_limit_down(9.01, 9.00, 10.00, 0.10))
        self.assertTrue(stats._is_limit_down(9.00, 9.00, 10.00, 0.10))

    def test_board_limit_pct(self):
        """双创 20%、主板 10%（ST 自 2025-04 起也是 10%）。"""
        self.assertEqual(stats._limit_pct("600000"), 0.10)
        self.assertEqual(stats._limit_pct("300750"), 0.20)
        self.assertEqual(stats._limit_pct("688981"), 0.20)
        self.assertEqual(stats._limit_prices(10.00, 0.20), (12.00, 8.00))

    def test_equity_model_marker(self):
        """口径标识必须变，否则新旧报告会被混读。"""
        close, raw = _mk([10.0] * 5, [(10.0, 10.0, 10.0)] * 5)
        out = _run([0.0] * 5, close, raw)
        self.assertEqual(out["metrics"]["equity_model"], "weight_strict_fill")


if __name__ == "__main__":
    unittest.main()
