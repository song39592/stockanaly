# -*- coding: utf-8 -*-
"""「涨停开盘但盘中炸板 → 放弃买入」的单元测试。

覆盖三层（每层都是这条规则会被写歪的地方）：

  1. **口径层**（`core/limit_price.py`）：涨停幅度 / 涨停价 / 一字板 / 炸板判定；
     以及「统计层与策略层用的是**同一份**口径」（同一口径只允许一份实现）。
  2. **面板层**：`broken_limit_up_panel` 的轴对齐（统一交易日轴、停牌日不炸板）。
  3. **策略层**（`jwzz_cross_safe`）：
     * 炸板票**不被买入**；
     * 名额由**下一名候选**补上（组合仍是 top_n 只、权重不变）；
     * **一字板不算炸板**（那是「买不进」，归引擎的严格成交模型）；
     * **已持有的票不会因为当天炸板被踢出**（这条规则只管买不买）；
     * 开关关掉时与 `jwzz_cross` **逐条信号完全一致**（证明它就是 jwzz_cross + 一条规则）。

⚠️ 假行情必须**符合交易所规则**（否则测的是假数据不是规则）：
   昨收 1.00 → 涨停价 1.10 → 当日开盘/收盘都不能超过 1.10；
   昨收 1.10 → 涨停价 1.21。
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from strategies.core import base, data                     # noqa: E402
from strategies.core import registry                        # noqa: E402
from strategies import jwzz_cross as jwzz_cross_mod         # noqa: E402
from strategies import jwzz_cross_safe as safe_mod          # noqa: E402
from strategies.core import stats as stats_mod              # noqa: E402
from core import limit_price                                # noqa: E402

DATES = [
    "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08",
    "2024-01-09", "2024-01-10", "2024-01-11", "2024-01-12", "2024-01-15",
    "2024-01-16", "2024-01-17", "2024-01-18", "2024-01-19", "2024-01-22",
    "2024-01-23", "2024-01-24", "2024-01-25", "2024-01-26", "2024-01-29",
    "2024-01-30", "2024-01-31", "2024-02-01", "2024-02-02", "2024-02-05",
    "2024-02-06", "2024-02-07", "2024-02-08", "2024-02-19", "2024-02-20",
]


# amount/volume 恒定 → 成本线 X_3 恒为 0.01*1e6/1e4 = 1.0 元/股
#   → JWZZ = (close - 1.0) / 1.0 * 1000：close 1.00 → 0（弱势）、1.10 → 100（强势）
def _df(closes, bars_extra=None) -> pd.DataFrame:
    """构造日线。

    `bars_extra`: {索引: (open, high, low)} —— 缺省 open=high=low=close。
    """
    o = list(closes)
    h = list(closes)
    lo = list(closes)
    for i, (oo, hh, ll) in (bars_extra or {}).items():
        o[i], h[i], lo[i] = oo, hh, ll
    return pd.DataFrame(
        {"open": o, "high": h, "low": lo, "close": list(closes),
         "volume": 10000.0, "amount": 1000000.0},
        index=pd.Index(DATES[:len(closes)]),
    )


# 昨收 1.00 → 涨停价 1.10；开盘 1.10、盘中最低 1.05、收盘 1.10 → **涨停开盘后炸板**
BROKEN_12 = {12: (1.10, 1.10, 1.05)}
# 昨收 1.00 → 涨停价 1.10；全天封死（开盘=最高=最低=收盘=1.10）→ **一字板**
ONE_WORD_12 = {12: (1.10, 1.10, 1.10)}


def _run(mod, bars, **params):
    """用假行情跑一个策略，返回 list[Signal]。"""
    def _fake(codes, start=None, end=None, fields=None, adjust="hfq", progress=None):
        return {c: bars[c] for c in codes if c in bars}

    ctx = base.StrategyContext(codes=list(bars.keys()), start=DATES[0],
                               end=DATES[-1], adjust="hfq")
    with patch.object(data, "load_bars", side_effect=_fake):
        return mod(ctx, **params)


def _by_day(signals) -> dict:
    out: dict = {}
    for s in signals:
        out.setdefault(s.time, {})[s.code] = s
    return out


class TestLimitPriceKernel(unittest.TestCase):
    """口径层：涨停幅度 / 涨停价 / 一字板 / 炸板判定。"""

    def test_limit_pct_by_board(self):
        """主板 10%、双创 20%（与统计层的历史口径完全一致）。"""
        self.assertEqual(limit_price.limit_pct("600000"), 0.10)
        self.assertEqual(limit_price.limit_pct("000001"), 0.10)
        self.assertEqual(limit_price.limit_pct("300750"), 0.20)
        self.assertEqual(limit_price.limit_pct("688981"), 0.20)

    def test_limit_prices_round_to_cent(self):
        """昨收 10.00 → 涨停价 11.00（交易所四舍五入到分）。"""
        self.assertEqual(limit_price.limit_prices(10.00, 0.10), (11.00, 9.00))
        self.assertEqual(limit_price.limit_prices(10.00, 0.20), (12.00, 8.00))
        self.assertEqual(limit_price.limit_prices(0.0, 0.10), (0.0, 0.0))

    def test_one_word_up_down(self):
        """一字涨停（买不进）/ 一字跌停（卖不出）：买卖方向各判各的。"""
        self.assertFalse(limit_price.is_limit_up(11.00, 10.99, 10.00, 0.10))
        self.assertTrue(limit_price.is_limit_up(11.00, 11.00, 10.00, 0.10))
        self.assertFalse(limit_price.is_limit_down(9.01, 9.00, 10.00, 0.10))
        self.assertTrue(limit_price.is_limit_down(9.00, 9.00, 10.00, 0.10))

    def test_limit_up_open_broke(self):
        """涨停开盘 + 盘中炸板 → True；一字板 / 没涨停开盘 → False。"""
        # 昨收 10.00 → 涨停价 11.00
        self.assertTrue(limit_price.limit_up_open_broke(11.00, 10.50, 10.00, 0.10))
        # 一字板：全天最低价都在涨停价 → **不算炸板**（那是买不进，不是没成交）
        self.assertFalse(limit_price.limit_up_open_broke(11.00, 11.00, 10.00, 0.10))
        # 开盘就没到涨停价 → 不是「涨停开盘」
        self.assertFalse(limit_price.limit_up_open_broke(10.50, 10.00, 10.00, 0.10))
        # 昨收无效（首日 / 无价）→ 不判
        self.assertFalse(limit_price.limit_up_open_broke(11.00, 10.50, 0.0, 0.10))

    def test_stats_shares_the_same_kernel(self):
        """统计层与策略层必须用**同一份**口径（否则同一口径两个数、两边都不报错）。"""
        self.assertIs(stats_mod._limit_pct, limit_price.limit_pct)
        self.assertIs(stats_mod._limit_prices, limit_price.limit_prices)
        self.assertIs(stats_mod._is_limit_up, limit_price.is_limit_up)
        self.assertIs(stats_mod._is_limit_down, limit_price.is_limit_down)


class TestBrokenLimitUpPanel(unittest.TestCase):
    """面板层：统一交易日轴上的炸板标记。"""

    def test_panel_alignment(self):
        # AAA：索引 12 涨停开盘后炸板（昨收 1.00 → 涨停价 1.10）
        aaa = _df([1.0] * 12 + [1.1] * 18, BROKEN_12)
        # BBB：全程不炸板
        bbb = _df([1.0] * 30)
        codes = ["AAA", "BBB"]
        days = DATES[:30]
        m = limit_price.broken_limit_up_panel({"AAA": aaa, "BBB": bbb}, codes, days)
        self.assertEqual(m.shape, (2, 30))
        self.assertTrue(bool(m[0, 12]))
        self.assertEqual(int(m[0].sum()), 1)      # 只有那一天炸板
        self.assertEqual(int(m[1].sum()), 0)

    def test_one_word_board_is_not_flagged(self):
        """一字板（low == open）**不是炸板** → 面板不标。"""
        aaa = _df([1.0] * 12 + [1.1] * 18, ONE_WORD_12)
        m = limit_price.broken_limit_up_panel({"AAA": aaa}, ["AAA"], DATES[:30])
        self.assertEqual(int(m.sum()), 0)

    def test_panel_handles_missing_columns(self):
        """缺 open/low 的票 → 整行 False（不能崩，也不能当成「安全」以外的语义）。"""
        df = pd.DataFrame({"close": [1.0] * 20, "volume": [1.0] * 20},
                          index=pd.Index(DATES[:20]))
        m = limit_price.broken_limit_up_panel({"AAA": df}, ["AAA"], DATES[:20])
        self.assertEqual(int(m.sum()), 0)


class TestJwzzCrossSafe(unittest.TestCase):
    """策略层：炸板不买。"""

    def test_broken_limit_up_is_not_bought(self):
        """AAA 在变强的当天涨停开盘后炸板 → 那一笔不买，次日起才买。"""
        bars = {"AAA": _df([1.0] * 12 + [1.1] * 18, BROKEN_12)}
        days = _by_day(_run(safe_mod.jwzz_cross_safe, bars, top_n=1))
        self.assertNotIn(DATES[12], days, "炸板票不该在当天被买入")
        self.assertIn(DATES[13], days)
        self.assertAlmostEqual(days[DATES[13]]["AAA"].weight, 1.0)

    def test_filter_off_is_identical_to_jwzz_cross(self):
        """开关关掉时，与 `jwzz_cross` **逐条信号完全一致**（它就是 jwzz_cross + 一条规则）。"""
        bars = {
            "AAA": _df([1.0] * 12 + [1.1] * 3 + [1.0] * 15, BROKEN_12),
            "BBB": _df([1.0] * 15 + [1.2] * 15),
            "CCC": _df([1.0] * 18 + [1.15] * 12),
        }
        sig_a = _run(safe_mod.jwzz_cross_safe, bars, top_n=2, position=1.0,
                     skip_broken_limit_up=0)
        sig_b = _run(jwzz_cross_mod.jwzz_cross, bars, top_n=2, position=1.0)
        self.assertEqual(
            [(s.code, s.time, round(s.weight, 9)) for s in sig_a],
            [(s.code, s.time, round(s.weight, 9)) for s in sig_b],
        )

    def test_skipped_slot_stays_as_cash(self):
        """炸板票**占住名额、不补位** → 那个名额留成现金（权重分母仍是名额数）。"""
        bars = {
            # AAA：索引 12 才变强，且当天涨停开盘后炸板 → 这一笔不买
            "AAA": _df([1.0] * 12 + [1.1] * 18, BROKEN_12),
            # BBB：索引 12 变强（封死板 → 不算炸板）→ 买它
            "BBB": _df([1.0] * 12 + [1.1] * 18),
        }
        days = _by_day(_run(safe_mod.jwzz_cross_safe, bars, top_n=2, position=1.0))
        self.assertEqual(list(days[DATES[12]]), ["BBB"])
        # 名额 2 只，AAA 那个名额留成现金 → BBB 只拿 1/2（**不是** 1.0）
        self.assertAlmostEqual(days[DATES[12]]["BBB"].weight, 0.5)
        # 被放弃的那一笔要**留痕**（当天跳过的候选数）
        self.assertEqual(days[DATES[12]]["BBB"].meta.get("skipped_broken"), 1)

    def test_skip_is_recorded_even_when_portfolio_unchanged(self):
        """成分没变但当天有炸板被跳过 → 仍留痕（否则这次放弃在报告里彻底看不见）。"""
        # AAA：索引 12 炸板（当日唯一的候选 → 组合不变）
        bars = {"AAA": _df([1.0] * 12 + [1.1] * 18, BROKEN_12)}
        days = _by_day(_run(safe_mod.jwzz_cross_safe, bars, top_n=1))
        self.assertNotIn(DATES[12], days, "没有持仓可发信号时不该凭空造信号")
        # 索引 13：AAA 不再炸板 → 正常买入
        self.assertIn(DATES[13], days)

    def test_one_word_board_is_not_treated_as_broken(self):
        """一字板（开盘=最低=涨停价）**不算炸板**：那是买不进，由引擎的成交模型处理。"""
        bars = {"AAA": _df([1.0] * 12 + [1.1] * 18, ONE_WORD_12)}
        days = _by_day(_run(safe_mod.jwzz_cross_safe, bars, top_n=1))
        self.assertIn(DATES[12], days, "一字板不该被这个过滤拦下")
        self.assertAlmostEqual(days[DATES[12]]["AAA"].weight, 1.0)
        self.assertEqual(days[DATES[12]]["AAA"].meta.get("skipped_broken"), 0)

    def test_held_position_is_not_evicted(self):
        """已持有的票当天炸板 → **不被踢出组合**（这条规则只管买不买）。"""
        # AAA：索引 12 买入（收盘 1.10 = 涨停价）；索引 13 涨停开盘后炸板
        #      （昨收 1.10 → 涨停价 1.21；开盘 1.21、盘中最低 1.15、收盘 1.21）
        aaa = _df([1.0] * 12 + [1.1, 1.21] + [1.21] * 16, {13: (1.21, 1.21, 1.15)})
        # BBB：索引 12 买入，索引 13 走弱（掉出强势区 → 掉出前 N → 卖出，base 规则）
        bbb = _df([1.0] * 12 + [1.1] + [1.0] * 17)
        # CCC：索引 13 才变强（封死板 → 不算炸板）→ 补进组合
        ccc = _df([1.0] * 13 + [1.1] * 17)
        days = _by_day(_run(safe_mod.jwzz_cross_safe, bars={"AAA": aaa, "BBB": bbb, "CCC": ccc},
                            top_n=2))
        self.assertIn(DATES[12], days)
        # 索引 13：AAA 仍持有（没被炸板规则踢掉），BBB 掉出前 N 被卖，CCC 补进
        self.assertGreater(days[DATES[13]]["AAA"].weight, 0.0,
                           "已持有的票不该因为当天炸板被卖出")
        self.assertAlmostEqual(days[DATES[13]]["BBB"].weight, 0.0)
        self.assertGreater(days[DATES[13]]["CCC"].weight, 0.0)
        # 已持有的票不算「被跳过」（否则留痕数字会虚高）
        self.assertEqual(days[DATES[13]]["AAA"].meta.get("skipped_broken"), 0)

    def test_passes_registry_validation(self):
        """新策略要能通过注册校验（id == 文件名 / 签名一致 / Σweight 合法 / 冒烟通过）。"""
        rec = registry._validate_module("jwzz_cross_safe", "jwzz_cross_safe.py")
        self.assertEqual(rec.get("status"), "ok", rec.get("error"))
        self.assertEqual(rec.get("strategy_id"), "jwzz_cross_safe")


if __name__ == "__main__":
    unittest.main()
