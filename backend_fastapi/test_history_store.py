import tempfile
import unittest
from pathlib import Path

import pandas as pd

import history_service
import history_store


class HistoryStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        history_store.DATA_DIR = Path(self.tmp.name)
        history_store.DB_PATH = Path(self.tmp.name) / "history.db"
        history_store.init_db()

    def tearDown(self):
        self.tmp.cleanup()

    def test_snapshot_spans_bars_and_events(self):
        stock = {"code": "000001", "name": "测试", "industry": "银行", "price": 10.2, "change": 1.5}
        history_store.save_snapshot("2026-09-01", [stock])
        history_store.save_snapshot("2026-09-02", [stock])
        history_store.save_snapshot("2026-09-03", [])
        history_store.save_snapshot("2026-09-04", [stock])
        pool = history_store.pool_history("000001")
        self.assertEqual(pool["spans"][0], {"start": "2026-09-01", "end": "2026-09-02", "days": 2, "open": False})
        self.assertTrue(pool["spans"][1]["open"])

        count = history_store.upsert_bars("000001", [{
            "date": "2026-09-01", "open": 10, "high": 11, "low": 9.8, "close": 10.8, "volume": 1000,
        }])
        self.assertEqual(count, 1)
        self.assertEqual(history_store.list_bars("000001")[0]["close"], 10.8)

        history_store.upsert_events("000001", [{
            "kind": "news", "title": "测试新闻", "date": "2026-09-01", "source": "测试源",
        }])
        self.assertEqual(history_store.list_events("000001")[0]["title"], "测试新闻")

    def test_normalize_akshare_bars(self):
        frame = pd.DataFrame([{
            "日期": "2026-09-01", "开盘": 10, "收盘": 10.5, "最高": 11, "最低": 9.9,
            "成交量": 12345, "成交额": 45678, "涨跌幅": 5,
        }])
        bars = history_service.normalize_bars(frame)
        self.assertEqual(bars[0]["date"], "2026-09-01")
        self.assertEqual(bars[0]["close"], 10.5)


if __name__ == "__main__":
    unittest.main()
