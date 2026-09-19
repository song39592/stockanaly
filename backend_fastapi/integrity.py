# -*- coding: utf-8 -*-
"""数据完整性校验：判断数据库是否被外部工具改动过。

**可信原则**（与 `price_store` 一致，完整版见 `docs/database.md` 第八节）：

    丢数据可重拉，**错误数据不可接受**；可用性可以让步，正确性不能让步。

三层检查：

1. **签名有效性** —— `_meta` 每行的 HMAC 是否与内容匹配。
   能发现「手改 value 但没同步改签名」这类最直接的篡改；
2. **写入时间吻合** —— 库文件（含 `-wal`）的最后修改时间应与
   `_meta.last_write_at` 接近。明显晚于它，说明存在**程序之外**的写入；
   由程序自己写入时两者天然一致，所以不需要维护额外的状态。

   ⚠️ **计时必须早于打开库**：打开 WAL 库（哪怕纯读查询）会创建 `-wal`，
   其 mtime 就是「此刻」；若在连接之后再取值，会把「打开库」误判成「被外部写入」，
   造成只要库有年龄就必然误报。同理刻意排除 `-shm`
   （共享内存索引，任何一次打开都会更新它）。见 `check_library`。
3. **结构版本** —— `schema_version` 与当前代码期望是否一致，不一致提示需要迁移。

**本模块只报告、不自动删除**：校验全程只读（`PRAGMA query_only=ON`），
绝不修改数据；发现异常仅写入 `issues`，由调用方决定。
日 K 的自动修复在 `price_service.refetch_bars`——用数据源真值**整体替换**，
且**先抓取、后写入**，替换失败时库中原数据保持不动。
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

import crypto
import db

# 文件 mtime 与记录时间的允许偏差：WAL 合并、文件系统时间粒度、跨秒边界、
# 以及启动时读取库带来的轻微时间扰动。真被外部写入时偏差会是分钟级以上。
TOLERANCE_SECONDS = 300

# 各库期望存在的表（用于发现「表被删掉」这类结构性破坏）
EXPECTED = {
    "stock_history.db": ("pool_snapshots", "pool_members", "stock_events", "sync_jobs"),
    "factors.db": ("adjust_factors", "dividends"),
}


def _latest_mtime(path: Path) -> float:
    """库文件与其 WAL 日志中最新的修改时间。

    **刻意不含 `-shm`**：那是共享内存索引文件，任何一次打开库（哪怕纯读取）
    都会更新它的时间，把它算进来会造成大量误报。

    ⚠️ `-wal` 代表「可能有未合并的写入」，但它**在打开库时也会被创建**
    （实测：`sqlite3.connect` + 纯查询即出现 `-wal`，关闭后又被删掉），
    其 mtime 就是「此刻」。因此**必须在打开库之前调用本函数**，
    否则量到的是当下时间，会把「打开库」误判成「被外部写入」——
    这正是此前必然出现的「文件修改时间明显晚于程序记录」假警报的来源。
    """
    latest = 0.0
    for suffix in ("", "-wal"):
        item = Path(f"{path}{suffix}")
        if item.exists():
            latest = max(latest, item.stat().st_mtime)
    return latest


def _parse_iso(value: str | None) -> float | None:
    try:
        return dt.datetime.fromisoformat(str(value)).timestamp()
    except (TypeError, ValueError):
        return None


def check_library(name: str, path: Path, expect_tables: tuple[str, ...] = ()) -> dict:
    """只读校验单个库。绝不修改数据。"""
    item: dict = {"name": name, "path": str(path), "exists": path.exists(), "issues": []}
    if not path.exists():
        item["status"] = "missing"
        return item
    if not crypto.has_secret():
        item["issues"].append("未找到校验密钥（DATA_SECRET），无法验证签名")
        item["status"] = "unsigned"
        return item

    # 取时间必须早于打开库：打开 WAL 库会创建 `-wal`（mtime 为「此刻」），
    # 连接后再取值会把「打开库」误算成「被外部写入」，造成必然的假警报。
    mtime = _latest_mtime(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only=ON")        # 确保校验过程零写入
        try:
            tables = {row["name"] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            meta = db.meta_all(conn)
        except sqlite3.DatabaseError as exc:
            item["issues"].append(f"数据库无法读取：{exc}")
            item["status"] = "broken"
            return item

        if not meta:
            item["status"] = "uninitialized"        # 首次运行尚未写入任何数据
            return item

        # 1) 签名有效性
        for key, row in meta.items():
            payload = f"{key}|{row['value']}|{row['updated_at']}"
            if not crypto.verify(payload, row["hmac"]):
                item["issues"].append(f"元信息「{key}」签名不匹配，疑似被直接修改")

        # 2) 写入时间吻合
        recorded = _parse_iso(meta.get(db.META_LAST_WRITE, {}).get("value"))
        if recorded is None:
            item["issues"].append("缺少最后写入时间记录")
        else:
            drift = mtime - recorded          # mtime 取自打开库之前（见上方说明）
            item["mtime_drift_seconds"] = round(drift, 1)
            if drift > TOLERANCE_SECONDS:
                item["issues"].append(
                    "文件修改时间明显晚于程序记录（相差 %.0f 分钟），"
                    "疑似被外部工具改动" % (drift / 60))

        # 3) 结构版本
        version = meta.get(db.META_SCHEMA, {}).get("value")
        if version and version != db.SCHEMA_VERSION:
            item["issues"].append(
                f"入库格式版本为 {version}，当前程序期望 {db.SCHEMA_VERSION}，可能需要迁移")

        # 4) 期望的表是否齐全
        missing = [t for t in expect_tables if t not in tables]
        if missing:
            item["issues"].append("缺少数据表：" + "、".join(missing))

        item["schema_version"] = version
        item["last_write_at"] = meta.get(db.META_LAST_WRITE, {}).get("value")
        item["status"] = "tampered" if item["issues"] else "ok"
        return item
    finally:
        conn.close()


def _all_targets() -> list[tuple[str, Path, tuple[str, ...]]]:
    """全部待校验的库：主库 + 因子库 + 每个日K分片。"""
    targets: list[tuple[str, Path, tuple[str, ...]]] = [
        ("股票池与消息库", db.DB_PATH, EXPECTED["stock_history.db"]),
        ("复权因子与除权库", db.FACTORS_DB, EXPECTED["factors.db"]),
    ]
    if db.BARS_DIR.exists():
        for shard in sorted(db.BARS_DIR.glob("bars_*.db")):
            targets.append((f"日K分片 {shard.stem}", shard, ("daily_bars",)))
    return targets


def ensure_signed() -> dict:
    """为尚未记录元信息的库补一次签名，把既有数据纳入保护范围。

    注意：这**无法发现签名之前**已经发生的改动——这是首次引入本机制时的固有限制；
    此后任何程序之外的写入都会体现在「文件时间 vs 记录时间」的偏差上。
    """
    signed: list[str] = []
    for _name, path, _tables in _all_targets():
        if not path.exists():
            continue
        try:
            with db.connect(path, touch_meta=False) as conn:
                meta = db.meta_all(conn)
                if db.META_LAST_WRITE not in meta:
                    db.touch(conn)              # 写入 schema 版本 + 最后写入时间（含签名）
                    signed.append(path.name)
        except Exception:                       # noqa: BLE001 - 单个库失败不影响其他库
            continue
    return {"signed": signed}


def resign() -> dict:
    """把当前状态重新记为可信基线（刷新所有库的签名与记录时间）。

    ⚠️ **这会接受当前数据状态**——若库里确实有被篡改的内容，重签等于放行。
    因此只在**明确知道变动来源**时使用（例如刚执行过维护操作、或刚做过数据迁移），
    不要用它来"消掉报警"。
    """
    signed: list[str] = []
    for _name, path, _tables in _all_targets():
        if not path.exists():
            continue
        try:
            with db.connect(path, force_touch=True) as conn:
                db.touch(conn)
            signed.append(path.name)
        except Exception:                       # noqa: BLE001
            continue
    return {"ok": True, "resigned": signed}


def check_all() -> dict:
    """校验全部数据库，返回汇总结果。"""
    libraries = [check_library(name, path, tables) for name, path, tables in _all_targets()]
    issues = [f"{lib['name']}：{text}" for lib in libraries for text in lib["issues"]]
    return {
        "ok": not issues,
        "checked_at": db.now_iso(),
        "tolerance_seconds": TOLERANCE_SECONDS,
        "schema_version": db.SCHEMA_VERSION,
        "libraries": libraries,
        "issues": issues,
    }


def deep_check(codes: list[str] | None = None) -> dict:
    """逐股重算指纹并比对，返回**不可信清单**（可只查指定股票）。

    比结构级校验更彻底：能定位到具体哪只股票的日K被改过。
    全库扫描会遍历所有分片与个股，耗时随数据量增长，适合按需触发。
    """
    import price_store                          # 函数内导入避免模块级耦合

    untrusted: list[dict] = []
    checked = 0
    for year in price_store._shard_years():
        path = db.bars_db(year)
        with db.connect(path) as conn:
            for row in conn.execute("SELECT DISTINCT code FROM daily_bars"):
                code = row["code"]
                if codes and code not in codes:
                    continue
                checked += 1
                result = price_store.verify_digest(conn, code)
                if not result["ok"]:
                    untrusted.append({"code": code, "shard": path.stem,
                                      "reason": result["reason"] or "指纹不一致"})
    return {"ok": not untrusted, "checked": checked,
            "untrusted": untrusted, "checked_at": db.now_iso()}


def summary() -> dict:
    """给 /health 用的精简结果：只关心结论与问题列表。"""
    result = check_all()
    return {
        "ok": result["ok"],
        "issues": result["issues"],
        "libraries": [
            {"name": lib["name"], "status": lib.get("status"), "issues": lib["issues"]}
            for lib in result["libraries"]
        ],
    }
