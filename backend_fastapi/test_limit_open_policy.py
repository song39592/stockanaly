# -*- coding: utf-8 -*-
"""「涨停开盘应对」三档（keep / cancel / reseal）的单元测试。

口径在 `core/limit_price.py`，引擎接线在 `strategies/core/stats.py`
（严格成交模型段）。本文件钉住四件事：

  1. **口径层**：三档在四种当日形态下的结论
     （涨停开盘+炸板+没封回 / 涨停开盘+炸板+封回 / 一字板 / 没涨停开盘）。
  2. **引擎层**：撤单 → **建仓不发生**、该份额度当日就是**现金**、**不重分配**给其他票。
  3. **归因分开**：一字板是**物理买不进**（buy_blocked），撤单是**策略主动放弃**
     （limit_open.canceled_builds）—— 两者的计数不能混。
  4. **回归保护**：默认（不传 policy = keep）时净值与改动前**逐位相同**。

⚠️ 引擎的建仓日 = **信号日**（`dw[t] = W[t] - W[t-1]`，按收盘价成交、隔日承担收益），
故「涨停开盘」判定落在**建仓日当天**——与既有的「一字涨停买不进」同一建仓日、
同一份 raw 数据（口径同源）。
"""
from __future__ import annotations

import contextlib
import os
import sys
import unittest
import warnings

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from strategies.core import stats                                # noqa: E402
from core import limit_price                                     # noqa: E402


@contextlib.contextmanager
def _ignore_drop_guard():
    """静音「持仓数骤降」守卫告警。

    本测试的仓位序列是**刻意清仓**（末位 0），不是漏发信号 →
    引擎的守卫会照例告警，属预期噪声，不该混进单测输出。
    """
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=".*骤降.*", category=RuntimeWarning)
        yield

DATES = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05",
         "2024-01-08", "2024-01-09"]
CODE = "AAA"
CODE2 = "BBB"


def _mk(closes, raws, code=CODE):
    """closes: hfq 收盘价序列；raws: [(open, high, low, close), ...]（未复权）。"""
    idx = pd.Index(DATES)
    close = pd.Series(closes, index=idx, dtype=float)
    raw = pd.DataFrame(
        {
            "open": [r[0] for r in raws],
            "high": [r[1] for r in raws],
            "low": [r[2] for r in raws],
            "close": [r[3] for r in raws],
            "volume": [1000.0] * len(DATES),
        },
        index=idx,
    )
    return close, raw


# 昨收 10.00 → 涨停价 11.00
#   d1 基准 10.00；d2 涨停开盘 + 炸板 + **收盘没封回**（真炸板）
BROKEN = [(10.00, 10.00, 10.00, 10.00),
          (11.00, 11.00, 10.50, 10.60),
          (10.60, 10.70, 10.50, 10.65),
          (10.65, 10.75, 10.60, 10.70),
          (10.70, 10.80, 10.65, 10.75),
          (10.75, 10.85, 10.70, 10.80)]
#   d1 基准；d2 涨停开盘 + 炸板 + **收盘封回**（回封）
RESEAL = [(10.00, 10.00, 10.00, 10.00),
          (11.00, 11.00, 10.50, 11.00),
          (11.00, 11.20, 10.90, 11.10),
          (11.10, 11.30, 11.00, 11.20),
          (11.20, 11.40, 11.10, 11.30),
          (11.30, 11.50, 11.20, 11.40)]
#   d1 基准；d2 一字板（开盘=最高=最低=收盘=涨停价，全天没开板 → 物理买不进）
ONE_WORD = [(10.00, 10.00, 10.00, 10.00),
            (11.00, 11.00, 11.00, 11.00),
            (11.00, 11.20, 10.90, 11.10),
            (11.10, 11.30, 11.00, 11.20),
            (11.20, 11.40, 11.10, 11.30),
            (11.30, 11.50, 11.20, 11.40)]
#   d1 基准；d2 普通高开（没到涨停价）→ 与本条件无关
PLAIN = [(10.00, 10.00, 10.00, 10.00),
         (10.50, 10.80, 10.40, 10.70),
         (10.70, 10.90, 10.60, 10.80),
         (10.80, 11.00, 10.70, 10.90),
         (10.90, 11.10, 10.80, 11.00),
         (11.00, 11.20, 10.90, 11.10)]


def _run(raws, closes=None, positions=None, policy=None, code=CODE):
    if closes is None:
        closes = [r[3] for r in raws]
    if positions is None:
        # d2 建仓（权重变正），d5 清零 → 覆盖「建仓 + 持有 + 卖出」
        positions = [0.0, 1.0, 1.0, 1.0, 1.0, 0.0]
    close, raw = _mk(closes, raws, code)
    kw = {}
    if policy is not None:
        kw["limit_open_policy"] = policy
    with _ignore_drop_guard():
        return stats.compute(
            {code: pd.Series(positions, index=pd.Index(DATES), dtype=float)},
            {code: close},
            initial_capital=100000.0,
            commission=0.0, stamp_duty=0.0, slippage=0.0, min_commission=0.0,
            raw_by_code={code: raw},
            **kw,
        )


class TestLimitOpenAction(unittest.TestCase):
    """口径层：四种当日形态 × 三档。"""

    def test_not_limit_open_is_na(self):
        """没涨停开盘 → 与本条件无关（三档都一样）。"""
        for p in limit_price.POLICIES:
            act = limit_price.limit_open_action(10.50, 10.80, 10.40, 10.70,
                                                10.00, 0.10, p)
            self.assertEqual(act, limit_price.ACT_NA, p)

    def test_cancel_always_cancels_limit_open(self):
        """撤单：涨停开盘（炸板/回封都算）一律不建仓。"""
        for o, h, lo, c in ((11.00, 11.00, 10.50, 10.60),     # 炸板没封回
                            (11.00, 11.00, 10.50, 11.00)):    # 炸板后封回
            act = limit_price.limit_open_action(o, h, lo, c, 10.00, 0.10,
                                                limit_price.POLICY_CANCEL)
            self.assertEqual(act, limit_price.ACT_CANCEL)

    def test_keep_always_buys_limit_open(self):
        """不撤单：涨停开盘照常建仓（一字板仍由引擎的物理拦截处理）。"""
        act = limit_price.limit_open_action(11.00, 11.00, 10.50, 10.60,
                                            10.00, 0.10, limit_price.POLICY_KEEP)
        self.assertEqual(act, limit_price.ACT_BUY)

    def test_reseal_needs_close_sealed(self):
        """涨停条件单：炸板后**收盘仍封住涨停价**才进；没封住 → 撤单。"""
        act = limit_price.limit_open_action(11.00, 11.00, 10.50, 11.00,
                                            10.00, 0.10, limit_price.POLICY_RESEAL)
        self.assertEqual(act, limit_price.ACT_BUY, "收盘封住 → 应建仓")
        act = limit_price.limit_open_action(11.00, 11.00, 10.50, 10.60,
                                            10.00, 0.10, limit_price.POLICY_RESEAL)
        self.assertEqual(act, limit_price.ACT_CANCEL, "收盘没封住 → 应撤单")

    def test_one_word_board_is_not_a_cancel(self):
        """一字板不是「策略撤单」→ 返回 buy（物理拦截归 buy_blocked）。"""
        act = limit_price.limit_open_action(11.00, 11.00, 11.00, 11.00,
                                            10.00, 0.10, limit_price.POLICY_CANCEL)
        self.assertEqual(act, limit_price.ACT_BUY)


class TestEnginePolicy(unittest.TestCase):
    """引擎层：撤单 → 建仓不发生、该份额度当日为现金、不重分配。"""

    def test_cancel_blocks_the_build(self):
        """撤单：d2 的建仓没发生（引擎次日自动重试 → d3 才建成），并单独计数。"""
        r = _run(BROKEN, policy=limit_price.POLICY_CANCEL)
        self.assertEqual(r["limit_open"]["canceled_builds"], 1)
        self.assertEqual(r["fill_blocked"]["buy_blocked"], 0,
                         "一字板没发生，不该记成物理拦截")
        self.assertEqual(r["limit_open"]["policy"], limit_price.POLICY_CANCEL)
        # 明细单独给一组（reason=limit_open_cancel），与一字板分开
        reasons = [f.get("reason") for f in r["build_failures"]]
        self.assertEqual(reasons, ["limit_open_cancel"])
        self.assertEqual(r["build_failures"][0]["date"], DATES[1],
                         "撤单发生在建仓日当天")
        # 撤单当天的建仓**没有成交** → 成交发生在**次日**（引擎的每日重试：
        # 目标权重仍在、次日不再涨停开盘 → 自动补上），绝不在 d2 成交
        self.assertEqual([t["buy_date"] for t in r["trades"]], [DATES[2]])
        self.assertEqual([t["code"] for t in r["trades"]], [CODE])

    def test_keep_is_the_default_and_unchanged(self):
        """默认（不传 policy）= keep，且与显式 keep **逐位相同**（回归保护）。"""
        a = _run(BROKEN)
        b = _run(BROKEN, policy=limit_price.POLICY_KEEP)
        self.assertEqual(a["equity"], b["equity"])
        self.assertEqual(a["limit_open"]["canceled_builds"], 0)
        # keep：d2 建成 → d3 起有收益
        self.assertGreater(b["metrics"]["total_return"], 0.0)

    def test_cancel_defers_the_return(self):
        """撤单比 keep 少赚：撤单那一笔根本没买 → 收益往后挪。"""
        keep = _run(BROKEN, policy=limit_price.POLICY_KEEP)
        cancel = _run(BROKEN, policy=limit_price.POLICY_CANCEL)
        self.assertLess(cancel["metrics"]["total_return"],
                        keep["metrics"]["total_return"])

    def test_reseal_buys_the_sealed_one(self):
        """涨停条件单：收盘封住 → 建仓（与 keep 同）；没封住 → 撤单。"""
        sealed = _run(RESEAL, policy=limit_price.POLICY_RESEAL)
        self.assertEqual(sealed["limit_open"]["canceled_builds"], 0)
        self.assertGreater(sealed["metrics"]["total_return"], 0.0)
        broken = _run(BROKEN, policy=limit_price.POLICY_RESEAL)
        self.assertEqual(broken["limit_open"]["canceled_builds"], 1)

    def test_one_word_board_stays_a_physical_block(self):
        """一字板 → 归**物理拦截**（buy_blocked），不是策略撤单（归因分开）。"""
        for p in limit_price.POLICIES:
            r = _run(ONE_WORD, policy=p)
            self.assertEqual(r["fill_blocked"]["buy_blocked"], 1, p)
            self.assertEqual(r["limit_open"]["canceled_builds"], 0, p)

    def test_plain_day_never_touched(self):
        """没涨停开盘 → 三档都不撤单，净值逐位相同。"""
        eq = _run(PLAIN, policy=limit_price.POLICY_KEEP)["equity"]
        for p in limit_price.POLICIES:
            self.assertEqual(_run(PLAIN, policy=p)["equity"], eq, p)

    def test_cancel_does_not_redistribute_to_others(self):
        """撤单 → 该份额度当日是**现金**、**不重分配**给其他票（Σw 不变）。"""
        idx = pd.Index(DATES)
        # AAA 在 d2 涨停开盘（撤单）；BBB 正常 → 两票各 0.5，AAA 撤单后 Σw = 0.5
        closes_a = [r[3] for r in BROKEN]
        closes_b = [10.00, 10.20, 10.30, 10.40, 10.50, 10.60]
        raws_b = [(c, c, c, c) for c in closes_b]
        close_a, raw_a = _mk(closes_a, BROKEN)
        close_b, raw_b = _mk(closes_b, raws_b, CODE2)
        pos = [0.0, 0.5, 0.5, 0.5, 0.5, 0.0]
        kw = dict(
            initial_capital=100000.0,
            commission=0.0, stamp_duty=0.0, slippage=0.0, min_commission=0.0,
        )
        with _ignore_drop_guard():
            r = stats.compute(
                {CODE: pd.Series(pos, index=idx, dtype=float),
                 CODE2: pd.Series(pos, index=idx, dtype=float)},
                {CODE: close_a, CODE2: close_b},
                raw_by_code={CODE: raw_a, CODE2: raw_b},
                limit_open_policy=limit_price.POLICY_CANCEL,
                **kw,
            )
        self.assertEqual(r["limit_open"]["canceled_builds"], 1)
        # BBB 在 d2 正常建成（权重 0.5 → 50000）；若发生重分配会被抬到 1.0（100000）
        bbb = [t for t in r["trades"] if t["code"] == CODE2]
        self.assertEqual(len(bbb), 1)
        self.assertEqual(bbb[0]["buy_date"], DATES[1])
        self.assertAlmostEqual(bbb[0]["buy_notional"], 50000.0, places=4,
                               msg="BBB 权重被抬高 = 撤单的额度被重分配了")
        # AAA：撤单当天没成交，次日重试才建成（权重仍是 0.5，不是 1.0）
        aaa = [t for t in r["trades"] if t["code"] == CODE]
        self.assertEqual(len(aaa), 1)
        self.assertEqual(aaa[0]["buy_date"], DATES[2])
        self.assertLess(aaa[0]["buy_notional"], 60000.0,
                        "AAA 的建仓权重不该被抬到 1.0")


if __name__ == "__main__":
    unittest.main()
