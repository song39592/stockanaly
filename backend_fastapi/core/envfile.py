# -*- coding: utf-8 -*-
"""安全地改写 `.env`：先备份、写临时文件，再**原子替换**。

`.env` 里不只有一处配置——还有 `LLM_BASE_URL`、`LLM_MODEL`、`DATA_DIR`，
以及完整性校验密钥 `DATA_SECRET_SEALED`。直接用 `write_text` 属于「打开即截断」，
写入过程中一旦失败（异常、断电、磁盘满），整个配置文件就损坏了：
**配置丢失比单个密钥丢失严重得多**（密钥丢了指纹全失效，全库都要重拉）。

因此所有对 `.env` 的改写统一走这里：
  1. 先复制一份 `.env.bak` 留退路；
  2. 写入同目录的临时文件（写坏了也不影响原文件）；
  3. `os.replace` 原子替换——同一分区内就是一次 rename，不会留下半截文件。
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path


def rewrite(path: Path, text: str, backup: bool = True) -> dict:
    """原子改写文件，返回 `{"backup": 备份路径或 None}`。"""
    result: dict = {"backup": None}
    if backup and path.exists():
        target = path.with_name(path.name + ".bak")
        shutil.copy2(path, target)
        result["backup"] = str(target)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)                 # 原子替换
    return result
