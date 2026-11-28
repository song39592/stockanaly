# -*- coding: utf-8 -*-
"""买入价上限（信号日未复权收盘价 > 上限 → 放弃建仓）单测。"""
import unittest

import numpy as np
import pandas as pd

from strategies.core.stats import compute

CODE = "000001"
DATES = ["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]


def _setup(closes, target=None):
    """构造 positions + hfq close + 未复权 raw。high/low 有振幅，避开一字板判定。"""
    close = pd.Series([10.0] * len(DATES), index=DATES)
    if target is None:
        target = [0.0, 1.0, 1.0, 1.0, 1.0]      # t=1 起满仓（信号日 = t-1）
    pos = pd.Series(target, index=DATES)
    raw = pd.DataFrame({
        "open": closes,
        "high": [c * 1.005 if np.isfinite(c) else np.nan for c in closes],
        "low": [c * 0.995 if np.isfinite(c) else np.nan for c in closes],
        "close": closes,
        "volume": [1000] * len(DATES),
    }, index=DATES)
    return pos, close, raw


class TestMaxBuyPrice(unittest.TestCase):
    def test_signal_close_above_limit_skips_buy(self):
        """信号日收盘价 9 元 > 上限 8.7 元 → 建仓被拦（每日重试都拦）。"""
        pos, close, raw = _setup([9.0] * 5)
        out = compute({CODE: pos}, {CODE: close}, raw_by_code={CODE: raw},
                      max_buy_price=8.7)
        st = out["max_buy_price"]
        self.assertEqual(st["limit"], 8.7)
        # t=1..4 每次都是建仓（前日没买成 → 当日仍是建仓），信号日价均 9 元
        self.assertEqual(st["skipped_builds"], 4)
        self.assertEqual(st["skipped_weight"], 4.0)
        fails = [f for f in out["build_failures"]
                 if f.get("reason") == "price_above_max"]
        self.assertEqual(len(fails), 4)
        self.assertEqual(fails[0]["signal_date"], "2026-01-02")
        self.assertEqual(fails[0]["date"], "2026-01-05")
        self.assertEqual(fails[0]["signal_close"], 9.0)
        self.assertEqual(fails[0]["max_price"], 8.7)

    def test_only_blocks_build_not_holding(self):
        """只管买入：首日 8 元放行建仓，之后涨到 8.6 元仍持有，不触发卖出。"""
        pos, close, raw = _setup([8.0, 8.5, 8.6, 8.6, 8.6])
        out = compute({CODE: pos}, {CODE: close}, raw_by_code={CODE: raw},
                      max_buy_price=8.7)
        # 只有 t=1 一次建仓，信号日 t=0 = 8 元 ≤ 上限 → 放行；t=2..4 是持有不是建仓
        self.assertEqual(out["max_buy_price"]["skipped_builds"], 0)
        self.assertEqual(out["max_buy_price"]["skipped_weight"], 0.0)

    def test_rebuild_after_sell_is_blocked(self):
        """卖出后重新建仓，若信号日价超上限仍要拦（不是「买过就永久放行」）。"""
        pos, close, raw = _setup([8.0, 8.5, 9.0, 9.0, 9.0],
                                 [0.0, 1.0, 1.0, 0.0, 1.0])
        out = compute({CODE: pos}, {CODE: close}, raw_by_code={CODE: raw},
                      max_buy_price=8.7)
        # t=4 重新建仓，信号日 t=3 = 9 元 > 8.7 → 拦 1 次
        # （t=3 是卖出，不是建仓，不会被拦）
        self.assertEqual(out["max_buy_price"]["skipped_builds"], 1)
        self.assertEqual(out["build_failures"][-1]["signal_date"], "2026-01-07")

    def test_missing_signal_close_passes_with_count(self):
        """信号日收盘价缺失 → 判不出来放行，显式计数（不静默）。"""
        pos, close, raw = _setup([9.0, np.nan, 9.0, 9.0, 9.0])
        out = compute({CODE: pos}, {CODE: close}, raw_by_code={CODE: raw},
                      max_buy_price=8.7)
        # t=1 信号日 t=0=9 元超限 → 拦；t=2 信号日 t=1 缺失 → 判不出来放行并建仓；
        # t=3/4 已持有不再建仓。所以 1 次拦、1 次判不出来放行。
        self.assertEqual(out["max_buy_price"]["undecidable_passed"], 1)
        self.assertEqual(out["max_buy_price"]["skipped_builds"], 1)

    def test_disabled_by_default(self):
        """未启用时统计恒为 0，与旧回测完全一致。"""
        pos, close, raw = _setup([9.0] * 5)
        out = compute({CODE: pos}, {CODE: close}, raw_by_code={CODE: raw})
        st = out["max_buy_price"]
        self.assertIsNone(st["limit"])
        self.assertEqual(st["skipped_builds"], 0)
        self.assertEqual(st["skipped_weight"], 0.0)
        self.assertEqual(st["undecidable_passed"], 0)
        self.assertEqual([f for f in out["build_failures"]
                          if f.get("reason") == "price_above_max"], [])

    def test_strictly_greater(self):
        """恰好等于上限 → 放行（只拦 >，不拦 =）。"""
        pos, close, raw = _setup([8.7] * 5)
        out = compute({CODE: pos}, {CODE: close}, raw_by_code={CODE: raw},
                      max_buy_price=8.7)
        self.assertEqual(out["max_buy_price"]["skipped_builds"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
