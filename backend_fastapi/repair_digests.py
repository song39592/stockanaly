# -*- coding: utf-8 -*-
"""维护脚本：修复「指纹与数据不一致」的日 K 记录。

用法（在 backend_fastapi 目录、用 venv python）：
    .\\venv\\Scripts\\python.exe repair_digests.py            # 干跑：只列失配清单，不落库
    .\\venv\\Scripts\\python.exe repair_digests.py --apply    # 真正按当前数据重算指纹

背景：某次批量增量同步（source=腾讯证券）写库后没刷指纹，导致大量 (代码, 年份分片)
指纹停在旧行数 / 旧内容，`load_bars` 抛 UntrustedDataError。数据出自可信管道、非篡改，
故按「当前数据 = 新基线」修复（见 price_store.repair_digests）。

⚠️ 只在确认数据可信、且后端进程**没有正在写日 K** 时执行 --apply：
若同步任务恰好写库，重算出的指纹可能又立刻过期（无害但会重复触发校验失败）。
"""
from __future__ import annotations

import argparse
import sys

import price_store

# 控制台可能是 GBK（未 chcp 65001 时），中文输出会炸；统一按 UTF-8 输出
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    ap = argparse.ArgumentParser(description="修复日K指纹（默认干跑）")
    ap.add_argument("--apply", action="store_true", help="真正重算指纹（缺省只干跑）")
    args = ap.parse_args()

    res = price_store.repair_digests(dry_run=not args.apply)
    label = "已修复" if args.apply else "干跑（未落库）"
    print(f"{label}：失配 {res['count']} 处")
    for year, n in sorted(res["by_year"].items()):
        print(f"  {year} 年分片: {n}")
    shown = res["items"][:40]
    for item in shown:
        print(f"  {item['code']} @ {item['year']}: {item['reason']}")
    if len(res["items"]) > 40:
        print(f"  ... 其余 {len(res['items']) - 40} 处略")
    if args.apply and res["updated"]:
        print(f"已重算 {len(res['updated'])} 条指纹。")


if __name__ == "__main__":
    main()
