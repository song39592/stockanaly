# -*- coding: utf-8 -*-
"""单实例锁：同一数据目录同时只允许一个后端进程运行。

**为什么需要**：两个进程同时写同一个数据目录，SQLite 的 WAL 会互相打断、
按股指纹与完整性签名会来回覆盖（后写的把先写的当基线），属于「静默损坏」类问题——
不会立刻报错，但数据可信度已经没了。必须在入口处拦住。

**实现**：对 `<数据目录>/instance.lock` 加**排他文件锁**
（Windows 用 `msvcrt.locking`，其他平台用 `fcntl.flock`）。

- 加锁失败（非阻塞）→ 说明已有实例 → 抛 `AlreadyRunningError`，由启动流程报错退出。
- 进程退出（含崩溃被杀）→ 锁由**操作系统**释放，不会留下死锁。
  这正是**不用「PID 文件 + 判断进程是否存在」**的原因：那种做法在进程被强杀后
  会留下陈旧 PID，要么误判「已在运行」而拒绝启动，要么误判「已退出」而放行。
"""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

LOCK_NAME = "instance.lock"

# 锁区域放在内容的**后面**：Windows 的文件锁是强制锁，会阻止其他进程读写被锁区间；
# 放在偏移 0 会导致后来者读不到「是谁占着」，所以把内容写在前面、锁打在后面。
# 取 64 KB 而非「刚好越过内容区」：实测不同读取方式行为不一致（Python 的 read_text 能读，
# 而按块读取的工具会撞上锁区报错），留足余量才能让占用者信息稳定可读。
LOCK_OFFSET = 65536
INFO_BYTES = 200                      # 持有者信息定长写入，避免截断文件干扰锁区

_handle: int | None = None            # 保持文件描述符打开：关闭即释放锁
_locked_path: Path | None = None      # 本进程已持有的锁文件（重复调用直接返回）


class AlreadyRunningError(RuntimeError):
    """已有实例占用同一数据目录。"""

    def __init__(self, path: Path, owner: str = ""):
        self.path = path
        self.owner = owner
        detail = f"\n    占用者：{owner}" if owner else ""
        super().__init__(
            "启动被拒绝：程序已在运行，同一数据目录只能有一个实例。"
            f"{detail}\n"
            f"    锁文件：{path}\n"
            f"    说明：该锁由操作系统持有，上个进程退出（含崩溃、被强杀）会自动释放，"
            f"无需手工清理；\n"
            f"          若确认没有实例在运行却仍报此错，删除上面的锁文件后重试即可。")


def _try_lock(fd: int) -> bool:
    """非阻塞地加排他锁；已被他人占用返回 False。"""
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, LOCK_OFFSET, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _read_owner(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def acquire(data_dir: Path) -> Path:
    """对数据目录加单实例锁，返回锁文件路径；已在运行则抛 `AlreadyRunningError`。"""
    global _handle, _locked_path
    if _handle is not None:           # 本进程已持锁：幂等返回，避免重复加锁与句柄泄漏
        return _locked_path or (data_dir / LOCK_NAME)
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / LOCK_NAME
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o644)
    if not _try_lock(fd):
        owner = _read_owner(path)
        os.close(fd)
        raise AlreadyRunningError(path, owner)
    # 已拿到锁：写入自己的身份，便于后来者看到是谁占着（定长覆盖，不截断文件）
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    info = f"PID {os.getpid()}  启动于 {stamp}".encode("utf-8")[:INFO_BYTES]
    os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, info.ljust(INFO_BYTES, b" "))
    _handle, _locked_path = fd, path  # 有意持有到进程结束，不主动释放
    return path


def owner(data_dir: Path) -> str:
    """只读查看锁文件里记录的持有者（**不代表锁此刻真的被持有**）。"""
    return _read_owner(data_dir / LOCK_NAME)


def release() -> None:
    """主动释放锁（幂等）。

    生产流程**不需要**调用：进程退出（含崩溃、被强杀）时操作系统会自动释放，
    刻意不做「进程 A 释放进程 B 的锁」这类操作。此函数仅供测试在同一进程内
    反复建立/拆除持锁状态使用。
    """
    global _handle, _locked_path
    if _handle is not None:
        os.close(_handle)             # 关闭描述符即释放锁
        _handle, _locked_path = None, None
