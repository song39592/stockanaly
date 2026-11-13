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

    def test_note_declares_bias_and_limits(self):
        """口径说明必须点明：前视偏差 + 股本当前值 + 上市日近似。"""
        _codes, detail = universe.build_detail(
            _spec(exclude=["st", "ipo", "micro"]), force=True)
        note = universe.scope_note(detail)
        self.assertIn("2023-11-13", note)          # 判定基准日
        self.assertIn("当前", note)                 # ST 用当前名单
        self.assertIn("上市日", note)               # 上市日近似
        self.assertIn("剔除明细", note)             # 每项只数


if __name__ == "__main__":
    unittest.main()
