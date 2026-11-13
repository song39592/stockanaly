# -*- coding: utf-8 -*-
"""后端「口径唯一来源」约定自检（纯源码扫描，**不 import 业务模块**，不碰数据）。

背景（2026-11-13 复盘）：回测范围口径（判定基准日 / 市值 / 剔除特征）一次优化
牵动了 **10 个文件**；按分层设计本该只改 `strategies/core/universe.py` 一个 ——
因为两处接缝当时不存在：

  ① **spec 构造散在路由层**：预览一份、实跑一份 → 改口径漏一处 →
     界面显示与实跑口径不一致 → **静默失真**（曾出现「界面显示剔除后 = 基池、
     实跑剔掉一千多只」）；
  ② **阈值 / 特征键没有「只此一处」的护栏** → 很容易在别处再抄一份。

本文件把这两条变成**可自动检查的硬约束**，与前端 `launcher/test-launcher-conventions.ps1`
同构（那边扫「控件文本不得当传参通道」，这边扫「口径不得有第二份实现」）。

规则：
  R1 阈值 / 特征键唯一      —— 只允许出现在 `strategies/core/universe.py`
  R2 spec 构造唯一入口      —— 其它文件不得出现 spec 字典字面量
  R3 分层不反向依赖          —— core 不得 import 路由层；universe 不得 import backtest
  R4 股票池入口唯一          —— `build_detail` 之外不得再定义 `build()`

跑法：cd backend_fastapi && python -m unittest test_conventions -v
"""
from __future__ import annotations

import os
import re
import unittest

BASE = os.path.dirname(os.path.abspath(__file__))
CORE_DIR = os.path.join(BASE, "strategies", "core")
UNIVERSE_PATH = os.path.join(CORE_DIR, "universe.py")
ROUTES_PATH = os.path.join(BASE, "strategies_routes.py")

# 只允许出现在 universe.py 的「口径符号」
_UNIQUE_SYMBOLS = (
    "CATEGORIES",
    "FEATURE_LABELS",
    "IPO_DAYS",
    "SUB_NEW_DAYS",
    "MICRO_CAP",
    "_FORWARD_LOOKING",
)


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _scan_targets() -> list[str]:
    """除 universe.py 以外、会被口径污染的源文件（core/*.py + strategies/*.py + 路由）。"""
    out = []
    for d in (CORE_DIR, os.path.join(BASE, "strategies")):
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if not name.endswith(".py"):
                continue
            p = os.path.join(d, name)
            if os.path.abspath(p) == os.path.abspath(UNIVERSE_PATH):
                continue
            out.append(p)
    out.append(ROUTES_PATH)
    return out


class TestScopeConventions(unittest.TestCase):
    """范围口径（股票池层）的唯一来源约定。"""

    def test_r1_threshold_symbols_only_in_universe(self):
        """R1：阈值 / 特征键常量只允许出现在 universe.py。"""
        bad = []
        for path in _scan_targets():
            text = _read(path)
            for sym in _UNIQUE_SYMBOLS:
                if re.search(r"\b%s\b" % re.escape(sym), text):
                    bad.append("%s 引用了 %s" % (os.path.relpath(path, BASE), sym))
        self.assertEqual([], bad, "口径常量被复制到别处：\n  " + "\n  ".join(bad))

    def test_r2_spec_built_in_one_place(self):
        """R2：spec 只能由 universe.spec_from_request 构造。

        判据（两条，都要成立才算合规）：
          ① 除 universe.py 外**不得再定义** spec 构造器（`def xxx_spec(...)` 之类）；
          ② 调用股票池层入口时**不得直接传字典字面量** —— 必须走 spec_from_request。

        ⚠️ 不要简单查 `"universe_mode":` 字符串：报告 scope 里**回显**这个字段是正常的
        （值来自股票池层给的 scope_extra），那是「透传」不是「第二份实现」。
        """
        bad = []
        for path in _scan_targets():
            text = _read(path)
            for m in re.finditer(r"^\s*def\s+\w*spec\w*\s*\(", text, re.M):
                line = text[: m.start()].count("\n") + 1
                bad.append("%s:%d 定义了第二个 spec 构造器" % (
                    os.path.relpath(path, BASE), line))
            for m in re.finditer(r"(build_detail|summary)\(\s*\{", text):
                line = text[: m.start()].count("\n") + 1
                bad.append("%s:%d 调用股票池层时直接传了字典字面量" % (
                    os.path.relpath(path, BASE), line))
        self.assertEqual([], bad, "spec 在别处被手工拼装：\n  " + "\n  ".join(bad))

    def test_r3_no_reverse_dependency(self):
        """R3：core 不得反向 import 路由层；universe 不得 import backtest。"""
        bad = []
        for path in _scan_targets():
            if os.path.dirname(os.path.abspath(path)) != os.path.abspath(CORE_DIR):
                continue
            text = _read(path)
            if re.search(r"""^\s*(from|import)\s+.*strategies_routes""", text, re.M):
                bad.append("%s 反向 import 了路由层" % os.path.relpath(path, BASE))
        uni = _read(UNIVERSE_PATH)
        if re.search(r"""^\s*(from\s+\.\s+import\s+backtest|from\s+\.backtest\s+import|import\s+backtest)""",
                     uni, re.M):
            bad.append("strategies/core/universe.py import 了 backtest（股票池层不得依赖编排层）")
        self.assertEqual([], bad, "分层被反向依赖：\n  " + "\n  ".join(bad))

    def test_r4_single_pool_entry(self):
        """R4：股票池层入口唯一 —— 除 build_detail 外不许再有 build()。"""
        bad = []
        for path in _scan_targets():
            if os.path.dirname(os.path.abspath(path)) != os.path.abspath(CORE_DIR):
                continue
            text = _read(path)
            for m in re.finditer(r"^def\s+build\s*\(", text, re.M):
                line = text[: m.start()].count("\n") + 1
                bad.append("%s:%d 定义了第二个股票池入口 build()" % (
                    os.path.relpath(path, BASE), line))
        self.assertEqual([], bad, "股票池入口不唯一：\n  " + "\n  ".join(bad))

    def test_spec_from_request_is_the_entry(self):
        """spec_from_request 必须存在，且 as_of 固定取区间起点（回测当时视角）。"""
        import strategies.core.universe as universe

        spec = universe.spec_from_request(
            {"start": "2023-11-13", "end": "2026-11-13", "exclude": ["micro", "st"]}
        )
        self.assertEqual("2023-11-13", spec["as_of"])
        self.assertEqual(["micro", "st"], spec["exclude"])
        self.assertEqual("interval_data", spec["universe_mode"])
        # 对象形态（pydantic 请求）同样支持
        class _Req:
            start = "2023-11-13"
            end = "2026-11-13"
            exclude = ["micro"]
            universe_mode = "pool_snapshot"
        spec2 = universe.spec_from_request(_Req())
        self.assertEqual("2023-11-13", spec2["as_of"])
        self.assertEqual("pool_snapshot", spec2["universe_mode"])


if __name__ == "__main__":
    unittest.main()
