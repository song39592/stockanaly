# -*- coding: utf-8 -*-
"""兼容转发：`service` 已移入 `features/download/service.py`（第 31 项）。

**这是过渡文件，不是实现。** 新代码请直接用 `features.download.service`。

⚠️ **本模块一被 import 就启动后台**（模块级 `_start_background()`）：
`recover_stale()` 把上次没跑完的任务转 paused，再起 `SCHEDULER_INTERVAL = 60` 秒一轮的
调度线程（异常全吞，线程死了不会自动更新）。

**顶层必须有这个转发**，否则 `main.py` 的 `"download_routes"` -> 转发 -> `routes` ->
`service` 这条链断掉，后台根本不会启动 —— 而症状极隐蔽：接口全正常，只是不自动更新。

## 为什么用 `sys.modules` 别名，而不是 `from ... import *`

别名转发拿到的是**同一个模块对象**，因此下划线私有名照常可见、模块级状态
（`download_service` 的 `_runtimes` / `_claim_lock` / 调度线程）是同一份，
测试里的 `patch.object(...)` 真正生效。`import *` 只复制名字做不到这些。
"""

import sys

from features.download import service as _impl

sys.modules[__name__] = _impl
