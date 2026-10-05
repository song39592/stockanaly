# -*- coding: utf-8 -*-
"""数据目录管理：位置解析、旧数据迁移与用量统计。

所有运行期数据集中放在 `config.DATA_DIR`（默认与仓库并列的 stockanaly-data，
可用 .env 的 `DATA_DIR` 或环境变量 `STOCK_DATA_DIR` 指向别处），并按用途分区：

    config/      用户配置：可手工修改的 json
    state/       持久化状态：stock_history.db、mentor_lab.db、instance.lock
    logs/        日志
    cache/       可重建缓存
    bars/        行情分片（**体积最大，刻意留在根目录不搬**）
    chip/        SCR 原始文件与计算结果
      raw/        用户导入的原始导出文件
      processed/  三档分类结果（json + csv）
      meta.json   文件 → 日期映射

旧版本把这些文件**平铺在根目录**，由 `migrate_to_subdirs()` 在启动时一次性搬进分区。
"""
from __future__ import annotations

import shutil
import sqlite3
import tempfile
from pathlib import Path

from . import config
from . import envfile
from dotenv import dotenv_values

# 分区目录常量定义在 config.py（与 DATA_DIR 同源，避免各模块各自拼一份路径）；
# 这里转出一份，方便读「数据目录管理」这一模块时直接看到全貌。
CONFIG_DIR = config.CONFIG_DIR
STATE_DIR = config.STATE_DIR
LOGS_DIR = config.LOGS_DIR
CACHE_DIR = config.CACHE_DIR
CHIP_DIR = config.DATA_DIR / "chip"
ENV_PATH = config.BASE_DIR / ".env"
_DB_FILES = ("stock_history.db", "mentor_lab.db")


def ensure_dirs() -> None:
    """确保数据根目录及**全部分区子目录**存在。"""
    for path in (config.DATA_DIR, CONFIG_DIR, STATE_DIR, LOGS_DIR, CACHE_DIR,
                 CHIP_DIR, CHIP_DIR / "raw", CHIP_DIR / "processed"):
        path.mkdir(parents=True, exist_ok=True)


def _copy_if_missing(src: Path, dst: Path) -> bool:
    """仅在目标不存在时复制，且保留源文件。"""
    if not src.is_file() or dst.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return True


def migrate_legacy() -> dict:
    """把**项目内**旧位置的数据复制到当前数据目录的 `state/`（仅在目标缺失时，不删除源文件）。

    迁移逻辑再小心也可能有边界情况，因此采取「只复制、不删除」的策略：
    即便新位置出问题，旧数据仍在，不会出现数据凭空消失的情况。
    WAL 模式下的 `-wal` / `-shm` 附属文件一并带上，避免库不完整。

    ⚠️ 注意「目标已有就不动」的语义：两份数据不一致时**保留数据目录里那份**。
    实测曾出现 `mentor_lab.db` 项目内那份是空库、数据盘那份才是真数据的情况，
    若反过来覆盖就会把用户数据清空。必须先跑 `migrate_to_subdirs()` 把根目录的真数据
    落进 `state/`，本函数就只会跳过、不会覆盖。
    """
    ensure_dirs()
    copied: list[str] = []

    if config.DATA_DIR != config.LEGACY_DATA_DIR:
        for name in _DB_FILES:
            for suffix in ("", "-wal", "-shm"):
                src = config.LEGACY_DATA_DIR / f"{name}{suffix}"
                if _copy_if_missing(src, STATE_DIR / f"{name}{suffix}"):
                    copied.append(f"{name}{suffix}")

    if CHIP_DIR != config.LEGACY_CHIP_DIR:
        for src in sorted(config.LEGACY_CHIP_DIR.rglob("*")):
            if not src.is_file():
                continue
            rel = src.relative_to(config.LEGACY_CHIP_DIR)
            if _copy_if_missing(src, CHIP_DIR / rel):
                copied.append(f"chip/{rel.as_posix()}")

    return {"migrated": copied, "from": str(config.LEGACY_DATA_DIR), "to": str(STATE_DIR)}


# 旧结构 → 新结构的一次性迁移：这些文件原先平铺在**数据根目录**，现在按用途归位。
_TO_STATE = ("stock_history.db", "mentor_lab.db")
# 注：`history_stock_view.json`（「最近查看」）启动器已改为存到自己的 %APPDATA% 目录
# —— 界面状态不该跟着数据目录走（数据目录可能在别的盘、也随时会被改）。
# 数据根目录这份是**旧版本遗留**，仍搬进 config/ 保留（不删用户数据），
# 启动器的遗留读取也已同步加上 config/ 这个候选位置。
_TO_CONFIG = ("history_stock_view.json",)
_SIDECARS = ("-wal", "-shm")        # WAL 模式下的附属文件，不一起搬会导致库不完整
_STALE_LOCK = "instance.lock"       # 运行期锁：每次启动重新生成，没有搬迁价值


def _verify_readable(path: Path) -> bool:
    """校验迁移后的文件可读（SQLite 库额外跑一次 `quick_check`）。"""
    try:
        if path.suffix == ".db":
            con = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
            try:
                return con.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            finally:
                con.close()
        with path.open("rb") as handle:
            handle.read(1)
        return True
    except Exception:                                    # noqa: BLE001
        return False


def migrate_to_subdirs() -> dict:
    """把平铺在数据根目录的状态文件搬进 `state/`（**一次性、非破坏性**）。

    安全策略 —— 宁可留着旧文件，也不能让数据消失：
      - 仅当「源存在 **且** 目标不存在」时才搬；目标已有则**一律不动**，
        绝不用旧文件覆盖可能更新的数据；
      - 先复制 → 校验（大小一致 + SQLite `quick_check`）→ 通过才删源；
        任一步失败就保留原状，只把错误记进返回值的 `errors`；
      - 数据库连同 `-wal` / `-shm` 一起搬，避免 WAL 未落盘导致库不完整。

    数据盘在仓库外、不受 git 跟踪，老用户升级后**只有靠这段逻辑**才能完成搬迁，
    不能指望「提交里带上新目录」。

    单实例锁 `instance.lock` 特殊处理：它每次启动重新生成，没有搬迁价值；
    新锁位于 `state/`（由 `instance_lock.acquire(STATE_DIR)` 创建），
    根目录那份确认已成陈旧文件后直接清掉。
    """
    ensure_dirs()
    moved: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []
    cleaned: list[str] = []

    for target_dir, names in ((STATE_DIR, _TO_STATE), (CONFIG_DIR, _TO_CONFIG)):
        for name in names:
            src, dst = config.DATA_DIR / name, target_dir / name
            if not src.is_file():
                continue
            if dst.exists():                   # 目标已有：不动，避免覆盖更新的数据
                skipped.append(name)
                continue
            try:
                shutil.copy2(src, dst)
                if src.stat().st_size != dst.stat().st_size or not _verify_readable(dst):
                    raise OSError(f"迁移后校验未通过：{dst}")
                for suffix in _SIDECARS:       # WAL 附属文件一并搬（仅库文件会有）
                    side = config.DATA_DIR / f"{name}{suffix}"
                    if side.is_file():
                        shutil.copy2(side, target_dir / f"{name}{suffix}")
                src.unlink()
                for suffix in _SIDECARS:
                    side = config.DATA_DIR / f"{name}{suffix}"
                    if side.is_file():
                        side.unlink()
                moved.append(name)
            except Exception as exc:           # noqa: BLE001 - 失败一律保留原状
                errors.append(f"{name}: {type(exc).__name__}: {exc}")

    # 根目录下的陈旧锁文件：新锁已在 state/，且本进程正持有它，可以安全清理
    stale = config.DATA_DIR / _STALE_LOCK
    if stale.is_file() and (STATE_DIR / _STALE_LOCK).exists():
        try:
            stale.unlink()
            cleaned.append(_STALE_LOCK)
        except Exception as exc:               # noqa: BLE001
            errors.append(f"{_STALE_LOCK}: {type(exc).__name__}: {exc}")

    return {"moved": moved, "skipped": skipped, "errors": errors, "cleaned": cleaned}


def _size_of(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    if not path.exists():
        return 0
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024


def snapshot() -> dict:
    """当前数据目录、各项占用，以及是否在使用默认位置。"""
    ensure_dirs()
    items = []
    for name, rel in (("股票池与消息库", "state/stock_history.db"),
                      ("行情分片（日K+因子）", "bars"),
                      ("大佬实验室库", "state/mentor_lab.db"),
                      ("SCR 选股数据", "chip")):
        target = config.DATA_DIR / rel
        size = _size_of(target)
        items.append({"name": name, "path": rel, "size": size,
                      "size_text": _human(size), "exists": target.exists()})
    total = sum(item["size"] for item in items)
    return {
        **configuration_state(),
        "root": str(config.DATA_DIR),
        "root_display": str(config.DATA_DIR),
        "default_root": str(config.DEFAULT_DATA_DIR),
        "legacy_root": str(config.LEGACY_DATA_DIR),
        "is_default": config.DATA_DIR == config.DEFAULT_DATA_DIR,
        "is_inside_project": config.PROJECT_DIR in config.DATA_DIR.parents
                             or config.DATA_DIR == config.PROJECT_DIR,
        "total": total,
        "total_text": _human(total),
        "items": items,
        # 分区目录一览（第 21 项引入的新结构，便于页面 / 排障确认已生效）
        "dirs": {key: {"path": path.relative_to(config.DATA_DIR).as_posix(),
                       "exists": path.exists()}
                 for key, path in (("config", CONFIG_DIR), ("state", STATE_DIR),
                                   ("logs", LOGS_DIR), ("cache", CACHE_DIR),
                                   ("bars", config.DATA_DIR / "bars"),
                                   ("chip", CHIP_DIR))},
    }


def configuration_state() -> dict:
    values = {**dotenv_values(ENV_PATH), **config.DATA_DIR_OVERRIDES}
    value = values.get("STOCK_DATA_DIR") or values.get("DATA_DIR")
    configured = Path(value).expanduser() if value else config.DEFAULT_DATA_DIR
    return {"configured_root": str(configured),
            "restart_required": configured != config.DATA_DIR,
            "environment_override": bool(config.DATA_DIR_OVERRIDES)}


def update_data_dir(value: str) -> dict:
    """把数据目录写入 backend_fastapi/.env（保留其它配置项），重启后生效。

    只改配置、不搬数据：改路径后旧数据不会被自动带走，
    重启时由 `migrate_legacy()` 从**项目内旧位置**补齐，其余路径需自行迁移。
    """
    text = str(value or "").strip()
    if not text:
        return {"ok": False, "error": "数据目录不能为空"}
    if len(text) > 260:
        return {"ok": False, "error": "路径过长，请检查输入"}

    target = Path(text).expanduser()
    if not target.is_absolute() or any(c in text for c in "\r\n\x00"):
        return {"ok": False, "error": "请填写绝对路径，例如 D:\\stockanaly-data"}
    if config.DATA_DIR_OVERRIDES:
        return {"ok": False, "error": "数据目录由启动进程的环境变量覆盖，请先移除 STOCK_DATA_DIR / DATA_DIR 环境变量后重启后端"}
    try:
        target.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=target) as probe:
            probe.write(b"ok")
    except Exception as exc:                      # noqa: BLE001
        return {"ok": False, "error": f"该目录不可写：{exc}"}

    lines: list[str] = []
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            key = line.strip().removeprefix("export ").split("=", 1)[0].strip()
            if key not in ("DATA_DIR", "STOCK_DATA_DIR"):
                lines.append(line)
    # 单引号及反斜杠按 dotenv 规则转义，路径里的 # 和空格不会变成注释。
    escaped = str(target).replace("\\", "\\\\").replace("'", "\\'")
    lines.append(f"DATA_DIR='{escaped}'")
    try:
        envfile.rewrite(ENV_PATH, "\n".join(lines) + "\n")
    except Exception as exc:                     # noqa: BLE001
        return {"ok": False, "error": f"写入 .env 失败：{exc}"}

    return {"ok": True, "data_dir": str(target), **configuration_state(),
            "message": "已保存，重启后端后生效。当前数据目录的行情、任务和下载设置不会自动搬迁，请先备份并复制到新目录。"}


def main(argv=None) -> int:
    """后端无法启动时，启动器仍可安全修正目录；不导入应用或打开数据库。"""
    import argparse
    import base64
    import json
    parser = argparse.ArgumentParser()
    parser.add_argument("--set-data-dir-base64", required=True)
    args = parser.parse_args(argv)
    try:
        value = base64.b64decode(args.set_data_dir_base64, validate=True).decode("utf-8")
        result = update_data_dir(value)
    except Exception as exc:
        result = {"ok": False, "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
