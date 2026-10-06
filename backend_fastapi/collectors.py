# -*- coding: utf-8 -*-
"""兼容转发：`collectors` 已移入 `features/mentor/collectors.py`（第 33 项）。

**这是过渡文件，不是实现。** 新代码请直接用 `features.mentor.collectors`。

**跨功能共享**：个股页（`features/stock/routes.py`）与股票池历史
（`features/history/service.py`）都用它的公告 / 新闻 / 研报 / 事件信号采集。
本转发保留这些旧路径引用可用；新代码请直接用 `features.mentor.collectors`。

## 为什么用 `sys.modules` 别名，而不是 `from ... import *`

别名转发拿到的是**同一个模块对象**，因此下划线私有名照常可见、模块级状态是同一份
（`collectors` 的 `ak` / `httpclient`，`llm_client` 的 `config`），
测试里的 `patch.object(...)` 真正生效。`import *` 只复制名字做不到这些。
"""

import sys

from features.mentor import collectors as _impl

sys.modules[__name__] = _impl
