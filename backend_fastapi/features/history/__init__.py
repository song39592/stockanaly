# -*- coding: utf-8 -*-
"""股票池历史（第 30 项）。

    service.py   <- 原 history_service.py  K线读取 / 入池出池轨迹 / 消息面
    store.py     <- 原 history_store.py    pool_snapshots 快照
    routes.py    <- 原 history_routes.py   HTTP 出口

## 踩坑点三条的处置（逐条遵守）

1. **`service.load_trusted_bars` 仍直接调 `price_store.load_bars`**，
   不改走 `kline_service.get_bars` —— 它靠捕获 `price_store.UntrustedDataError`
   实现「脏数据自动重抓」，而 `kline_service` 不抛这个异常（见第 17 项）。
   这是**必要绕过**，不是历史遗留。
2. **周/月合样仍走 `kline_service.resample`**，保持原样。
3. **`store.latest_snapshot_codes()` 是「当前股票池代码」的来源之一**，
   `download_service` 用它挑下载范围；旧路径 `history_store` 保留别名转发，
   调用方无需改动。

## ⚠️ 遗留的层级倒置（不在本项修）

`core/db.py` 的 `init_db()` 里**函数内** `import history_store`（原注释：
「避免与 db 形成模块级循环依赖」），本项之后它指向 `features.history.store`
—— 也就是 **core 层反过来依赖 features 层**。

这是**既有状况**：`db.init_db()` 本来就要编排 `price_store` / `download_store` /
`history_store` 三家的建表，而那三者都在上层。本项只做位置移动、不动调用方式
（`core/db.py` 一行未改，仍走顶层转发），**这个倒置留给后续单独处理**。
"""
