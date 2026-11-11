# -*- coding: utf-8 -*-
"""check_deps 的单测：依赖解析（致命 vs 可选）与版本比较。

这几个函数决定「启动时会不会拦住用户」——
判错一次就是：把好环境说成坏的（引导用户反复重装），或把坏环境说成好的（功能静默失效）。
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import check_deps  # noqa: E402


class TestParseRequirements(unittest.TestCase):
    def _write(self, text):
        fd, path = tempfile.mkstemp(suffix=".txt")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        self.addCleanup(os.remove, path)
        return path

    def test_取包名与版本约束(self):
        got = check_deps.parse_requirements(
            self._write("pandas>=2.2,<3.0\nnumpy>=1.26,<2.2\nfastapi\n"))
        self.assertEqual([g[0] for g in got], ["pandas", "numpy", "fastapi"])
        self.assertEqual(got[0][1], ">=2.2,<3.0")
        self.assertEqual(got[2][1], "")

    def test_注释与选项行被跳过(self):
        got = check_deps.parse_requirements(self._write(
            "# 整行注释\n-r other.txt\n--index-url https://x\nrequests\n"))
        self.assertEqual([g[0] for g in got], ["requests"])

    def test_可选依赖标记(self):
        got = check_deps.parse_requirements(self._write(
            "fastapi\npypinyin  # optional: 缺了仅拼音搜索失效\n"))
        self.assertFalse(got[0][2])      # fastapi 致命
        self.assertTrue(got[1][2])       # pypinyin 可选

    def test_带BOM的文件首行不能被吞(self):
        """⚠️ 回归：BOM 让首行多一个 U+FEFF → 整行被静默跳过 → 该包永远检不出来。"""
        path = self._write("numpy>=1.26,<2.2\npandas>=2.2,<3.0\n")
        with open(path, "r", encoding="utf-8-sig") as fh:
            data = fh.read()
        with open(path, "w", encoding="utf-8-sig") as fh:
            fh.write(data)
        got = check_deps.parse_requirements(path)
        self.assertEqual([g[0] for g in got], ["numpy", "pandas"])

    def test_重复依赖只取一次(self):
        got = check_deps.parse_requirements(self._write("requests\nRequests\n"))
        self.assertEqual([g[0] for g in got], ["requests"])


class TestSatisfies(unittest.TestCase):
    def test_区间约束(self):
        self.assertFalse(check_deps.satisfies("3.0.5", ">=2.2,<3.0"))
        self.assertTrue(check_deps.satisfies("2.3.3", ">=2.2,<3.0"))
        self.assertTrue(check_deps.satisfies("2.2", ">=2.2,<3.0"))
        self.assertTrue(check_deps.satisfies("1.26.0", ">=1.26,<2.2"))
        self.assertFalse(check_deps.satisfies("2.2.0", ">=1.26,<2.2"))

    def test_等于与不等于(self):
        self.assertTrue(check_deps.satisfies("2.3.0", ">=2.3"))
        self.assertFalse(check_deps.satisfies("2.2.9", ">=2.3"))
        self.assertFalse(check_deps.satisfies("2.3.0", "!=2.3.0"))
        self.assertTrue(check_deps.satisfies("2.3.1", "!=2.3.0"))

    def test_看不懂的一律不判定(self):
        """宁可漏报，绝不误报 —— 误报会把好环境说成坏的。"""
        for ver, spec in [("3.0.5", ""), ("3.0.5rc1", ">=2.2"),
                          ("3.0.5", "~=3.0"), ("3.0.5", "==3.0.*"), ("1!2.0", ">=2.2")]:
            self.assertIsNone(check_deps.satisfies(ver, spec), ver + " " + spec)


class TestInspect(unittest.TestCase):
    def test_致命与可选分流(self):
        """缺可选依赖 → 归 optional_missing，**不进 missing**（否则会拦住启动）。"""
        missing, optional_missing, _ = check_deps.inspect([
            ("json", "", False),
            ("definitely-not-installed-xyz", "", False),
            ("also-not-installed-xyz", "", True)])
        self.assertIn("also-not-installed-xyz", optional_missing)
        self.assertNotIn("also-not-installed-xyz", missing)
        self.assertIn("definitely-not-installed-xyz", missing)
