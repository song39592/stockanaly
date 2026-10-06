# -*- coding: utf-8 -*-
"""盘面与板块（第 28 项）。

    service.py   <- 原 `market_service.py`
    routes.py    <- 原 `market_routes.py`

## 为什么 `market_service` 归到「盘面」而不是 `core/`

它看起来像基础设施（被 7 个模块 import、还提供全仓唯一的 akshare 封装 `_ak`），
但它本身是**盘面业务逻辑**（TTL 缓存、盘后延长、连板梯队、板块资金流…），
依赖的是 pandas + akshare 而非其它 core 模块。放进 `core/` 会让 core 反过来依赖业务口径。

## 旧路径必须保留别名转发（不是 `import *`）

`market_service` 是**全仓 akshare 的唯一封装提供者** —— `_ak(fn, *args, timeout=...)`
被 7 个模块使用（`board_service` / `collectors` / `core.price_service` / `download_service` /
`stock_profile` / `strategies.core.backtest` / `valuation_service`）。
所以顶层 `market_service.py` / `market_routes.py` 的转发**必须用 `sys.modules` 别名**：
`from ... import *` **不会**导出下划线私有的 `_ak`，那 7 处会全部 AttributeError。
`main.py` 的 `ROUTE_MODULES` 也按**字符串** `"market_routes"` 引用，同样依赖这个转发。

另注：`valuation_service` 用 `ms._ak(ms.ak...)` 形式（属性访问），
别名转发下 `ms._ak` 与 `ms.ak` 均可用。

## 本次未一并搬的

`board_service.py`（板块成分股索引）留在顶层：它服务于**个股**页的
`/api/stock/boards`（查某只股票所属板块），且只在 `stock_routes` 里被调用，
归到「盘面」不如留给第 29 项（features/stock）一起处理。
"""
