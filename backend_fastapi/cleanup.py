# -*- coding: utf-8 -*-
"""清理：日志轮转旧文件、旧诊断包、可重建缓存、跨周的周榜结果。

**为什么独立成一个模块、且刻意零业务依赖**
清理逻辑必须和「谁触发它」解耦：定时清理跑在**后端进程里**，
而「程序打不开」恰恰最需要清理（日志撑爆、缓存损坏）——那时定时器根本没机会执行。
所以本模块**不 import FastAPI / main / 任何业务 service**，配合 `清理日志与缓存.bat`
可以在后端完全无法启动时直接双击运行。连 `config` 都读不了时，退回默认数据目录照常工作。

清理对象（默认策略）：
    logs/*.log.<日期>        轮转下来的旧日志      → 超过 N 天删除（默认 7 天）
    logs/dump-*.zip          诊断包（dumplog 产出） → 超过 N 天删除
    cache/**                 可重建缓存             → 全部可清（定义上就能重建）
    chip/processed/scr90_rank_*.json  跨周周榜结果  → **当前周那份保留**，其余删除
    bars/*.db.bak            分片备份（迁移退路）   → **默认不动**，需显式 `bak=True`

**永不清理**：`state/`（库与锁）、`config/`（用户配置）、`bars/*.db`（行情分片）、
`chip/raw`、`chip/meta.json` —— 全是用户数据，删了不可逆。
宁可多占空间，也不能让清理功能变成数据事故。
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

# 与 logutil.KEEP_DAYS 保持一致（日志轮转 + 定时清理共用同一个保留口径）
DEFAULT_RETAIN_DAYS = 7

# 调度去重用的进程内兜底（download_store 不可用时使用）
_last_cleanup_date: str | None = None


def _human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"


def _data_dir() -> Path:
    """数据根目录。

    刻意做成「读不了 config 也能工作」：本脚本要在后端起不来时救急，
    那时 `.env` 可能正是坏掉的那个东西。失败就退回默认位置（仓库上一级）。
    """
    try:
        import config
        return Path(config.DATA_DIR)
    except Exception:                           # noqa: BLE001
        return Path(__file__).resolve().parent.parent.parent / "stockanaly-data"


def _log(message: str) -> None:
    """写日志（走 logutil 双写）；logutil 不可用时静默——清理不该因为日志失败而中断。"""
    try:
        import logutil
        logutil.logger.info(message)
    except Exception:                           # noqa: BLE001
        pass


def _cutoff(days: int) -> float:
    return (dt.datetime.now() - dt.timedelta(days=max(0, int(days)))).timestamp()


def _expired(path: Path, cutoff_ts: float) -> bool:
    try:
        return path.stat().st_mtime < cutoff_ts
    except OSError:
        return False


def _group(name: str, enabled: bool = True) -> dict:
    return {"kind": name, "enabled": enabled, "scanned": 0,
            "removed": [], "freed": 0, "errors": []}


def _remove(group: dict, path: Path, dry_run: bool) -> None:
    """记一笔再删（`先记录再删`，便于追溯）。"""
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    group["removed"].append({"path": path.name, "size": size, "size_text": _human(size)})
    group["freed"] += size
    if dry_run:
        return
    try:
        path.unlink()
    except Exception as exc:                    # noqa: BLE001
        group["errors"].append(f"{path.name}: {type(exc).__name__}: {exc}")


def _clean_logs(root: Path, cutoff_ts: float, dry_run: bool) -> dict:
    """轮转下来的旧日志（`backend.log.2026-10-05` 这种），**当前正在写的那份不动**。"""
    group = _group("logs")
    logs_dir = root / "logs"
    if not logs_dir.is_dir():
        return group
    for path in sorted(logs_dir.glob("*.log.*")):
        group["scanned"] += 1
        if _expired(path, cutoff_ts):
            _remove(group, path, dry_run)
    return group


def _clean_dumps(root: Path, cutoff_ts: float, dry_run: bool) -> dict:
    """旧诊断包（dumplog 产出）。"""
    group = _group("dumps")
    logs_dir = root / "logs"
    if not logs_dir.is_dir():
        return group
    for path in sorted(logs_dir.glob("dump-*.zip")):
        group["scanned"] += 1
        if _expired(path, cutoff_ts):
            _remove(group, path, dry_run)
    return group


def _clean_cache(root: Path, dry_run: bool) -> dict:
    """`cache/` 里的东西定义上就是可重建的，直接清（不含目录本身）。"""
    group = _group("cache")
    cache_dir = root / "cache"
    if not cache_dir.is_dir():
        return group
    for path in sorted(cache_dir.rglob("*")):
        if not path.is_file():
            continue
        group["scanned"] += 1
        _remove(group, path, dry_run)
    return group


def _current_week() -> dt.date | None:
    """当前周节点（周一）。复用 chip_rank_service 的口径，避免这里再抄一份规则。"""
    try:
        import chip_rank_service
        return chip_rank_service._week_nodes(1)[0]
    except Exception:                           # noqa: BLE001
        return None


def _clean_scr90(root: Path, dry_run: bool) -> dict:
    """跨周的周榜结果：**只删非当前周**的，当前周那份必须留着（页面要用）。"""
    group = _group("scr90")
    processed = root / "chip" / "processed"
    if not processed.is_dir():
        return group
    week = _current_week()
    if week is None:
        group["errors"].append("无法确定当前周节点（chip_rank_service 不可用），已跳过")
        return group
    keep = "scr90_rank_" + week.strftime("%Y%m%d") + ".json"
    for path in sorted(processed.glob("scr90_rank_*.json")):
        group["scanned"] += 1
        if path.name == keep:
            continue
        _remove(group, path, dry_run)
    return group


def _clean_bak(root: Path, dry_run: bool) -> dict:
    """`bars/*.db.bak`：早期分片迁移留下的备份（约 1 GB），是迁移失败时唯一的回滚退路。

    **默认不动** —— 只有显式 `bak=True` 才清，因为删了不可逆。
    """
    group = _group("bak")
    bars_dir = root / "bars"
    if not bars_dir.is_dir():
        return group
    for path in sorted(bars_dir.glob("*.db.bak")):
        group["scanned"] += 1
        _remove(group, path, dry_run)
    return group


def clean(days: int = DEFAULT_RETAIN_DAYS, cache: bool = True,
          scr90: bool = True, bak: bool = False,
          dry_run: bool = False) -> dict:
    """执行一次清理，返回明细。

    每一项**独立 try/except**：日志清不掉也要继续清缓存，不会因为一处失败全盘放弃。

    Args:
        days:    日志与诊断包保留天数（超过即删）
        cache:   是否清 `cache/`（可重建，默认清）
        scr90:   是否清跨周的周榜结果（当前周保留，默认清）
        bak:     是否清 `bars/*.db.bak`（**默认否**，删了不可逆）
        dry_run: 只统计不删除
    """
    root = _data_dir()
    cutoff_ts = _cutoff(days)
    items: list[dict] = []

    for name, func in (
        ("logs", lambda: _clean_logs(root, cutoff_ts, dry_run)),
        ("dumps", lambda: _clean_dumps(root, cutoff_ts, dry_run)),
        ("cache", lambda: _clean_cache(root, dry_run) if cache
         else _group("cache", enabled=False)),
        ("scr90", lambda: _clean_scr90(root, dry_run) if scr90
         else _group("scr90", enabled=False)),
        ("bak", lambda: _clean_bak(root, dry_run) if bak
         else _group("bak", enabled=False)),
    ):
        try:
            items.append(func())
        except Exception as exc:                # noqa: BLE001 - 单项失败不影响其它
            group = _group(name)
            group["errors"].append(f"{type(exc).__name__}: {exc}")
            items.append(group)

    freed = sum(item["freed"] for item in items)
    removed_total = sum(len(item["removed"]) for item in items)
    result = {
        "ok": True,
        "dry_run": bool(dry_run),
        "data_dir": str(root),
        "retain_days": int(days),
        "removed": removed_total,
        "freed": freed,
        "freed_text": _human(freed),
        "items": items,
    }
    _log("清理%s：删除 %d 个文件，释放 %s（保留 %d 天，bars 备份%s）"
         % ("（预览）" if dry_run else "", removed_total, result["freed_text"],
            int(days), "一并清理" if bak else "未动"))
    return result


def schedule_tick(days: int = DEFAULT_RETAIN_DAYS) -> dict | None:
    """给调度循环用：**每天最多真跑一次**。今天已执行过则返回 None。

    去重按**日期**而非「每 tick」——调度循环 60 秒一轮，不去重会疯狂扫目录。
    日期优先存 `download_store`（重启后仍记得），读不到就用进程内变量。
    """
    global _last_cleanup_date
    today = dt.date.today().isoformat()

    store = None
    try:
        import download_store
        store = download_store
        last = store.settings_map().get("last_cleanup_date")
    except Exception:                           # noqa: BLE001
        last = _last_cleanup_date
    if last == today:
        return None

    result = clean(days=days)
    _last_cleanup_date = today
    if store is not None:
        try:
            store.set_setting("last_cleanup_date", today)
        except Exception:                       # noqa: BLE001
            pass
    return result


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="清理日志轮转旧文件、旧诊断包、可重建缓存与跨周周榜结果")
    parser.add_argument("--days", type=int, default=DEFAULT_RETAIN_DAYS,
                        help=f"日志与诊断包保留天数（默认 {DEFAULT_RETAIN_DAYS}）")
    parser.add_argument("--dry-run", action="store_true", help="只列出将要删除的内容，不真删")
    parser.add_argument("--no-cache", action="store_true", help="不清理 cache/")
    parser.add_argument("--no-scr90", action="store_true", help="不清理跨周周榜结果")
    parser.add_argument("--bak", action="store_true",
                        help="一并清理 bars/*.db.bak（约 1 GB，删了不可逆）")
    args = parser.parse_args(argv)

    result = clean(days=args.days, cache=not args.no_cache,
                   scr90=not args.no_scr90, bak=args.bak, dry_run=args.dry_run)

    print("数据目录   :", result["data_dir"])
    print("保留天数   :", result["retain_days"], "天")
    print("模式       :", "预览（未删除）" if result["dry_run"] else "实际清理")
    print()
    for item in result["items"]:
        if not item.get("enabled", True):
            mark = "  未启用"
        elif not item["scanned"]:
            mark = "  无可清理项"
        else:
            mark = ""
        print("[%s]%s 扫描 %d 个，删除 %d 个，释放 %s"
              % (item["kind"], mark, item["scanned"], len(item["removed"]),
                 _human(item["freed"])))
        for removed in item["removed"]:
            print("      -", removed["path"], removed["size_text"])
        for err in item["errors"]:
            print("      !", err)
    print()
    print("合计：删除 %d 个文件，释放 %s" % (result["removed"], result["freed_text"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
