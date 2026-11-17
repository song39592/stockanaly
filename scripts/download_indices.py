# -*- coding: utf-8 -*-
"""下载并落盘**指数**日 K（独立表 `index_bars`）。

为什么需要这个脚本：
  指数行情**不**在个股日 K 表里（`daily_bars` 没有品种字段，`000001` 既是上证指数
  也是平安银行 → 混存会撞主键，还会被当成股票进回测池）。
  所以指数走自己的库 `bars/indices.db`、自己的表 `index_bars`、自己的下载器
  （`core/price_service.py::sync_index`）。

用法：
    python scripts/download_indices.py                 # 全部指数，拉全历史
    python scripts/download_indices.py --codes sh000001 sh000852
    python scripts/download_indices.py --list          # 只看清单与本地已有跨度
    python scripts/download_indices.py --refetch       # 全量重抓（修复历史被改）

⚠️ 落盘后**不**进股票池：`strategies/core/data.py::list_universe_codes` 只查
个股日 K 表，指数永远不会出现在回测池里。
"""
from __future__ import annotations

import argparse
import os
import sys
import time

ROOT = r"c:\Users\Administrator\Desktop\stockanaly\backend_fastapi"
sys.path.insert(0, ROOT)
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001 - 老版本 Python 没有 reconfigure
    pass

from core import price_service, price_store   # noqa: E402


def show_local() -> None:
    """打印本地已落盘的指数与跨度。"""
    spans = price_store.index_date_spans()
    catalog = price_service.index_catalog()
    print("=== 本地指数库（%s）===" % price_store.db.INDEX_DB)
    if not spans:
        print("  （空）—— 先跑一次下载")
        return
    for code, (first, last) in spans.items():
        meta = catalog.get(code, {})
        print("  %-10s %-10s %s ~ %s  (%d 根)  %s"
              % (code, meta.get("name", "?"), first, last,
                 price_store.index_bar_count(code), meta.get("kind", "")))


def main() -> int:
    ap = argparse.ArgumentParser(description="下载指数日 K（独立表）")
    ap.add_argument("--codes", nargs="*", default=None,
                    help="要下载的指数代码（默认：清单里全部）")
    ap.add_argument("--list", action="store_true", help="只打印清单与本地跨度")
    ap.add_argument("--refetch", action="store_true", help="全量重抓并整体替换")
    ap.add_argument("--start", default="1990-01-01", help="起始日（默认 1990-01-01 = 全历史）")
    args = ap.parse_args()

    catalog = price_service.index_catalog()
    if args.list:
        print("=== 可用指数 ===")
        for code, meta in sorted(catalog.items()):
            print("  %-10s %-10s %s" % (code, meta["name"], meta["kind"]))
        show_local()
        return 0

    codes = list(args.codes) if args.codes else sorted(catalog)
    bad = [c for c in codes if c not in catalog]
    if bad:
        print("未知指数代码：%s（可用：%s）" % ("、".join(bad), "、".join(sorted(catalog))))
        return 1

    t0 = time.time()
    print("=== 下载指数日 K（%d 条）===" % len(codes))
    for code in codes:
        meta = catalog.get(code, {})
        try:
            if args.refetch:
                res = price_service.refetch_index(code)
            else:
                import datetime as dt
                res = price_service.sync_index(code, start=dt.date.fromisoformat(args.start))
            print("  %-10s %-8s %s ~ %s  写入 %4d 根  来源=%s"
                  % (code, meta.get("name", "?"), res.get("start"), res.get("end"),
                     res.get("count", res.get("after", 0)), res.get("source")), flush=True)
        except Exception as exc:                       # noqa: BLE001
            print("  %-10s %-8s ❌ %s: %s" % (code, meta.get("name", "?"),
                                             type(exc).__name__, exc), flush=True)
    show_local()
    print("\nelapsed = %.1fs" % (time.time() - t0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
