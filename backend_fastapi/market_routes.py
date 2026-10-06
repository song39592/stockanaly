# -*- coding: utf-8 -*-
"""兼容转发：`market_routes` 已移入 `features/market/routes.py`（第 28 项）。

**这是过渡文件，不是实现。** 新代码请直接用 `features.market.routes`。

这个转发尤其关键：`main.py` 的 `ROUTE_MODULES` 与 `/health` 的模块清单
都以**字符串** `"market_routes"` 引用它（`importlib.import_module("market_routes")`），
所以顶层必须保留这个可导入的同名模块。

注意：`router` 的 `prefix` 仍是 `/api/market`，**HTTP 路径没有任何变化**，
启动器与网页端不需要改。
"""
import sys

from features.market import routes as _impl

sys.modules[__name__] = _impl