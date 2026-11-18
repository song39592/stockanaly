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
             patch.object(service.price_store, "latest_bar_date", return_value=None), \
             patch.object(download_store, "finish_code"):
            service._download_one("test", runtime, {"code": "000001", "seq": 0, "phase": 1})
        self.assertEqual(sync.call_args.kwargs["start"], dt.date(2026, 1, 1))

    def test_incremental_stale_stock_is_fetched_not_skipped(self):
        """回落十几天的正常股不能被当成停牌跳过（回归：数据卡在 2026-10-13）。"""
        runtime = Mock()
        runtime.stop.is_set.return_value = False
        runtime.options = {"mode": service.MODE_INCREMENTAL, "overlap_days": 0,
                           "source": service.SOURCE_ONLINE, "with_reference": False}
        with patch.object(service, "_expected_data_day", return_value="2026-10-26"), \
             patch.object(service.price_store, "latest_bar_date", return_value="2026-10-13"), \
             patch.object(service, "_tdx_is_valid", return_value=False), \
             patch.object(service.price_service, "sync_all_adjusts", return_value={}) as sync, \
             patch.object(download_store, "finish_code"):
            service._download_one("test", runtime, {"code": "000001", "seq": 0, "phase": 1})
        # 必须真的去取数（start = 库内最新 + 1 天），而不是 finish_code 后直接返回
        sync.assert_called_once()
        self.assertEqual(sync.call_args.kwargs["start"], dt.date(2026, 10, 14))

    # ---- 请求区间的展示口径（回归：起止颠倒的假区间）----

    def test_incremental_span_not_inverted_when_already_latest(self):
        """库里最新日已追到应有交易日 → 不给「2026-11-18 ~ 2026-11-17」这种倒区间。"""
        options = {"mode": service.MODE_INCREMENTAL, "overlap_days": 0}
        with patch.object(service, "_expected_data_day", return_value="2026-11-17"), \
             patch.object(service.price_store, "code_latest_dates",
                          return_value={"000001": "2026-11-17"}), \
             patch.object(service.stock_status, "is_suspended_like", return_value=False):
            service._stamp_span(options, ["000001"])
        self.assertEqual(options.get("span_begin"), "")
        self.assertIn("无新增", options.get("span_note") or "")
        self.assertEqual(service._requested_span({"options": options}), (None, None))

    def test_incremental_span_keeps_begin_before_end(self):
        """还有新增可补时，区间照常是「最新日+1 → 应有交易日」。"""
        options = {"mode": service.MODE_INCREMENTAL, "overlap_days": 0}
        with patch.object(service, "_expected_data_day", return_value="2026-11-17"), \
             patch.object(service.price_store, "code_latest_dates",
                          return_value={"000001": "2026-11-14"}), \
             patch.object(service.stock_status, "is_suspended_like", return_value=False):
            service._stamp_span(options, ["000001"])
        self.assertEqual(options["span_begin"], "2026-11-15")
        self.assertEqual(options["span_end"], "2026-11-17")
        self.assertEqual(service._requested_span({"options": options}),
                         ("2026-11-15", "2026-11-17"))

    def test_full_span_end_is_expected_trade_day_not_today(self):
        """完整下载的终点是「应有交易日」：今天还没收盘时不该显示要下今天的数据。"""
        task = {"options": {"mode": service.MODE_FULL, "start_date": "2020-01-01"}}
        with patch.object(service, "_expected_data_day", return_value="2026-11-17"):
            begin, end = service._requested_span(task)
        self.assertEqual(begin, "2020-01-01")
        self.assertEqual(end, "2026-11-17")

    def test_stored_inverted_span_is_reported_as_no_new_data(self):
        """老任务存着「最新日+1」这个起点、终点又回到上一交易日 → 显示「无新增」，不给假区间。"""
        task = {"id": "test", "status": "running", "completed": 0, "failed": 0, "total": 1,
                "options": {"mode": service.MODE_INCREMENTAL, "span_begin": "2026-11-18",
                            "span_end": "2026-11-17"}}
        with patch.object(service, "_expected_data_day", return_value="2026-11-17"):
            view = service._decorate(task)
        self.assertIsNone(view["requested_begin"])
        self.assertIsNone(view["requested_end"])
        self.assertIn("无新增", view["requested_note"])

    def test_full_phase2_fills_earlier_gap_from_start_year(self):
        """起始年份早于库里已有起点时，阶段 2 只补「起始日 → 库内最早-1」这段缺口。"""
        runtime = Mock()
        runtime.stop.is_set.return_value = False
        runtime.options = {"mode": service.MODE_FULL, "start_date": "2020-01-01",
                           "split_date": "2023-11-18", "source": service.SOURCE_ONLINE,
                           "with_reference": False}
        with patch.object(service, "_skip_if_covered", return_value=False), \
             patch.object(service.price_store, "earliest_bar_date", return_value="2021-08-02"), \
             patch.object(service, "_no_older_history", return_value=False), \
             patch.object(service.price_service, "sync_all_adjusts", return_value={}) as sync, \
             patch.object(download_store, "finish_code"):
            service._download_one("test", runtime, {"code": "000001", "seq": 0, "phase": 2})
        sync.assert_called_once()
        self.assertEqual(sync.call_args.kwargs["start"], dt.date(2020, 1, 1))
        self.assertEqual(sync.call_args.kwargs["end"], dt.date(2021, 8, 1))

    def test_two_phase_progress_is_counted_by_stage_rows(self):
        """两轮任务：股票级计数在「两个阶段都落定」前是 0，
        真实完成度必须按**阶段行**给（否则第一轮跑完仍显示 0%，看着像卡死）。"""
        download_store.create_task("t2", "all", {"mode": service.MODE_FULL, "phases": 2},
                                   ["000001", "000002"], 2)
        # 000001：两个阶段都落定；000002：只落定第一阶段（近期段）
        download_store.finish_code("t2", 0, ok=True)      # 000001 阶段 1
        download_store.finish_code("t2", 2, ok=True)      # 000001 阶段 2
        download_store.finish_code("t2", 1, ok=True)      # 000002 阶段 1
        task = download_store.get_task("t2")
        view = service._decorate(task)
        self.assertEqual(view["completed"], 1)            # 只有 000001 两只都落定
        self.assertEqual(view["phase_settled"], 3)        # 已落定的阶段行：3 行
        self.assertEqual(view["phase_total"], 4)          # 2 只 × 2 轮
        self.assertEqual(view["phase1_settled"], 2)
        self.assertEqual(view["phase2_settled"], 1)
        self.assertEqual(view["phase_percent"], 75.0)     # 真实完成度不是 0%
        self.assertEqual(view["current_phase"], 2)        # 第一轮已跑完 → 进入第二轮


if __name__ == "__main__":
    unittest.main()
