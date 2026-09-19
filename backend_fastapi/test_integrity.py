import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

import crypto
import db
import history_store
import integrity
import price_store


class IntegrityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        db.DATA_DIR = base
        db.DB_PATH = base / "history.db"
        db.BARS_DIR = base / "bars"
        db.FACTORS_DB = db.BARS_DIR / "factors.db"
        crypto.ENV_PATH = base / ".env"          # 隔离：不要写真实的 .env
        crypto._key = None
        os.environ["DATA_SECRET"] = "unit-test-secret"
        price_store.init_db()
        history_store.init_db()
        # 写入一点数据，触发元信息记录
        price_store.upsert_bars("000001", [{"date": "2026-09-01", "close": 10.0}], "raw")
        history_store.save_snapshot("2026-09-01", [{"code": "000001", "name": "测试"}])

    def tearDown(self):
        os.environ.pop("DATA_SECRET", None)
        crypto._key = None
        self.tmp.cleanup()

    def test_clean_database_passes(self):
        """正常库应通过校验，且元信息记录了结构版本与写入时间。"""
        result = integrity.check_all()
        self.assertTrue(result["ok"], result["issues"])
        self.assertEqual(result["schema_version"], db.SCHEMA_VERSION)
        shard = next(lib for lib in result["libraries"] if "bars_" in lib["path"])
        self.assertEqual(shard["status"], "ok")
        self.assertEqual(shard["schema_version"], db.SCHEMA_VERSION)
        self.assertIsNotNone(shard["last_write_at"])

    def test_meta_value_change_is_detected(self):
        """直接改 _meta 里的值而不同步改签名 → 签名校验应发现。"""
        path = db.bars_db(2026)
        conn = sqlite3.connect(path)
        conn.execute("UPDATE _meta SET value='2099-01-01T00:00:00+00:00' WHERE key=?",
                     (db.META_LAST_WRITE,))
        conn.commit()
        conn.close()

        result = integrity.check_library("日K分片", path, ("daily_bars",))
        self.assertFalse(result["issues"] == [])
        self.assertTrue(any("签名不匹配" in text for text in result["issues"]), result["issues"])

    def test_external_write_is_detected_by_time(self):
        """绕过程序直接写库（时间记录不变、文件时间变新）→ 应被时间校验发现。"""
        path = db.bars_db(2026)
        # 模拟外部工具：直接写数据，并手动把文件时间推到未来
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO daily_bars(code,trade_date,adjust,close,fetched_at,source)"
                     " VALUES('000002','2026-09-02','raw',99.0,'x','')")
        conn.commit()
        conn.close()
        future = time.time() + 3600          # 1 小时后
        os.utime(path, (future, future))

        result = integrity.check_library("日K分片", path, ("daily_bars",))
        self.assertTrue(any("外部工具改动" in text for text in result["issues"]), result["issues"])

    def test_missing_table_is_detected(self):
        """表被删除 → 结构完整性检查应发现。"""
        path = db.bars_db(2026)
        conn = sqlite3.connect(path)
        conn.execute("DROP TABLE daily_bars")
        conn.commit()
        conn.close()

        result = integrity.check_library("日K分片", path, ("daily_bars",))
        self.assertTrue(any("缺少数据表" in text for text in result["issues"]), result["issues"])

    def test_program_writes_keep_time_consistent(self):
        """程序自己的写入会同步刷新记录时间，因此不会误报。"""
        price_store.upsert_bars("000001", [{"date": "2026-09-03", "close": 11.0}], "raw")
        result = integrity.check_library("日K分片", db.bars_db(2026), ("daily_bars",))
        self.assertEqual(result["issues"], [], result["issues"])

    def test_digest_allows_clean_read(self):
        """写入后自动生成指纹，正常读取应通过校验。"""
        bars = price_store.load_bars("000001", "raw")
        self.assertEqual(len(bars), 1)

    def test_read_refuses_tampered_data(self):
        """直接改一行数据 → 读取时必须拒绝返回，而不是照常给出可疑数据。"""
        path = db.bars_db(2026)
        conn = sqlite3.connect(path)
        conn.execute("UPDATE daily_bars SET close=9999 WHERE code='000001'")
        conn.commit()
        conn.close()
        with self.assertRaises(price_store.UntrustedDataError):
            price_store.load_bars("000001", "raw")

    def test_incremental_write_cannot_launder_tampering(self):
        """增量写入不得把篡改「洗白」。

        修复前的反例：改历史行 → 等一次日常同步 → 指纹被整体重算，
        篡改被纳入新基线，此后读取一切正常，问题**永久隐身**。
        """
        conn = sqlite3.connect(db.bars_db(2026))
        conn.execute("UPDATE daily_bars SET close=9999 WHERE code='000001'")
        conn.commit()
        conn.close()
        with self.assertRaises(price_store.UntrustedDataError):
            price_store.load_bars("000001", "raw")

        # 模拟日常增量同步：只补最新一天，碰不到被改的那行
        price_store.upsert_bars("000001", [{"date": "2026-09-02", "close": 11.0}], "raw")

        # 修复后：仍必须拒绝；此处若不抛异常，说明篡改被同一次写入洗白了
        with self.assertRaises(price_store.UntrustedDataError):
            price_store.load_bars("000001", "raw")

    def test_clean_incremental_write_still_refreshes(self):
        """数据未被改动时，增量写入仍应正常刷新指纹（不能因噎废食）。"""
        price_store.upsert_bars("000001", [{"date": "2026-09-02", "close": 11.0}], "raw")
        self.assertEqual(len(price_store.load_bars("000001", "raw")), 2)

    def test_deep_check_lists_untrusted(self):
        """深校验能定位到具体不可信的股票。"""
        path = db.bars_db(2026)
        conn = sqlite3.connect(path)
        conn.execute("UPDATE daily_bars SET close=8888 WHERE code='000001'")
        conn.commit()
        conn.close()

        result = integrity.deep_check()
        self.assertFalse(result["ok"])
        self.assertEqual(result["untrusted"][0]["code"], "000001")

    def test_dpapi_seal_roundtrip(self):
        """DPAPI 密封后能原样解开，且密文中不含明文。"""
        if not crypto.dpapi_available():
            self.skipTest("当前环境不支持 DPAPI")
        sealed = crypto.dpapi_seal("hello-secret")
        self.assertNotIn("hello-secret", sealed)
        self.assertEqual(crypto.dpapi_unseal(sealed), "hello-secret")

    def test_secret_written_sealed_without_plaintext(self):
        """写入 .env 时优先密封（绑定本机），不应残留明文密钥。"""
        crypto._key = None
        crypto.secret(force_new=True)
        text = crypto.ENV_PATH.read_text(encoding="utf-8")
        if crypto.dpapi_available():
            self.assertIn(crypto.ENV_SEALED, text)
            self.assertNotIn(f"{crypto.ENV_KEY}=", text)
        else:
            self.assertIn(f"{crypto.ENV_KEY}=", text)

    def test_check_is_read_only(self):
        """校验过程不得产生任何写入（否则会自己把自己判成被改动）。"""
        path = db.bars_db(2026)
        before = path.stat().st_mtime
        integrity.check_all()
        self.assertEqual(path.stat().st_mtime, before)


if __name__ == "__main__":
    unittest.main()
