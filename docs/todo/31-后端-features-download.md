# 31 · 后端：`features/download/`（数据下载与每日调度）

> 状态：待办　|　优先级：中　|　收益 ★★★☆☆ / 风险 ★★★☆☆　|　**依赖第 27 项**

## 问题
下载与调度相关模块混在扁平层。这一组**自带调度循环与任务引擎**，是全仓最"有状态"的一块。

## 建议目标
```
backend_fastapi/features/download/
├─ __init__.py
├─ service.py   ← 原 download_service.py（含调度器：_scheduler_tick / _scheduler_loop / _start_background）
├─ routes.py    ← 原 download_routes.py
└─ store.py     ← 原 download_store.py（任务落库、断点续跑、暂停/继续/取消）
```

## 注意（踩坑点）
1. **`_start_background()`（:946-953）是「模块导入即启动」** —— 移动后必须确认它仍会在启动时被调用
   （现在依赖 `main.py` 的 import 顺序）。这是最容易被搬家打断的隐式副作用。
2. `_scheduler_tick`（:662）是**第 24 项（定时清理）的挂载点** —— 若 24 已做，本项搬运要连带调整清理的调用位置。
3. 任务引擎（`create_task` / `_spawn` / `_worker` / `_RateLimiter` / `recover_stale()`）**落库且支持断点续跑**，
   `recover_stale()` 保证重启后恢复 —— 搬家时不要改变任务落库路径，否则运行中的任务会"丢失"。
4. 退出码文案（启动器 :2120-2125）硬编码了 `backend_fastapi\requirements.txt` / `.venv` 等路径 —— 这些文案属于
   第 35 项范围，本项不要动。
5. `download_service` 有 `cf.ThreadPoolExecutor(max_workers=len(jobs))`（:1161）按数据源并发（5 个 block 各一线程），
   与按标的批量并发语义不同 —— 搬运时别与第 07 项（批量并发）混淆。

## 建议做法
同第 27 项的**兼容转发**模式。

## 验收
- [ ] `features/download/` 建立，三个模块迁入
- [ ] 后端启动后**调度线程确实起来了**（等一个 tick 或看日志）
- [ ] 下载页：新建任务 / 进度 / 暂停 / 继续 / 取消 正常
- [ ] `recover_stale()` 在重启后仍能恢复未完成任务
- [ ] 若第 24 项已完成：定时清理仍每天执行一次
- [ ] `/health` 的 `_module_errors` 为空
