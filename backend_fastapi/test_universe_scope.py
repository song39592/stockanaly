# -*- coding: utf-8 -*-
"""回测范围（股票池层）口径的单元测试。

覆盖五件事：
  1. 判定基准日 = **区间起点**（不是区间末日、也不是今天）；
  2. 微盘股按**区间起点**的市值判定（point-in-time，不用「今天的市值」）；
  3. 新股/次新股以**交易所公布的真实上市日**、从区间起点起算：基准日之后才上市的票要剔掉；
  4. 每项剔除了多少只**必须回传**（否则报告无法自证样本）；
  5. 口径说明必须点明前视偏差与数据限制（ST 用当前名单 / 股本只有当前值 /
     上市日来源与覆盖率 / 兜底只数）。
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from strategies.core import universe  # noqa: E402

BASE = ["600000", "600001", "300001", "688001", "000002"]


def _spec(**kw):
    s = {"universe_mode": "interval_data", "start": "2023-11-13",
         "end": "2026-11-13", "as_of": "2023-11-13", "exclude": []}
    s.update(kw)
    return s


class TestAsOfDate(unittest.TestCase):
    """判定基准日：as_of → start → 今天。"""

    def test_as_of_wins(self):
        d = universe._as_of_date(_spec(as_of="2020-01-02", start="2023-11-13"))
        self.assertEqual(d.isoformat(), "2020-01-02")

    def test_fallback_to_start(self):
        s = _spec()
        s.pop("as_of")
        self.assertEqual(universe._as_of_date(s).isoformat(), "2023-11-13")

    def test_fallback_to_today(self):
        s = _spec()
        s.pop("as_of")
        s.pop("start")
        self.assertEqual(universe._as_of_date(s).isoformat(),
                         universe.dt.date.today().isoformat())


class TestExclusions(unittest.TestCase):
    """剔除口径与明细回传。"""

    def _patched(self, **over):
        """把数据层三个入口打桩，返回 (base, spec, 记录到的 as_of)。"""
        seen = {}

        def _list_universe_codes(universe_mode="interval_data", start=None, end=None):
            return list(BASE)

        def _load_names(codes):
            # 名单必须**覆盖全部**待判定代码：覆盖不足会中止（见 TestStNameUnavailable）
            return {"600000": "ST测试", "600001": "*ST测试", "300001": "正常",
                    "688001": "正常", "000002": "正常"}

        def _load_listing_dates(codes):
            # **真实上市日**（交易所公布口径）：
            # 600001 上市于基准日前 73 天（新股）、300001 基准日**之后**才上市、
            # 600000 / 688001 是老票、000002 老票但不在夹具里 → 走兜底。
            return {"600000": "2022-10-09", "600001": "2023-09-01",
                    "300001": "2023-12-01", "688001": "2019-01-01",
                    "000002": "2015-06-01"}

        def _earliest_bar_dates():
            # 「库里最早日K」——**不是上市日**，只在上市日取不到时保守兜底。
            # 夹具里上市日齐全 → 用不到。
            return {}

        def _market_caps_at(codes, as_of):
            seen["as_of"] = as_of
            return ({"600001": 1.0e9, "300001": 2.0e9},
                    {"shares_source": "latest", "fallback_codes": 1,
                     "price_date": as_of})

        p = {"list_universe_codes": _list_universe_codes,
             "load_names": _load_names,
             "names_source_status": lambda: {"ok": True, "size": len(BASE),
                                             "source": "test", "error": ""},
             "load_listing_dates": _load_listing_dates,
             "listing_source_status": lambda: {"ok": True, "size": len(BASE),
                                               "source": "test", "error": ""},
             "earliest_bar_dates": _earliest_bar_dates,
             "market_caps_at": _market_caps_at}
        p.update(over)
        return patch.multiple(universe.data, **p), seen

    def test_micro_uses_start_date_price(self):
        """微盘判定必须用区间起点当日的市值（不是今天）。"""
        patches, seen = self._patched()
        with patches:
            codes, dropped, extra = universe._apply_exclusions(
                list(BASE), _spec(exclude=["micro"]))
        self.assertEqual(seen["as_of"], "2023-11-13")
        self.assertEqual(dropped["micro"], 2)          # 600001 / 300001 市值 ≤30 亿
        self.assertNotIn("600001", codes)
        self.assertEqual(extra["market_cap_meta"]["shares_source"], "latest")

    def test_forward_listed_removed_and_counted(self):
        """基准日之后才上市的票要剔掉，且每项只数要回传。

        夹具里 600001 上市于基准日前 73 天（真新股）、300001 上市于基准日**之后**
        （基准日时还不存在）—— 两者都该被剔；688001 是老票，必须留下。
        """
        patches, _ = self._patched()
        with patches:
            codes, dropped, _extra = universe._apply_exclusions(
                list(BASE), _spec(exclude=["ipo"]))
        self.assertNotIn("300001", codes)              # 上市日 2023-12-01 > 基准日
        self.assertNotIn("600001", codes)              # 上市 73 天 = 新股
        self.assertIn("688001", codes)                 # 老票保留
        self.assertEqual(dropped["ipo"], 2)

    def test_each_feature_counted(self):
        """每项剔除只数必须分别统计（ST 与 *ST 不混在一起）。"""
        patches, _ = self._patched()
        with patches:
            _codes, dropped, _extra = universe._apply_exclusions(
                list(BASE), _spec(exclude=["st", "star_st"]))
        self.assertEqual(dropped["st"], 1)
        self.assertEqual(dropped["star_st"], 1)

    def test_no_exclude_returns_all(self):
        codes, dropped, extra = universe._apply_exclusions(list(BASE), _spec())
        self.assertEqual(len(codes), len(BASE))
        self.assertEqual(dropped, {})
        self.assertEqual(extra, {})


class TestBuildDetail(unittest.TestCase):
    """明细字段与口径说明。"""

    def setUp(self):
        self._patches = patch.multiple(
            universe.data,
            list_universe_codes=lambda universe_mode="interval_data", start=None, end=None: list(BASE),
            load_names=lambda codes: {c: "正常" for c in BASE},
            names_source_status=lambda: {"ok": True, "size": len(BASE),
                                         "source": "test", "error": ""},
            load_listing_dates=lambda codes: {c: "2015-06-01" for c in BASE},
            listing_source_status=lambda: {"ok": True, "size": len(BASE),
                                          "source": "test", "error": ""},
            earliest_bar_dates=lambda: {},
            market_caps_at=lambda codes, as_of: ({}, {"shares_source": "as_of",
                                                       "fallback_codes": 0,
                                                       "price_date": as_of}),
        )
        self._patches.start()
        universe.invalidate()

    def tearDown(self):
        self._patches.stop()
        universe.invalidate()

    def test_detail_fields(self):
        codes, detail = universe.build_detail(
            _spec(exclude=["st", "micro"]), force=True)
        self.assertEqual(detail["base_count"], len(BASE))
        self.assertEqual(detail["filtered_count"], len(codes))
        self.assertEqual(detail["as_of"], "2023-11-13")
        self.assertEqual(detail["exclude"], ["micro", "st"])
        self.assertEqual(detail["forward_looking"], ["st"])
        self.assertIn("dropped", detail)

    def test_detail_does_not_judge_availability(self):
        """特征剔除（build_detail）与**注入判定**是两道关，不能混在一起。

        注入判定要用「实际取数区间」，那个区间在运行时才定（短窗口要向前预热），
        所以它由编排层在定下区间后调 `universe.filter_available`；预览走 `summary`。
        """
        _codes, detail = universe.build_detail(_spec(exclude=["st"]), force=True)
        self.assertNotIn("available_count", detail)
        self.assertNotIn("skipped_insufficient", detail)

    def test_note_declares_bias_and_limits(self):
        """口径说明必须点明：前视偏差 + 股本当前值 + 上市日近似。"""
        _codes, detail = universe.build_detail(
            _spec(exclude=["st", "ipo", "micro"]), force=True)
        note = universe.scope_note(detail)
        self.assertIn("2023-11-13", note)          # 判定基准日
        self.assertIn("当前", note)                 # ST 用当前名单
        self.assertIn("上市日", note)               # 上市日近似
        self.assertIn("剔除明细", note)             # 每项只数


class TestStNameUnavailable(unittest.TestCase):
    """ST 名称名单取不到 / 覆盖不足 → **必须中止**，不能静默降级（实测踩出来的坑）。

    旧口径会静默降级成「一只 ST 都没剔」→ 报告的剔除明细里只是没有 st 这一项，
    口径指纹也算不出差别 → 两份报告「看起来同口径」而池子差 126 只、收益差 5%。
    现在：勾了剔除、名单却拿不到 → 明确中止，并给出可操作的下一步。
    """

    def _patches(self, names_status, names_map):
        return patch.multiple(
            universe.data,
            list_universe_codes=lambda universe_mode="interval_data", start=None, end=None: list(BASE),
            load_names=lambda codes: names_map,
            names_source_status=lambda: names_status,
            load_listing_dates=lambda codes: {c: "2015-06-01" for c in BASE},
            listing_source_status=lambda: {"ok": True, "size": len(BASE),
                                          "source": "test", "error": ""},
            earliest_bar_dates=lambda: {},
            market_caps_at=lambda codes, as_of: ({}, {"shares_source": "as_of",
                                                       "fallback_codes": 0,
                                                       "price_date": as_of}),
        )

    def test_unavailable_names_abort(self):
        p = self._patches({"ok": False, "size": 0, "error": "Timeout"}, {})
        with p:
            with self.assertRaises(universe.ScopeDataUnavailable) as cm:
                universe._apply_exclusions(list(BASE), _spec(exclude=["st", "star_st"]))
        msg = str(cm.exception)
        self.assertIn("取不到", msg)
        self.assertIn("取消", msg)          # 必须给出可操作的下一步

    def test_low_coverage_aborts(self):
        p = self._patches({"ok": True, "size": 5, "error": ""},
                           {"600000": "正常"})          # 只覆盖 1/5 = 20%
        with p:
            with self.assertRaises(universe.ScopeDataUnavailable) as cm:
                universe._apply_exclusions(list(BASE), _spec(exclude=["st"]))
        self.assertIn("覆盖", str(cm.exception))

    def test_full_coverage_passes_and_reports(self):
        names = {"600000": "ST测试", "600001": "*ST测试", "300001": "正常",
                 "688001": "正常", "000002": "正常"}
        p = self._patches({"ok": True, "size": 5, "error": ""}, names)
        with p:
            _codes, dropped, extra = universe._apply_exclusions(
                list(BASE), _spec(exclude=["st", "star_st"]))
        self.assertEqual(dropped["st"], 1)
        self.assertEqual(dropped["star_st"], 1)
        self.assertEqual(extra["st_name_coverage"], 1.0)

    def test_note_declares_st_not_excluded(self):
        """没勾 ST 剔除 → 口径说明必须**显式写明**，否则读者会以为「本来就没有 ST」。"""
        note = universe.scope_note({"base_count": 10, "filtered_count": 10,
                                    "dropped": {}, "exclude": [], "as_of": "2024-01-02"})
        self.assertIn("未剔除", note)
        self.assertIn("ST", note)


class TestListingDateUnavailable(unittest.TestCase):
    """上市日取不到 / 覆盖不足 → **必须中止**（与 ST 名单同一纪律）。

    旧口径用「库里最早日K」当上市日 —— 那只是**本地下载起点**（2021-08-02），
    实测 5601 只里 **4283 只**的「最早日K」= 那天（连 000001 / 600519 都算那天上市）
    → 判定基准日改成区间起点后「天龄 = 0 ≤ 90」→ `ipo` 一次剔掉 **5599 只**，
    实跑只剩 2 只；而页面预览按「今天」算只剔 430 只 → 预览与实跑口径分叉。

    新口径：交易所公布的**真实上市日**；取不到就**中止**，不许拿近似值硬顶
    （「勾了剔除新股却一只没剔」会让剔除明细看起来像「本来就没有新股」）。
    """

    def _patches(self, status, listing_map, fallback=None):
        return patch.multiple(
            universe.data,
            list_universe_codes=lambda universe_mode="interval_data", start=None, end=None: list(BASE),
            load_names=lambda codes: {c: "正常" for c in BASE},
            names_source_status=lambda: {"ok": True, "size": len(BASE),
                                         "source": "test", "error": ""},
            load_listing_dates=lambda codes: listing_map,
            listing_source_status=lambda: status,
            earliest_bar_dates=lambda: (fallback or {}),
            market_caps_at=lambda codes, as_of: ({}, {"shares_source": "as_of",
                                                       "fallback_codes": 0,
                                                       "price_date": as_of}),
        )

    def test_unavailable_listing_aborts(self):
        p = self._patches({"ok": False, "size": 0, "error": "Timeout"}, {})
        with p:
            with self.assertRaises(universe.ScopeDataUnavailable) as cm:
                universe._apply_exclusions(list(BASE), _spec(exclude=["ipo"]))
        msg = str(cm.exception)
        self.assertIn("取不到", msg)
        self.assertIn("取消", msg)          # 必须给出可操作的下一步

    def test_low_coverage_aborts(self):
        p = self._patches({"ok": True, "size": 5, "error": ""},
                           {"600000": "2020-01-01"})          # 只覆盖 1/5 = 20%
        with p:
            with self.assertRaises(universe.ScopeDataUnavailable) as cm:
                universe._apply_exclusions(list(BASE), _spec(exclude=["ipo"]))
        self.assertIn("覆盖", str(cm.exception))

    def test_missing_code_falls_back_to_earliest_bar(self):
        """少数票不在交易所当前上市表（已退市等）→ 用「库里最早日K」保守兜底，
        且**兜底只数必须落盘**（读者要能分开看「真实数据」与「兜底」）。"""
        base = ["60%04d" % i for i in range(100)]
        listing = {c: "2020-01-01" for c in base[:98]}
        # 兜底日期离基准日（2023-11-13）不到 90 天 → 按新股剔掉
        fallback = {base[98]: "2023-11-01", base[99]: "2023-11-02"}
        p = self._patches({"ok": True, "size": 98, "error": ""}, listing, fallback)
        with p:
            _codes, dropped, extra = universe._apply_exclusions(
                base, _spec(exclude=["ipo"]))
        self.assertEqual(extra["listing_date_source"], "exchange_listing_date")
        self.assertEqual(extra["listing_date_coverage"], 0.98)
        self.assertEqual(extra["listing_date_fallback"], 2)
        self.assertEqual(extra["listing_date_unknown"], 0)
        self.assertEqual(dropped["ipo"], 2)

    def test_unknown_listing_is_declared_not_silently_dropped(self):
        """上市日完全取不到 → 不判定（按非新股），但**只数要落盘**。"""
        base = ["60%04d" % i for i in range(100)]
        listing = {c: "2020-01-01" for c in base[:97]}
        p = self._patches({"ok": True, "size": 97, "error": ""}, listing, {})
        with p:
            _codes, dropped, extra = universe._apply_exclusions(
                base, _spec(exclude=["ipo"]))
        self.assertEqual(extra["listing_date_unknown"], 3)
        self.assertEqual(extra["listing_date_fallback"], 0)
        self.assertNotIn("ipo", dropped)   # 没上市日 → 不判定，不算新股

    def test_note_declares_listing_source(self):
        """口径说明必须说清上市日来源 + 覆盖率 + 兜底只数。"""
        base = ["60%04d" % i for i in range(100)]
        listing = {c: "2020-01-01" for c in base[:98]}
        fallback = {base[98]: "2023-11-01", base[99]: "2023-11-02"}
        p = self._patches({"ok": True, "size": 98, "error": ""}, listing, fallback)
        with p:
            _codes, _dropped, extra = universe._apply_exclusions(
                base, _spec(exclude=["ipo"]))
            note = universe.scope_note({
                "base_count": len(base), "filtered_count": len(base),
                "dropped": {}, "exclude": ["ipo"], "as_of": "2023-11-13",
                **extra,
            })
        self.assertIn("上市日", note)
        self.assertIn("真实上市日", note)
        self.assertIn("兜底", note)


class TestPoolSignature(unittest.TestCase):
    """池子**结果**指纹：两份报告一比就知道池子是不是真的同一个。

    `spec_signature` 只压常量 → 覆盖不到运行时差异（ST 名单这次取到没有、
    区间端点、实际剔了多少只）→ 曾出现指纹完全相同、池子差 126 只、收益差 5%
    却「看起来同口径」。`pool_signature` 把这些都压进去。
    """

    def setUp(self):
        self._patches = patch.multiple(
            universe.data,
            list_universe_codes=lambda universe_mode="interval_data", start=None, end=None: list(BASE),
            load_names=lambda codes: {c: "正常" for c in BASE},
            names_source_status=lambda: {"ok": True, "size": len(BASE),
                                         "source": "test", "error": ""},
            load_listing_dates=lambda codes: {c: "2015-06-01" for c in BASE},
            listing_source_status=lambda: {"ok": True, "size": len(BASE),
                                          "source": "test", "error": ""},
            earliest_bar_dates=lambda: {},
            market_caps_at=lambda codes, as_of: ({}, {"shares_source": "as_of",
                                                       "fallback_codes": 0,
                                                       "price_date": as_of}),
        )
        self._patches.start()
        universe.invalidate()

    def tearDown(self):
        self._patches.stop()
        universe.invalidate()

    def test_signature_present_and_stable(self):
        _c1, d1 = universe.build_detail(_spec(exclude=["st"]), force=True)
        _c2, d2 = universe.build_detail(_spec(exclude=["st"]), force=True)
        self.assertTrue(d1.get("pool_signature"))
        self.assertEqual(d1["pool_signature"], d2["pool_signature"])

    def test_signature_differs_when_pool_differs(self):
        _c1, d1 = universe.build_detail(_spec(exclude=[]), force=True)
        _c2, d2 = universe.build_detail(_spec(exclude=["st"]), force=True)
        self.assertNotEqual(d1["pool_signature"], d2["pool_signature"])


class TestAvailability(unittest.TestCase):
    """注入判定（能不能注入给策略）—— 股票池层的**唯一**口径。"""

    def _counts(self, mapping):
        return patch.multiple(universe.data, bar_counts=lambda start, end, adjust="raw": mapping)

    def test_keeps_only_codes_with_enough_bars(self):
        """区间内根数 < MIN_BARS 的票不可注入；只数必须回传。"""
        mapping = {"600000": universe.MIN_BARS, "600001": universe.MIN_BARS - 1,
                   "300001": 0, "688001": 120, "000002": 100}
        with self._counts(mapping):
            keep, info = universe.filter_available(list(BASE), "2023-11-13", "2026-11-13")
        self.assertEqual(keep, ["600000", "688001", "000002"])
        self.assertEqual(info["insufficient"], 2)
        self.assertEqual(info["window"], ["2023-11-13", "2026-11-13"])

    def test_no_interval_keeps_all(self):
        """区间未定 → 无法判断数据够不够（口径不猜），全部放行。"""
        with self._counts({}):
            keep, info = universe.filter_available(list(BASE), None, None)
        self.assertEqual(keep, list(BASE))
        self.assertEqual(info["insufficient"], 0)

    def test_resolve_window_extends_for_warmup(self):
        """短窗口要向前多取预热根 —— 取数区间只有一份实现。"""
        days = ["2024-01-%02d" % d for d in range(1, 29)]
        with patch.multiple(universe.data, recent_trading_days=lambda n: days):
            start, end = universe.resolve_window("2024-01-01", "2024-01-10", 5)
        self.assertEqual((start, end), (days[0], days[-1]))
        # 不给 window → 原样返回
        with patch.multiple(universe.data, recent_trading_days=lambda n: days):
            self.assertEqual(universe.resolve_window("2024-01-01", "2024-01-10"),
                             ("2024-01-01", "2024-01-10"))

    def test_resolve_lookback_window_fills_missing_bound(self):
        """推荐页：缺任一端 → 最近 lookback 个交易日（与回测同一套区间口径）。"""
        days = ["2024-02-%02d" % d for d in range(1, 29)]
        with patch.multiple(universe.data, recent_trading_days=lambda n: days):
            start, end = universe.resolve_lookback_window(None, None, 250)
        self.assertEqual((start, end), (days[0], days[-1]))
        with patch.multiple(universe.data, recent_trading_days=lambda n: days):
            self.assertEqual(universe.resolve_lookback_window("2024-01-01", "2024-01-10"),
                             ("2024-01-01", "2024-01-10"))


class TestPreviewEqualsRun(unittest.TestCase):
    """预览显示的「可注入 N 只」必须等于实跑参与统计的只数。"""

    def setUp(self):
        self._patches = patch.multiple(
            universe.data,
            list_universe_codes=lambda universe_mode="interval_data", start=None, end=None: list(BASE),
            load_names=lambda codes: {c: "正常" for c in BASE},
            names_source_status=lambda: {"ok": True, "size": len(BASE),
                                         "source": "test", "error": ""},
            load_listing_dates=lambda codes: {c: "2015-06-01" for c in BASE},
            listing_source_status=lambda: {"ok": True, "size": len(BASE),
                                          "source": "test", "error": ""},
            earliest_bar_dates=lambda: {},
            market_caps_at=lambda codes, as_of: ({}, {"shares_source": "as_of",
                                                       "fallback_codes": 0,
                                                       "price_date": as_of}),
            bar_counts=lambda start, end, adjust="raw": {
                "600000": 100, "600001": 5, "300001": 100, "688001": 100,
                "000002": 29},
        )
        self._patches.start()
        universe.invalidate()

    def tearDown(self):
        self._patches.stop()
        universe.invalidate()

    def test_summary_available_count_matches_filter(self):
        spec = _spec(exclude=["st"])
        out = universe.summary(spec)
        codes, _detail = universe.build_detail(spec)
        start, end = universe.resolve_window(spec.get("start"), spec.get("end"),
                                            spec.get("window"))
        keep, avail = universe.filter_available(codes, start, end)
        self.assertEqual(out["available_count"], len(keep))
        self.assertEqual(out["skipped_insufficient"], avail["insufficient"])
        # 根数不足的票（5 根 / 29 根）确实被剔掉了
        self.assertLess(out["available_count"], out["filtered_count"])

    def test_note_reports_available_count(self):
        out = universe.summary(_spec(exclude=["st"]))
        self.assertIn("可注入", out["note"])
        self.assertIn(str(out["available_count"]), out["note"])

    def test_note_without_feature_detail_has_no_fake_base_count(self):
        """自定义代码池（没做特征剔除）→ 口径说明里不能出现「基池 None 只」。"""
        note = universe.scope_note({"available_count": 7, "skipped_insufficient": 2})
        self.assertNotIn("None", note)
        self.assertIn("可注入 7 只", note)


class TestOrchestratorIsConsumer(unittest.TestCase):
    """编排层只消费股票池层的结论，不再自己判「根数够不够」。"""

    def test_no_threshold_constant_in_backtest(self):
        from strategies.core import backtest
        self.assertFalse(hasattr(backtest, "_MIN_BARS"),
                         "阈值住在股票池层（universe.MIN_BARS），编排层不该再有一份")

    def test_prepare_keeps_short_history_code(self):
        """`_prepare` 只取数：根数少的票不再被编排层悄悄剔掉。"""
        import pandas as pd
        from strategies.core import backtest
        df = pd.DataFrame({"close": [1.0, 2.0, 3.0]})
        with patch.multiple(backtest.data, load_bars=lambda *a, **k: {"600000": df}):
            usable, errors = backtest._prepare(["600000"], "2024-01-01", "2024-01-31")
        self.assertIn("600000", usable)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
