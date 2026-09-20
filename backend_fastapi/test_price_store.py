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
        db.BARS_DIR = Path(self.tmp.name) / "bars"
        db.FACTORS_DB = db.BARS_DIR / "factors.db"
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

    def test_sharded_by_year(self):
        """日K 按年分片：不同年份写入不同库，跨年区间能合并读回。"""
        price_store.upsert_bars("000001", [
            {"date": "2024-12-31", "close": 10.0},
            {"date": "2025-06-30", "close": 11.0},
            {"date": "2026-01-05", "close": 12.0},
        ], "raw")

        shards = sorted(p.name for p in db.BARS_DIR.glob("bars_*.db"))
        self.assertEqual(shards, ["bars_2024.db", "bars_2025.db", "bars_2026.db"])

        all_bars = price_store.list_bars("000001")
        self.assertEqual([b["trade_date"] for b in all_bars],
                         ["2024-12-31", "2025-06-30", "2026-01-05"])

        # 跨年区间
        crossed = price_store.list_bars("000001", "2024-12-01", "2025-12-31")
        self.assertEqual([b["trade_date"] for b in crossed], ["2024-12-31", "2025-06-30"])

        # 只查单年不应带出别的年份
        only_2025 = price_store.list_bars("000001", "2025-01-01", "2025-12-31")
        self.assertEqual([b["trade_date"] for b in only_2025], ["2025-06-30"])

        # 最新日期取自分片
        self.assertEqual(price_store.latest_bar_date("000001"), "2026-01-05")

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

    def test_validate_bars_rejects_bad_rows(self):
        """入库前的质量校验：宁可缺数据，也不写入不可信的数据。"""
        good = {"date": "2026-09-01", "open": 10.0, "high": 11.0, "low": 9.5, "close": 10.5}
        rows = [
            good,
            {"date": "2026-09-02", "open": 10, "high": 9, "low": 11, "close": 10},   # 高<低
            {"date": "2026-09-03", "open": 10, "high": 11, "low": 9, "close": 12},   # 收盘越界
            {"date": "2026-09-04", "open": 10, "high": 11, "low": 9, "close": 0},    # 非正数
            {"date": "2026-09-05", "open": None, "high": 11, "low": 9, "close": 10}, # 缺失
            {"date": "2026-09-06", "open": 9.5, "high": 11, "low": 9, "close": 9.8}, # 正常
        ]
        ok, rejected = price_service.validate_bars(rows)
        self.assertEqual(len(ok), 2)
        self.assertEqual(len(rejected), 4)
        self.assertTrue(any("最高价低于最低价" in text for text in rejected))
        self.assertTrue(any("超出当日高低区间" in text for text in rejected))
        self.assertTrue(any("非正数" in text for text in rejected))
        self.assertTrue(any("缺失" in text for text in rejected))

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

    def test_factors_live_in_separate_db(self):
        """复权因子与除权明细存放在 bars/factors.db，与日K分片分离。"""
        price_store.upsert_factors("000004", [{"ex_date": "2026-06-11", "hfq_factor": 2.0}])
        price_store.upsert_dividends("000004", [{
            "ex_date": "2026-06-11", "cash_per_10": 5.0, "progress": "实施",
        }])
        self.assertTrue(db.FACTORS_DB.exists())
        self.assertEqual(len(price_store.list_factors("000004")), 1)
        self.assertEqual(price_store.list_dividends("000004")[0]["cash_per_10"], 5.0)


if __name__ == "__main__":
    unittest.main()
