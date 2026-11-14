# -*- coding: utf-8 -*-
"""「隔日一字涨停 → 建仓失败」标记的契约测试。

为什么要有这个测试：
- 建仓失败（买不进）**根本不发生成交**，所以它不在 trades 明细里（那里一行 = 一次买卖）；
  只有 `fill_blocked.buy_blocked` 一个总数的话，报告说不清"哪只票、哪天、为什么没买成"
  → 必须有逐条明细（code / 执行日 / 信号日 / 原因）。
- 这个标记的**日期口径**最容易被写错：执行日 = 信号日的**隔日**（权重 t_b 日发出、
  t_b+1 日才生效），弄反了用户就找不到是哪天没买成。
- 买不进时**该份额度当日就是现金、不重分配、也不计收益** —— 这条净值口径
  改动时很容易被顺手改坏，必须钉住。
"""
import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from strategies.core import backtest
from strategies.core import stats

CODE = "600000"


def _bars(rows):
    """rows: [(日期, 前收, 最低, 最高)] → 收盘价序列 + raw OHLCV。"""
    dates = [r[0] for r in rows]
    close = pd.Series([r[1] for r in rows], index=dates)
    raw = pd.DataFrame(
        {"open": [r[3] for r in rows], "high": [r[3] for r in rows],
         "low": [r[2] for r in rows], "close": [r[1] for r in rows]},
        index=dates)
    return close, raw


def _positions(weights, dates):
    return pd.Series([weights.get(d, 0.0) for d in dates], index=dates)


class TestBuildFailureEvents(unittest.TestCase):
    """引擎层：stats.compute 的建仓失败事件。"""

    def _run(self, rows, weights):
        close, raw = _bars(rows)
        pos = _positions(weights, list(raw.index))
        return stats.compute({CODE: pos}, {CODE: close}, raw_by_code={CODE: raw})

    def test_limit_up_next_day_is_build_failure(self):
        # 11-01 信号（权重 0.3）→ 11-02 一字涨停（11.00 = round(10.00×1.1, 2)）买不进
        res = self._run(
            [("2026-11-01", 10.00, 9.80, 10.20),
             ("2026-11-02", 11.00, 11.00, 11.00),
             ("2026-11-03", 11.50, 11.10, 11.60)],
            {"2026-11-02": 0.3, "2026-11-03": 0.3})
        self.assertEqual(1, res["fill_blocked"]["buy_blocked"])
        self.assertEqual(1, len(res["build_failures"]))
        ev = res["build_failures"][0]
        self.assertEqual(CODE, ev["code"])
        self.assertEqual("2026-11-02", ev["date"])          # 执行日 = 隔日
        self.assertEqual("2026-11-01", ev["signal_date"])    # 信号日
        self.assertEqual("limit_up", ev["reason"])

    def test_blocked_build_not_counted_in_returns(self):
        """买不进 → 该份额度当日是现金：净值当天不吃这只票的涨跌。"""
        res = self._run(
            [("2026-11-01", 10.00, 9.80, 10.20),
             ("2026-11-02", 11.00, 11.00, 11.00),
             ("2026-11-03", 11.50, 11.10, 11.60)],
            {"2026-11-02": 0.3, "2026-11-03": 0.3})
        # 11-02 买不进 → 当日净值不因这只票变化（与 11-01 持平）
        self.assertAlmostEqual(res["equity"][0], res["equity"][1], places=6)
        # 11-03 重新建仓成功 → 才有成交明细（买不进的那笔不在里面）
        self.assertEqual(1, len(res["trades"]))
        self.assertEqual("2026-11-03", res["trades"][0]["buy_date"])

    def test_normal_day_has_no_failure(self):
        res = self._run(
            [("2026-11-01", 10.00, 9.80, 10.20),
             ("2026-11-02", 10.30, 10.00, 10.40)],
            {"2026-11-02": 0.3})
        self.assertEqual(0, res["fill_blocked"]["buy_blocked"])
        self.assertEqual([], res["build_failures"])

    def test_two_consecutive_blocked_days_two_events(self):
        """d1 买不进 → d1 实际空仓 → d2 策略仍选中它 → d2 仍是一次建仓（再拦一次）。"""
        # 11-02 涨停价 = round(10.00×1.1,2) = 11.00；11-03 涨停价 = round(11.00×1.1,2) = 12.10
        res = self._run(
            [("2026-11-01", 10.00, 9.80, 10.20),
             ("2026-11-02", 11.00, 11.00, 11.00),
             ("2026-11-03", 12.10, 12.10, 12.10)],
            {"2026-11-02": 0.3, "2026-11-03": 0.3})
        self.assertEqual(2, res["fill_blocked"]["buy_blocked"])
        self.assertEqual(["2026-11-02", "2026-11-03"],
                         [e["date"] for e in res["build_failures"]])


class TestBuildFailureResponse(unittest.TestCase):
    """响应层：补名称 / 倒序 / 截断标注。"""

    def test_names_and_order_and_count(self):
        items = [{"code": "600001", "date": "2026-11-02", "signal_date": "2026-11-01", "reason": "limit_up"},
                 {"code": "600002", "date": "2026-11-05", "signal_date": "2026-11-04", "reason": "limit_up"}]
        out = backtest._build_failure_items(items, {"600002": "乙股"})
        self.assertEqual(2, out["count"])
        self.assertEqual(2, out["shown"])
        self.assertEqual(0, out["dropped"])
        # 按执行日倒序（新的在前），与成交明细的排序口径一致
        self.assertEqual("600002", out["items"][0]["code"])
        self.assertEqual("乙股", out["items"][0]["name"])
        self.assertIsNone(out["items"][1]["name"])       # 没查到名 → null（前端显示「—」）

    def test_truncation_is_marked(self):
        n = backtest.MAX_BUILD_FAILURES + 10
        items = [{"code": "6000%04d" % i, "date": "2026-11-%02d" % ((i % 28) + 1),
                  "signal_date": "2026-11-01", "reason": "limit_up"}
                 for i in range(n)]
        out = backtest._build_failure_items(items, {})
        self.assertEqual(n, out["count"])
        self.assertEqual(backtest.MAX_BUILD_FAILURES, out["shown"])
        self.assertEqual(10, out["dropped"])

    def test_feasibility_carries_build_failures(self):
        feas = backtest._feasibility_summary([], 0, fill_blocked={"buy_blocked": 2},
                                              build_failures={"count": 2, "shown": 2,
                                                              "dropped": 0, "items": []})
        self.assertEqual(2, feas["build_failures"]["count"])
        self.assertEqual(2, feas["fill_blocked"]["buy_blocked"])

    def test_feasibility_without_build_failures_defaults_empty(self):
        feas = backtest._feasibility_summary([], 0)
        self.assertEqual(0, feas["build_failures"]["count"])
        self.assertEqual([], feas["build_failures"]["items"])


if __name__ == "__main__":
    unittest.main()
