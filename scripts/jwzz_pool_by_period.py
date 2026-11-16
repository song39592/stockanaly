# -*- coding: utf-8 -*-
"""各区间的实际可用池子大小（注入判定口径）。

换区间验证的名义设计是「股票池固定，只换时间窗口」，但生产编排层的
`universe.filter_available` 会按区间内行情是否充足再剔一遍，
名义同一个池子，不同区间的实际可用只数其实不同。

本脚本只做取数判定，不跑回测。
"""
from __future__ import annotations

import os
import sys

ROOT = r"c:\Users\Administrator\Desktop\stockanaly\backend_fastapi"
sys.path.insert(0, ROOT)
os.chdir(ROOT)
sys.stdout.reconfigure(encoding="utf-8")

from strategies.core import data, universe          # noqa: E402

PERIODS = [
    ("2021-01-04", "2021-12-31"),
    ("2022-01-04", "2022-12-30"),
    ("2023-01-03", "2023-12-29"),
    ("2024-01-02", "2024-12-31"),
    ("2025-01-02", "2025-12-31"),
    ("2021-01-04", "2022-12-30"),
    ("2021-01-04", "2023-12-29"),
    ("2023-11-16", "2026-11-16"),
]


def main():
    codes = sorted(data.list_universe_codes())
    print("全市场代码数 = %d\n" % len(codes))
    print("  %-26s %8s %8s %14s" % ("区间", "请求", "可用", "因行情不足剔除"))
    for s, e in PERIODS:
        try:
            usable, avail = universe.filter_available(codes, s, e)
            ins = avail.get("insufficient") if isinstance(avail, dict) else None
            print("  %-26s %8d %8d %14s"
                  % (s + "~" + e, len(codes), len(usable),
                     ins if ins is not None else "-"))
        except Exception as exc:
            print("  %-26s 失败: %s: %s" % (s + "~" + e, type(exc).__name__, exc))
    data.release_cache(codes, adjust="hfq")


if __name__ == "__main__":
    main()
