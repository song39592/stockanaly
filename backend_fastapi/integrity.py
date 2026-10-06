# -*- coding: utf-8 -*-
"""兼容转发：`integrity` 已移入 `features/system/integrity.py`（第 32 项）。

**这是过渡文件，不是实现。** 新代码请直接用 `features.system.integrity`。

⚠️ **本模块被三方依赖，全部走本转发**：
- `main.py:28` 的模块级 import —— 启动时算一次并缓存进 `_integrity_state`（踩坑点 1）
- `dumplog.py` —— 第 23 项诊断包的数据源之一
- `test_integrity.py`

拿到的是同一模块对象，所以缓存与运行时重绑定都真正生效。

## 为什么用 `sys.modules` 别名，而不是 `from ... import *`

别名转发拿到的是**同一个模块对象**，因此下划线私有名照常可见
（`integrity._all_targets` / `_latest_mtime` / `_parse_iso` 三个是跨模块可见的），
`patch.object(...)` 真正生效。`import *` 只复制名字做不到这些。
"""

import sys

from features.system import integrity as _impl

sys.modules[__name__] = _impl
