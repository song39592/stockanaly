# -*- coding: utf-8 -*-
"""单股仓位上限（回测范围级设置）的单元测试。

口径：
  * **默认不限**（不传 / None）→ 行为与改动前**逐位一致**（回归防线）；
  * 上限语义：策略给出的目标权重逐票取 min(权重, 上限) —— 只变小、绝不变大
    （故 Σweight ≤ 1 的硬约束不可能被破坏）；
  * 截断必须**留痕**：metrics 给出被压低的票数与「日×票」数，flags 给出人读说明
    （静默改权重 = 静默改数据）；
  * 路由层折算：占比 与 金额 两个入口**取更严者**，金额按**初始资金**折算成占比。
"""
from __future__ import annotations

import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from strategies.core import stats                      # noqa: E402
from strategies_routes import _resolve_position_cap    # noqa: E402

DATES = ["2024-01-02", "2024-01-03", "2024-01-04"]


def _pos(weights):
    return pd.Series(weights, index=pd.Index(DATES), dtype=float)


def _close(prices):
    return pd.Series(prices, index=pd.Index(DATES), dtype=float)


def _compute(positions, closes, **kw):
    """零成本口径（commission/stamp/slippage 全 0）→ 净值可精确断言。"""
    params = dict(initial_capital=100000.0, commission=0.0, stamp_duty=0.0,
                  slippage=0.0, min_commission=0.0)
    params.update(kw)
    return stats.compute(positions, closes, **params)


class TestPositionCap(unittest.TestCase):
    # ---------- 1. 默认不限 → 逐位不变 ----------
    def test_default_cap_is_identical(self):
        positions = {"AAA": _pos([0.5, 0.5, 0.5])}
        closes = {"AAA": _close([10.0, 11.0, 11.0])}
        a = _compute(positions, closes)
        b = _compute(positions, closes, position_cap=None)
        self.assertEqual(a["equity"], b["equity"])
        self.assertEqual(a["metrics"], b["metrics"])
        self.assertIsNone(a["metrics"]["position_cap"])
        self.assertEqual(a["metrics"]["position_capped_codes"], 0)
        self.assertEqual(a["metrics"]["position_capped_points"], 0)

    # ---------- 2. 上限真的压低了权重 ----------
    def test_cap_truncates_weight(self):
        positions = {"AAA": _pos([0.5, 0.5, 0.5])}
        closes = {"AAA": _close([10.0, 11.0, 11.0])}
        out = _compute(positions, closes, position_cap=0.2)
        # 0.5 → 0.2：净值 = 100000 * (1 + 0.2 * (11/10-1)) = 102000
        self.assertAlmostEqual(out["equity"][-1], 102000.0, places=2)
        self.assertAlmostEqual(out["metrics"]["position_cap"], 0.2, places=6)
        self.assertEqual(out["metrics"]["position_capped_codes"], 1)
        self.assertEqual(out["metrics"]["position_capped_points"], 3)
        flags = out["flags"]
        self.assertTrue(any(f.startswith("position_cap:") for f in flags),
                        "缺 position_cap 说明：%s" % flags)

    def test_cap_above_weight_leaves_it_untouched(self):
        positions = {"AAA": _pos([0.05, 0.05, 0.05])}
        closes = {"AAA": _close([10.0, 11.0, 11.0])}
        out = _compute(positions, closes, position_cap=0.2)
        # 0.05 不动：净值 = 100000 * (1 + 0.05 * 0.1)
        self.assertAlmostEqual(out["equity"][-1], 100500.0, places=2)
        self.assertEqual(out["metrics"]["position_capped_codes"], 0)
        self.assertEqual(out["metrics"]["position_capped_points"], 0)
        self.assertFalse(any(f.startswith("position_cap:") for f in out["flags"]))

    # ---------- 3. 只变小、绝不变大 ----------
    def test_cap_never_increases_weights(self):
        positions = {"AAA": _pos([0.5, 0.5, 0.5]), "BBB": _pos([0.5, 0.5, 0.5])}
        closes = {"AAA": _close([10.0, 10.0, 10.0]), "BBB": _close([10.0, 10.0, 10.0])}
        out = _compute(positions, closes, position_cap=0.9)
        # 上限 0.9 大于原权重 0.5 → 不动，两票各 0.5 → 利用率 1.0
        self.assertEqual(out["metrics"]["position_capped_codes"], 0)
        self.assertAlmostEqual(out["metrics"]["capital_utilization"], 1.0, places=6)

    def test_cap_applies_per_code(self):
        positions = {"AAA": _pos([0.5, 0.5, 0.5]), "BBB": _pos([0.1, 0.1, 0.1])}
        closes = {"AAA": _close([10.0, 11.0, 11.0]), "BBB": _close([10.0, 10.0, 10.0])}
        out = _compute(positions, closes, position_cap=0.2)
        # 只有 AAA 被压低（0.5→0.2），BBB(0.1) 不动
        self.assertEqual(out["metrics"]["position_capped_codes"], 1)
        self.assertEqual(out["metrics"]["position_capped_points"], 3)
        self.assertAlmostEqual(out["equity"][-1], 100000.0 * 1.02, places=2)

    # ---------- 4. 路由层折算：两个入口取更严者 ----------
    def test_resolve_position_cap_takes_stricter(self):
        # 都没给 → 不限
        self.assertIsNone(_resolve_position_cap(None, None, 100000.0))
        # 0 视为「没设」（不是「上限 0」）
        self.assertIsNone(_resolve_position_cap(0.0, None, 100000.0))
        self.assertIsNone(_resolve_position_cap(None, 0.0, 100000.0))
        # 只给占比
        self.assertAlmostEqual(_resolve_position_cap(0.1, None, 100000.0), 0.1)
        # 只给金额 → 按初始资金折算：5000 / 100000 = 5%
        self.assertAlmostEqual(_resolve_position_cap(None, 5000.0, 100000.0), 0.05)
        # 两个都给 → 取更严：占比 10% vs 金额 5% → 5%
        self.assertAlmostEqual(_resolve_position_cap(0.1, 5000.0, 100000.0), 0.05)
        # 两个都给 → 取更严：占比 30% vs 金额 50% → 30%
        self.assertAlmostEqual(_resolve_position_cap(0.3, 50000.0, 100000.0), 0.3)
        # 超过 100% → 夹到 1
        self.assertAlmostEqual(_resolve_position_cap(2.0, None, 100000.0), 1.0)

    # ---------- 5. 标量口径 与 向量化口径 必须一致 ----------
    def test_cap_weight_semantics(self):
        self.assertEqual(stats.cap_weight(0.5, None), 0.5)
        self.assertEqual(stats.cap_weight(0.5, 0.0), 0.5)     # 0 = 不限
        self.assertAlmostEqual(stats.cap_weight(0.5, 0.2), 0.2)   # 压低
        self.assertAlmostEqual(stats.cap_weight(0.1, 0.2), 0.1)   # 只压低、绝不抬高
        self.assertAlmostEqual(stats.cap_weight(0.0, 0.2), 0.0)

    def test_cap_weight_matches_vectorized(self):
        """`cap_weight`（推荐页用）与 compute 的向量化版必须给出同一个结果。

        两边漂移的后果：回测净值按一套仓位算、推荐页金额按另一套算 →
        界面显示的买入金额与回测跑出来的仓位对不上（静默失真）。
        """
        import numpy as np
        vals = [0.0, 0.01, 0.05, 0.1, 0.2, 0.5, 1.0]
        for cap in (0.05, 0.1, 0.2, 0.5):
            arr = np.array(vals, dtype=np.float32)
            over = arr > np.float32(cap) * (1.0 + stats._CAP_TOL)
            vec = np.where(over, np.float32(cap), arr)
            for v, want in zip(vals, vec.tolist()):
                self.assertAlmostEqual(stats.cap_weight(v, cap), float(want), places=6)


if __name__ == "__main__":
    unittest.main()

