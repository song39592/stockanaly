# -*- coding: utf-8 -*-
"""筹码矩阵的**内存热缓存**（进程内，最近 512 条）。

跨进程 / 跨天的持久与「增量落盘」由 `chip_matrix_store`（SQLite）负责；本模块只解决
**同进程内**同请求的重复切片——避免每次都去读磁盘 / 重新切窗口。

键含 start/end/window（与 `chip_matrix_store` 存「全历史」的键不同）：这里命中等价于
「同一只看同一段」，直接返回已切好的 ChipResult。
"""
from __future__ import annotations

from collections import OrderedDict

_MEM_MAX = 512


class _Mem:
    def __init__(self, maxsize: int):
        self.maxsize = maxsize
        self.d: "OrderedDict" = OrderedDict()

    def get(self, key):
        v = self.d.get(key)
        if v is None:
            return None
        self.d.move_to_end(key)
        return v

    def put(self, key, val) -> None:
        self.d[key] = val
        self.d.move_to_end(key)
        while len(self.d) > self.maxsize:
            self.d.popitem(last=False)


_MEM = _Mem(_MEM_MAX)


def load(key):
    """返回内存中缓存的 ChipResult，未命中返回 None。"""
    return _MEM.get(key)


def save(key, res) -> None:
    """写入内存热缓存。"""
    _MEM.put(key, res)


def clear() -> None:
    """清空内存热缓存。"""
    _MEM.d.clear()
