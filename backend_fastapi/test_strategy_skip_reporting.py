# -*- coding: utf-8 -*-
"""策略层「跳过标的」的可见性（回归测试）。

背景（真实事故）：策略逐票计算时用 `except Exception: continue` 兜底，
把「因数据不可得跳过的票」**静默丢掉** —— 报告只说「参与统计 N 只」，
读者看不到「另有 M 只没参与」，于是「同一区间不同时间跑出完全不同的结果」
既解释不了、也发现不了（实测：KL 入池策略只交易到 9 只票，每次还不一样）。

覆盖三件事：
  1. 策略能登记跳过（`data.record_skip`），并带原因；
  2. 编排层取走后即清空（不跨任务残留）；
  3. 编排层把跳过记录并进 errors，**汇总条排在首位**（先看总数再看明细）。
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from strategies.core import backtest as bt  # noqa: E402
from strategies.core import data  # noqa: E402


class TestRecordSkip(unittest.TestCase):
    def setUp(self):
        data.drain_skips()          # 清掉其它测试可能留下的残留

    def tearDown(self):
        data.drain_skips()

    def test_record_then_drain(self):
        data.record_skip("000001", "筹码矩阵不可得（RuntimeError: 行情数据不足）")
        out = data.drain_skips()
        self.assertEqual(len(out), 1)
        self.assertIn("000001", out[0])
        self.assertIn("行情数据不足", out[0])

    def test_drain_clears(self):
        """取走即清空：否则下一次运行会捡到上一次的跳过记录。"""
        data.record_skip("000001", "原因 A")
        self.assertEqual(len(data.drain_skips()), 1)
        self.assertEqual(data.drain_skips(), [])


class TestCollectSkips(unittest.TestCase):
    def setUp(self):
        data.drain_skips()          # 清掉别的测试（含导入期冒烟）留下的残留

    def tearDown(self):
        data.drain_skips()

    def test_summary_comes_first(self):
        data.record_skip("000001", "原因 A")
        data.record_skip("000002", "原因 B")
        errors: list[str] = []
        skips = bt._collect_skips(errors)
        self.assertEqual(len(skips), 2)
        self.assertEqual(len(errors), 3)
        self.assertIn("2 只标的", errors[0])      # 汇总条在首位
        self.assertIn("000001", errors[1])
        self.assertIn("000002", errors[2])

    def test_no_skip_leaves_errors_untouched(self):
        errors: list[str] = []
        self.assertEqual(bt._collect_skips(errors), [])
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
