# -*- coding: utf-8 -*-
"""数据目录管理：位置解析、旧数据迁移与用量统计。

所有运行期数据集中放在 `config.DATA_DIR`（默认是**本程序所在目录下的 data**，
可用 .env 的 `DATA_DIR` 或环境变量 `STOCK_DATA_DIR` 指向别处）：

    stock_history.db   股票池快照 / 日 K / 复权因子 / 除权明细 / 消息面 / 同步任务
    mentor_lab.db      大佬策略实验室
    chip/              SCR 原始文件与计算结果
      raw/             用户导入的原始导出文件
      processed/       三档分类结果（json + csv）
      meta.json        文件 → 日期映射
"""
from __future__ import annotations

import shutil
from pathlib import Path

import config

CHIP_DIR = config.DATA_DIR / "chip"
ENV_PATH = config.BASE_DIR / ".env"
_DB_FILES = ("stock_history.db", "mentor_lab.db")


def ensure_dirs() -> None:
    """确保数据根目录及子目录存在。"""
    for path in (config.DATA_DIR, CHIP_DIR, CHIP_DIR / "raw", CHIP_DIR / "processed"):
        path.mkdir(parents=True, exist_ok=True)


def _copy_if_missing(src: Path, dst: Path) -> bool:
    """仅在目标不存在时复制，且保留源文件。"""
    if not src.is_file() or dst.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return True


def migrate_legacy() -> dict:
    """把项目内旧位置的数据复制到当前数据根目录（仅在目标缺失时，不删除源文件）。

    迁移逻辑再小心也可能有边界情况，因此采取「只复制、不删除」的策略：
    即便新位置出问题，旧数据仍在，不会出现数据凭空消失的情况。
    WAL 模式下的 `-wal` / `-shm` 附属文件一并带上，避免库不完整。
    """
    ensure_dirs()
    copied: list[str] = []

    if config.DATA_DIR != config.LEGACY_DATA_DIR:
        for name in _DB_FILES:
            for suffix in ("", "-wal", "-shm"):
                src = config.LEGACY_DATA_DIR / f"{name}{suffix}"
                if _copy_if_missing(src, config.DATA_DIR / f"{name}{suffix}"):
                    copied.append(f"{name}{suffix}")

    if CHIP_DIR != config.LEGACY_CHIP_DIR:
        for src in sorted(config.LEGACY_CHIP_DIR.rglob("*")):
            if not src.is_file():
                continue
            rel = src.relative_to(config.LEGACY_CHIP_DIR)
            if _copy_if_missing(src, CHIP_DIR / rel):
                copied.append(f"chip/{rel.as_posix()}")

    return {"migrated": copied, "from": str(config.LEGACY_DATA_DIR), "to": str(config.DATA_DIR)}


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
    for name, rel in (("股票池与消息库", "stock_history.db"),
                      ("行情分片（日K+因子）", "bars"),
                      ("大佬实验室库", "mentor_lab.db"),
                      ("SCR 选股数据", "chip")):
        target = config.DATA_DIR / rel
        size = _size_of(target)
        items.append({"name": name, "path": rel, "size": size,
                      "size_text": _human(size), "exists": target.exists()})
    total = sum(item["size"] for item in items)
    return {
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
    }


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
    try:
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except Exception as exc:                      # noqa: BLE001
        return {"ok": False, "error": f"该目录不可写：{exc}"}

    lines: list[str] = []
    replaced = False
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("DATA_DIR="):
                lines.append(f"DATA_DIR={target}")
                replaced = True
            else:
                lines.append(line)
    if not replaced:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("# 运行期数据目录：留空则依次尝试 D:\\stockanaly-data 与项目内 data")
        lines.append(f"DATA_DIR={target}")
    try:
        ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception as exc:                     # noqa: BLE001
        return {"ok": False, "error": f"写入 .env 失败：{exc}"}

    return {"ok": True, "data_dir": str(target), "restart_required": True,
            "message": "已保存，重启后端后生效（旧位置的数据会在启动时自动补到新目录）"}
