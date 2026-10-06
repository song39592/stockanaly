# -*- coding: utf-8 -*-
"""历史数据下载与每日调度（第 31 项）。

    service.py   <- 原 download_service.py  任务引擎 + 调度循环（最"有状态"的一块）
    store.py     <- 原 download_store.py    任务落库、断点续跑、暂停/继续/取消
    routes.py    <- 原 download_routes.py   HTTP 出口

## ⚠️ 本包最重要的隐式副作用：`service.py` 一被 import 就启动后台

`service.py` **模块级**有一行 `_start_background()`，它做两件事：

1. `download_store.recover_stale()` —— 把上次进程没跑完的任务（库里写着
   running/queued 但内存 worker 已随进程消失）统一转成 `paused`，等用户点继续。
   **不处理的话这些任务会永远卡住没人推进。**
2. `threading.Thread(target=_scheduler_loop, daemon=True).start()` ——
   调度线程，`SCHEDULER_INTERVAL = 60` 秒一轮，永不退出（异常全吞，线程死了不会自动更新）。

**触发链**（搬家后仍然成立，一环都没改）：
`main.py` 的 `ROUTE_MODULES` 里有字符串 `"download_routes"` -> 转发
`download_routes` -> `features.download.routes` -> import
`features.download.service` -> **模块级 `_start_background()` 执行**。

因此顶层**必须**保留 `download_routes.py` 这个可导入的同名模块，否则后台根本不会启动，
而症状很隐蔽：接口全部正常，只是永远不自动更新、日志里没有调度痕迹。

## 其余踩坑点的处置

- **定时清理（第 24 项）**：由调度线程驱动 —— `_scheduler_tick()` 里
  `import cleanup; cleanup.schedule_tick()`，按**日期**去重（60 秒一轮也只真清一次）。
  去重日期存 `download_store.settings_map()["last_cleanup_date"]`，`cleanup.py` 未搬动，
  链路完整。
- **落库路径**：三个文件里**没有任何 `__file__` / 路径推导**，DB 路径来自
  `core/db.py` 的 `DB_PATH = config.STATE_DIR / "stock_history.db"`（数据盘，非 `__file__`）。
  搬家不会让运行中的任务"丢失"。
- **按数据源并发**：`ThreadPoolExecutor(max_workers=len(jobs))`，5 个 block 各一线程。
  语义是"按数据源"，与第 07 项的"按标的批量并发"**不是一回事**，别混淆。
"""
