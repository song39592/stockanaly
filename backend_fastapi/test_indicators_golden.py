# -*- coding: utf-8 -*-
"""指标**数值基准**自检：任何指标数值变化都会让本测试失败。

与 `test_price_store` 等并列跑（见 `run_tests.ps1`）。

为什么要有这个测试：`scan.py` 只验「能不能跑通」，**算出来的数对不对**没人管 ——
改一处共享内核会让若干指标的数值全变，而自检照样全绿，直到实盘出假信号才发现。
详见 `indicators/golden.py` 顶部说明。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from indicators import golden


class TestIndicatorGolden(unittest.TestCase):
    """基准比对。"""

    def test_golden_file_exists(self):
        """基准文件必须在（否则下面的比对无从谈起，且说明没跑过 --update）。"""
        self.assertTrue(os.path.exists(golden.GOLDEN_FILE),
                        f"数值基准文件不存在：{golden.GOLDEN_FILE}\n"
                        f"请运行：python -m indicators.golden --update")

    def test_numeric_values_unchanged(self):
        """全部指标的数值必须与基准逐项一致。"""
        expected = golden.load()
        problems = golden.diff(expected, golden.snapshot())
        if problems:
            self.fail(
                "指标数值与基准不一致（%d 处）：\n  - %s\n"
                "若这是**有意**的算法变更，请运行：python -m indicators.golden --update"
                % (len(problems), "\n  - ".join(problems))
            )


if __name__ == "__main__":
    unittest.main()
