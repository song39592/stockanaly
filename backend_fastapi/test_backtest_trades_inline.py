# -*- coding: utf-8 -*-
"""逐笔明细「内联进回测结果体」的契约测试。

为什么要有这个测试：
- `stats.trades_for_response` 的裁剪口径必须**按买入日期倒序取最近 N 笔**
  （按 (code, buy_date) 截前 N 笔 = 只留下代码靠前的票，用户想看的近期交易反而看不到），
  且必须**如实给出总数与被丢掉的笔数** —— 截断不标注就等于静默改数据。
- `report.build_report` 落盘时，**总笔数不能因为 payload 已被页面上限截断而失真**
  （否则报告会把「15 万笔」写成「5000 笔、没截断」）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from strategies.core import stats
from strategies import report


def _trade(code, buy_date, sell_date="2026-01-02"):
    return {"code": code, "name": "某股", "buy_date": buy_date, "buy_price": 10.0,
            "sell_date": sell_date, "sell_price": 10.5, "ret": 0.05, "pnl": 100.0,
            "cost": 1.0, "holding_days": 2, "open": False}


class TestTradesForResponse(unittest.TestCase):
    def test_under_limit_keeps_all(self):
        src = [_trade("60000%d" % i, "2026-01-%02d" % (i + 1)) for i in range(3)]
        kept, meta = stats.trades_for_response(src, limit=10)
        self.assertEqual(3, len(kept))
        self.assertFalse(meta["truncated"])
        self.assertEqual(3, meta["total"])
        self.assertEqual(0, meta["dropped"])

    def test_over_limit_keeps_newest_by_buy_date(self):
        src = [_trade("60000%d" % i, "2026-01-%02d" % (i + 1)) for i in range(20)]
        kept, meta = stats.trades_for_response(src, limit=5)
        self.assertEqual(5, len(kept))
        self.assertTrue(meta["truncated"])
        self.assertEqual(20, meta["total"])
        self.assertEqual(15, meta["dropped"])
        # 保留的是**买入日期最近**的 5 笔，不是代码序最靠前的那几笔
        self.assertEqual(["2026-01-20", "2026-01-19", "2026-01-18",
                          "2026-01-17", "2026-01-16"],
                         [t["buy_date"] for t in kept])

    def test_missing_buy_date_sorted_last(self):
        src = [_trade("600001", None), _trade("600002", "2026-01-01")]
        kept, meta = stats.trades_for_response(src, limit=1)
        self.assertEqual("2026-01-01", kept[0]["buy_date"])

    def test_empty(self):
        kept, meta = stats.trades_for_response([], limit=5)
        self.assertEqual([], kept)
        self.assertEqual(0, meta["total"])
        self.assertEqual(0, meta["dropped"])


class TestReportTradesMeta(unittest.TestCase):
    def _payload(self, trades, total, truncated):
        return {"strategy": {"id": "x", "name": "X"}, "scope": {}, "dates": [],
                "metrics": {}, "trades": trades, "trades_total": total,
                "trades_truncated": truncated}

    def test_total_not_lost_when_payload_truncated(self):
        rep = report.build_report(self._payload([_trade("600001", "2026-01-01")], 1000, 999))
        self.assertEqual(1000, rep["trades_total"])       # 不能被算成 1
        self.assertEqual(999, rep["trades_truncated"])     # 「还有 999 笔没落盘」如实写
        self.assertEqual(1, len(rep["trades"]))

    def test_without_meta_uses_list_length(self):
        rep = report.build_report(self._payload([_trade("600001", "2026-01-01")], None, None))
        self.assertEqual(1, rep["trades_total"])
        self.assertEqual(0, rep["trades_truncated"])