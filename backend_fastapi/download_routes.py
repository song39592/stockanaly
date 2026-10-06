# -*- coding: utf-8 -*-
"""兼容转发：`routes` 已移入 `features/download/routes.py`（第 31 项）。

**这是过渡文件，不是实现。** 新代码请直接用 `features.download.routes`。

**这个转发尤其关键**：`main.py` 的 `ROUTE_MODULES` 与 `/health` 模块清单
都以**字符串** `"download_routes"` 引用它，顶层必须保留这个可导入的同名模块。

它是触发后台启动的**链条起点**（见 features/download/__init__.py）。
`router` 的 prefix 仍是 `/api/history`，**HTTP 路径没有任何变化**。

## 为什么用 `sys.modules` 别名，而不是 `from ... import *`

别名转发拿到的是**同一个模块对象**，因此下划线私有名照常可见、模块级状态
（`download_service` 的 `_runtimes` / `_claim_lock` / 调度线程）是同一份，
测试里的 `patch.object(...)` 真正生效。`import *` 只复制名字做不到这些。
"""

import sys

from features.download import routes as _impl

sys.modules[__name__] = _impl
