# -*- coding: utf-8 -*-
"""兼容转发：`mentor_store` 已移入 `features/mentor/store.py`（第 33 项）。

**这是过渡文件，不是实现。** 新代码请直接用 `features.mentor.store`。

被三方依赖，全部走本转发：
- `main.py:30` 的模块级 import + `:175` 的 `mentor_store.init_db()`
  （失败时以「大佬策略实验室（本地库初始化）」为 label 记入 `_module_errors`）
- `features/mentor/routes.py`（已改指包内）
- `test_mentor_store.py`

## 为什么用 `sys.modules` 别名，而不是 `from ... import *`

别名转发拿到的是**同一个模块对象**，因此下划线私有名照常可见、模块级状态是同一份
（`collectors` 的 `ak` / `httpclient`，`llm_client` 的 `config`），
测试里的 `patch.object(...)` 真正生效。`import *` 只复制名字做不到这些。
"""

import sys

from features.mentor import store as _impl

sys.modules[__name__] = _impl
