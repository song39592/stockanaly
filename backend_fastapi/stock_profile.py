# -*- coding: utf-8 -*-
"""兼容转发：`profile` 已移入 `features/stock/profile.py`（第 29 项）。

**这是过渡文件，不是实现。** 新代码请直接用 `features.stock.profile`。

本模块提供 `lockup_ratio` / `free_top_holders`（筹码锁仓修正的数据源）与
`profile()`（个股基本信息）。依赖它的调用方：
- `chip_formulas/core/data.py` —— `import stock_profile` 取 `lockup_ratio`
- `features/stock/routes.py` —— 个股基本信息接口

## 为什么必须用 `sys.modules` 别名，而不是 `from ... import *`

别名转发拿到的是**同一个模块对象**，因此：
- 下划线私有名（`lockup_ratio` / `free_top_holders` / `_market_prefix` 等）照常可见；
- 模块级可变状态（`stock_profile._CACHE`、board_index 的 TTL 状态）**是同一份**，
  `clear_cache()` 清的就是实现里那份 —— `import *` 复制出的副本做不到这点；
- 测试里的 `patch.object(...)` 真正生效。

对应实现里的函数体**完全没动**（第 29 项只做位置移动）。
"""

import sys

from features.stock import profile as _impl

sys.modules[__name__] = _impl
