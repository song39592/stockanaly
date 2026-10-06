# -*- coding: utf-8 -*-
"""兼容转发：`store` 已移入 `features/download/store.py`（第 31 项）。

**这是过渡文件，不是实现。** 新代码请直接用 `features.download.store`。

任务落库（主库 `stock_history.db`）、断点续跑、暂停/继续/取消、`recover_stale()`。

调用方：`features.download.service`（`import download_service` 已改包内引用）、
`cleanup.py`（定时清理的日期去重存这里，**模块级 + 函数内各 import 一次**，
两处都走本转发）、`core/db.py`（`init_db()` 里函数内 import，避免循环依赖）、
`test_settings_download.py`。

## 为什么用 `sys.modules` 别名，而不是 `from ... import *`

别名转发拿到的是**同一个模块对象**，因此下划线私有名照常可见、模块级状态
（`download_service` 的 `_runtimes` / `_claim_lock` / 调度线程）是同一份，
测试里的 `patch.object(...)` 真正生效。`import *` 只复制名字做不到这些。
"""

import sys

from features.download import store as _impl

sys.modules[__name__] = _impl
