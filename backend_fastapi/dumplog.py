# -*- coding: utf-8 -*-
"""dumplog：一键导出诊断包（zip），供排障时提交给开发者。

包内容：日志末尾、系统与完整性状态、模块挂载错误、**脱敏**后的配置、
依赖清单、周榜状态、元信息。

**安全是第一位**（本项目最敏感的是 LLM 密钥与完整性签名密钥）：
  - 只写「状态」（是否已密封 / 是否可用），**绝不写密钥值**；
  - 打包前对**每一份待写入的文本**做密钥模式扫描，命中即**拒绝生成整个包**并报错。
    这是**兜底**：日志内容是别的模块写进去的、本模块控制不了，
    所以不能只靠「记得脱敏」——必须机器校验一次；
  - 绝不打包数据库文件本身（`stock_history.db` / `bars/` 合计约 2 GB）。

导出位置：`<data>/logs/dump-<YYYYMMDD-HHMMSS>.zip`（logs/ 由第 21 项建立，
日志由第 22 项产出；过期包的清理归定时任务）。
"""
from __future__ import annotations

import collections
import datetime as dt
import json
import os
import platform
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import config
import crypto
import integrity
import logutil
import storage

logger = logutil.logger

DEFAULT_TAIL = 500            # 每个日志文件只取末尾 N 行


class SecretLeakError(RuntimeError):
    """待打包内容命中了密钥模式 —— 整个包拒绝生成。"""


# 兜底扫描：命中任一条即拒绝生成。
# ⚠️ `sk-` 必须带左边界 + 足够长度，否则 "task-" / "disk-" / "risk-" 这类普通词会被误判，
#    导致正常排障时永远导不出包。`(?<![A-Za-z0-9])` 排掉前面是字母数字的情况。
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("LLM 密钥（sk- 前缀）", re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_\-]{16,}")),
    ("明文密钥 LLM_API_KEY=", re.compile(r"(?i)LLM_API_KEY\s*=")),
    ("密封密钥 LLM_API_KEY_SEALED=", re.compile(r"(?i)LLM_API_KEY_SEALED\s*=")),
    ("完整性签名密钥 DATA_SECRET", re.compile(r"(?i)DATA_SECRET")),
    ("通用密钥字段 api_key=", re.compile(r"(?i)\bapi[_-]?key\s*=")),
)


def _scan(name: str, text: str) -> list[dict]:
    """对一份待打包文本做密钥模式扫描，返回命中列表（空 = 安全）。

    ⚠️ **只记录「哪个文件的哪类模式」，绝不回显命中的值**：命中结果会被写进日志、
    也会作为错误信息返回给调用方，一旦回显就等于把密钥从一个地方复制到另一个地方。
    """
    hits: list[dict] = []
    for label, pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            hits.append({"file": name, "pattern": label})
    return hits


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"


def _human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB"):
        if value < 1024 or unit == "MB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} MB"


def _tail(path: Path, lines: int) -> str:
    """取日志末尾 N 行（用 deque，再大的文件也不会整读进内存）。"""
    if not path.is_file():
        return f"（无此文件：{path.name}）\n"
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return "".join(collections.deque(handle, maxlen=lines))
    except Exception as exc:                       # noqa: BLE001
        return f"（读取失败 {path.name}：{type(exc).__name__}: {exc}）\n"


def _env_text() -> str:
    """运行环境 + `pip freeze`（即第 20 项锁文件所对应的**实际**状态）。"""
    lines = [
        f"Python      : {platform.python_version()}",
        f"Platform    : {platform.platform()}",
        f"Executable  : {sys.executable}",
        f"Generated at: {dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "---- pip freeze ----",
    ]
    try:
        out = subprocess.run([sys.executable, "-m", "pip", "freeze"],
                             capture_output=True, text=True, timeout=30)
        lines.append(out.stdout.strip() or out.stderr.strip() or "（pip freeze 无输出）")
    except Exception as exc:                       # noqa: BLE001
        lines.append(f"（pip freeze 失败：{type(exc).__name__}: {exc}）")
    return "\n".join(lines) + "\n"


def _git_commit() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             cwd=str(config.BASE_DIR), capture_output=True,
                             text=True, timeout=3)
        if out.returncode == 0:
            return out.stdout.strip() or None
    except Exception:                              # noqa: BLE001
        pass
    return None


def _module_errors() -> dict:
    """启动期记录的模块挂载错误。

    延迟 import main：调用时它已在 sys.modules，不存在循环导入问题；
    放在函数里是为了让 dumplog 本身可被单独导入测试。
    """
    try:
        import main
        return {"ok": True, "errors": list(getattr(main, "_module_errors", []))}
    except Exception as exc:                       # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "errors": []}


def _chip_rank_status() -> dict:
    """SCR90 周榜状态（排障时最常被问的一项）。"""
    try:
        import chip_rank_service
        return chip_rank_service.status()
    except Exception as exc:                       # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _safe_config() -> dict:
    """**脱敏**后的配置：只有路径 / 端口 / 「是否配置」，**没有任何密钥值**。

    密钥只出现 `config.llm_key_state()` 的「是否密封 / 明文 / 可用」三态；
    完整性签名密钥只取 `crypto.seal_state()` 的状态字段，
    **刻意不带它的 `error` 详情**（DPAPI 的异常信息里可能带密文片段）。
    """
    state = storage.configuration_state()
    seal = crypto.seal_state()
    return {
        "data_dir": str(config.DATA_DIR),
        "default_data_dir": str(config.DEFAULT_DATA_DIR),
        "is_default_data_dir": config.DATA_DIR == config.DEFAULT_DATA_DIR,
        "configured_data_dir": state.get("configured_root"),
        "data_dir_env_override": bool(config.DATA_DIR_OVERRIDES),
        "data_dir_restart_required": state.get("restart_required"),
        "port": config.PORT,
        "llm": {
            "base_url_configured": bool(config.LLM_BASE_URL),
            "model_configured": bool(config.LLM_MODEL),
            "key_state": config.llm_key_state(),
            "problem": config.llm_config_problem(),
        },
        "integrity_key": {
            "sealed": seal.get("sealed"),
            "plain": seal.get("plain"),
            "usable": seal.get("usable"),
        },
    }


def _collect(tail: int) -> list[tuple[str, str]]:
    """收集全部待打包条目：`(包内路径, 文本)`。"""
    entries: list[tuple[str, str]] = [
        ("logs/backend.log", _tail(config.LOGS_DIR / "backend.log", tail)),
        ("logs/uvicorn-error.log", _tail(config.LOGS_DIR / "uvicorn-error.log", tail)),
    ]

    system: dict = {"storage": storage.snapshot()}
    try:
        system["integrity"] = integrity.summary()
    except Exception as exc:                       # noqa: BLE001
        system["integrity"] = {"error": f"{type(exc).__name__}: {exc}"}
    entries.append(("system.json", _dump(system)))

    entries.append(("health.json", _dump(_module_errors())))
    entries.append(("config.json", _dump(_safe_config())))
    entries.append(("env.txt", _env_text()))
    entries.append(("chip_rank_status.json", _dump(_chip_rank_status())))
    entries.append(("meta.json", _dump({
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "git_commit": _git_commit(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "pid": os.getpid(),
        "tail_lines": tail,
        "note": "本包已做密钥扫描；如发现任何密钥泄漏请立即删除并告知开发者。",
    })))
    return entries


def build(tail: int = DEFAULT_TAIL) -> dict:
    """生成诊断包，返回 `{ok, path, name, size, size_text, entries}`。

    抛出：
        SecretLeakError  内容命中密钥模式 —— **整个包不会生成**（宁可不导，也不能泄漏）
    """
    entries = _collect(tail)

    hits: list[dict] = []
    for name, text in entries:
        hits.extend(_scan(name, text))
    if hits:
        # 只说「哪个文件命中了哪类模式」，**不回显命中的值**
        # （这条会进日志、也会返回给调用方，回显等于把密钥再抄一份出去）。
        where = "；".join(f"{h['file']} → {h['pattern']}" for h in hits)
        logger.error("诊断包生成被拒绝：检测到疑似密钥，共 %d 处 —— %s", len(hits), where)
        raise SecretLeakError(
            "诊断包生成被拒绝：内容里检测到疑似密钥，已阻止打包以防泄漏。"
            f"共命中 {len(hits)} 处 —— {where}。"
            "请先清理命中的文件（例如日志里被写进了密钥）再重试。")

    config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    name = "dump-" + dt.datetime.now().strftime("%Y%m%d-%H%M%S") + ".zip"
    path = config.LOGS_DIR / name
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for entry_name, text in entries:
            archive.writestr(entry_name, text)

    size = path.stat().st_size
    logger.info("诊断包已生成：%s（%s，%d 项）", path, _human(size), len(entries))
    return {"ok": True, "path": str(path), "name": name, "size": size,
            "size_text": _human(size), "entries": [n for n, _ in entries]}
