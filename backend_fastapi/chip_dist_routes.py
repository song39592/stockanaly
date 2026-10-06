# -*- coding: utf-8 -*-
"""兼容转发：`chip_dist_routes` 已移入 `features/chip/dist_routes.py`（第 34 项，**并改名**）。

**这是过渡文件，不是实现。** 新代码请直接用 `features.chip.dist_routes`。

⚠️ **本模块名被 `main.py` 以字符串引用**：`ROUTE_MODULES` 里有
`("筹码体系 · 筹码分布", "chip_dist_routes")`，靠 `importlib` 按名字导入。
**顶层必须保留这个可导入的同名模块**，否则该路由不会被挂载。

它还 import `chip_formulas`（`:20`）—— 走顶层转发即可，
`core/registry.py` 的动态导入命名空间已改为按 `__package__` 推导，不受影响。
`router` 的 prefix 仍是 `/api/chip/dist`，**HTTP 路径没有任何变化**。
"""
import sys

from features.chip import dist_routes as _impl

sys.modules[__name__] = _impl