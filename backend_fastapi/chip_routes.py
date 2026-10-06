# -*- coding: utf-8 -*-
"""兼容转发：`chip_routes` 已移入 `features/chip/scr_routes.py`（第 34 项，**并改名**）。

**这是过渡文件，不是实现。** 新代码请直接用 `features.chip.scr_routes`。

⚠️ **本模块名被 `main.py` 以字符串引用**：`ROUTE_MODULES` 里有
`("筹码体系 · SCR 选股", "chip_routes")`，靠 `importlib` 按名字导入。
**顶层必须保留这个可导入的同名模块**，否则该路由不会被挂载。

`router` 的 prefix 仍是 `/api/chip/scr`，**HTTP 路径没有任何变化**。
改名原因见 `chip_service.py` 转发；转发名保持原样，既有调用方零改动。
"""
import sys

from features.chip import scr_routes as _impl

sys.modules[__name__] = _impl