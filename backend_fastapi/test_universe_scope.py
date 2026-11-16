# -*- coding: utf-8 -*-
"""回测范围（股票池层）口径的单元测试。

覆盖五件事：
  1. 判定基准日 = **区间起点**（不是区间末日、也不是今天）；
  2. 微盘股按**区间起点**的市值判定（point-in-time，不用「今天的市值」）；
  3. 新股/次新股以区间起点起算：基准日之后才上市的票要剔掉；
  4. 每项剔除了多少只**必须回传**（否则报告无法自证样本）；
  5. 口径说明必须点明前视偏差与数据限制（ST 用当前名单 / 股本只有当前值 /
     上市日以库里最早日K近似）。
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
            return {"600000": "ST测试", "600001": "*ST测试", "300001": "正常"}

        def _code_first_dates():
            # 600002 于基准日之后才上市；600000 上市于基准日前 400 天
            return {"600000": "2022-10-09", "600001": "2023-09-01",
                    "300001": "2023-12-01", "688001": "2019-01-01"}

        def _market_caps_at(codes, as_of):
            seen["as_of"] = as_of
            return ({"600001": 1.0e9, "300001": 2.0e9},
                    {"shares_source": "latest", "fallback_codes": 1,
                     "price_date": as_of})

        p = {"list_universe_codes": _list_universe_codes,
             "load_names": _load_names,
             "code_first_dates": _code_first_dates,
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
            load_names=lambda codes: {},
            code_first_dates=lambda: {},
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
            load_names=lambda codes: {},
            code_first_dates=lambda: {},
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
