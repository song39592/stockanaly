# -*- coding: utf-8 -*-
"""筹码矩阵**磁盘缓存**的增量判定口径（回归测试）。

背景（真实事故）：回测结束日常填成今天/周末，而本地最后一根 K 线在其之前。
`compute_matrix(end=...)` 曾把「请求的 end 晚于本地最后交易日」误判成
「数据推进了」→ 走增量续算 → 增量取不到新 K 线 → 抛「行情数据不足」
→ 调用方（策略）把整只票**静默丢掉** → 同一区间不同时间跑，结果完全不同
（实测：KL 入池策略只交易到 9 只票，且每次跑的票还不一样）。

覆盖三件事：
  1. `_target_end`：判定基准取 min(请求 end, 本地最后交易日)；
  2. 缓存已追到本地最新时**不走增量**（直接用缓存切片）；
  3. 增量取不到新 K 线时**回退到缓存**（不抛错、不丢整只票）。
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from chipformulas.core import base as chip_base  # noqa: E402


def _fake_stored(last_date: str = "2026-11-13") -> dict:
    """构造一份最小的「已持久化的全历史矩阵」缓存条目（结构对齐 _stored_from_res）。"""
    dates = ["2026-11-11", "2026-11-12", "2026-11-13"]
    n = len(dates)
    return {
        "last_date": last_date,
        "dates": list(dates),
        "pct": np.full((n, 2), 50.0),
        "close": np.array([10.0, 10.2, 10.4]),
        "turnover": np.full((n, 2), 0.01),
        "centers": np.array([9.5, 10.5]),
        "edges": np.array([9.0, 10.0, 11.0]),
        "gray": None,
        "red": None,
        "terminal_chips": np.array([0.5, 0.5]),
        "meta": {"float_shares": 1e8, "warm": 250, "adjust": "qfq", "period": "day",
                 "formula_id": "tri_decay", "formula_name": "三段衰减",
                 "params": {}, "lockup_ratio": 0.0, "lockup_factor": 1.0},
    }


class TestTargetEnd(unittest.TestCase):
    """「缓存够用」的判定基准。"""

    def test_end_after_data_last_uses_data_last(self):
        # 请求到周末，本地只有到周五 → 判定基准取周五
        self.assertEqual(chip_base._target_end("2026-11-15", "2026-11-13"), "2026-11-13")

    def test_end_before_data_last_keeps_end(self):
        self.assertEqual(chip_base._target_end("2026-11-01", "2026-11-13"), "2026-11-01")

    def test_end_none_uses_data_last(self):
        self.assertEqual(chip_base._target_end(None, "2026-11-13"), "2026-11-13")

    def test_no_data_last_keeps_end(self):
        self.assertEqual(chip_base._target_end("2026-11-15", None), "2026-11-15")


class TestIncrementalDecision(unittest.TestCase):
    """缓存已最新 → 走缓存；增量取不到新数据 → 回退缓存。"""

    def test_cache_up_to_date_with_later_end_uses_cache(self):
        """缓存已追到本地最新、请求 end 更晚 → 直接用缓存，**不走增量**。"""
        stored = _fake_stored("2026-11-13")
        with patch.object(chip_base.chip_matrix_store, "load", return_value=stored), \
             patch.object(chip_base, "_latest_data_date", return_value="2026-11-13"), \
             patch.object(chip_base, "_build_incremental",
                          side_effect=AssertionError("不该走增量续算")), \
             patch.object(chip_base, "_build_matrix",
                          side_effect=AssertionError("不该全量重建")):
            res = chip_base._incremental_or_build(
                "000001", "tri_decay", "", None, "2026-11-15", "qfq", None, 80, "day", True)
        self.assertEqual(res.dates[-1], "2026-11-13")
        self.assertEqual(len(res.dates), 3)

    def test_incremental_without_new_bars_falls_back_to_cache(self):
        """缓存末日比本地最新少一天 → 走增量；增量取不到新 K 线 → 回退缓存。"""
        stored = _fake_stored("2026-11-12")
        with patch.object(chip_base.chip_matrix_store, "load", return_value=stored), \
             patch.object(chip_base, "_latest_data_date", return_value="2026-11-13"), \
             patch.object(chip_base, "_build_incremental",
                          side_effect=chip_base._NeedsNewData("行情数据不足: 000001")):
            res = chip_base._incremental_or_build(
                "000001", "tri_decay", "", None, "2026-11-15", "qfq", None, 80, "day", True)
        self.assertEqual(res.dates[-1], "2026-11-13")
        self.assertEqual(len(res.dates), 3)


class TestBuildIncrementalGuard(unittest.TestCase):
    """增量取数失败时的异常分类：只把「没有新数据」转成 _NeedsNewData。"""

    def test_insufficient_new_bars_becomes_needs_new_data(self):
        stored = _fake_stored("2026-11-13")
        with patch.object(chip_base.data, "load_input",
                          side_effect=RuntimeError("行情数据不足: 000001 (adjust=qfq)")):
            with self.assertRaises(chip_base._NeedsNewData):
                chip_base._build_incremental(
                    "000001", "tri_decay", "", "qfq", 80, "day", True, stored)

    def test_other_runtime_error_is_not_swallowed(self):
        """别的错误（如缺流通股本）必须照常抛，不能被降级成「没有新数据」。"""
        stored = _fake_stored("2026-11-13")
        with patch.object(chip_base.data, "load_input",
                          side_effect=RuntimeError("缺少流通股本: 000001")):
            with self.assertRaises(RuntimeError) as cm:
                chip_base._build_incremental(
                    "000001", "tri_decay", "", "qfq", 80, "day", True, stored)
        self.assertNotIsInstance(cm.exception, chip_base._NeedsNewData)


if __name__ == "__main__":
    unittest.main()
