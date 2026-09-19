import tempfile
import unittest
from pathlib import Path

import db
import history_store


class HistoryStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db.DATA_DIR = Path(self.tmp.name)
        db.DB_PATH = Path(self.tmp.name) / "history.db"
        history_store.init_db()

    def tearDown(self):
        self.tmp.cleanup()

    def test_snapshot_spans(self):
        stock = {"code": "000001", "name": "测试", "industry": "银行", "price": 10.2, "change": 1.5}
        history_store.save_snapshot("2026-09-01", [stock])
        history_store.save_snapshot("2026-09-02", [stock])
        history_store.save_snapshot("2026-09-03", [])
        history_store.save_snapshot("2026-09-04", [stock])
        pool = history_store.pool_history("000001")
        self.assertEqual(pool["spans"][0],
                         {"start": "2026-09-01", "end": "2026-09-02", "days": 2, "open": False})
        self.assertTrue(pool["spans"][1]["open"])

    def test_events(self):
        history_store.upsert_events("000001", [{
            "kind": "news", "title": "测试新闻", "date": "2026-09-01", "source": "测试源",
        }])
        self.assertEqual(history_store.list_events("000001")[0]["title"], "测试新闻")
        self.assertIsNotNone(history_store.event_state("000001"))


if __name__ == "__main__":
    unittest.main()
