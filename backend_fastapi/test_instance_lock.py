# -*- coding: utf-8 -*-
"""单实例锁测试：核心是「只有一个能拉起」，以及「不会死锁」。"""
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import instance_lock

BACKEND = str(Path(__file__).resolve().parent)


class InstanceLockTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        # 本测试进程可能在用例中持有了锁——必须**真正释放**（关掉 fd），
        # 仅把模块变量置空不会释放锁，会污染后续用例。
        instance_lock.release()
        self.tmp.cleanup()

    @staticmethod
    def _stop(proc: subprocess.Popen) -> None:
        """结束子进程并关掉管道，避免 ResourceWarning。"""
        proc.kill()
        proc.wait()
        if proc.stdout:
            proc.stdout.close()

    def _spawn_holder(self) -> tuple[subprocess.Popen, str]:
        """起一个子进程持有锁，返回 (进程, 子进程自报的 PID)。

        取子进程自报的 PID 而非 `proc.pid`：Windows 上 venv 的 `python.exe`
        是转发器，`Popen` 拿到的是启动器的 PID，与真正写入锁文件的解释器 PID 不同。
        """
        code = (
            f"import os, sys, time\n"
            f"sys.path.insert(0, {BACKEND!r})\n"
            f"import instance_lock\n"
            f"from pathlib import Path\n"
            f"instance_lock.acquire(Path({str(self.dir)!r}))\n"
            f"print(os.getpid(), flush=True)\n"
            f"time.sleep(30)\n"
        )
        proc = subprocess.Popen([sys.executable, "-c", code],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        reported = proc.stdout.readline().strip()
        self.assertTrue(reported.isdigit(), f"子进程未成功持锁：{reported!r}")
        return proc, reported

    def test_rejects_when_another_process_holds_it(self):
        """已有进程持锁时，本进程获取必须被拒绝，且能报出占用者。"""
        proc, _ = self._spawn_holder()
        try:
            with self.assertRaises(instance_lock.AlreadyRunningError) as ctx:
                instance_lock.acquire(self.dir)
            self.assertIn("PID", ctx.exception.owner)
        finally:
            self._stop(proc)

    def test_lock_released_when_holder_is_killed(self):
        """持有者被强杀后，锁应由操作系统释放——否则会「没人运行却启动不起来」。"""
        proc, _ = self._spawn_holder()
        self._stop(proc)
        time.sleep(1.0)
        path = instance_lock.acquire(self.dir)
        self.assertTrue(path.exists())

    def test_acquire_is_idempotent_within_process(self):
        """同一进程重复获取直接返回，不重复加锁、不漏句柄。"""
        first = instance_lock.acquire(self.dir)
        self.assertEqual(instance_lock.acquire(self.dir), first)

    def test_owner_readable_while_locked(self):
        """持锁期间，占用者信息仍可被其他进程读到（锁区刻意避开内容区）。"""
        proc, child_pid = self._spawn_holder()
        try:
            self.assertIn(child_pid, instance_lock.owner(self.dir))
        finally:
            self._stop(proc)


if __name__ == "__main__":
    unittest.main()
