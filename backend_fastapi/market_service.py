# -*- coding: utf-8 -*-
"""兼容转发：`market_service` 已移入 `features/market/service.py`（第 28 项）。

**这是过渡文件，不是实现。** 新代码请直接用 `features.market.service`。

## 为什么必须用 `sys.modules` 别名，而不是 `from ... import *`

`market_service` 是**全仓 akshare 的唯一封装提供者**：`_ak(fn, *args, timeout=...)`
被 7 个模块 import（`board_service` / `collectors` / `core.price_service` /
`download_service` / `stock_profile` / `strategies.core.backtest` / `valuation_service`）。

`from x import *` **不导出下划线开头的名字**，用它转发会让那 7 处的
`from market_service import _ak` 全部 AttributeError。别名转发是**同一个模块对象**，
`_ak` / `ak` / 其它私有名都照常可见，`patch.object` 之类的打补丁也真正生效。
"""
import sys

from features.market import service as _impl

sys.modules[__name__] = _impl