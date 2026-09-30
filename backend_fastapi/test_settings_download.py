"""设置持久化与下载控制回归：临时目录，禁止真实网络与后台线程。"""
import datetime as dt
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from dotenv import dotenv_values
import config
import db
import download_store
import storage
import crypto
from test_support import isolate_crypto

# download_service 目前在导入时启动调度器，测试必须隔离此副作用。
with patch.object(download_store, "recover_stale"), patch("threading.Thread.start"):
    import download_service as service


class StorageSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.env = self.base / ".env"
        self.env.write_text("LLM_MODEL=test\nexport DATA_DIR = old\nSTOCK_DATA_DIR=older\n", encoding="utf-8")
        for owner, key, value in ((storage, "ENV_PATH", self.env),
                                  (config, "DATA_DIR", self.base / "current"),
                                  (config, "DATA_DIR_OVERRIDES", {})):
            p = patch.object(owner, key, value)
            p.start()
            self.addCleanup(p.stop)

    def test_saved_path_survives_new_process_and_remains_pending(self):
        target = self.base / "行情 # user's data"
        result = storage.update_data_dir(str(target))
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["restart_required"])
        values = dotenv_values(self.env)
        self.assertEqual(values["DATA_DIR"], str(target))
        self.assertNotIn("STOCK_DATA_DIR", values)
        self.assertEqual(values["LLM_MODEL"], "test")
        env = dict(os.environ)
        env.pop("DATA_DIR", None)
        env.pop("STOCK_DATA_DIR", None)
        env["PYTHONIOENCODING"] = "utf-8"
        output = subprocess.check_output([sys.executable, "-c",
            "import sys; from dotenv import load_dotenv; load_dotenv(sys.argv[1]); "
            "import config; print(config.DATA_DIR)", str(self.env)],
            cwd=config.BASE_DIR, env=env, encoding="utf-8")
        self.assertEqual(output.strip(), str(target))
        self.assertEqual(config.DATA_DIR, self.base / "current")

    def test_relative_path_rejected(self):
        self.assertFalse(storage.update_data_dir("relative/data")["ok"])

    def test_offline_save_recovers_unavailable_configured_directory(self):
        import base64
        import contextlib
        import io
        import json
        target = self.base / "offline data"
        encoded = base64.b64encode(str(target).encode()).decode()
        with contextlib.redirect_stdout(io.StringIO()) as output:
            result = storage.main(["--set-data-dir-base64", encoded])
        self.assertEqual(result, 0)
        self.assertTrue(json.loads(output.getvalue())["ok"])
        self.assertEqual(dotenv_values(self.env)["DATA_DIR"], str(target))

    def test_offline_invalid_input_leaves_config_unchanged(self):
        import contextlib
        import io
        before = self.env.read_bytes()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(storage.main(["--set-data-dir-base64", "!invalid!"]), 1)
        self.assertEqual(before, self.env.read_bytes())

    def test_external_override_rejected_without_changing_config(self):
        before = self.env.read_bytes()
        with patch.object(config, "DATA_DIR_OVERRIDES", {"STOCK_DATA_DIR": "elsewhere"}):
            self.assertFalse(storage.update_data_dir(str(self.base / "new"))["ok"])
        self.assertEqual(before, self.env.read_bytes())

    def test_atomic_replace_failure_preserves_original(self):
        before = self.env.read_bytes()
        with patch("envfile.os.replace", side_effect=OSError("disk failure")):
            self.assertFalse(storage.update_data_dir(str(self.base / "new"))["ok"])
        self.assertEqual(before, self.env.read_bytes())


class DownloadSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        isolate_crypto(self, Path(self.tmp.name))
        p = patch.object(db, "DB_PATH", Path(self.tmp.name) / "test.db")
        p.start()
        self.addCleanup(p.stop)
        download_store.init_db()
        for name, value in (("_runtimes", {}), ("_auto_state", {}), ("_auto_state_at", 0)):
            p = patch.object(service, name, value)
            p.start()
            self.addCleanup(p.stop)

    def task(self, status="paused", origin="manual"):
        download_store.create_task("test", "all", {}, ["000001"], origin=origin)
        download_store.update_task("test", status=status)

    def test_locked_secret_is_not_silently_replaced(self):
        with patch.object(crypto, "_read_secret", return_value=""), \
             patch.object(crypto, "_env_value", return_value="existing-sealed-value"), \
             patch.object(crypto, "_write_secret") as write:
            with self.assertRaises(RuntimeError):
                crypto.secret()
        write.assert_not_called()

    def test_key_write_failure_does_not_allow_memory_only_signing(self):
        with patch.object(crypto, "_read_secret", return_value=""), \
             patch.object(crypto, "_env_value", return_value=""), \
             patch.object(crypto, "_write_secret", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                crypto.secret()
        self.assertIsNone(crypto._key)

    def test_invalid_enable_does_not_partially_save(self):
        download_store.set_settings({"auto_update": "0", "idle_download": "0"})
        with patch.object(service, "_downloaded_staleness", return_value=(0, 0, [], None)):
            with self.assertRaises(ValueError):
                service.update_settings(auto_update=True, idle_download=True)
        self.assertEqual(download_store.get_setting("auto_update"), "0")
        self.assertEqual(download_store.get_setting("idle_download"), "0")

    def test_database_setting_batch_rolls_back(self):
        with db.connect() as conn:
            conn.execute("CREATE TRIGGER reject_setting BEFORE INSERT ON download_settings "
                         "WHEN NEW.key='bad' BEGIN SELECT RAISE(ABORT,'test failure'); END")
        with self.assertRaises(Exception):
            download_store.set_settings({"first": "1", "bad": "1"})
        self.assertEqual(download_store.settings_map(), {})

    def test_turning_off_idle_stops_only_its_task(self):
        self.task("running", service.ORIGIN_IDLE)
        service.update_settings(idle_download=False)
        self.assertEqual(download_store.get_task("test")["status"], "cancelled")

    def test_manual_task_not_cancelled_by_automatic_toggle(self):
        self.task("running")
        service.update_settings(idle_download=False, auto_update=False)
        self.assertEqual(download_store.get_task("test")["status"], "running")

    def test_cached_coverage_does_not_return_old_settings(self):
        service._auto_state = {"auto_update": False, "downloaded": 100}
        service._auto_state_at = time.time()
        download_store.set_setting("auto_update", "1")
        self.assertTrue(service.auto_state()["auto_update"])

    def test_spawn_failure_reports_saved_setting_separately(self):
        with patch.object(service, "_downloaded_staleness", return_value=(100, 0, [], "2026-09-25")), \
             patch.object(service, "_kick_auto_update", side_effect=OSError("offline")):
            result = service.update_settings(auto_update=True)
        self.assertTrue(result["auto_update"])
        self.assertIn("offline", result["scheduling_warning"])

    def test_resume_keeps_inflight_claim(self):
        self.task()
        runtime = Mock()
        runtime.manager.is_alive.return_value = True
        service._runtimes["test"] = runtime
        with patch.object(download_store, "requeue_running") as requeue, patch.object(service, "_spawn"):
            service.resume("test")
        requeue.assert_not_called()

    def test_restart_resume_requeues_orphaned_claim(self):
        self.task()
        with patch.object(download_store, "requeue_running") as requeue, patch.object(service, "_spawn"):
            service.resume("test")
        requeue.assert_called_once_with("test")

    def test_second_download_rejected_before_fetching_universe(self):
        with patch.object(service, "_has_alive_task", return_value=True), \
             patch.object(service, "_resolve_codes") as resolve:
            with self.assertRaises(RuntimeError):
                service.start_task()
        resolve.assert_not_called()

    def test_retry_waits_for_existing_workers(self):
        self.task("cancelled")
        with patch.object(service, "_has_alive_task", return_value=True), \
             patch.object(download_store, "reset_failed") as reset:
            with self.assertRaises(RuntimeError):
                service.retry_failed("test")
        reset.assert_not_called()

    def test_recent_start_date_is_respected(self):
        runtime = Mock()
        runtime.stop.is_set.return_value = False
        runtime.options = {"mode": "full", "start_date": "2026-01-01",
                           "split_date": "2023-01-01", "with_reference": False}
        with patch.object(service, "_skip_if_covered", return_value=False), \
             patch.object(service.price_service, "sync_all_adjusts", return_value={}) as sync, \
             patch.object(download_store, "finish_code"):
            service._download_one("test", runtime, {"code": "000001", "seq": 0, "phase": 1})
        self.assertEqual(sync.call_args.kwargs["start"], dt.date(2026, 1, 1))


if __name__ == "__main__":
    unittest.main()
