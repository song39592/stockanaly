# -*- coding: utf-8 -*-
"""JWZZ「持有到下穿」策略（`jwzz_hold`）与共享面板（`formulas.jwzz.jwzz_panel`）的单元测试。

覆盖的口径（每条都是策略契约里写死的那条）：

  1. **卖出只有一条**：JWZZ 下穿阈值才卖 —— 排名下滑（但仍强势）**不卖**；
  2. **买入候选**：当日强势 + 当日有行情 + 昨日未持有，按 JWZZ 降序取前 N；
  3. **槽位制**：最多持 `max_slots` 只；槽位满且没有票下穿 → 当天买不进；
  4. **卖出与买入分开一天**：昨日卖出腾出的槽位**当天不能用，次日才补**；
  5. **每日买入只数** `buys_per_day`：设成 2 就是每天买前 2；
  6. **整组重发**：调仓日必须输出全部持仓（少发一条 = 静默清仓）；
  7. **Σweight ≤ 1**：单票仓位**自动 = 1 / 槽位数**（不单独设），满槽恰好 100%；
  8. 面板口径：`bull` 停牌沿用上一有值状态、`dn` 只在有行情的日子产生、
     `bar` 标出当日是否有行情（停牌的票买不进去，不能占买入名额）。
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from strategies.core import base, data                    # noqa: E402
from strategies.core import registry                       # noqa: E402
from strategies import jwzz_hold as jwzz_hold_mod          # noqa: E402
from strategies import jwzz_cross as jwzz_cross_mod        # noqa: E402
from formulas.jwzz import jwzz_panel                      # noqa: E402

DATES = [
    "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08",
    "2024-01-09", "2024-01-10", "2024-01-11", "2024-01-12", "2024-01-15",
    "2024-01-16", "2024-01-17", "2024-01-18", "2024-01-19", "2024-01-22",
    "2024-01-23", "2024-01-24", "2024-01-25", "2024-01-26", "2024-01-29",
    "2024-01-30", "2024-01-31", "2024-02-01", "2024-02-02", "2024-02-05",
    "2024-02-06", "2024-02-07", "2024-02-08", "2024-02-19", "2024-02-20",
]


# amount/volume 恒定 → 成本线 X_3 恒为 0.01*1e6/1e4 = 1.0 元/股
#   → JWZZ = (close - 1.0) / 1.0 * 1000
#      close 1.00 → 0（弱势）  1.05 → 50  1.10 → 100  1.12 → 120
#      1.15 → 150           1.20 → 200（强势，> 95）
def _df(closes, drop_idx=()) -> pd.DataFrame:
    drop = set(drop_idx)
    idx = [d for i, d in enumerate(DATES) if i not in drop]
    vals = [closes[i] for i in range(len(DATES)) if i not in drop]
    return pd.DataFrame(
        {"close": vals, "volume": 10000.0, "amount": 1000000.0},
        index=pd.Index(idx),
    )


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


class TestJwzzHold(unittest.TestCase):
    # ---------- 1. 基本节奏：买入 / 持有 / 下穿卖出 / 整组重发 ----------
    def test_buy_hold_sell_only_on_down_cross(self):
        bars = {
            "AAA": _df([1.0] * 12 + [1.1] * 18),              # 索引 12 起强势(100)
            "BBB": _df([1.0] * 15 + [1.2] * 7 + [1.0] * 8),    # 15 起强势(200)，22 起下穿
            "CCC": _df([1.0] * 18 + [1.15] * 12),              # 18 起强势(150)
        }
        sig = _run(jwzz_hold_mod.jwzz_hold, bars)
        days = _by_day(sig)

        self.assertIn(DATES[12], days)
        self.assertEqual(list(days[DATES[12]]), ["AAA"])
        self.assertAlmostEqual(days[DATES[12]]["AAA"].weight, 0.1)

        # 索引 15：买 BBB，且必须把已持有的 AAA 一起重发（整组重发 = 不静默清仓）
        self.assertEqual(sorted(days[DATES[15]]), ["AAA", "BBB"])
        self.assertIn("买入", days[DATES[15]]["BBB"].reason)
        self.assertIn("持有", days[DATES[15]]["AAA"].reason)

        # 索引 16/17：没有新候选 → 不发信号（ffill 维持持仓）
        self.assertNotIn(DATES[16], days)
        self.assertNotIn(DATES[17], days)

        # 索引 18：买 CCC，三只都在（再次整组重发）
        self.assertEqual(sorted(days[DATES[18]]), ["AAA", "BBB", "CCC"])

        # 索引 22：BBB 下穿 → 卖出（weight=0），其余两只重发
        self.assertEqual(sorted(days[DATES[22]]), ["AAA", "BBB", "CCC"])
        self.assertAlmostEqual(days[DATES[22]]["BBB"].weight, 0.0)
        self.assertIn("下穿", days[DATES[22]]["BBB"].reason)

    def test_sum_weight_never_exceeds_one_and_slots_respected(self):
        bars = {f"C{i:02d}": _df([1.0] * 12 + [1.0 + 0.01 * i] * 18)
                for i in range(1, 16)}
        sig = _run(jwzz_hold_mod.jwzz_hold, bars, max_slots=10)
        days = _by_day(sig)
        for day, sigs in days.items():
            total = sum(s.weight for s in sigs.values())
            self.assertLessEqual(total, 1.0 + 1e-6, f"{day} Σweight={total}")
            held = sum(1 for s in sigs.values() if s.weight > 0)
            self.assertLessEqual(held, 10, f"{day} 持仓 {held} 只 > 槽位 10")

    # ---------- 2. 排名下滑但仍强势 → 不卖 ----------
    def test_rank_drop_without_down_cross_is_not_sold(self):
        bars = {
            "XXX": _df([1.0] * 12 + [1.2] * 3 + [1.12] * 15),   # 12 起强势，15 起降到120
            "YYY": _df([1.0] * 13 + [1.15] * 17),               # 13 起强势(150)
        }
        sig = _run(jwzz_hold_mod.jwzz_hold, bars)
        days = _by_day(sig)
        # XXX 在索引 15 被 YYY 超过排名，但它 JWZZ=120 仍 > 95 → 不该被卖
        for day, sigs in days.items():
            if "XXX" in sigs:
                self.assertGreater(sigs["XXX"].weight, 0.0,
                                   f"{day} XXX 被卖了，但只是排名下滑、未下穿")
        self.assertIn(DATES[12], days)
        self.assertIn(DATES[13], days)

    # ---------- 3. 槽位满 → 当天买不进；卖出腾出的槽位次日才补 ----------
    def test_slots_full_blocks_buy_and_slot_frees_next_day(self):
        bars = {
            "AAA": _df([1.0] * 12 + [1.1] * 3 + [1.0] * 15),   # 12 强势，15 下穿
            "BBB": _df([1.0] * 16 + [1.2] * 14),                # 16 起强势
        }
        sig = _run(jwzz_hold_mod.jwzz_hold, bars,
                   max_slots=1, buys_per_day=1)
        days = _by_day(sig)

        # 索引 12：买入 AAA（占满唯一槽位）
        self.assertEqual(list(days[DATES[12]]), ["AAA"])
        self.assertAlmostEqual(days[DATES[12]]["AAA"].weight, 1.0)

        # 索引 15：AAA 下穿 → 卖出；**当天不再补位**（卖出与买入分开一天）
        self.assertEqual(list(days[DATES[15]]), ["AAA"])
        self.assertAlmostEqual(days[DATES[15]]["AAA"].weight, 0.0)

        # 索引 16：槽位空出来了 → 这天才买 BBB
        self.assertEqual(list(days[DATES[16]]), ["BBB"])
        self.assertAlmostEqual(days[DATES[16]]["BBB"].weight, 1.0)

    def test_slot_full_blocks_new_buy(self):
        """槽位满时，即使有更强势的票也不买（= 买入失败）。"""
        bars = {
            "AAA": _df([1.0] * 12 + [1.1] * 3 + [1.0] * 15),   # 12 强势，15 下穿
            "BBB": _df([1.0] * 13 + [1.2] * 17),               # 13 起强势(200)，更强势
        }
        sig = _run(jwzz_hold_mod.jwzz_hold, bars,
                   max_slots=1, buys_per_day=1)
        days = _by_day(sig)
        # AAA 还占着唯一槽位时，BBB 更强也买不进
        for day, sigs in days.items():
            if day < DATES[15]:
                self.assertNotIn("BBB", sigs, f"{day} BBB 不该在槽位满时被买进")
        # 索引 15：AAA 下穿卖出当天，槽位当天腾不出来 → BBB 作为候选被挡下
        self.assertGreaterEqual(days[DATES[15]]["AAA"].meta.get("blocked", 0), 1)

    # ---------- 4. 每日买入只数 ----------
    def test_buys_per_day_two_buys_top_two(self):
        bars = {
            "AAA": _df([1.0] * 12 + [1.1] * 18),    # 100
            "BBB": _df([1.0] * 12 + [1.2] * 18),    # 200
            "CCC": _df([1.0] * 12 + [1.15] * 18),   # 150
        }
        sig = _run(jwzz_hold_mod.jwzz_hold, bars, buys_per_day=2)
        days = _by_day(sig)
        self.assertEqual(sorted(days[DATES[12]]), ["BBB", "CCC"])
        self.assertEqual(days[DATES[12]]["BBB"].meta.get("rank"), 1)
        self.assertEqual(days[DATES[12]]["CCC"].meta.get("rank"), 2)

    # ---------- 5. 单票仓位自动 = 1 / 槽位数（不再单独设置） ----------
    def test_position_is_auto_one_over_slots(self):
        """单票仓位 = 1/槽位：10 槽 → 10%，20 槽 → 5%（用户不用、也配不出越界组合）。"""
        bars = {"AAA": _df([1.0] * 12 + [1.1] * 18)}
        for slots, expect in ((10, 0.1), (20, 0.05), (4, 0.25), (1, 1.0)):
            sig = _run(jwzz_hold_mod.jwzz_hold, bars, max_slots=slots)
            days = _by_day(sig)
            self.assertAlmostEqual(days[DATES[12]]["AAA"].weight, expect,
                                   msg=f"槽位 {slots} 的单票仓位应为 {expect}")

    def test_sum_weight_never_exceeds_one_for_any_slots(self):
        """任意槽位数下同一事件日 Σweight ≤ 1（满槽恰好 100%，不满就是现金）。"""
        for slots in (1, 3, 7, 10, 20, 49):
            bars = {f"C{i:02d}": _df([1.0] * 12 + [1.0 + 0.01 * i] * 18)
                    for i in range(1, 16)}
            sig = _run(jwzz_hold_mod.jwzz_hold, bars, max_slots=slots)
            for day, sigs in _by_day(sig).items():
                total = sum(s.weight for s in sigs.values())
                self.assertLessEqual(total, 1.0 + 1e-6,
                                     f"槽位 {slots}：{day} Σweight={total}")

    def test_position_each_param_removed(self):
        """「单票仓位」不再是参数 —— 参数面板由 ParamSpec 生成，少一项就少一个输入框。"""
        import inspect
        params = inspect.signature(jwzz_hold_mod.jwzz_hold).parameters
        self.assertNotIn("position_each", params)
        self.assertIn("max_slots", params)

    # ---------- 6. 注册门槛：文件能通过校验（id==文件名/签名/Σweight/meta） ----------
    def test_passes_registry_validation(self):
        rec = registry._validate_module("jwzz_hold", "jwzz_hold.py")
        self.assertEqual(rec.get("status"), "ok", rec.get("error"))


class TestJwzzPanel(unittest.TestCase):
    """面板口径（公式层）：停牌沿用、事件只在有行情的日子、`bar` 标有行情。"""

    def _panel(self, bars):
        return jwzz_panel(bars, period=13, threshold=95.0, smooth_period=0)

    def test_bull_down_cross_and_bar_flags(self):
        bars = {
            "AAA": _df([1.0] * 12 + [1.1] * 3 + [1.0] * 15),   # 12 强势，15 下穿
            "BBB": _df([1.0] * 30, drop_idx={20}),             # 索引 20 停牌
        }
        p = self._panel(bars)
        self.assertEqual(p.cols, ["AAA", "BBB"])
        row = p.cols.index("AAA")
        self.assertFalse(bool(p.bull[row, 11]))
        self.assertTrue(bool(p.bull[row, 12]))
        self.assertFalse(bool(p.dn[row, 14]))
        self.assertTrue(bool(p.dn[row, 15]))       # 下穿事件只在穿越那天
        self.assertFalse(bool(p.dn[row, 16]))
        self.assertAlmostEqual(float(p.val[row, 12]), 100.0, places=3)

        row_b = p.cols.index("BBB")
        self.assertFalse(bool(p.bar[row_b, 20]))   # 停牌日：没有行情
        self.assertTrue(bool(p.bar[row_b, 19]))
        self.assertTrue(bool(p.bar[row_b, 21]))

    def test_bull_carries_over_on_suspended_day(self):
        """停牌日沿用上一个有值的状态（停牌不清仓、不改状态）。"""
        # ZZZ 全勤：统一轴是**各票交易日的并集**，只有 AAA 时停牌日会整段从轴上消失，
        # 导致按位置断言错位 —— 这里用一只全勤票把轴撑满 30 天。
        bars = {
            "AAA": _df([1.0] * 5 + [1.1] * 25, drop_idx={10}),
            "ZZZ": _df([1.0] * 30),
        }
        p = self._panel(bars)
        row = p.cols.index("AAA")
        self.assertEqual(len(p.days), 30)
        self.assertTrue(bool(p.bull[row, 9]))
        self.assertTrue(bool(p.bull[row, 10]))      # 停牌日：沿用强势
        self.assertFalse(bool(p.bar[row, 10]))


class TestJwzzCrossUnchanged(unittest.TestCase):
    """`jwzz_cross` 改用共享面板后行为不变（掉出前 N 仍当天卖出）。"""

    def test_drop_out_of_top_n_sells_same_day(self):
        bars = {
            "AAA": _df([1.0] * 12 + [1.1] * 18),    # 12 起强势(100)
            "BBB": _df([1.0] * 15 + [1.2] * 15),    # 15 起强势(200)
        }
        sig = _run(jwzz_cross_mod.jwzz_cross, bars, top_n=1, position=1.0)
        days = _by_day(sig)
        self.assertEqual(list(days[DATES[12]]), ["AAA"])
        # 索引 15：BBB 更强 → AAA 掉出前 N 当天卖出（不是等它下穿）
        self.assertEqual(sorted(days[DATES[15]]), ["AAA", "BBB"])
        self.assertAlmostEqual(days[DATES[15]]["AAA"].weight, 0.0)
        self.assertAlmostEqual(days[DATES[15]]["BBB"].weight, 1.0)
