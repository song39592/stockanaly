# 31 · 后端：`features/download/`（数据下载与每日调度）

> 状态：**已完成**（2026-10-06）　|　优先级：中　|　收益 ★★★☆☆ / 风险 ★★★☆☆
> 依赖第 27 项（已完成）

## 回填实际改动

```
backend_fastapi/features/download/
├─ __init__.py
├─ service.py   <- 原 download_service.py  任务引擎 + 调度循环（975 行，最"有状态"的一块）
├─ store.py     <- 原 download_store.py    任务落库、断点续跑、暂停/继续/取消（368 行）
└─ routes.py    <- 原 download_routes.py   HTTP 出口（135 行）
```

顶层保留 3 个**别名转发**（`sys.modules[__name__] = _impl`）：
`download_service.py` / `download_routes.py` / `download_store.py`。

### 函数体零改动 —— 逐行核对过

`git diff` 显示三个文件**只改了 import 块，共 9 行**：

| 文件 | 改动 |
|---|---|
| `service.py` | `db` / `price_service` / `price_store` / `tdx_reader` → `from core import ...`（第 27 项已搬）<br>`download_store` → `from features.download import store as download_store`（同包）<br>`history_store` → `from features.history import store as history_store`（第 30 项刚搬）<br>`from market_service import _ak` → `from features.market.service import _ak`（第 28 项已搬）<br>L682 函数内 `import cleanup` **不动**（`cleanup.py` 仍在顶层）<br>`from core.fallback import first_ok` 已正确，不动 |
| `routes.py` | `import download_service` → `from features.download import service as download_service` |
| `store.py` | `from db import ...` → `from core.db import ...` |

## 踩坑点五条的处置

1. **`_start_background()`（模块级 :975）仍会在启动时被调用** —— 触发链一环未改：
   `main.py` 的 `ROUTE_MODULES` 字符串 `"download_routes"` → 转发 → `features.download.routes`
   → import `features.download.service` → **模块级 `_start_background()` 执行**。
   **实测证据**（两条，见验收）：
   - 导入转发链后进程内出现 **`Thread-1 (_scheduler_loop)`** 线程；
   - 把 `download_store` 的 `last_cleanup_date` 清空再起后端，**85 秒内被调度线程写回
     `2026-10-06`**（`SCHEDULER_INTERVAL = 60`）—— 不只是"线程在"，而是真的跑了一轮。
2. **定时清理（第 24 项）链路完整** —— 上面那个 `last_cleanup_date` 就是证据：
   `_scheduler_tick()` → `import cleanup` → `cleanup.schedule_tick()` → 按**日期**去重
   （60 秒一轮也只真清一次）→ 日期存 `download_store.settings_map()`。
   `cleanup.py` 本身**未搬动**（它还服务日志轮转与独立 CLI）。
3. **落库路径未变，运行中任务不会"丢失"** —— 搬家前先查过：三个文件里
   **没有任何 `__file__` / `Path()` / `os.path` 路径推导**（这是第 27 项 `config.PROG_DIR`
   踩过的坑，本项先查后搬）。DB 路径来自 `core/db.py` 的
   `DB_PATH = config.STATE_DIR / "stock_history.db"`，落在**数据盘**：
   `E:\stockanaly-data\state\stock_history.db`（搬家前后一致）。
   表 `download_tasks`(25 行) / `download_settings`(8 行) / `download_codes` 全部健在。
4. **启动器退出码文案未动** —— `StockPoolLauncher.cs` 里硬编码的
   `backend_fastapi\requirements.txt` / `.venv` 属第 35 项范围，本次提交**不含任何 .cs**。
5. **按数据源并发未被混淆** —— `ThreadPoolExecutor(max_workers=len(jobs))`，
   5 个 block 各一线程，语义是"按数据源"；与第 07 项的"按标的批量并发"不是一回事。
   本项只改 import，未触碰这段。

## 搬家前做的三道专项检查（都是本项特有的风险）

1. **隐式副作用链** —— 查 `_start_background` 的全部调用点，确认它只在**模块级**被调一次；
   再查 `main.py` 对 download 的引用，确认只有 `ROUTE_MODULES` 里的**字符串** `"download_routes"`
   （`main.py` 不直接 import `download_service`）。
2. **路径推导** —— 三个文件 `__file__` / `Path()` / `os.path` 检索结果为**空**（见踩坑点 3）。
3. **全 walk 扫 import** —— 不只扫模块级，而是 `ast.walk` 扫任意嵌套层，
   抓出 L682 的函数内 `import cleanup`（确认不需改）。
   前置扫描还发现 `cleanup.py` 对 `download_store` 有**模块级 + 函数内各一次** import，
   两处都靠转发工作，已在转发文件注释里写明。

## 与搬家前基线逐项对照

| 项 | 搬家前 | 搬家后 |
|---|---|---|
| `STATE_DIR` | `E:\stockanaly-data\state` | 同 |
| `DB_PATH` | `E:\stockanaly-data\state\stock_history.db` | 同 |
| `STALE_TOLERANCE_MIN` | 10 | 同 |
| `MAX_CONCURRENCY` | 8 | 同 |
| `settings_map`（8 个键） | — | **零差异** |
| `last_cleanup_date` | `2026-10-06` | 同（后被清空做实验，已写回） |

## 验收（2026-10-06 实测）

- [x] `features/download/` 建立，三个模块迁入 ✅ 另加 `__init__.py`，重点说明
      「模块级 `_start_background()`」这条隐式副作用与它的触发链
- [x] 后端启动后**调度线程确实起来了** ✅ **两条独立证据**：
      ① 导入转发链后进程内出现 `Thread-1 (_scheduler_loop)` 线程；
      ② 把 `last_cleanup_date` 清空为空串再起后端，**85 秒内被写回 `2026-10-06`**
      —— 证明不只是线程存活，而是真的跑完一轮并触发了清理
- [x] 下载页：新建任务 / 进度 / 暂停 / 继续 / 取消 正常 ✅ **完整跑了一遍生命周期**：
      `新建 → queued(total=100)` → `进度 → running` → `暂停 → paused` →
      `进度 → paused` → `继续 → running` → `取消 → cancelled`，每步 `ok=true`
      （范围用 `scope=pool` + `mode=incremental` + `source=tdx` 本机数据，不打外网）
- [x] `recover_stale()` 在重启后仍能恢复未完成任务 ✅ 直接构造实验：
      把任务在库里写回 `running`（模拟进程被强杀、内存 worker 已消失），
      调 `recover_stale()` 返回 **1**，状态变回 **`paused`**。
      另外启动后端时那两个 `origin=auto` / `total=5585` 的任务也确实是 `paused` 状态
- [x] 定时清理仍每天执行一次 ✅ 见上（`last_cleanup_date` 实验即证据）；`cleanup.py` 未搬动
- [x] `/health` 的 `_module_errors` 为空 ✅ `ok=True`、`integrity.ok=True`、
      **11/11 路由模块挂载**、stderr **无任何告警**
- [x] 额外验证 ✅ 10 个模块全部导入成功（含 `cleanup`、`core.db`、`test_settings_download`、`main`）；
      3 个转发与实现**是同一对象**；全仓 .py 三引号成对 + `ast.parse` **全部通过**；
      4 个只读端点（list/settings/tdx-status/auto-state）全部 `ok=true`；
      52 个单元测试全绿；真实 `.env` 未被改动
- [x] **函数体零改动** ✅ `git diff` 只有 9 行 import 改动
- [x] HTTP 路径零变化 ✅ prefix 仍是 `/api/history`，`main.py` 的字符串引用照旧可用

## 记录

- 2026-10-06 执行，位置为阶段 C 第 20 步（排在 30 之后）。
- 流程沿用第 29/30 项确立的做法：**所有 Python 文件生成与替换都走脚本 + 脚本内双校验**
  （三引号个数为偶 + `ast.parse` 通过）才落盘。本项 **11 处 import 替换 + 4 个新文件，
  一次事故都没出**。
