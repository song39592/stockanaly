# -*- coding: utf-8 -*-
"""串行多源回退的**总时间预算**（bug-01 / bug-02 共用件）。

## 要解决的问题

「多数据源 / 多报告期**串行**回退」是最容易写出**无上限等待**的一种结构：
每个源各自带超时（`_ak(..., timeout=20)`），源码里看每个都合理，
但它们**串行累加** —— 8 个源 × 20s = **160s**。而调用方（HTTP 接口在启动器一侧）
往往只等 30~90s，一旦上游变慢，用户看到的就是「无响应」。

代码里几乎所有降级注释都写着「失败一律回退，不该拖垮主流程」，
但那只防住了「**失败**」，没防住「**变慢**」：
上游卡住时不抛异常、不返回 None，只是每次都慢满超时，保护形同虚设。

## 用法

    label, value = first_ok(sources, budget=6.0, per_call=20.0)

    sources = [("新浪", lambda t: _ak(ak.stock_zh_a_daily, timeout=t)),
               ("腾讯", lambda t: _ak(ak.stock_zh_a_hist_tx, timeout=t))]
    label, frame = first_ok(sources, budget=6.0, per_call=20.0)
    if frame is None:
        return {"error": "所有数据源都不可用"}      # 既有降级语义保持不变

## 为什么不做成「重试」

这里要的不是「失败重试」，而是「**别把用户等太久**」。
所以：不指数退避、不在预算内反复重试同一个源、预算耗尽就明确放弃。

## 两种本件**解决不了**的反模式（已在排查中遇到，勿重复踩）

- **多线程并行 + V8 全局锁**：`market_service._ak` 对依赖 py_mini_racer 的接口
  要抢一把**全局串行锁** `_V8_LOCK`，所以「并行」发起 2 个 V8 接口实际仍会排队串行。
  这种要用 `join` 的总 deadline 控制，不走本件。
- **循环按数据量展开**：`board_service._sync_worker` 是「N 指标 × M 板块」两层循环，
  耗时随数据规模走，没有可用的静态预算（它在后台线程里跑，不阻塞 HTTP 请求）。
"""
from __future__ import annotations

import time
from typing import Any, Callable, Iterable, Optional, Tuple

__all__ = ["first_ok"]


def first_ok(sources: Iterable[Tuple[str, Callable[[float], Any]]],
             budget: float, per_call: float,
             is_ok: Optional[Callable[[Any], bool]] = None) -> Tuple[Optional[str], Any]:
    """依次尝试 sources，返回第一个取到值的 (标签, 值)；全失败返回 (None, None)。

    `sources` 是 (标签, 取数函数) 序列，标签用于回报「数据来自哪个源」。
    取数函数的入参是**本次允许它阻塞的秒数**，调用方应把它透传给底层
    （如 `_ak(fn, timeout=秒)`），这样预算才真正管得住。

    `is_ok` 判定「取到了」。默认 `值 is not None and 值 is not False`；
    调用方若用**空 DataFrame / 空列表**表示失败，需自行传入判定，例如
    `lambda df: df is not None and not getattr(df, "empty", True)`。

    **总耗时上限 = budget 秒，与源的数量无关。** 预算用尽立即停，
    哪怕后面的源其实可用 —— 取不到就由调用方按既有约定降级。
    """
    deadline = time.monotonic() + max(0.0, float(budget))
    check = is_ok if is_ok is not None else (lambda v: v is not None and v is not False)
    label: Optional[str] = None
    for label, fetch in sources:
        left = deadline - time.monotonic()
        if left <= 0:
            break
        try:
            value = fetch(min(float(per_call), left))
        except Exception:                    # noqa: BLE001 - 单源失败换下一源，与既有降级一致
            continue
        if check(value):
            return label, value
    return None, None