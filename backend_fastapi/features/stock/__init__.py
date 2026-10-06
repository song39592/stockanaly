# -*- coding: utf-8 -*-
"""个股 / 估值 / 板块指数（第 29 项）。

    profile.py       <- 原 stock_profile.py    个股档案：行业 / 市值 / 十大股东 / 涨停连板
    routes.py        <- 原 stock_routes.py     quote / valuation / research / boards
    valuation.py     <- 原 valuation_service.py 估值模型 + 行情抓取
    board_index.py   <- 原 board_service.py     板块成分股索引（DB 持久化 TTL）

## 搬运时刻意**没有**做的事（待办 29 的踩坑点，逐条遵守）

1. **没有**重构 `stock_profile` 里的任何函数。原先它带的 NaN 清洗与
   锁仓修正数据源（`free_top_holders` / `lockup_ratio`）都保持原样 ——
   第 09 项已把清洗收进 `core/jsonutil.py`（这里只剩薄封装），
   第 10 项要动的 `scr90` 合并不在本项范围。
2. **没有**统一 `_cached`（失败也缓存）与 `lockup_ratio`（失败不缓存）的矛盾语义 ——
   那是独立缺陷，统一时必须单独验收「取数失败后能否重试」。
3. **没有**动 `valuation` 的出口清洗（已由第 09 项用 `json_safe_deep` 补上）。
4. **没有**统一三份互相矛盾的 market 前缀规则（`_market_prefix` /
   `price_service.market_symbol` / `share_service.market_symbol`）—— 留给第 16 项。
5. **没有**把 `board_index` 的 TTL 从 DB 改成进程内缓存（`_TTL_HOURS=24` 是跨进程持久化的）。

## 旧路径必须保留别名转发

`chip_formulas/core/data.py` 用 `import stock_profile` 取 `lockup_ratio`；
`test_main.py` 与 `main.py`（字符串 `"stock_routes"`）也都按旧名引用。
顶层 4 个转发文件用 `sys.modules` 别名实现，所以这些调用方一行都不用改。
"""
