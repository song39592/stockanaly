# -*- coding: utf-8 -*-
"""名称取数的**本地优先**口径测试（2026-11-23 起）。

背景：回测的 ST / *ST 剔除依赖「当前名称名单」。旧口径每次都联网取
`akshare.stock_info_a_code_name`（冷启动实测 50.8s、5572~5581 行）→ 断网跑不了；
在线表还比本地主数据表 `stock_meta`（5931 只、名称 100% 有）少 359 只
→ 那 359 只在 ST 判定里被当成「非 ST」放进来。

这里钉死四件事：
  1. 本地表可用 → **一次网络都不发**（断网也能跑）；
  2. 本地表空（新装环境）→ 才联网兜底；
  3. 本地覆盖不足 → 联网**只补缺口**，不覆盖本地已有项（本地优先）；
  4. 两边都没有 → `names_source_status().ok=False` → 上层**中止**（不放行降级）。
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from strategies.core import data  # noqa: E402
from strategies.core import universe  # noqa: E402

LOCAL = {"600000": "ST本地", "600001": "平安银行", "300001": "宁德时代"}
SNAP = "2026-11-20 08:30:00"


class TestNamesLocalFirst(unittest.TestCase):
    """取数顺序：本地 → （覆盖不足才）联网补缺口。"""

    def setUp(self):
        data.reset_names_cache()
        self.addCleanup(data.reset_names_cache)

    def test_local_first_never_touches_network(self):
        """本地表够用 → **一次网络都不发**（断网也能跑）。"""
        with patch.object(data.price_store, "stock_names",
                          return_value=(dict(LOCAL), SNAP)), \
             patch.object(data.price_store, "upsert_stock_names") as upsert, \
             patch.object(data, "_fetch_names_online") as online:
            out = data.load_names(["600000", "300001"])
        self.assertFalse(online.called, "本地够用时不该联网")
        self.assertFalse(upsert.called, "本地够用时不该有写库")
        self.assertEqual(out["600000"], "ST本地")
        self.assertEqual(out["300001"], "宁德时代")

    def test_online_fallback_persists_to_local_table(self):
        """联网兜底取到的名单必须**落盘**（取到就存）——
        只放进程内缓存的话，下次启动又要重取一遍（27~50s）。"""
        import market_service
        import pandas as pd

        frame = pd.DataFrame({"code": ["600000"], "name": ["ST在线"]})

        class _FakeAk:
            @staticmethod
            def stock_info_a_code_name():
                return frame

        saved = {}

        def _fake_upsert(names):
            saved.update(names)
            return {"upserted": len(names), "inserted": 1, "updated": 0, "error": None}

        with patch.dict(sys.modules, {"akshare": _FakeAk}), \
             patch.object(market_service, "_ak", return_value=frame), \
             patch.object(data.price_store, "upsert_stock_names",
                          side_effect=_fake_upsert) as upsert, \
             patch.object(data.price_store, "stock_names",
                          return_value=({"600000": "ST在线"}, "2026-11-23T10:00:00")):
            data._fetch_names_online()
        upsert.assert_called_once()
        self.assertEqual(saved, {"600000": "ST在线"})
        # 落盘后这份名单就是**本地表**里的 → 来源标签变纯本地
        self.assertEqual(data.names_source_status()["source"], "local.stock_meta")

    def test_status_exposes_source_and_snapshot(self):
        """来源与**快照时刻**必须透出来（报告要能写明判定依据是哪份表）。"""
        with patch.object(data.price_store, "stock_names",
                          return_value=(dict(LOCAL), SNAP)):
            data.load_names(["600000"])
        st = data.names_source_status()
        self.assertTrue(st["ok"])
        self.assertEqual(st["source"], "local.stock_meta")
        self.assertEqual(st["updated_at"], SNAP)
        self.assertIsNotNone(st["age_hours"])

    def test_empty_local_falls_back_online(self):
        """本地表空（新装环境）→ 才联网兜底，来源如实标成在线表。"""
        def _online():
            data._name_cache["online"] = {"600000": "ST在线"}
            data._name_cache["at"] = 1.0

        with patch.object(data.price_store, "stock_names", return_value=({}, "")), \
             patch.object(data, "_fetch_names_online", side_effect=_online) as online:
            out = data.load_names(["600000"])
        self.assertTrue(online.called, "本地为空时必须联网兜底")
        self.assertEqual(out["600000"], "ST在线")
        self.assertEqual(data.names_source_status()["source"],
                         "akshare.stock_info_a_code_name")

    def test_partial_local_triggers_supplement_not_overwrite(self):
        """本地覆盖不足 → 联网补缺口，但**本地已有的项不被覆盖**（本地优先）。"""
        local = {"600000": "ST本地", "600001": "平安银行"}   # 3 只里只有 2 只

        def _online():
            # 在线表覆盖全部 3 只，但 600000 的名字与本地不同 → 以本地为准
            data._name_cache["online"] = {"600000": "在线名字", "300001": "宁德时代"}
            data._name_cache["at"] = 1.0

        with patch.object(data.price_store, "stock_names",
                          return_value=(local, SNAP)), \
             patch.object(data, "_fetch_names_online", side_effect=_online) as online:
            out = data.load_names(["600000", "600001", "300001"])
        self.assertTrue(online.called, "本地覆盖 2/3 < 95% → 必须联网补")
        self.assertEqual(out["600000"], "ST本地", "本地已有的项不能被在线表覆盖")
        self.assertEqual(out["300001"], "宁德时代", "缺口要补上")

    def test_nothing_available_is_reported_not_silently_empty(self):
        """两边都取不到 → `ok=False` + 记原因（上层据此中止，不当「没有 ST」）。"""
        with patch.object(data.price_store, "stock_names", return_value=({}, "")), \
             patch.object(data, "_fetch_names_online") as online:
            online.side_effect = None
            out = data.load_names(["600000"])
        self.assertEqual(out["600000"], "")
        st = data.names_source_status()
        self.assertFalse(st["ok"])
        self.assertTrue(st["error"])


class TestStScopeAbortsWhenNamesUnavailable(unittest.TestCase):
    """上层纪律不放松：名单取不到 → **中止**（不是静默当成「没有 ST」）。"""

    def test_abort_when_no_names_at_all(self):
        with patch.multiple(
            universe.data,
            list_universe_codes=lambda universe_mode="interval_data", start=None,
            end=None: ["600000", "600001"],
            load_names=lambda codes: {},
            names_source_status=lambda: {"ok": False, "size": 0, "source": "",
                                        "error": "本地名称表为空或不可读"},
        ):
            with self.assertRaises(universe.ScopeDataUnavailable) as ctx:
                universe._apply_exclusions(
                    ["600000", "600001"],
                    {"exclude": ["st"], "start": "2023-11-13", "as_of": "2023-11-13"})
        msg = str(ctx.exception)
        self.assertIn("ST", msg)
        self.assertIn("stock_meta", msg)

    def test_source_and_snapshot_land_in_extra(self):
        """判定依据（来源 + 快照时刻）要回传给报告层。"""
        with patch.multiple(
            universe.data,
            list_universe_codes=lambda universe_mode="interval_data", start=None,
            end=None: ["600000", "600001"],
            load_names=lambda codes: {"600000": "ST测试", "600001": "正常"},
            names_source_status=lambda: {"ok": True, "size": 2,
                                        "source": "local.stock_meta",
                                        "updated_at": SNAP, "age_hours": 3.0,
                                        "error": ""},
        ):
            _codes, dropped, extra = universe._apply_exclusions(
                ["600000", "600001"],
                {"exclude": ["st"], "start": "2023-11-13", "as_of": "2023-11-13"})
        self.assertEqual(dropped["st"], 1)
        self.assertEqual(extra["st_name_source"], "local.stock_meta")
        self.assertEqual(extra["st_name_updated_at"], SNAP)


if __name__ == "__main__":
    unittest.main()
