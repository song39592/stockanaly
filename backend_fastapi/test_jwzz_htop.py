# -*- coding: utf-8 -*-
"""jwzz_htop 策略单测：买入候选、两种清仓事件、Σw≤1、不向后补位。

JWZZ 面板 / 筹码矩阵都有各自的内核测试；这里 mock 掉两者，
只喂可控的 bull/dn/HTOP/low 矩阵，专注验证**策略逻辑**。
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:                                        # noqa: BLE001
    pass

from strategies.core.base import StrategyContext               # noqa: E402
from formulas.jwzz import JwzzPanel                             # noqa: E402
from strategies import jwzz_htop as mod                         # noqa: E402


DAYS = ["2024-01-%02d" % d for d in range(2, 22)]
CODES = ["A", "B", "C", "D"]
T, C = len(DAYS), len(CODES)


def _bars(low: float | list[float] = 9.9) -> dict[str, pd.DataFrame]:
    idx = pd.Index(DAYS)
    lows = [low] * T if isinstance(low, (int, float)) else low
    return {c: pd.DataFrame({"close": [10.0] * T, "low": lows,
                             "volume": [1e6] * T, "amount": [1e7] * T},
                            index=idx) for c in CODES}


def _panel(bull: np.ndarray, dn: np.ndarray | None = None,
           val: np.ndarray | None = None,
           bar: np.ndarray | None = None) -> JwzzPanel:
    return JwzzPanel(
        cols=list(CODES), days=list(DAYS),
        bull=np.asarray(bull, dtype=bool),
        val=np.asarray(val if val is not None else np.ones((C, T)) * 100,
                       dtype=np.float32),
        dn=np.asarray(dn if dn is not None else np.zeros((C, T)), dtype=bool),
        bar=np.asarray(bar if bar is not None else np.ones((C, T)), dtype=bool),
    )


def _run(panel: JwzzPanel, htop_by_code: dict[str, list[float]], *,
         low: float | list[float] = 9.9, top_n=2, M=0.05, threshold=95.0):
    bars = _bars(low)

    def fake_load(codes, start, end, fields=None, adjust="hfq", progress=None):
        return {c: bars[c][fields] if fields else bars[c] for c in codes if c in bars}

    def fake_htop(code, start, end, adjust):
        h = htop_by_code.get(code)
        return pd.Series(h, index=DAYS[:len(h)]) if h is not None else None

    with mock.patch.object(mod.data, "load_bars", side_effect=fake_load), \
         mock.patch.object(mod, "_htop_series", side_effect=fake_htop), \
         mock.patch.object(mod, "jwzz_panel", return_value=panel):
        return mod.jwzz_htop(
            StrategyContext(codes=list(CODES), start=DAYS[0], end=DAYS[-1],
                            adjust="hfq"),
            top_n=top_n, M=M, threshold=threshold)


def _all_bull() -> np.ndarray:
    return np.ones((C, T), dtype=bool)


def _flat_htop(v: float) -> dict[str, list[float]]:
    return {c: [v] * T for c in CODES}


class TestJwzzHtop(unittest.TestCase):
    def test_buy_fixed_M_and_leader_only(self):
        """强势票按 JWZZ 降序取前 top_n，每只固定 M；不向后补位到第 N+1 名。"""
        val = np.array([[100, 90, 80, 10]] * T, dtype=np.float32).T
        panel = _panel(_all_bull(), val=val)
        sigs = _run(panel, _flat_htop(9.0), top_n=2, M=0.05)
        buys = [s for s in sigs if s.weight > 0]
        self.assertTrue(buys, "应产生买入")
        self.assertEqual({"A", "B"}, {s.code for s in buys}, "只买前 2 名")
        self.assertTrue(all(abs(s.weight - 0.05) < 1e-9 for s in buys))
        self.assertTrue(all(s.meta.get("M") == 0.05 for s in buys))
        self.assertEqual([1, 2], sorted(s.meta["rank"] for s in buys))

    def test_price_break_low_triggers_exit(self):
        """最低价下破 HTOP → 清仓（触发条件用 low，不是 close）。"""
        htop = {c: [9.0, 10.5] + [10.5] * (T - 2) for c in CODES}
        sigs = _run(_panel(_all_bull()), htop, top_n=2, M=0.05)
        exits = [s for s in sigs if s.weight == 0.0
                 and s.meta.get("trigger") == "htop"]
        self.assertTrue(exits, "最低价下破 HTOP 应触发清仓")
        self.assertEqual(DAYS[1], exits[0].time, "下破当天清仓")

    def test_jwzz_downcross_triggers_exit(self):
        """JWZZ 收盘下穿阈值 → 清仓（trigger=dn），且优先于 HTOP 判定。"""
        dn = np.zeros((C, T), dtype=bool)
        dn[0, 1] = True
        sigs = _run(_panel(_all_bull(), dn=dn), _flat_htop(1.0),
                    top_n=2, M=0.05)
        dn_exits = [s for s in sigs if s.weight == 0.0
                    and s.meta.get("trigger") == "dn"]
        self.assertEqual(1, len(dn_exits))
        self.assertEqual("A", dn_exits[0].code)
        self.assertEqual(DAYS[1], dn_exits[0].time)

    def test_no_buy_without_htop(self):
        """HTOP 算不出（NaN）→ 该票不买（没有卖出线就不建仓）。"""
        sigs = _run(_panel(_all_bull()),
                    {c: [float("nan")] * T for c in CODES}, top_n=2, M=0.05)
        self.assertEqual([], [s for s in sigs if s.weight > 0])

    def test_held_not_dropped_when_out_of_topn(self):
        """持有后跌出前 N → 不卖（与 jwzz_cross 的关键差别）。"""
        val = np.array([[100, 90, 80, 10], [100, 90, 80, 10]] +
                       [[10, 10, 100, 90]] * (T - 2), dtype=np.float32).T
        panel = _panel(_all_bull(), val=val)
        sigs = _run(panel, _flat_htop(9.0), top_n=2, M=0.05)
        sells = [s for s in sigs if s.weight == 0.0]
        self.assertEqual([], sells, "跌出前 N 不是卖出条件")

    def test_sum_weight_never_exceeds_one(self):
        """Σw ≤ 1：top_n 很大、M=0.2 时只补足到不超额。"""
        sigs = _run(_panel(_all_bull()), _flat_htop(9.0), top_n=20, M=0.2)
        cur = 0.0
        by_day: dict[str, float] = {}
        for s in sigs:
            by_day[s.time] = by_day.get(s.time, 0.0) + (
                s.weight if s.weight > 0 else -0.2)
        for day in sorted(by_day):
            cur += by_day[day]
            self.assertLessEqual(cur, 1.0 + 1e-6, day)

    def test_meta_json_serializable(self):
        """meta 必须可 json.dumps（不能含 NaN/inf）。"""
        import json
        sigs = _run(_panel(_all_bull()), _flat_htop(9.0), top_n=2, M=0.05)
        for s in sigs:
            json.dumps(s.meta, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
