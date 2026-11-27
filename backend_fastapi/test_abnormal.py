# -*- coding: utf-8 -*-
"""`formulas.abnormal` 异动计算内核的单测。

重点：比值口径（3日累计不是逐日相加）、阈值边界、方向、换手率双条件、
严重异动三条件、无前视 lag、缺数据 valid=False 不误判。
"""
import unittest

import numpy as np
import pandas as pd

from formulas.abnormal import (
    abnormal_detail, abnormal_state, abnormal_block, threshold_for_board,
    THRESHOLD_MAIN, THRESHOLD_ST, THRESHOLD_INNOVATION,
    FILTER_PRICE, FILTER_TURNOVER, FILTER_SEVERE,
    FILTER_NEAR_PRICE, FILTER_NEAR_SEVERE,
)


def _days(n):
    return pd.Index([f"2026-01-{i + 1:02d}" for i in range(n)])


class TestThresholdForBoard(unittest.TestCase):
    def test_board_thresholds(self):
        self.assertEqual(threshold_for_board("main"), THRESHOLD_MAIN)
        self.assertEqual(threshold_for_board("chinext"), THRESHOLD_INNOVATION)
        self.assertEqual(threshold_for_board("star"), THRESHOLD_INNOVATION)
        self.assertEqual(threshold_for_board("bse"), THRESHOLD_INNOVATION)
        self.assertEqual(threshold_for_board("main", is_st=True), THRESHOLD_ST)
        self.assertEqual(threshold_for_board("star", is_st=True), THRESHOLD_ST)


class TestPriceDeviation(unittest.TestCase):
    def test_ratio_not_sum_of_daily_returns(self):
        """3 日累计是期末/期初前收盘的比值，不是 3 天涨跌幅相加。"""
        # 指数恒定 100；个股 100 -> 110 -> 121 -> 133.1（每日 +10%）
        # 比值口径 3 日累计 = 33.1%，而非 30%（3×10%）—— 用比值会触发 ±20%
        days = _days(6)
        close = pd.Series([100, 110, 121, 133.1, 133.1, 133.1], index=days)
        idx = pd.Series([100] * 6, index=days)
        turn = pd.Series([0.01] * 6, index=days)
        d = abnormal_detail(close, idx, turn)
        # t=3 处 deviation_3 = 0.331
        self.assertAlmostEqual(d["deviation_3"].iloc[3], 0.331, places=5)
        self.assertTrue(bool(d["is_abnormal"].iloc[3]))
        self.assertEqual(d["direction"].iloc[3], 1)
        # 早期窗口不足不判
        self.assertFalse(bool(d["valid"].iloc[0]))
        self.assertFalse(bool(d["is_abnormal"].iloc[0]))

    def test_index_moves_with_stock_no_abnormal(self):
        """个股与分类指数同步上涨 → 偏离 0，不异动（这是用综指替代会出的错）。"""
        days = _days(6)
        close = pd.Series([100, 110, 121, 133.1, 133.1, 133.1], index=days)
        idx = pd.Series([100, 110, 121, 133.1, 133.1, 133.1], index=days)
        turn = pd.Series([0.01] * 6, index=days)
        d = abnormal_detail(close, idx, turn)
        self.assertAlmostEqual(d["deviation_3"].iloc[3], 0.0, places=6)
        self.assertFalse(bool(d["is_abnormal"].iloc[3]))

    def test_downward_direction(self):
        days = _days(6)
        close = pd.Series([100, 90, 81, 72.9, 72.9, 72.9], index=days)
        idx = pd.Series([100] * 6, index=days)
        turn = pd.Series([0.01] * 6, index=days)
        d = abnormal_detail(close, idx, turn)
        self.assertAlmostEqual(d["deviation_3"].iloc[3], -0.271, places=5)
        self.assertEqual(d["direction"].iloc[3], -1)
        self.assertTrue(bool(d["is_abnormal"].iloc[3]))

    def test_threshold_boundary(self):
        """恰好 = 阈值即触发（>=），低于则不触发。"""
        days = _days(6)
        idx = pd.Series([100] * 6, index=days)
        turn = pd.Series([0.01] * 6, index=days)
        close = pd.Series([100, 100, 100, 120, 120, 120], index=days)
        d = abnormal_detail(close, idx, turn)
        self.assertAlmostEqual(d["deviation_3"].iloc[3], 0.20, places=6)
        self.assertTrue(bool(d["is_abnormal"].iloc[3]))
        close2 = pd.Series([100, 100, 100, 119.99, 119.99, 119.99], index=days)
        d2 = abnormal_detail(close2, idx, turn)
        self.assertFalse(bool(d2["is_abnormal"].iloc[3]))

    def test_st_threshold_tighter(self):
        days = _days(6)
        idx = pd.Series([100] * 6, index=days)
        turn = pd.Series([0.01] * 6, index=days)
        close = pd.Series([100, 103, 106, 115, 115, 115], index=days)  # +15%
        d_main = abnormal_detail(close, idx, turn, threshold=THRESHOLD_MAIN)
        d_st = abnormal_detail(close, idx, turn, threshold=THRESHOLD_ST)
        self.assertFalse(bool(d_main["is_abnormal"].iloc[3]))
        self.assertTrue(bool(d_st["is_abnormal"].iloc[3]))


class TestTurnoverAbnormal(unittest.TestCase):
    def test_requires_both_conditions(self):
        """放大 30 倍但累计换手不足 20% → 不异动；两个都满足才异动。"""
        days = _days(12)
        idx = pd.Series([100] * 12, index=days)
        close = pd.Series([100] * 12, index=days)  # 价格不动
        # 前 5 日 0.001，后 3 日 0.03（30 倍）但累计仅 9% < 20%
        turn = pd.Series([0.001] * 8 + [0.03] * 3 + [0.001], index=days)
        d = abnormal_detail(close, idx, turn)
        self.assertFalse(bool(d["abnormal_turnover"].iloc[10]))
        # 后 3 日 0.10（100 倍）累计 30% >= 20%
        turn2 = pd.Series([0.001] * 8 + [0.10] * 3 + [0.001], index=days)
        d2 = abnormal_detail(close, idx, turn2)
        self.assertTrue(bool(d2["abnormal_turnover"].iloc[10]))
        self.assertTrue(bool(d2["is_abnormal"].iloc[10]))
        self.assertEqual(d2["direction"].iloc[10], 0)  # 换手型异动无价格方向


class TestSevereAbnormal(unittest.TestCase):
    def test_severe_dev10(self):
        days = _days(12)
        idx = pd.Series([100] * 12, index=days)
        turn = pd.Series([0.01] * 12, index=days)
        close = pd.Series([100] * 10 + [205] * 2, index=days)  # 10日 +105%
        d = abnormal_detail(close, idx, turn)
        self.assertTrue(bool(d["severe_dev10"].iloc[10]))
        self.assertTrue(bool(d["is_severe"].iloc[10]))

    def test_severe_count_four_same_direction(self):
        """10 日内 4 次同向异动**事件** → severe_count（事件之间不重叠）。"""
        days = _days(20)
        idx = pd.Series([100] * 20, index=days)
        turn = pd.Series([0.01] * 20, index=days)
        # 每 3 日翻一次（×1.25 复利），让 t=3/6/9/12 各自成为一次事件。
        # 不能用「每次 +25 绝对值」——那会让后几次的 3 日累计偏离递减（25%→20%→16.7%）。
        close = pd.Series([100.0] * 20)
        for k in (3, 6, 9, 12):
            close.iloc[k:] *= 1.25
        close.index = days
        d = abnormal_detail(close, idx, turn)
        self.assertEqual(
            [bool(d["is_abnormal"].iloc[i]) for i in (3, 6, 9, 12)],
            [True, True, True, True],
        )
        # 第 12 日的 10 日窗口（3..12）含 4 次同向事件
        self.assertTrue(bool(d["severe_count"].iloc[12]))
        self.assertTrue(bool(d["is_severe"].iloc[12]))

    def test_consecutive_abnormal_days_are_one_event(self):
        """连续多日异动只算 1 次事件（东财口径）——逐日计数会误判严重异动。"""
        days = pd.bdate_range("2026-01-01", periods=10).strftime("%Y-%m-%d")
        idx = pd.Series([100.0] * 10, index=days)
        turn = pd.Series([0.01] * 10, index=days)
        close = pd.Series([100.0] * 10, index=days)
        for i in range(1, 10):
            close.iloc[i] = close.iloc[i - 1] * 1.10  # 每日刚好 10% 涨停
        d = abnormal_detail(close, idx, turn)
        # 滚动 3 日偏离天天 ≥20%，但事件基准每次重置 → 10 日内只有 3~4 次事件
        self.assertTrue(bool(d["is_abnormal"].iloc[5]))
        self.assertFalse(bool(d["severe_count"].iloc[9]))

    def test_severe_dev_within_30_day_window(self):
        """30 日规则看「30 日以内任一窗口」：14 日 -70% 也要触发。

        东财奥精医疗 2026-11-19：14 日 -73.98% 触发 -70%，
        固定 30 日窗口只有 -58% 会漏。
        """
        days = pd.bdate_range("2026-01-01", periods=40).strftime("%Y-%m-%d")
        idx = pd.Series([100.0] * 40, index=days)
        turn = pd.Series([0.01] * 40, index=days)
        close = pd.Series([100.0] * 11 + [200.0] * 15 + [59.0] * 14, index=days)
        d = abnormal_detail(close, idx, turn)
        # t=39：14 日前收盘 200 → -70.5%；30 日前收盘 100 → -41%
        self.assertAlmostEqual(d["dev30"].iloc[-1], -0.41, places=6)
        self.assertTrue(bool(d["severe_dev30"].iloc[-1]))
        self.assertTrue(bool(d["is_severe"].iloc[-1]))


class TestAbnormalWarningFields(unittest.TestCase):
    """预警字段：事件次数 / 距阈值比例 / near_abnormal（产品定义，非东财规则）。"""

    def test_event_counts_exposed(self):
        days = _days(20)
        idx = pd.Series([100] * 20, index=days)
        turn = pd.Series([0.01] * 20, index=days)
        close = pd.Series([100.0] * 20)
        for k in (3, 6, 9, 12):
            close.iloc[k:] *= 1.25
        close.index = days
        d = abnormal_detail(close, idx, turn)
        self.assertGreaterEqual(int(d["event_count_up"].iloc[12]), 4)
        self.assertEqual(int(d["event_count_dn"].iloc[12]), 0)
        self.assertTrue(bool(d["severe_count"].iloc[12]))

    def test_dev3_ratio_and_near_abnormal(self):
        days = _days(6)
        idx = pd.Series([100] * 6, index=days)
        turn = pd.Series([0.01] * 6, index=days)
        # 3 日 +18%：比例 0.9 → 未触发但预警
        close = pd.Series([100, 100, 100, 118, 118, 118], index=days)
        d = abnormal_detail(close, idx, turn)
        self.assertAlmostEqual(float(d["dev3_ratio"].iloc[3]), 0.9, places=5)
        self.assertFalse(bool(d["is_abnormal"].iloc[3]))
        self.assertTrue(bool(d["near_abnormal"].iloc[3]))
        # +12%：比例 0.6 → 不预警
        close2 = pd.Series([100, 100, 100, 112, 112, 112], index=days)
        d2 = abnormal_detail(close2, idx, turn)
        self.assertAlmostEqual(float(d2["dev3_ratio"].iloc[3]), 0.6, places=5)
        self.assertFalse(bool(d2["near_abnormal"].iloc[3]))
        # +22%：已触发 → 不算「将触发」
        close3 = pd.Series([100, 100, 100, 122, 122, 122], index=days)
        d3 = abnormal_detail(close3, idx, turn)
        self.assertTrue(bool(d3["is_abnormal"].iloc[3]))
        self.assertFalse(bool(d3["near_abnormal"].iloc[3]))

    def test_warning_fields_lag_shift(self):
        days = _days(8)
        idx = pd.Series([100] * 8, index=days)
        turn = pd.Series([0.01] * 8, index=days)
        close = pd.Series([100, 100, 100, 118, 118, 118, 118, 118], index=days)
        d0 = abnormal_detail(close, idx, turn)
        d1 = abnormal_detail(close, idx, turn, lag=1)
        self.assertFalse(bool(d1["near_abnormal"].iloc[3]))
        self.assertTrue(bool(d1["near_abnormal"].iloc[4]))
        self.assertEqual(str(d1["event_count_up"].dtype), "int32")


class TestLagAndValid(unittest.TestCase):
    def test_lag_is_no_lookahead(self):
        """lag=1 时 state[t] = 原 state[t-1]（截至昨日收盘），不会用到今日。"""
        days = _days(8)
        idx = pd.Series([100] * 8, index=days)
        turn = pd.Series([0.01] * 8, index=days)
        close = pd.Series([100] * 3 + [130] * 5, index=days)
        d0 = abnormal_detail(close, idx, turn, lag=0)
        d1 = abnormal_detail(close, idx, turn, lag=1)
        # 今日(t=3)异动，lag=1 下推进到 t=4 才可见
        self.assertTrue(bool(d0["is_abnormal"].iloc[3]))
        self.assertFalse(bool(d1["is_abnormal"].iloc[3]))
        self.assertTrue(bool(d1["is_abnormal"].iloc[4]))

    def test_missing_index_not_misjudged(self):
        """整条指数取不到 → valid=False、is_abnormal=False（判不出来 ≠ 没异动）。

        注意：单个交易日指数缺值会被 align_to_days 前向填充（指数偶尔缺一天属正常
        兜底），所以要测「判不出来」必须让整条指数都取不到。
        """
        days = _days(6)
        idx = pd.Series([np.nan] * 6, index=days)
        turn = pd.Series([0.01] * 6, index=days)
        close = pd.Series([100, 100, 100, 130, 130, 130], index=days)
        d = abnormal_detail(close, idx, turn)
        self.assertFalse(bool(d["valid"].iloc[3]))
        self.assertFalse(bool(d["is_abnormal"].iloc[3]))

    def test_abnormal_state_shortcut(self):
        days = _days(8)
        idx = pd.Series([100] * 8, index=days)
        turn = pd.Series([0.01] * 8, index=days)
        close = pd.Series([100] * 3 + [130] * 5, index=days)
        arr = abnormal_state(close, idx, turn)
        self.assertIsInstance(arr, np.ndarray)
        self.assertEqual(arr.dtype, bool)
        self.assertTrue(bool(arr[3]))

    def test_empty_input(self):
        d = abnormal_detail(pd.Series(dtype=float), pd.Series(dtype=float), pd.Series(dtype=float))
        self.assertTrue(d.empty)


class TestBenchmarkIndex(unittest.TestCase):
    def test_board_to_index_mapping(self):
        """沪深主板必须分开；科创板用科创50 而非科创综指。"""
        from core.indices import benchmark_index_for
        self.assertEqual(benchmark_index_for("600180"), "sh000002")
        self.assertEqual(benchmark_index_for("002685"), "sz399107")
        self.assertEqual(benchmark_index_for("300949"), "sz399102")
        self.assertEqual(benchmark_index_for("688066"), "sh000688")
        self.assertEqual(benchmark_index_for("688613"), "sh000688")


class TestAbnormalFilter(unittest.TestCase):
    """回测执行层「买入时滤除异动」判定入口：分类型、判不出来放行、无前视。"""

    def test_filter_types(self):
        # 价格异动单独测（换手恒定不触发）
        days = _days(10)
        idx = pd.Series([100] * 10, index=days)
        close = pd.Series([100, 110, 121, 133.1] + [133.1] * 6, index=days)
        d = abnormal_detail(close, idx, pd.Series([0.01] * 10, index=days), lag=1)
        price, _ = abnormal_block(d, [FILTER_PRICE])
        turnover, _ = abnormal_block(d, [FILTER_TURNOVER])
        self.assertTrue(bool(price[4]))          # t=3 异动，lag=1 推进到 t=4
        self.assertFalse(bool(turnover[4]))      # 换手恒定，换手类型不拦

        # 换手异动单独测（价格恒定不触发）：t=8 起 3日均 10% / 前5日均 0.1%
        days2 = _days(12)
        idx2 = pd.Series([100] * 12, index=days2)
        close2 = pd.Series([100] * 12, index=days2)
        turn2 = pd.Series([0.001] * 8 + [0.10] * 4, index=days2)
        d2 = abnormal_detail(close2, idx2, turn2, lag=1)
        turnover2, _ = abnormal_block(d2, [FILTER_TURNOVER])
        price2, _ = abnormal_block(d2, [FILTER_PRICE])
        self.assertTrue(bool(turnover2[10]))     # t=9 换手异动，lag=1 推进到 t=10
        self.assertFalse(bool(price2[10]))       # 价格恒定，价格类型不拦

    def test_undecidable_passes_through(self):
        """判不出来（valid=False）必须放行：blocked=False、undecidable=True。"""
        days = _days(6)
        idx = pd.Series([np.nan] * 6, index=days)
        close = pd.Series([100] * 3 + [130] * 3, index=days)
        turn = pd.Series([0.01] * 6, index=days)
        d = abnormal_detail(close, idx, turn, lag=1)
        blocked, undecidable = abnormal_block(d, [FILTER_PRICE, FILTER_NEAR_PRICE])
        self.assertFalse(bool(blocked[-1]))
        self.assertTrue(bool(undecidable[-1]))

    def test_near_price_and_near_severe_are_independent(self):
        """「即将触发」两档必须独立：near_price 只认 20/30% 价格临界，
        near_severe 只认 10/30 日严重异动临界，互不串台。"""
        days = _days(8)
        # 用最简 detail（valid 全 True），只置需要判定的列
        d = pd.DataFrame({"valid": [True] * 8}, index=days)
        for c in ("abnormal_price", "abnormal_turnover", "is_abnormal",
                  "is_severe", "near_abnormal", "near_severe"):
            d[c] = False
        d.loc[days[3], "near_abnormal"] = True
        d.loc[days[5], "near_severe"] = True

        p, _ = abnormal_block(d, [FILTER_NEAR_PRICE])
        s, _ = abnormal_block(d, [FILTER_NEAR_SEVERE])
        self.assertTrue(bool(p[3]) and not bool(p[5]))   # 价格临界只拦 t=3
        self.assertTrue(bool(s[5]) and not bool(s[3]))   # 严重临界只拦 t=5
        both, _ = abnormal_block(d, [FILTER_NEAR_PRICE, FILTER_NEAR_SEVERE])
        self.assertTrue(bool(both[3]) and bool(both[5]))

    def test_no_filter_blocks_nothing(self):
        days = _days(8)
        d = abnormal_detail(pd.Series([100] * 3 + [130] * 5, index=days),
                            pd.Series([100] * 8, index=days),
                            pd.Series([0.01] * 8, index=days), lag=1)
        blocked, _ = abnormal_block(d, [])
        self.assertFalse(blocked.any())

    def test_lag1_no_lookahead(self):
        """lag=1：t=3 的异动在 t=4 才拦；t=3 买入不拦。"""
        days = _days(10)
        idx = pd.Series([100] * 10, index=days)
        close = pd.Series([100, 110, 121, 133.1] + [133.1] * 6, index=days)
        turn = pd.Series([0.01] * 10, index=days)
        d = abnormal_detail(close, idx, turn, lag=1)
        blocked, _ = abnormal_block(d, [FILTER_PRICE])
        self.assertFalse(bool(blocked[3]))
        self.assertTrue(bool(blocked[4]))

    def test_unknown_type_rejected(self):
        days = _days(5)
        d = abnormal_detail(pd.Series([100.] * 5, index=days),
                            pd.Series([100.] * 5, index=days),
                            pd.Series([0.01] * 5, index=days))
        with self.assertRaises(ValueError):
            abnormal_block(d, ["bogus"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
