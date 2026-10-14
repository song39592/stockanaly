# -*- coding: utf-8 -*-
"""运行期控制：**暂停 / 继续 / 停止**（独立小模块，供 base 与 data 共用）。

## 为什么要单独成文件
`base`（契约层）需要它来给策略提供 `checkpoint()`；`data`（数据层）需要在
「逐标的取数」的进度点检查它。而 `data` **不能**反过来 import `base`
（契约层比数据层高，依赖方向反了），`base` 也不该为了一个异常类型去依赖 `data`。
所以把「运行控制」抽成第三方：两层都只依赖它，谁也不依赖谁。

## 为什么是协作式而不是抢占式
Python 没有安全的办法抢占中断一个正在跑的线程（`PyThreadState_SetAsyncExc`
只在字节码边界生效，且会在任意一行抛异常 —— 那样 SQLite 连接与
`data._PER_CACHE` 会停在半途）。所以这里只置标志，由**正在跑的那次计算**
在指定的检查点主动响应：

  · 数据加载阶段 → `data.load_bars` 的每票进度回调里检查（每只都触发）
  · 策略计算阶段 → 策略在自己的**逐标的循环**里调 `checkpoint()`

## ⚠️ 代价（必须说清，别假装覆盖了全部）
没写 `checkpoint()` 的策略，在它自己的计算循环里**停不住、也暂停不了**。
全市场实测（2026-10-14，5578 只 × 3 年）：取数约 350s、策略计算约 30s，
覆盖取数阶段已能解决绝大部分等待；但 scr90 这类重策略的信号计算（可达 25 分钟）
必须由策略自己埋点，否则停不掉。

## 线程安全
`pause` 用 `Event` 而不是裸 bool：暂停时工作线程要**阻塞在这里**等继续，
需要一个可安全多线程访问的对象。`cancel` 同理。
"""
from __future__ import annotations

import threading
import time


class Cancelled(Exception):
    """回测/推荐被用户**停止**（区别于「跑失败」）。

    单独一个类型而不是复用 RuntimeError：编排层要把它认成「用户主动取消」，
    不在界面上显示成红色错误、不弹堆栈。

    ⚠️ 它必须能**穿透**任何「回调不影响主流程」的兜底 except —— 例如
    `data.load_bars` 的进度回调里有 `except Exception: pass`（那个兜底本身是对的：
    进度回调不该让取数失败）。若不专门 re-raise，取消信号会被静默吃掉，
    表现为「点了停止没反应」。故各层凡是吞异常的地方都要显式放行本类型。
    """


class RunControl:
    """一次运行的控制句柄。线程安全；可在多个线程里重复调用暂停/继续/停止。"""

    __slots__ = ("cancel", "pause")

    def __init__(self):
        self.cancel = threading.Event()
        self.pause = threading.Event()

    @property
    def paused(self) -> bool:
        return self.pause.is_set()

    @property
    def cancelled(self) -> bool:
        return self.cancel.is_set()

    def checkpoint(self) -> None:
        """在循环里调用：已停止则抛 `Cancelled`；已暂停则**阻塞**到继续。

        暂停用 0.2s 轮询而不是 `Event.wait()`：这样「暂停期间再点停止」能被
        立刻看到，不会卡在 wait() 里直到有人唤醒。
        """
        if self.cancel.is_set():
            raise Cancelled("用户停止了本次运行")
        while self.pause.is_set():
            if self.cancel.is_set():
                raise Cancelled("用户停止了本次运行")
            time.sleep(0.2)

    def request_pause(self) -> None:
        self.pause.set()

    def resume(self) -> None:
        self.pause.clear()

    def request_cancel(self) -> None:
        self.cancel.set()
        # 取消时必须解开暂停：否则工作线程正卡在 pause 的 while 循环里。
        self.pause.clear()


# 当前运行的控制句柄。编排层（backtest）在启动前 install、结束时必须 clear。
# 为 None 时 checkpoint() 立刻返回 —— 单测与注册冒烟都走 None，
# 因此给策略层加检查点不会给这些路径带来任何行为变化。
RUN_CONTROL: "RunControl | None" = None


def install_control(ctrl: "RunControl | None") -> "RunControl | None":
    """安装/清除运行控制，返回**上一个**（便于调用方自己恢复）。"""
    global RUN_CONTROL
    prev = RUN_CONTROL
    RUN_CONTROL = ctrl
    return prev


def checkpoint() -> None:
    """在逐标的/逐日循环里调一次，即可支持暂停与停止。

    未安装控制器时是一个 `is None` 判断，无任何开销。
    """
    ctrl = RUN_CONTROL
    if ctrl is not None:
        ctrl.checkpoint()


def is_paused() -> bool:
    """当前是否处于暂停状态（供编排层上报给前端）。"""
    ctrl = RUN_CONTROL
    return bool(ctrl.paused) if ctrl is not None else False


def is_cancelled() -> bool:
    """当前是否已被请求停止（供编排层在阶段边界自查）。"""
    ctrl = RUN_CONTROL
    return bool(ctrl.cancelled) if ctrl is not None else False