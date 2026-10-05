# -*- coding: utf-8 -*-
"""日志：**stderr 实时输出** + `<data>/logs/` 落盘（按天轮转，保留 N 天）。

**为什么必须双写，不能只写文件**：启动器把后端的 stderr 流实时捕获到日志窗口，
并按流打标签（stderr → `[err]`）。所以 stderr **不能停**，否则界面上什么都看不到；
但关掉窗口日志就没了、事后无法回溯，故**同时**写一份到文件。

**为什么用具名 logger 而不是 root**：uvicorn 启动时会用 `--log-config` 调
`logging.config.dictConfig()`。本项目的 `uvicorn_logging.json` 带
`disable_existing_loggers: false`，且只声明 `uvicorn` / `uvicorn.error` /
`uvicorn.access` 三个 logger、**不声明 root**，因此这里自建的具名 logger
不会被它清掉（uvicorn 的日志配置发生在导入应用之前，之后也不再动它）。

**日志里绝不写密钥**：只记录「已配置 / 未配置」这类状态，不输出
`LLM_API_KEY`、DPAPI 密封值或 `.env` 内容。
"""
from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

from . import config

LOGGER_NAME = "stockpool"
# 轮转文件保留天数。**与 cleanup.DEFAULT_RETAIN_DAYS 保持一致** ——
# 两个数字打架时以清理为准（它更严格），但默认就该是同一个口径。
KEEP_DAYS = 7

# 级别 → 启动器一直在用的标签，保持 stderr 输出与改造前完全一致
_LEVEL_TAGS = {logging.INFO: "info", logging.WARNING: "warn",
               logging.ERROR: "error", logging.CRITICAL: "fatal"}


class _TagFormatter(logging.Formatter):
    """把级别映射成 `[info]` / `[warn]` / `[error]` / `[fatal]` 前缀。"""

    def format(self, record: logging.LogRecord) -> str:
        record.level_tag = _LEVEL_TAGS.get(record.levelno, "info")   # type: ignore[attr-defined]
        return super().format(record)


def _file_handler(path: Path, level: int) -> logging.Handler:
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.TimedRotatingFileHandler(
        str(path), when="midnight", interval=1, backupCount=KEEP_DAYS,
        encoding="utf-8")
    handler.setLevel(level)
    handler.setFormatter(_TagFormatter("%(asctime)s [%(level_tag)s] %(message)s",
                                       datefmt="%Y-%m-%d %H:%M:%S"))
    return handler


def setup() -> logging.Logger:
    """装配 logger。文件不可写时**降级为只写 stderr**，绝不让日志拖垮启动。"""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False          # 不走 root，避免与 uvicorn 的日志配置互相干扰
    for handler in list(logger.handlers):
        logger.removeHandler(handler)

    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(_TagFormatter("[%(level_tag)s] %(message)s"))
    logger.addHandler(stream)

    # backend.log：全部 INFO 及以上
    # uvicorn-error.log：只收 WARNING 及以上，与 uvicorn 的错误日志同名便于对照。
    #   旧路径 backend_fastapi/uvicorn-error.log **保持不变**（启动器「读取错误日志」
    #   硬编码读它），这里只是新位置的副本，启动器无需改动。
    for name, level in (("backend.log", logging.INFO),
                        ("uvicorn-error.log", logging.WARNING)):
        try:
            logger.addHandler(_file_handler(config.LOGS_DIR / name, level))
        except Exception as exc:                       # noqa: BLE001
            logger.warning("日志文件不可用（%s）：%s: %s", name, type(exc).__name__, exc)
    return logger


logger = setup()
