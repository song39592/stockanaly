import tempfile
import unittest
from pathlib import Path

import pandas as pd

import db
import price_service
import price_store


class PriceStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.DATA_DIR = Path(self.tmp.name)
        db.DB_PATH = Path(self.tmp.name) / "history.db"
        price_store.init_db()

    def tearDown(self):
        self.tmp.cleanup()

    def test_normalize_akshare_bars(self):
        """兼容东财中文列与新浪/腾讯英文列。"""
        frame = pd.DataFrame([{
            "日期": "2026-09-01", "开盘": 10, "收盘": 10.5, "最高": 11, "最低": 9.9,
            "成交量": 12345, "成交额": 45678, "涨跌幅": 5,
        }])
        bars = price_service.normalize_bars(frame)
        self.assertEqual(bars[0]["date"], "2026-09-01")
        self.assertEqual(bars[0]["close"], 10.5)

        english = pd.DataFrame([{"date": "2026-09-02", "open": 1, "close": 2, "high": 3, "low": 0.5}])
        self.assertEqual(price_service.normalize_bars(english)[0]["close"], 2)

    def test_upsert_and_list_raw(self):
        count = price_store.upsert_bars("000001", [{
            "date": "2026-09-01", "open": 10, "high": 11, "low": 9.8, "close": 10.8, "volume": 1000,
        }], "raw", source="测试")
        self.assertEqual(count, 1)
        self.assertEqual(price_store.list_bars("000001")[0]["close"], 10.8)
        self.assertEqual(price_store.latest_bar_date("000001", "raw"), "2026-09-01")

    def test_adjust_calculation(self):
        """复权价现算：hfq = raw × factor；qfq 以 as_of 为基准（基准日不同结果不同）。"""
        price_store.upsert_bars("000001", [
            {"date": "2026-01-05", "close": 10.0},          # 除权前
            {"date": "2026-06-11", "close": 10.0},          # 除权日（10 送 10 → 因子翻倍）
            {"date": "2026-09-01", "close": 10.0},          # 除权后
        ], "raw")
        price_store.upsert_factors("000001", [
            {"ex_date": "1900-01-01", "hfq_factor": 1.0},
            {"ex_date": "2026-06-11", "hfq_factor": 2.0},
        ])

        hfq = price_store.load_bars("000001", "hfq")
        self.assertAlmostEqual(hfq[0]["close"], 10.0)        # 除权前 ×1
        self.assertAlmostEqual(hfq[2]["close"], 20.0)        # 除权后 ×2

        # 常规前复权：基准 = 区间最后交易日（因子 2.0）→ 除权前价格被缩减
        qfq = price_store.load_bars("000001", "qfq")
        self.assertAlmostEqual(qfq[0]["close"], 5.0)
        self.assertAlmostEqual(qfq[2]["close"], 10.0)

        # 滚动复权：以除权前为基准 → 该段完全不缩放，且不含未来信息
        rolled = price_store.load_bars("000001", "qfq", as_of="2026-01-05")
        self.assertAlmostEqual(rolled[0]["close"], 10.0)
        self.assertAlmostEqual(rolled[2]["close"], 20.0)

    def test_missing_factor_falls_back(self):
        """缺因子时退化为原始价并标记 adjusted=False，避免静默给出看似正确的复权价。"""
        price_store.upsert_bars("000002", [{"date": "2026-09-01", "close": 10.0}], "raw")
        bars = price_store.load_bars("000002", "hfq")
        self.assertFalse(bars[0]["adjusted"])
        self.assertAlmostEqual(bars[0]["close"], 10.0)

    def test_raw_needs_no_factor(self):
        """不复权口径不读因子，直接返回原始价。"""
        price_store.upsert_bars("000003", [{"date": "2026-09-01", "close": 7.5}], "raw")
        bars = price_store.load_bars("000003", "raw")
        self.assertAlmostEqual(bars[0]["close"], 7.5)
        self.assertNotIn("adjusted", bars[0])


if __name__ == "__main__":
    unittest.main()
