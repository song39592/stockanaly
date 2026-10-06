# -*- coding: utf-8 -*-
"""兼容转发：`store` 已移入 `features/history/store.py`（第 30 项）。

**这是过渡文件，不是实现。** 新代码请直接用 `features.history.store`。

`pool_snapshots` 快照存储。依赖它的调用方：
- `core/db.py` —— `init_db()` 里**函数内** import（避免循环依赖）
- `download_service.py` —— 用 `latest_snapshot_codes()` 挑下载范围
- `test_history_store.py` / `test_integrity.py`

## 为什么用 `sys.modules` 别名，而不是 `from ... import *`

别名转发拿到的是**同一个模块对象**，因此下划线私有名照常可见、
模块级状态（`history_store` 的连接与建表标志）是同一份，测试里的
`patch.object(...)` 真正生效。`import *` 只复制名字做不到这些。
"""

import sys

from features.history import store as _impl

sys.modules[__name__] = _impl
