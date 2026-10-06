# -*- coding: utf-8 -*-
"""兼容转发：`valuation` 已移入 `features/stock/valuation.py`（第 29 项）。

**这是过渡文件，不是实现。** 新代码请直接用 `features.stock.valuation`。

`features/stock/routes.py` 以 `from features.stock import valuation as valuation_service`
引用它；旧名 `valuation_service` 仍被文档与外部脚本使用，故保留转发。

注意：`valuation._market_prefix` 与 `price_service.market_symbol` /
`share_service.market_symbol` 是**三份互相矛盾的市场前缀规则** —— 本项**不统一**，
留给第 16 项。

## 为什么必须用 `sys.modules` 别名，而不是 `from ... import *`

别名转发拿到的是**同一个模块对象**，因此：
- 下划线私有名（`lockup_ratio` / `free_top_holders` / `_market_prefix` 等）照常可见；
- 模块级可变状态（`stock_profile._CACHE`、board_index 的 TTL 状态）**是同一份**，
  `clear_cache()` 清的就是实现里那份 —— `import *` 复制出的副本做不到这点；
- 测试里的 `patch.object(...)` 真正生效。

对应实现里的函数体**完全没动**（第 29 项只做位置移动）。
"""

import sys

from features.stock import valuation as _impl

sys.modules[__name__] = _impl
