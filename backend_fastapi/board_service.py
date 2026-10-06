# -*- coding: utf-8 -*-
"""兼容转发：`board_index` 已移入 `features/stock/board_index.py`（第 29 项）。

**这是过渡文件，不是实现。** 新代码请直接用 `features.stock.board_index`。

板块成分股索引。**TTL 持久化在 DB**（`_TTL_HOURS=24`，跨进程有效），
不是进程内缓存 —— 搬运时保持原样，别改成 `lru_cache`。

现被 `features/stock/routes.py` 以 `board_index` 之名引用；旧名保留转发。

## 为什么必须用 `sys.modules` 别名，而不是 `from ... import *`

别名转发拿到的是**同一个模块对象**，因此：
- 下划线私有名（`lockup_ratio` / `free_top_holders` / `_market_prefix` 等）照常可见；
- 模块级可变状态（`stock_profile._CACHE`、board_index 的 TTL 状态）**是同一份**，
  `clear_cache()` 清的就是实现里那份 —— `import *` 复制出的副本做不到这点；
- 测试里的 `patch.object(...)` 真正生效。

对应实现里的函数体**完全没动**（第 29 项只做位置移动）。
"""

import sys

from features.stock import board_index as _impl

sys.modules[__name__] = _impl
