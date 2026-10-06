# 变更日志

所有重要变更记录于此，格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [未发布]

### 变更

- **筹码体系归位 `features/chip/`**（待办 34，**阶段 C 收官**，全仓耦合面最大、所以最后搬）：
  ```
  features/chip/
  ├─ __init__.py
  ├─ scr_service.py    <- 原 chip_service.py       SCR 导入/合并/三档分类（529 行）
  ├─ rank_service.py   <- 原 chip_rank_service.py  SCR90 周级三档（439 行）
  ├─ scr_routes.py     <- 原 chip_routes.py        /api/chip/scr/*
  ├─ dist_routes.py    <- 原 chip_dist_routes.py   /api/chip/dist/*
  ├─ rank_routes.py    <- 原 chip_rank_routes.py   /api/chip/rank/*
  └─ formulas/         <- 原 chip_formulas/ 整包（含 ARCHITECTURE.md，7 个 .py）
  ```
  顶层保留 **6 个别名转发**。**5 个模块改名**（三个 routes 否则会撞名），
  但**转发名一律保持原样** —— `main.py` 的 `ROUTE_MODULES` 字符串引用与
  `cleanup.py:140` / `dumplog.py:141` 等既有调用方**一行都不用改**。
  - ⭐ **发现「转发做不到的事」（本项最关键的认知）**：
    `sys.modules` 别名转发能覆盖「import 该模块后用它的公开名」，
    但**覆盖不了「按包名动态导入它的子模块」**。
    `formulas/core/registry.py:34` 原本**硬编码** `_FORMULA_PACKAGE = "chip_formulas"`，
    `:69` 用 `importlib.import_module(f"{_FORMULA_PACKAGE}.{mod_name}")` 拼公式子模块名。
    硬编码在搬家后会去 import 一个已不存在的顶层命名空间；
    而只靠转发的话，`importlib.import_module("chip_formulas.tri_decay")`
    会因父模块被换成别名对象而**把同一个文件当成两个模块各加载一次**
    （`sys.modules` 出现两个键，公式注册表与 `__module__` 全乱）。
    改为 **`_FORMULA_PACKAGE = __package__.rsplit(".", 1)[0]`**（本文件位于 `<包>.core`，
    去掉最后一段即包名）—— **以后再搬也不必手工同步**。
    验证：公式 `__module__` 由 `chip_formulas.tri_decay` 变为
    `features.chip.formulas.tri_decay`，**这正是修复生效的证据**。
  - **函数体零改动**：12 个搬入文件里 **11 个零差异**；唯一改动是上面那个常量
    （+4 行注释）。`formulas/` 其余 6 个 .py 与 `ARCHITECTURE.md` 原样迁移，
    注册机制（`@chip_formula` / 文件名即 id / LOAD 五步校验）完全未动。
  - **周口径权威定义方仍是 `scr_service`**：`MARKET_CLOSE_HOUR=15` / `_as_moment` /
    `week_start` / `expected_weeks` —— `week_start` 5 个样本、`expected_weeks(3)`/`(5)`、
    函数签名、9 个模块级常量（含 `MIN_MARKET` / `LAUNCH_THRESHOLD`）**全部与基线一致**。
    `rank_service._week_nodes` 仍走 `scr_service.expected_weeks`（源码断言），未改成各算一份。
    *文档小误*：它说 `_as_date` 也是薄封装，实际 `_as_date` 只是 `datetime→date` 转换器
    （`dt.date.fromisoformat`），从不调 `_as_moment` —— 原有实现，未改动。
  - **三方依赖零循环导入**：`rank_service` 的 `:52` 依赖 `strategies.core.data`、
    `:54` **模块级**读 `chip_service.PROCESSED_DIR`（实测与 `scr_service` 同一值）、
    `:140` **函数内**延迟 import `strategies.core.backtest` —— **刻意没提到顶层**
    （`strategies.scr90` 依赖 `chip_formulas`，提到顶层会立刻成环）。
    11 个引用方逐个单独导入全部 OK。
  - **`indicators/data.py:190` 的延迟 import 一个字没动**（踩坑点 5）——
    提到顶层会立刻触发它与 `formulas/core/data.py` 的循环依赖。
  - 验收实测：`/api/chip/rank/status` → **`state=ready`**；
    `/api/chip/rank?weeks=1` → 100 条字段完整（`week=2026-09-28`）；
    `/api/chip/dist?code=600519` → 80 分箱 × **1252 帧**、峰位 `1299.84`（高于收盘 `1235.58`，
    三角形峰形合理）、峰占比 `6.51%`、**锁仓已应用**（`ratio=0.544991`）；
    `strategies.scr90` 导入正常；`/api/chip/*` **12 条路由全挂上**；
    `validation_report()` → `total=1 valid=1 invalid=0`；
    `/health` 无模块错误、11/11 路由、stderr 无告警。
  - **筹码矩阵用加权校验和验证，不是抽样** —— 13 个统计量
    （`pct.shape` / `pct.max` / **`pct.hash` 加权校验和** / `centers.hash` /
    `close.hash` / `turnover.hash` / `float_shares` / `lockup_ratio` /
    `lockup_factor` / `warm` / `dates.len` …）**与基线零差异**，
    避免「形状对、数值错」的漏网。
  - 全仓 **132 个** `.py` 三引号成对 + `ast.parse` 全部通过；52 个单元测试全绿；
    `.env` 未被改动；HTTP 路径零变化（三组 prefix 各自不变）。
  - **转发暂不删**：`indicators/data.py` / `indicators/registry.py` / `strategies/scr90.py`
    三个**尚未搬**的模块仍 import `chip_formulas`，转发要继续服役；
    等它们各自搬家时改指 `features.chip.formulas` 后再删。
  - 执行方式：按原建议 4 步走（formulas 整包 → scr_service → rank_service → 三个 routes），
    **每步搬完立刻验证再继续**，每步的验证结果都记进了待办文档。

- **大佬策略实验室归位 `features/mentor/`**（待办 33，后端按功能搬家倒数第 2 项）：
  ```
  features/mentor/
  ├─ __init__.py
  ├─ routes.py       <- 原 mentor_routes.py  15 条路由，前缀 /api/mentor
  ├─ store.py        <- 原 mentor_store.py   人物/素材/技能/评估（321 行）
  ├─ collectors.py   <- 原 collectors.py     东财摘要等采集
  └─ llm_client.py   <- 原 llm_client.py     LLM 调用封装
  ```
  - ⭐ **原文档漏列了 `store.py`**（`mentor_store`，321 行）。它显然属于本功能 ——
    `main.py:30` 模块级 import、`:175` 调 `init_db()`（失败以「大佬策略实验室（本地库初始化）」
    为 label 记入 `_module_errors`），`mentor_routes.py:14` 与 `test_mentor_store.py:8` 也 import 它。
    按 4 模块搬，否则顶层会留一个孤立文件。
  - **函数体零改动** —— difflib 逐行对比 HEAD：7 个文件 20 行差异**全是 import**。
  - ⭐ **`collectors` 与 `llm_client` 是跨功能共享的**（个股页 / 股票池历史 / 盘面页都在用），
    **3 个已搬文件**（`features/history/service.py`、`features/market/routes.py`、
    `features/stock/routes.py`）共 4 处引用**一并改到规范位置**。理由：这些文件已经在
    `features/` 里，再指向顶层转发等于**在新架构内部留反向依赖**；趁本项清掉只有 3 行。
  - ⭐ **原文档踩坑点 1 写错了位置**：`_ai_cache` / `_ai_cache_lock` / `AI_CACHE_TTL = 600`
    在 **`features/market/routes.py:37-39`**（**盘面页**），**不在** `mentor_routes`；
    全仓在 `mentor_routes` 里搜不到这两个名字。实验室页**根本没有 AI 缓存**。
    两套 TTL 实现（market 的进程内 600s vs `board_service._TTL_HOURS` 的 DB 持久化）
    在**第 28 项**就已按「不统一」原则原样保留，本项无需处理。
    故原验收「`force` 绕过 AI 缓存」改为**对第 28 项做回归**。
  - 踩坑点 2（超时语义）：`call_llm(prompt, timeout=180)` **显式传 timeout** 给
    `core.httpclient.post`（后者 timeout 是必填参数，正是为了防止误用行情口径）。
    本项只改 `import config` 的指向，**一个字没动 timeout**。
  - 踩坑点 3（不打印密钥）：`config.LLM_API_KEY` 只进 Authorization 头。
    验证方式：把真实密钥取出来在启动日志里全文检索 —— **0 处命中**。
  - 踩坑点 4：`agent_dsh/` **在本仓库不存在**（原文档提到它含 node_modules 32843 文件），
    本项无对象、未移动任何目录。
  - 验收实测：**真实调了一次 LLM**，`call_llm('只回复两个字：收到')` **2.5 秒**返回 `'收到'`；
    `/api/mentor/list` `ok=true`（1 个人物）、`/api/mentor/state` `ok=true`；
    **第 28 项回归三连**：`force=false` 首调 `cached=false` 52.9s → 再调 `cached=true` **0.0s**
    → `force=true` `cached=false` 54.8s，把缓存语义钉死。
    基线逐项对照**零差异**（15 条路由 / `mentor_store` 44 个公开名 / `collectors` 26 个 /
    `llm_client` 5 个 / `call_llm` 签名 / LLM 配置状态）。
    4 个转发与实现是同一对象；13 个模块全部导入成功；11/11 路由挂载、stderr 无告警；
    全仓 .py 三引号成对 + `ast.parse` 全部通过；52 个单元测试全绿；`.env` 未被改动。

- **系统设置 / 完整性 / 修复归位 `features/system/`**（待办 32，后端按功能搬家第 5 项）：
  ```
  features/system/
  ├─ __init__.py
  ├─ routes.py     <- 原 system_routes.py   数据目录/存储信息/完整性/诊断包/重建
  ├─ integrity.py  <- 原 integrity.py       启动期数据完整性校验
  └─ repair.py     <- 原 repair_digests.py  指纹修复（独立 CLI）
  ```
  - **函数体零改动** —— difflib 逐行对比 HEAD：18 行差异**全是 import**，0 行非 import：
    `integrity.py` 的 `crypto`/`db`/L215 函数内 `price_store` → `core`；
    `routes.py` 的 `config`/`crypto`/`storage`/L138 函数内 `price_service` → `core`，
    `integrity` → 同包；`repair.py` 的 `price_store` → `core`。
    `import dumplog` 不动（`dumplog` 还在顶层）。
  - ⭐ **`repair_digests.py` 是独立 CLI，转发额外带 `__main__` 守卫**：
    全仓无人 import 它，它原本靠 `__main__` 跑，docstring 记载了
    `python backend_fastapi/repair_digests.py [--apply]`。
    **只做别名转发会让这条已写进文档的用法静默失效**，所以比第 27 项 `storage.py`
    多做一步。实测干跑输出「失配 1 处」、`--help` 的 prog 名仍是 `repair_digests.py`、`EXIT=0`。
  - 踩坑点四条：① `main.py:28` 的模块级 `import integrity` 走转发拿到同一模块对象，
    `_integrity_state` 仍是「启动算一次并缓存」，实测 `/health` 的 `integrity.ok=True`；
    ② `APIRouter(prefix="/api/system")` 一字未改，启动器
    `StockPoolLauncher.cs:1535/1576/1589` 的 `Probe`/`PostJson` 照旧可用；
    ③ 启动器 `storage.py --set-data-dir-base64` 那条离线链路（第 27 项的 core）
    实测 `EXIT=0`；④ `POST /api/system/storage` 幂等实测 `ok=true`，`.env` 值未变。
  - 基线逐项对照全部一致：`STATE_DIR` / `ENV_PATH` / `DB_PATH` / `storage.STATE_DIR` /
    `integrity.ensure_signed()` / `router prefix` / **11 条路由清单** /
    **`integrity` 全部公开名（含 `_all_targets` 等 3 个私有名）**。
  - ⚠️ **排查出一处既有误报并证明与本项无关**：核对基线时 `integrity.summary()` 报
    「`bars_2021` 文件修改时间明显晚于程序记录」。用 `git stash` 回到搬家前
    （HEAD = 第 31 项、本项零改动）跑同样调用，**稳定复现同一条**（4/4 次）；
    真实服务端流程下 `/health` 的 `integrity.ok=True`。
    成因是 `integrity.py` **自己 docstring 就写明**的陷阱 —— 打开 WAL 库会创建 `-wal`，
    其 mtime 是「此刻」，若在连接之后取值会把「打开库」误判成「被外部写入」。
    属既有时效性敏感点，**不在纯搬家项里修**，需单独立项。
  - 流程教训两条：① 转发生成器首版把「转发名」当「实现文件名」（`repair_digests` 的实现是
    `repair.py`），**`ast.parse` 查不出**（运行期 `ImportError`），靠导入自检才暴露
    —— **语法校验必须再配一个导入自检**；
    ② import 扫描器的判据不该是我记得的那几个模块名，应对照「所有项目内模块」的完整清单
    （先前漏掉了 `routes.py` 的 `crypto` 与 L138 的 `price_service`）。
  - 验证：10 个模块全部导入成功；3 个转发与实现是同一对象；11/11 路由挂载、
    stderr 无告警；全仓 .py 三引号成对 + `ast.parse` 全部通过；52 个单元测试全绿。

- **下载与调度归位 `features/download/`**（待办 31，后端按功能搬家第 4 项）：
  ```
  features/download/
  ├─ __init__.py
  ├─ service.py   <- 原 download_service.py  任务引擎 + 调度循环（975 行）
  ├─ store.py     <- 原 download_store.py    任务落库、断点续跑、暂停/继续/取消
  └─ routes.py    <- 原 download_routes.py
  ```
  - **函数体零改动** —— `git diff` 只有 **9 行 import**：
    `service.py` 的 `db`/`price_service`/`price_store`/`tdx_reader` 改指 `core`、
    `download_store` 改指同包、`history_store` 改指 `features.history.store`、
    `from market_service import _ak` 改指 `features.market.service`；
    `routes.py` 的 `download_service` 改指同包；`store.py` 的 `db` 改指 `core.db`。
    L682 函数内 `import cleanup` 不动（`cleanup.py` 仍在顶层）。
  - ⭐ **踩坑点 1「模块级 `_start_background()`」是本项最大风险**，用**两条独立证据**验住：
    ① 沿转发链导入后进程内出现 **`Thread-1 (_scheduler_loop)`** 线程；
    ② 把 `download_store` 的 `last_cleanup_date` **清空**再起后端，
    **85 秒内被调度线程写回 `2026-10-06`**（`SCHEDULER_INTERVAL = 60`）——
    不只是"线程在"，而是真的跑完一轮并触发了 `cleanup.schedule_tick()`。
    **这同时也证通了第 24 项的定时清理链路**（`cleanup.py` 本身未搬动）。
    触发链一环未改：`main.py` 的 `ROUTE_MODULES` 字符串 `"download_routes"` → 转发 →
    `features.download.routes` → import service → 模块级 `_start_background()`。
  - ⭐ **先查后搬**：三个文件里 `__file__` / `Path()` / `os.path` 检索为**空**。
    这是第 27 项 `config.PROG_DIR` 踩过的坑（多退一级否则静默指错目录），本项提前排除。
    DB 路径来自 `core/db.py` 的 `config.STATE_DIR`，落在数据盘
    `E:\stockanaly-data\state\stock_history.db`，搬家前后一致 ——
    **运行中的任务不会因搬家"丢失"**（`download_tasks` 25 行 / `download_settings` 8 行健在）。
  - **下载页任务引擎完整跑通一遍生命周期**：
    `新建 → queued(total=100)` → `进度 → running` → `暂停 → paused` → `进度 → paused` →
    `继续 → running` → `取消 → cancelled`，每步 `ok=true`
    （用 `scope=pool` + `mode=incremental` + `source=tdx` 本机数据，不打外网）。
  - **`recover_stale()` 单独构造实验**：把任务在库里写回 `running`（模拟进程被强杀、
    内存 worker 已消失），调 `recover_stale()` 返回 **1**、状态变回 **`paused`**。
  - 启动器退出码文案（`requirements.txt` / `.venv` 硬编码路径）属第 35 项，本次**不含任何 .cs**。
  - 按数据源并发（`ThreadPoolExecutor(max_workers=len(jobs))`，5 个 block 各一线程）
    与第 07 项的按标的批量并发语义不同，本项只改 import、未触碰该段。
  - 验证：10 个模块全部导入成功（含 `cleanup`、`core.db`、`test_settings_download`、`main`）；
    3 个转发与实现是同一对象；`settings_map` 8 个键**零差异**；
    全仓 .py 三引号成对 + `ast.parse` 全部通过；4 个只读端点全 `ok=true`；
    `/health` 无模块错误、11/11 路由挂载、stderr 无告警；52 个单元测试全绿。

- **股票池历史归位 `features/history/`**（待办 30，后端按功能搬家第 3 项）：
  ```
  features/history/
  ├─ __init__.py
  ├─ service.py   <- 原 history_service.py   （K线 / 入池出池轨迹 / 消息面）
  ├─ store.py     <- 原 history_store.py     （pool_snapshots 快照）
  └─ routes.py    <- 原 history_routes.py
  ```
  - **只改了因搬家失效的 import，函数体一行未动**：`service.py` 的 `history_store`
    改指包内、`kline_service`/`price_service`/`price_store` 改指 `core`；
    `routes.py` 的 `periods` 改指 `core.periods`；`store.py` 的 `db` 改指 `core.db`。
    `from collectors import ...` 未动（`collectors` 还在顶层）。
  - 顶层保留 3 个**别名转发**（`sys.modules[__name__] = _impl`），其中 `history_routes`
    尤其关键 —— `main.py` 的 `ROUTE_MODULES` 与 `/health` 模块清单都按**字符串**
    `"history_routes"` 引用它。
  - ⭐ **踩坑点 1 用源码断言验住**（而不只是"没改"）：
    `load_trusted_bars` 里 `price_store.load_bars` 仍在（True）、
    `UntrustedDataError` 捕获仍在（True）、**`kline_service.get_bars` 不在**（True）
    —— 「脏数据自动重抓」能力完整保留，没有被"顺手优化"掉。
    周/月合样仍走 `kline_service.resample`（已断言）。
  - **HTTP 路径零变化**：prefix 仍是 `/api/history`。
  - ⚠️ **发现一处层级倒置（未修）**：`core/db.py` 的 `init_db()` 里有函数内
    `import history_store`（原注释「避免与 db 形成模块级循环依赖」），
    本项之后它指向 `features.history.store` —— 变成 **core 依赖 features**。
    这是既有状况（`db.init_db()` 本就要编排上层三家的建表），本项只做位置移动、
    `core/db.py` 一行未改。要治需把「建表编排」挪出 `core/db.py`，
    属职责边界变更，不该塞进纯搬家项。
  - 验证：11 个模块全部导入成功（含 `core.db`、`download_service`、两个测试模块、`main`）；
    3 个转发与实现是同一对象；**日线 1252 根 / 周线 264 根 / 月线 62 根**，
    `events` 73 项（轨迹/消息面）、`pool` 字段齐全；
    `/api/history/download/universe`（`latest_snapshot_codes()` 唯一的消费方）`ok=true`；
    `/health` 无模块错误、11 个路由全挂载、stderr 无告警；52 个单元测试全绿。
  - 流程改进已见效：第 29 项连续踩三次 PowerShell here-string 吞掉 docstring 收尾的坑，
    本项**全程改用 Python 脚本 + 脚本内自带双校验**（`"""` 个数为偶 + `ast.parse` 通过）
    才落盘，**一次事故都没出**；事后另跑全仓 `.py` 扫描确认 0 问题文件。

- **个股 / 估值 / 板块归位 `features/stock/`**（待办 29，后端按功能搬家第 2 项）：
  ```
  features/stock/
  ├─ __init__.py
  ├─ profile.py       <- 原 stock_profile.py
  ├─ routes.py        <- 原 stock_routes.py
  ├─ valuation.py     <- 原 valuation_service.py
  └─ board_index.py   <- 原 board_service.py
  ```
  - **只改了因搬家失效的 import，函数体一行未动**：
    `profile.py` 的 `price_service/price_store/share_service` 改指 `core`（第 27 项已搬）、
    `_ak` 改指 `features.market.service`；`valuation.py` 的 `ms` 同理；
    `board_index.py` 的 `db` 改指 `core`；
    `routes.py` 顶部 `valuation_service` 与**两处函数内延迟导入**
    （`stock_profile` / `board_service`）改为包内引用。
  - **待办 5 个踩坑点逐条遵守**：没有重构 `stock_profile` 任何函数（第 09/10 项的事）；
    没有统一 `_cached`（失败也缓存）与 `lockup_ratio`（失败不缓存）的矛盾语义；
    没有动估值出口清洗（第 09 项已补）；没有统一三份 market 前缀规则（第 16 项）；
    `board_index` 的 TTL 仍是 **DB 持久化**（`_TTL_HOURS=24`），没改成进程内缓存。
  - **转发必须用别名的理由多了一条**：除下划线私有名外，`stock_profile` 还有模块级
    可变状态 `_CACHE`（6 小时 TTL 的股东/行业缓存）。`from x import *` 复制出的名字
    **不含同一份缓存**，`clear_cache()` 就清不到实现里那份、缓存永远清不掉。
    已验证 `stock_profile._CACHE is features.stock.profile._CACHE` 为 `True`。
  - **HTTP 路径零变化**：`router` 的 prefix 仍是 `/api/stock`，
    `main.py` 仍按字符串 `"stock_routes"` 引用（靠转发），启动器与网页端不需要改。
  - 验证：10 个模块全部导入成功（含 `chip_formulas.core.data`、`main`）；
    4 个转发与实现是**同一对象**；私有名 `lockup_ratio` / `_market_prefix` / `_TTL_HOURS` 照常可见；
    个股页 4 条验收全通（基本信息 1.2s / 所属板块 0.0s / 重取名称 0.3s / 筹码分布 1.0s
    且 `lockup_applied=true`）；估值三档情景齐全 0.7s；连带回归 K线 0.4s、盘面板块β 1.0s；
    `/health` 无模块错误、stderr 无告警；52 个单元测试全绿。
  - 踩坑：同一个 PowerShell here-string 陷阱连续踩三次（docstring 收尾 `"""` 缺失），
    其中一次还用 `.Replace()` 把 `valuation.py` 整个改成
    `rrom __ruture__ mmport annotatmons`（`f`/`i`/`c` 被系统性替换），靠 `git diff` 才发现。
    **改进已落实**：批量生成/修改 Python 文件的脚本自带两道检查（`"""` 个数为偶 +
    `ast.parse` 通过）才落盘，事后又跑全仓 .py 扫描确认 0 个问题文件。
    > 结论：在这个环境里改 Python **不要用 PowerShell 的 `.Replace()`** ——
    > 它的编码/转义处理已坏过两次；用 Python 脚本替换并在脚本内自带校验。

- **盘面/板块归位 `features/market/`**（待办 28，后端按功能搬家的第一项）：
  ```
  backend_fastapi/features/
  ├─ __init__.py
  └─ market/
     ├─ __init__.py     说明这一层定位 + 为什么旧路径必须留别名转发
     ├─ service.py   ← 原 market_service.py   （纯 rename，内容零改动）
     └─ routes.py    ← 原 market_routes.py    （只改 1 行 import）
  ```
  - **`service.py` 是纯移动**：`git diff --stat -M` 显示为 `} | 0`。
    它唯一的项目内 import `from core import httpclient` 是绝对导入，搬后仍可用；
    TTL 缓存范式（`_cached` / `_ttl` 盘后延长到 23:59:59）**原样保留，未顺手「统一」**；
    `_MARKET_CLOSE_MIN = 15*60` 与 `chip_service.MARKET_CLOSE_HOUR` 的两份定义
    也按待办要求**留给第 12/16 项**。
  - `routes.py` 只改 1 行：`import market_service` → `from features.market import service as market_service`。
  - **顶层保留别名转发**（`sys.modules[__name__] = _impl`）—— 待办预警的坑成立：
    `_ak` 是下划线私有名，`from x import *` **不会**导出，那 7 处会全部 AttributeError。
    用别名后 **7 个依赖方一行都不用改**（6 处 `from market_service import _ak`
    + 1 处 `valuation_service` 的 `ms._ak(ms.ak...)` 属性访问），已逐个验证。
  - **HTTP 路径没有任何变化**：`router` 的 `prefix` 仍是 `/api/market`；
    `main.py` 的 `ROUTE_MODULES` 仍按**字符串** `"market_routes"` 引用（靠转发），
    启动器与网页端不需要改。
  - `board_service.py`（板块成分股索引）**留在顶层**：它服务个股页的 `/api/stock/boards`，
    只在 `stock_routes` 里被调用，归给第 29 项（features/stock）更合适。
    待办提到的 `bars.py` 不存在 —— `MktBars` / `MktRatioBar` 是启动器里的**前端控件**。
  - 验证：8 个模块（含 `main`）全部导入成功、`ms is features.market.service` 为 `True`；
    盘面 5 个接口全 `ok=true`（外围环境 0.6s / 大盘资金 14.3s / 板块β 0.8s /
    连板 0.6s / 涨跌停炸板 0.6s）；连带回归筹码分布、基本信息、个股板块、K线均正常；
    `/health` 无模块错误、11 个路由全挂载、stderr 无 import 错误；52 个单元测试全绿。
  - 踩坑：PowerShell here-string 吞掉了 `features/market/__init__.py` docstring 的闭合 `"""`，
    导致 `import market_service` 直接 SyntaxError。批量写 Python 文件时
    应加「三引号成对 + `ast.parse`」两道自动检查。

- **系统性排查并硬化「多数据源串行回退」**（`bug-02`，`bug-01` 的同类模式）：
  `bug-01` 修的是 `stock_profile` 两处，但根因是一类模式 —— 「多源 / 多报告期**串行**回退，
  每源各自带超时、整条链没有**总时间预算**」。代码里几乎所有降级注释都写着
  「失败一律回退，不该拖垮主流程」，但那只防住了「**失败**」，没防住「**变慢**」。
  - ⭐ **排查后必修项从 10 处缩到 3 处** —— 立项清单是 AST 扫的**候选**
    （「同一函数里有 ≥2 个 `_ak` 调用」），逐处读控制流后发现**一半不是串行回退**：
    - `market_service._hist_frame` 是 `source` 的**互斥分支**（us/hk/ff 三选一）且**不在循环里**；
    - `core/price_service.fetch_daily` 的北交所 / 其余分支也是**互斥**，
      每次只跑 2 源 → 最坏 **65s** 而非估算的 130s；
    - `strategies/core/data.list_universe_codes` **读本地库**，本就不是数据源回退。
    > AST 只能回答「函数里有几个 `_ak` 调用」，**回答不了「它们会不会串行执行」**。
    > 我自己立的规矩「待查项必须实测确认，不许照抄估算」，这次先遵守了才算数。
  - 单次上限用 `_uses_v8()` **实测**判定：依赖 py_mini_racer 的接口走 60s、其余 15s；
    **19 个接口里 6 个走 V8**，且 `stock_fund_flow_*` 全是 V8 —— 意味着「并行发起」也会因
    `_V8_LOCK` 全局锁而退化成排队串行。
  - **新增公共件 `core/fallback.py`**：`first_ok(sources, budget, per_call, is_ok)`
    给出**与源数量无关的总时间预算**，每次实际传 `min(per_call, 剩余预算)`；
    预算用尽立即停并由调用方按**既有约定**降级（本次未改变任何降级语义）。
  - 接入 3 处：`download_service._universe_codes`（**180s → 20s**）、
    `core/price_service.fetch_daily`（**65s → 45s**）、
    `stock_profile` 两个报告期遍历（bug-01 已加预算，本次**并入公共件**、删掉手写那份）。
  - **低于阈值的一律不改并写明理由**：`company_info` 40s、盘面各接口 30~40s
    （调用方 `VRequest` 默认 90s）、`sector_beta` **早已有 35s deadline**（别人修过）、
    `board_service._sync_worker` 在**后台线程**不阻塞 HTTP。
  - 另发现未修（需独立决策）：后台板块重建可能跑数十分钟且期间索引一直是旧的；
    V8 全局锁让并行退化、两处各写一遍 `join` 土办法没有公共件；
    `first_ok` 只解决串行，不覆盖「多线程 + 逐个 join」形态。
  - 验证：公共件 6 项单元测试（含**断言每次收到的 timeout 递减** ——
    这是预算能传导到底层 `_ak` 的唯一途径，第一版测试漏了这项）；
    真实上游 6 个函数全部正常（0.36~10.7s，取数与改动前一致）；
    接口冒烟 8 条全 `ok=true`；`/health` 无模块错误、stderr 无告警；52 个单元测试全绿。

- **`_json_safe` 提公共层 + 给缺清洗的出口补调用**（待办 09）：
  NaN / ±Inf / numpy 标量一旦到达响应层，FastAPI 的 `JSONResponse` 是严格模式
  （`allow_nan=False`），会抛 `ValueError: Out of range float values are not JSON compliant`，
  Starlette 兜底返回**纯文本 500** —— 前端只看到「无效的 JSON 基元: Internal」，
  完全指不到真正出问题的字段。
  - ⭐ **关键发现：待办的建议做法照原样做是空操作。**
    `_json_safe` 遇到 `dict` / `list` 是**原样返回**（`float(dict)` 抛 TypeError，
    走 `return v if isinstance(v, (list, dict)) else None`）——
    实测 `out is body` 为 `True`、嵌套 NaN 全部保留。
    现有代码一直是**逐字段**调用（`stock_profile` 12 处，全在列表推导里），所以从未暴露；
    一旦按建议改成「整块清洗」，会得到一个**看起来加了、实际没加**的假修复。
    故新增**递归版** `json_safe_deep` 供 HTTP 出口用，并在注释里写明两者分工
    （`json_safe` 单值 / 对 dict 恒等映射；`json_safe_deep` 整块 / 递归）。
  - 新增 `core/jsonutil.py` + 旧路径 `jsonutil.py` 别名转发；
    `stock_profile._json_safe` 改为薄封装，12 处既有调用一行未改。
  - **7 个 HTTP 出口**接入递归清洗：`/api/stock/valuation`、`/api/stock/quote`、
    盘面 5 个 GET（global / capital / sectors / limit-up / big-loss）。
  - 另统一 2 处**语义等价**的半成品：`core/kline_service.to_records`（内联 `pd.isna`）
    与 `indicators/base.py` 指标序列。
  - **两处刻意不统一**（待办归类有误，已在文件里说明）：
    `strategies/core/stats.py:_num` 带 `round(f, 3)`，替换会悄悄丢掉四舍五入；
    `chip_service.py` 那 4 处内联是**输入侧解析守卫**（`clean_code` 命中 NaN 返回 `""`
    而不是 `None`），不是出口清洗，替换会改变其返回值。
  - 踩坑点落实：`int` 分支仍在 `float` 之前（就地注释说明 numpy `bool_` 的原因）；
    只在出口洗一次、service 层不动；对已 JSON 安全的值是恒等映射。
  - 验证：6 类异常值（嵌套 NaN/Inf、numpy 标量、顶层 NaN、list 内 NaN、tuple、正常响应）
    用 `json.dumps(allow_nan=False)` 验证**全部由非法变合法**；
    9 个接口改前/改后逐字段对比，**确定性接口 `kline`/`profile`/`quote` 完全一致**。
    > 取证方法记档：盘面是实时数据，改前/改后快照的 24 处差异**不能**直接当成回归 ——
    > 另抓一份**噪声基线**（同代码、间隔 20s、自比）得到 17 处差异，位置与之完全重合，
    > 证明那些是数据源波动。验证「输出没变」必须先建立噪声基线。
    > 另发现**第二个失败模式**：numpy 标量在严格序列化下是 `TypeError` 而非 `ValueError`。

- **修复：筹码分布偶发「筹码接口无响应」**（`bug-01`）：
  个股页 K 线正常但筹码区域报红字「筹码接口无响应」，**重开即不复现**，后端日志无任何错误。
  - **根因**：`/api/chip/dist` 的冷路径上，锁仓修正要取「十大流通股东」，
    而 `stock_profile.free_top_holders` 是**无上限的串行回退** ——
    `_report_periods()` 给 **8 个**报告期，每期 `_ak(..., timeout=20)` 由看门狗硬切断，
    **串行累加最坏 8×20 = 160s**，而启动器只等 **30s**（`StockPage.cs:1147`）。
    平时第一个报告期就命中（实测 0.4~0.9s）所以看不出；只有东财股东接口**变慢或返空**时
    才逐期往后试，耗时堆过 30s 被判「无响应」。后端仍会跑完并写入
    `stock_profile._CACHE`（TTL 6 小时）——**这就是重开即好的原因**。
  - 值得注意的是 `lockup_ratio` 的注释本就声明「失败一律回退 (0.0, 1.0)，
    任何异常都不该拖垮筹码主流程」，但它只防住了「失败」、没防住「变慢」：
    上游卡住时不抛异常也不返回 None，只是每次都慢 20 秒，保护形同虚设。
  - **修法**：新增 `_walk_report_periods(fetch, budget)`，为「往回试报告期」加
    **总时间预算** `_HOLDERS_BUDGET = 6.0s`，每期实际超时取 `min(20, 剩余预算)`，
    预算用尽立即停并按既有约定降级（筹码页不做锁仓修正、系数 1.0）。
  - **同形态的第二个受害者** `top_holders`（十大股东，供 `/api/stock/profile`）
    是一模一样的 8 期 × 20s 结构，一并修掉。
  - 验证：模拟上游每期都卡满 20s 时，`free_top_holders` / `top_holders` / `lockup_ratio`
    均收敛到 **6.0s**（改前最坏 160s）并正确回退 `ratio=0 factor=1`；
    真实上游 3 只票仍 **0.39s** 命中首期、锁仓系数照常算出；
    冷启动后首次 `/api/chip/dist` **0.8s**、`lockup_applied=true`；
    52 个单元测试全绿；`/health` 无模块错误。
  - **验证时踩的坑（已记档）**：第一次 mock「数据源卡死」时写成「无视 timeout 睡死 20s」，
    预算没生效 —— 真实 `_ak` 是按传入 timeout 硬切断的，mock 没对齐这个语义，
    等于测了个不存在的行为。改成「按传入 timeout 睡满」后才忠实还原。

- **raw HTTP 收敛为 `core/httpclient.py`**（待办 15）：改之前 raw HTTP 有 **6 份**独立实现
  （待办只记了 4 份），UA **4 个变体**混用（Chrome `120.0`、`120.0.0.0`、`124.0.0` ×2），
  只有盘面那一份有重试 + 退避，也只有它复用连接。
  - 新增 `core/httpclient.py`（98 行）：UA / referer 常量唯一、进程内共用 `requests.Session`、
    `get()` 带重试与线性退避、`post()` 的超时必填。旧路径留 `httpclient.py` 别名转发。
  - 6 处接入：`market_service._http_get`（→ `retries=3`，全仓唯一允许重试的调用点）、
    `valuation_service._get`（→ 默认 `retries=1`）、`core/share_service` 内联、
    `collectors` 内联（东财公告）、`stock_routes` 的通达信扫雷宝 GET + POST、`llm_client`。
    原先各自携带的 UA 副本与常量的重复定义全部删除。
  - **akshare 侧按要求未动** —— `market_service._ak` 已是唯一封装、7 个模块在用。
  - ⭐ **`retries` 默认定 1 而不是 3**：待办点出的风险（估值页最坏耗时 12s → 38.5s）已规避。
    反过来让调用方**显式**传 3，这样「默认重试」这个坑不会再被无意踩到。
  - **`post` 的 `timeout` 必填、无默认值** —— LLM 是分钟级语义，与行情十秒级完全不同，
    强制显式传参是唯一可靠的防呆手段。
  - **顺带修掉改的过程中自己引入的一处行为回退**：`httpclient.post` 最初没加 `referer`，
    接过去后通达信扫雷宝的 `Referer` 头丢了（该接口要求 referer 带 code），已补上并复验。
  - 验证：改前/改后**逐个数据源实况对照**，7 条链路取值完全一致
    （新浪 200/570 字符、估值 price 1258.62 & shares 12.5008、
    流通股本 1,250,081,836 & turnover 0.31、东财公告 6 条、东财新闻 9 条、
    通达信 JSON 4 键 + POST 正常）；`/health` 无模块错误、stderr 无告警；
    接口冒烟 8 条全 `ok=true`；**单元测试 52 个全绿**；真实 `.env` 未被改动。

- **后端新增 `core/` 子包，基础设施层归位**（待办 27，后端搬家的第一步）：
  `backend_fastapi/` 原为扁平结构（约 87 个 `.py` 同层），现把**依赖图底部**的 14 个模块
  `git mv` 进 `core/`（保住历史）：`config` `crypto` `dpapi` `envfile` `storage` `db`
  `instance_lock` `logutil` `periods` `tdx_reader` `share_service` `price_store`
  `price_service` `kline_service`。
  - **比原清单多收 `crypto`**：`db` 与 `price_store` 都依赖它、它只依赖 `config`/`dpapi`/`envfile`，
    留在外层会让 core 反过来依赖顶层。
  - core 内部 17 处 import 改**相对导入**（`from . import config`）。
  - **踩坑点 1（最大的雷）**：`config.py` 移进子包后 `Path(__file__).parent` 变成了 `core/`。
    真正会静默出错的不只 `BASE_DIR` —— `ENV_PATH` 在文件顶部**直接**用
    `Path(__file__).resolve().parent` 取值、并不经过 `BASE_DIR`；漏改它会导致
    **`.env` 找不到、LLM 配置全丢且毫无报错**。修法是新增
    `PROG_DIR = Path(__file__).resolve().parent.parent`，让 `ENV_PATH` 与 `BASE_DIR`
    都由它派生，「多退一级」全文件只出现一次。
  - ⭐ **兼容转发用 `sys.modules` 别名，而不是 `from core.x import *`**（与原建议不同）：
    `import *` 只是**复制**一份名字，而测试里大量 `patch.object(config, "DATA_DIR", ...)`、
    `patch.object(storage, "ENV_PATH", ...)` —— 补丁打在**副本**上、实现仍读真实值，
    **补丁静默失效**。实测后果是 52 个测试里 26 个失败，且 `update_data_dir` 去改**真实 `.env`**，
    把临时目录路径写了进去（`.env` 与 `.env.bak` 双双被污染、`DATA_DIR` 一度指向已删除的 Temp 目录）。
    改成别名（`sys.modules[__name__] = _impl`）后 52 个全过，且下划线私有名自动可见
    （`price_store._shard_years`、`crypto._key` 这 4 处跨模块引用不必再手工补清单）。
  - **踩坑点 3**：启动器把 `backend_fastapi/storage.py` 当脚本跑
    （`--set-data-dir-base64`，`StockPoolLauncher.cs:1579` 路径写死），故该文件保留同名入口，
    `__main__` 分支显式调 `_impl.main()`；实测合法值返回 `ok:true`/退出 0、非法路径 `ok:false`/退出 1。
  - **踩坑点 2**：`main.py` 的 `ROUTE_MODULES` 里 11 项全是 `*_routes`，不含任何 core 模块。
  - 已知**反向依赖 1 条**（不影响运行，因 `market_service` 是叶子模块、不成环）：
    `core/price_service.py` 的 `from market_service import _ak`。
    顺带发现 `_ak` 其实是通用的 akshare 调用包装（带超时与降级），被从盘面页借走属于放错位置 ——
    **第 28 项搬 `market_service` 时必须一并处理**。
  - 验证：搬家前落 57 项基线（全部路径常量 / 分区目录 / 库常量 / `.env` 读取结果 /
    跨模块私有名 / 各模块公开名清单），搬家后**零差异**；`/health` 无模块错误、11 个路由全挂载；
    个股 / 盘面 / 下载三页冒烟全 `ok=true`；**单元测试 52 个全绿**；
    测试前后真实 `.env` 哈希一致。

- **日志保留期文档纠错**：第 22 / 24 项文档里写的「保留 14 天」与代码不符 ——
  `logutil.KEEP_DAYS` 与 `cleanup.DEFAULT_RETAIN_DAYS` **都是 7**（注释明确要求两者同口径）。
  已把 4 处「14 天」改为「7 天」。注意别与 `price_service.OVERLAP_DAYS = 14`
  （增量同步的重叠窗口）混淆，那是另一个常量。

- **后端日志落盘到 `<data>/logs/`**（待办 22）：原先全仓没有用 `logging`，后端靠
  `print(..., file=sys.stderr)` 输出，启动器一关窗口日志就没了、事后无法回溯。
  - 新增 `logutil.py`：具名 logger `stockpool` **双写** —— stderr 保持原样
    （启动器按输出流打 `[err]` 标签的行为不变）+ `<data>/logs/backend.log`（INFO+）
    与 `<data>/logs/uvicorn-error.log`（WARNING+），按天轮转、保留 7 天、UTF-8。
    日志文件不可写时**降级为只写 stderr**，不让日志拖垮启动。
  - `main.py` 11 处 `print` 换成 `logger.info/warning/critical`；`_record_error` 改带
    `exc_info=True`，**异常堆栈随之进文件**（原先只 `traceback.print_exc()` 打到 stderr）。
  - 用具名 logger + `propagate=False` 而非直接配 root：uvicorn 的 `--log-config` 会调
    `dictConfig`，其配置不动 root 且带 `disable_existing_loggers: false`，因此不会被冲掉。
  - 启动器**零改动**：`backend_fastapi/uvicorn-error.log` 旧路径保持不变（兼容方案 A）。
  - 约定文档：`backend_fastapi/README.md` 新增「十一、数据盘与日志约定」，一次写全
    第 21~24 项共用的那份契约 —— 数据根目录解析优先级、分区表（各放什么 / 谁在写 / 能否重建）、
    迁移规则（非破坏、幂等、执行顺序）、日志规则（位置 / 双写 / 轮转 7 天 / 级别 / 禁止写入内容）、
    新模块打日志的写法。该节**只做导航**，权威细节仍以 `config.py` / `storage.py` / `logutil.py`
    的注释为准，避免变成第二份需要同步的真相。

- **二级页机制收成 `SubTabStrip` 控件**（待办 05）：个股页 / 盘面页 / 策略页原先各写一套
  「顶部一排小按钮 + 一个内容容器」，`Add` / `Select` / 换肤循环三份近乎逐字重复，
  外加三份几乎一样的字段组与标签栏搭建代码。现收成 `launcher/SubTabStrip.cs` 一个控件
  （持有 bar / body / btns / pages / index），三页各持一个实例。
  - **`Add` 两个重载都保留**：`Add(title, Panel)` 供个股页（7 个子页外部先建好再传入，
    不改成委托式，否则要动 7 个调用点）；`Add(title, Action<TLP>)` 供盘面 / 策略（内部套 Stack）。
  - **两处懒加载特例靠 `OnSelected` 钩子承载**，不写死进 `Select`：
    盘面页「AI 分析」子页首次切到才生成（避免应用启动瞬间抢跑请求后端）、
    个股页切到 K 线子页把键盘焦点交给图表。钩子设计不到位 AI 页就会在启动时开始生成。
  - ⭐ **顺手修 bug**：`ApplyTheme()` 原先只调 `SkinMktSubTabs()` + `SkinStockSubTabs()`，
    **漏了策略页** —— 策略页二级页按钮在浅色↔深色切换后不会重新上色。现三条都挂上。
  - 策略页按钮原先误用 `"mkt-subtab"` 作 `Tag`（复制粘贴错标），改为 `"st-subtab"`；
    已确认全仓对这两个 `Tag` **只赋值、从不读取**（`Skin()` 只按 `"muted"` 分支），零影响。
  - **两处刻意保留的差异**（文档未记，实际存在）：策略页按钮没设 `AutoSize/Height/Width`，
    走默认紧凑形态；其内容容器另有 `AutoScroll = true`。统一前者会让策略页按钮整体变大，
    属视觉变化，故各留一个开关（`FixedButtonSize` / `BodyAutoScroll`）并注释「不要顺手统一」。
  - `build_exe.bat` 源文件清单加入 `SubTabStrip.cs`。

- **股票名称获取合一**（待办 04）：新增 `Http.FetchStockName(code, onOk, onFail)` 与
  `PriceSuffix(price)`，个股页 `StockLoadName` 与估值页 `ValuationLookupName`
  由各 32 行降到各 15 行，`api/stock/quote?code=` 全仓只剩一处。
  - **三处差异按踩坑点全部留在调用方**：竞态守卫（各自比自己的输入框 `_stockCode` / `_vCode`）、
    估值页独有的失败兜底「未找到该代码对应的股票」、个股页的副作用 `StockAddHistory(code, name)`。
  - **一处有意偏离建议做法**：回调签名由 `(name, price)` 改为 `(name, display)` ——
    「名称 + `  ` + 现价 + ` 元`」这段拼接格式两处也逐字相同，收进共享方法才真正消除最后一点重复；
    现价已格式化进 `display`，两个调用方都没单独用到裸 `price`，不损失信息。
  - 归位到 `Http.cs`：那里已有 `Probe`/`GetText`/`PostJson`，加业务级请求属同一层；
    `VRequest` 本身也是 HTTP 封装（只是"抛异常"那一 flavor），不算跨层。

- **小样板合集（3/6 类）**（待办 08）：`J.IsOk`(12 处) + `J.IsFailed`(3 处)、
  `UiKit.SetErr`(20 处)、`UiKit.Debounce`(2 处)。
  - ⚠️ **策略页那 3 处刻意没有合并**：它们是**取反且不容错**的写法
    （`TryGetValue` + `!(bool)okv`），与 `!IsOk(j)` 不等价 —— `ok` 字段缺失时
    `IsOk` 返回 false、取反就变成「失败」，而原写法不成立。故另给 `J.IsFailed(j)`。
  - `SetErr` 只替换**严格同构**的 20 处（`.Text = X;` 紧邻 `.ForeColor = C.UpErr;`）；
    剩下 8 处中间夹 `Tag = "bad"` 或是表格单元格（还要改字体），逐处判断风险大于收益，保留。
  - **另 3 类经核查判定不该做**：`ErrOf`（前提已被第 02 项改变 —— 那些地方现在已是单键读取，
    再抽会**给没有兜底的路径加上兜底 = 改变行为**）、`ToggleVisible`（收益低、控件类型不同）、
    `FlatBtn`（3 个 subtab 按钮形态确实不同，策略页走默认 `AutoSize`，强行统一会改变外观；
    且第 05 项 SubTab 抽取会一并吃掉）。

- **自绘表面抽公共基类 `ChartControl`**（待办 07）：新增 `launcher/ChartKit.cs`，
  `MktBars` / `MktRatioBar` / `ChipPanel` / `KLineChart` 改为继承它。
  - `SetStyle(...)` 从 4 份逐字相同 → 1 处（基类）；`GetPreferredSize` 3 份 → 1 处。
  - **抗锯齿现在 5 个表面全开** —— 原先只有 K 线与筹码面板开，条形图 / 分段条 / 净值曲线没开，
    观感不一致。这是本项**唯一有意的视觉变化**。
  - `StringFormat` 全部改用基类的 3 个静态缓存（`StockPage` 原先每帧 new 5 处 → 0）。
    踩坑点提醒过：静态缓存是**正确**写法（`StringFormat` 不可变但非线程安全），别改回每帧 new。
  - `StEquityPaint` 是 `PictureBox` 的事件处理器、不是 `Control` 子类，进不了基类，
    按文档例外处理：手工设一次 `SmoothingMode`。
  - **价格轴协作未被打断**：`ChipPanel` 依赖 `KLineChart` 的 `TryGetPriceAxis` /
    `PriceAxisChanged` 来对齐，两处一行未改。
  - 网格色 / 值域映射 / min-max 扫描**按建议不收**（网格色两套深色值差 4，留给调色板统一）。
  - 附注：`sealed` 不影响「继承基类」（它禁止的是被继承），4 个控件的 `sealed` 全部保留。

- **HTTP 封装补齐三行配置 + 归位到 `Http.cs`**（待办 03）：新增 `launcher/Http.cs`，
  `Probe`(×3) / `GetText` / `PostJson` 从骨架文件原样搬入，并补齐缺失的配置。
  - **修一个真 bug**：`GetText` 以前只设了 `Timeout`，而 `Timeout` **只保护到「收到响应头」**，
    之后读取响应体完全不受保护 —— 请求 `/api/chip/rank?limit=100` 这类大响应时，
    服务端中途卡住会一直挂着。补上 `ReadWriteTimeout` 后才与 `VRequest` 对齐。
  - 另补 `Proxy = null`（本机直连，绕开系统代理 / PAC 自动发现，否则同一台机器上
    一半请求走代理一半不走）与 `Expect100Continue = false`（少一个往返）。
    `GetText` 补 3 处，`PostJson` 补 2 处。
  - **刻意不做**：统一「抛异常 vs 返回 bool」的错误契约 —— 要动 20 余处错误处理分支，
    改动面最大，须单列评估。`VRequest` 把 4xx/5xx 响应体当正常返回的语义完整保留。
    超时值仍由调用方显式传入（15s/30s/120s/300s 各有业务原因，不设默认值）。
  - 留了一处不一致待定：`Probe` 仍缺 `Proxy = null`（本项收窄范围），它打的是本机健康检查。

- **KPI 卡片公共件归位**（待办 06）：新增 `launcher/Cards.cs`，
  `VKpiRow` / `AddKpi` 从 `ValuationPage.cs` **原样搬入**（`ValuationPage.cs` 722 → 593 行）。
  - **零行为改动**：两个函数自包含，且是 `partial class MainForm` 成员，
    **调用点一行未改**。调用数量与搬家前完全一致：`AddKpi` 24 处、`VKpiRow` 7 处。
  - 保留了 `card.Tag = "kpi"` —— `Skin()` 靠它给卡片上底色，丢了卡片就没底色。
  - **刻意不并入**：`StKpiCard` / `StCardRow`（150×66、**有边框**、值在上标签在下，
    并进来会让原本无边框的 24 张卡长出边框，42 处调用点视觉回归）、`MktRow`（单行形态不同）。
    两个行容器的 `WrapContents` / `MaximumSize` 差异是有意的，未统一。

- **启动器 JSON 工具收敛**（待办 02）：新增 `launcher/J.cs`（20 个方法），
  **删除 27 个重复定义、替换 829 处调用点**。
  - 4 份逐字等价的取键（`VSafe`/`StGet`/`DictVal`/`DlVal`）→ `J.Get` / `J.GetFrom`；
    6 份转 double → `J.NumOrNull` / `J.NumOr` / `J.Num` / `J.NumAt`；
    3 份转 int → `J.Int`；4 份转 string → `J.Str` / `J.StrOr` / `J.StrAt`；
    4 份百分比 → 3 个口径函数；金额/亿 → `J.Money` / `J.Yi` / `J.YiSigned`。
  - `VArr` 也一并搬进 `J.Arr`：它当时住在 `ValuationPage.cs` 却被 4 个文件使用，
    属于第 26 项说的「公共件住在页面文件里」，纯搬家、逻辑未变。
  - **三处故意不合并**（合并就是改口径）：`NumOrNull` 的 `null` 语义（估值页靠它显示「—」）、
    `PctRaw` **不乘 100**（盘面涨跌幅本身已是百分数，乘 100 会静默放大 100 倍）、
    `VNum` 对 `decimal` 的显式分支（JavaScriptSerializer 会把 JSON 小数解成 decimal）。
  - 一处有意的口径统一：转 double 统一用 `InvariantCulture`（原先只有下载页这么做；
    JSON 数字与数字字符串一律以 `.` 作小数点，zh-CN 下行为完全相同）。
  - 逐文件替换：StockPage 320、MarketPage 206、ValuationPage 132、StrategyPage 88、
    DownloadPage 45、ChipRankPage 32、StockPoolLauncher 6。`csc` 一次编译通过。

- **启动器色彩常量集中**（待办 01）：新增 `launcher/Palette.cs`（`internal static class C`），
  **31 个语义色常量**，硬编码 `Color.FromArgb(r,g,b)` 从 **171 处降到 37 处**（减少 134）。
  - 覆盖涨/跌、平/灰、警告、主题蓝、副图指标线（RPS / DIF / DEA）、筹码峰深浅七组。
  - **视觉零变化且可证明**：替换前先校验「常量取值 == 原字面量」，再按字面量精确匹配替换；
    事后逐文件核对「替换处数 == `C.xxx` 引用数」，全部对应。
  - **刻意没做**：① 没有把同语义的多套值合并（`Up` 与 `UpErr` 都是红的）——
    统一成一个值就是**改设计**，需要另外决策；好处是从此改一处即可全局生效。
    ② 没有纳入 `BuildPalette()` 换肤（语义色与主题底色仍是两套，互不影响）。
    ③ `Skin()` 里 `tag == "mkt-dir"` 那个跳过换肤的分支没动 —— 它保涨跌色，动不得。
  - 剩余 37 处都是应该留的：`BuildPalette()` 换肤底色 31、图表网格线 4、`Color.FromArgb(0)` 透明占位 1、
    以及 1 处既有的 `C.ToString` 链式调用。

- **启动器公共件归位 · 新增 `launcher/UiKit.cs`**（待办 26）：把散在骨架文件里的
  页面骨架与控件工厂搬到独立文件，`StockPoolLauncher.cs` 由 2778 → 2606 行。
  - 搬出 11 个方法：`NewPage` `Stack` `AddRow` `Row` `Group` `Dot` `Mute` `Lbl`
    `MiniBtn`(×2) `Check` —— 即「页面骨架 + 控件工厂」。
  - **零风险**：这些都是 `partial class MainForm` 的成员，**调用点一行没改**；
    唯一动的是 `build_exe.bat`（新增文件必须登记才参与编译）。以 `csc` exit 0 验证。
  - **刻意没建** `Palette.cs` / `J.cs` / `Http.cs` / `Cards.cs`：本项踩坑点明确说
    「别做这一项搬空壳」，它们分别是第 01/02/03/06 项的落点，做那几项时顺手落。
- **合并分支 `refactor/ui-grid-kit`**：`GridKit.cs`（`StockGrid` + `GridColumn`，
  13 张表统一、删除 9 份重复实现）此前**只存在于那条分支**、不在本分支，
  而待办 README 却把它记为「已完成」。按「所有改动放在一个分支」的要求合并进来
  （仅 exe 二进制冲突，按 ours 解决后重新编译）。

- **`frontend/` 按页面分子目录**（待办 25）：从「7 个文件平铺」改为
  `home/` `mentor-lab/` `chip-scr/`（各目录下统一 `index.html`）+ `shared/`（共享 js）。
  - 用 `git mv` 移动，git 状态显示为 `R`，**文件历史完整保留**。
  - `analysis-engine.js` 先确认过引用关系：被 `index` 与 `mentor-lab` **两个**页面引用，
    故放 `shared/`（不是盲目归类）；`theme.js` / `theme-state.js` 同样共享。
  - 同步改的引用（4 类）：HTML 内 `src=` → `../shared/...`；启动器 C# 四处
    （`OpenWebPage` 签名由「文件名」改「子目录名」、`OpenWeb()`、**`FindRoot()` 根目录探测**、
    `ToggleTheme()` 写主题状态）；`.gitignore` 的 `frontend/theme-state.js` → `shared/`；
    以及两处原计划外但必须改的 —— `启动系统.bat` 直接 `start` 打开 `index.html`、
    `agent_dsh/package.json` 的测试 glob `../frontend/*.test.cjs`（不改会因 glob 失配而**静默不执行**）。
  - 三份 README（根 / backend_fastapi / agent_dsh）的路径引用一并更新。exe 已重新编译。
  - 注：`chip-scr` 里指向 `stock-analysis.html` 的链接是**历史死链**
    （该页在 `3ed9e6f` 改为原生实现时已删除），非本次引入，未擅自改动。

- **数据盘按用途分区 + 一次性自动迁移**（`config/ state/ logs/ cache/`，待办 21）：
  原先用户配置、数据库、锁文件全部平铺在数据根目录，现在分区存放：
  `config/`（用户配置）、`state/`（库与单实例锁）、`logs/`、`cache/`；
  `bars/`（行情分片，2 GB）与 `chip/` **刻意留在根目录不搬**（体积大、搬动易中断且无收益）。
  - 分区常量集中在 `config.py`（`CONFIG_DIR / STATE_DIR / LOGS_DIR / CACHE_DIR`），
    各模块一律引用常量，不再各自拼路径。
  - 新增 `storage.migrate_to_subdirs()`：启动时**一次性、非破坏**搬迁 ——
    仅当「源存在 **且** 目标不存在」时搬，先复制 → 校验（大小一致 + SQLite `quick_check`）
    → 才删源，库文件连同 `-wal` / `-shm` 一起搬；任一步失败就保留原状。
    数据盘在仓库外、不受 git 跟踪，老用户升级**只能靠这段逻辑**完成搬迁。
  - `stock_history.db`、`mentor_lab.db` 与单实例锁均落到 `state/`；
    根目录的陈旧 `instance.lock` 在拿到 `state/` 新锁后自动清理。
  - `/api/system/storage` 新增 `dirs` 分区一览，`items` 路径同步为 `state/...`。
  - **顺带修 bug**：`mentor_store.py` 原先指向**项目内** `backend_fastapi/data/`，
    实测那是一份**空库**、真数据在数据盘 → 大佬实验室看起来是空的、数据被孤立。
    改到 `state/` 后两边归一，迁移前后逐表行数一致
    （1 位导师 / 1 份材料 / 1 份提取 / 1 份评估 / 1 个技能版本）。
  - 启动器「最近查看」的遗留读取同步加上 `<data>/config/` 候选位置（该文件本身已迁到
    `%APPDATA%\StockPoolLauncher\`，数据盘这份是旧版遗留但予以保留）。

- **筹码峰接入「锁仓修正」**：`k = 换手率 × 衰减系数 × 锁仓系数` 里的锁仓系数不再固定 1.0。
  口径对齐「原则上扣除前十大流通股东超 5% 的即可」：r = 前十大**流通**股东中
  「占流通股比例 > 5%」者的合计占比，锁仓系数 = 1/(1-r)
  （例：15.00% + 9.35% → 1/(1-24.35%) = 1.32）。
  **阈值取 5% 的依据**：5% 是举牌线（要约收购 / 权益变动披露界限），超限股东受减持规则
  约束、属长期持有人，其筹码不进入日常流通，故不计入可自由流通部分。
  **方向与预期一致**：存在非流通筹码 → 同样的成交量只能在剩余自由筹码内部倒手 →
  实际换手更快，故名义换手率放大 1/(1-r) 倍（>1，衰减加快），筹码交换更快符合预期。
  取数走新增的 `stock_profile.free_top_holders` / `lockup_ratio`（akshare 东财「十大流通股东」，
  带 6 小时 TTL 缓存），在 `chip_formulas/core/data.py` 装配进
  `ChipContext.lockup_ratio / lockup_factor`；**任何取数失败都回退 r=0、系数=1.0**，不影响主流程。
  `tri_decay` 原有的 `lockup_decay` 占位参数已移除（改为自动），响应新增
  `lockup_ratio / lockup_factor`（`lockup_applied` 随之反映真实状态），K 线右侧筹码区显示
  「已做锁仓修正（占流通股 xx.x%，系数 x.xxx）」。
  注：「5% 以下但属于锁仓」的情形需人工判断，本次只自动处理 >5% 的部分。

- **筹码窗口画面改为三角形分布**（`launcher/StockPage.cs::ChipPanel.OnPaint`）：
  原先每个价格分箱画一条从左轴伸出的**方块横条**，现改为取各分箱的「占比宽度 + 中心价」
  作为顶点、**相邻顶点直线相连**成峰形轮廓（`FillPolygon`），峰形自然呈三角形山脊而非阶梯方块；
  占比为 0 的分箱也参与连线，使峰间谷底真正归零。获利 / 套牢配色规则不变，
  且按「连续 + 同色」分段成面（配色对价格单调，实际最多两段），避免逐格填充产生接缝。
- **筹码窗口新增横坐标刻度**：横轴（占比轴）四等分画短刻度，**只标最高峰的百分比**
  （＝满刻度值，右对齐贴横轴显示，如 `10.69%`）；其余刻度不标数字，
  避免 172px 宽的小面板被文字挤满。取不到足够绘制高度（`plotH <= 30`）时不画。

- **移除「筹码体系」标签页**（`launcher/StockPoolLauncher.cs`）：该页只是个壳 ——
  一段使用说明 + 一个跳 `frontend/chip-scr.html` 的「进入系统」按钮。筹码相关的实际能力
  现在由「SCR90周榜」页（本地全市场自算、按周更新）和个股 K 线右侧的筹码分布承担，
  故删掉这层中间跳转。同步移除 `BuildChipPage` / `FillChipDoc` 与换肤钩子 `doc-chip`，
  并修正引用它的两处文案（分析工具页提示、个股页入口提示）。
  注：后端 `chip_service.py`（SCR 选股，导入 xls 三档分类）本轮**保留**——
  新增的 `chip_rank_service` 仍复用它的 `<data>/chip/processed` 目录与周口径工具。

- **前端表格抽象统一**：新增 `launcher/GridKit.cs`，把 13 张表格的显示收敛到一个
  自定义控件 `StockGrid`（已加进 `build_exe.bat`）。
  - 此前「只读 / 禁排序 / 禁选中高亮 / 自适应列宽列高 / 点击代码跳个股页」这套组合被
    **复制了五遍**（`MktGrid` / `StGrid` / `ScrGrid` 及两处内联），高度自适应也有四套算法。
    现在外观与行为内聚在控件里，各页只声明「有哪些列 + 每行是什么值」。
  - 列用**稳定标识**（`GridColumn.Name`）而非中文表头取单元格：此前有表用表头当列标识、
    有表叫 c0..cn、有表压根没设 Name，`Cells["代码"]` 换个表就炸。
  - 列宽**逐列控制**（AllCells / Fill / 固定宽），不由整表二选一——Fill 会把「名称」这类
    短列撑得很宽，AllCells 更适合窄列，两者各有场景。
  - 高度按内容撑开、封顶 620 才出滚动条；填完行调 `Fit()` 统一重算（避免逐行 Add 把列算窄）。
  - 「点击代码跳详情页」内置到控件（声明 `IsCode`/`Jump` 即可），跳转统一走新增的
    `OpenStock(code)`——原先「写 `_stockCode` + `SelectTab`」的两步式在盘面页、
    快捷搜索、历史导航各抄了一遍。回测明细表不设 Jump（它的点击是展开逐笔交易）。
  - 现价沿用各表自己数据里的价格（策略取 recommend 的 price、周榜取 close …），
    这一层不代为取数，保持「表格只负责显示」。
  - 删除：`MktGrid` / `StGrid` / `ScrGrid` / `MktFit` / `StFitGridHeight` /
    `StAutoWidth` / `StAdjustHeight` / `MktWireStockJump` / `MktJumpToStock`。
  - 表格**不要设 `BackgroundColor = Color.Transparent`**：DataGridView 不支持透明背景，
    运行时会抛 `ArgumentException`；建表发生在窗体构造期，表现就是「编译通过但打不开」。

- **修复 SCR90 周榜「名称列全空」**：名称原先在 6 线程跑满 CPU 之后才去取，akshare 请求
  容易超时；而 `_load_names` 是「拿不到就算了」的静默降级，结果是一整列名称全空且查不出原因。
  - 名称改为**计算前先取**（趁机器还空着），并新增 `names_resolved` 字段如实反映取到几条。
  - 名称与计算**解耦**：新增 `POST /api/chip/rank/names`，可给已算好的结果**单独补齐名称**，
    不必重跑十几分钟的重型计算；页面加了「重取名称」按钮，取不到时状态行给出提示。

- **表格「名称」列加宽度下限**：新增 `GridColumn.MinWidth`。名称会带 `*ST` / `XD` / `XR` /
  `DR` / `N` 这类前缀，纯按内容自适应在名字都较短（或全为空显示「—」）时会窄到看不全。
  统一由 `NameColumn()` 提供（点击跳转 + 92px 下限），周榜 / 策略 / 盘面各表的名称列都用它。

### 新增

- **清理：手动脚本 + 定时调用同一套逻辑**（待办 24）：按你的意见**先做「手动清理」本体，
  定时只是调用它** —— 定时清理跑在后端进程里，程序起不来时它根本没机会执行，
  而「程序打不开」恰恰最需要清理。
  - 新增 `cleanup.py`：**零业务依赖**（不 import FastAPI / main / 任何 service），
    连 `config` 都读不了时退回默认数据目录照常工作，因此**后端完全起不来也能跑**。
    清理对象：轮转旧日志 / 旧诊断包（均保留 7 天）、`cache/`（可重建，全清）、
    跨周周榜结果（**当前周那份保留**）。每一项独立 try/except，单项失败不影响其它，
    支持 `--dry-run` 先预览。
  - 新增 `清理日志与缓存.bat`（GBK 编码）：双击 = 预览，加 `--go` = 真删。
  - 定时：`download_service._scheduler_tick()` 里每天调一次 `cleanup.schedule_tick()`，
    按**日期**去重（不是每 tick）；调用位置**刻意放在 tick 最前面**，
    否则会被长时间运行的下载任务「饿死」，而有任务时最该清日志。
  - 保留天数统一为 **7 天**：`logutil.KEEP_DAYS` 由 14 改为 7，
    与 `cleanup.DEFAULT_RETAIN_DAYS` 对齐（两处必须一起改，已写进注释）。
  - `bars/*.db.bak`（6 个、**1.02 GB**）**默认不动**，仅 `--bak` 显式清：
    查过全仓当前代码不生成它（应是早期分片迁移遗留），是迁移失败时唯一的回滚退路。
  - **永不清理** `state/` / `config/` / `bars/*.db` / `chip/raw` / `chip/meta.json`
    —— 宁可多占空间，也不能让清理变成数据事故。

- **dumplog 诊断包：一键导出排障信息**（待办 23）：`POST /api/system/dump` 生成
  `<data>/logs/dump-<YYYYMMDD-HHMMSS>.zip`（实测 2.8 KB，8 项）——
  两类日志末尾各 500 行、`system.json`（数据目录 + 完整性）、`health.json`（模块挂载错误）、
  **脱敏** `config.json`、`env.txt`（Python + `pip freeze`）、`chip_rank_status.json`、`meta.json`（含 git commit）。
  - **密钥扫描兜底**：打包前对每份待写入文本扫 `sk-` / `LLM_API_KEY=` / `LLM_API_KEY_SEALED=` /
    `DATA_SECRET` / `api_key=`，命中即**拒绝生成整个包**并返回 500 说明命中位置 ——
    不能只靠「记得脱敏」，因为日志内容是别的模块写进去的、本模块控制不了。
    `sk-` 带左边界且要求长度 ≥16，否则 `task-` / `disk-` / `risk-` 会被误判导致永远导不出包。
  - **只写状态不写值**：密钥只出现 `llm_key_state()` 的三态；`crypto.seal_state()` 的
    `error` 详情刻意不带（DPAPI 异常信息可能含密文片段）。
  - **修掉一个自身缺陷**：初版拒绝生成时把命中的**密钥样本**写进了日志，
    等于把密钥从一个地方抄到另一个地方；现只记录「哪个文件命中哪类模式」，绝不回显值。
  - 不含数据库文件（只记录路径与体积）；启动器按钮等第 26/35 项编译 exe 时再加。

- **筹码体系 · SCR90 周级三档**：本机用**本地日线**给全市场算筹码分布，按 SCR90 升序
  每期取前 100，**按周**做三档分析（新增原生表格页签）。
  看的是**周级变化**而不是静态榜单：连续 5 周都还在前 100 里的有多少（磨主峰）、
  本周离开的是哪些、以及是**上涨离开**（启动型）还是**下跌离开**（破位）。
  三档沿用 `chip_service` 的命名与阈值语义（第一档全勤 / 第三档启动型离榜 / 第二档破位离榜），
  差别只在数据源：那边导入外部 xls，这里本地自算。
  离榜用**严格口径**：只看「**上一期在榜、本期才走**」。原 `chip_service` 是
  「曾入选但最新一期不在榜」，会把两三周前就走了的票一直留在榜里、越攒越多，
  淹没真正「本周离开」的那几只，故改为只统计本期新增的离榜。
  - 后端新增 `chip_rank_service.py` + `chip_rank_routes.py`（挂 `/api/chip/rank`）：
    `GET /status`（进度）/ `GET ?limit=`（本周榜）/ `POST /refresh`（触发计算）。
    「周」沿用 `chip_service.expected_weeks` 的口径（含「周五 15:00 收盘才算本周」），
    结果落 `<data>/chip/processed/scr90_rank_YYYYMMDD.json`，当周命中、跨周重算。
  - 计算走 `chip_formulas.compute_matrix(offline=True)`：为绕开两个隐藏联网点
    （锁仓比例走 akshare、缺流通股本走腾讯行情现采），批量**不联网**——几千只逐一打到
    外部接口会被拖垮并触发限流。`offline` 是**按次传参**而非全局开关，
    后台跑榜时交互式单票请求仍能拿到完整的锁仓修正。
    代价：榜单的 SCR90 **未做锁仓修正**，结果里 `lockup=false` 如实标注。
  - `chip_formulas` 的 `load_input` / `compute_matrix` 相应新增 `offline` 参数
    （ARCHITECTURE.md §2 已记录）。
  - 性能：6 线程约 0.25 s/只（200 只实测），全市场 5585 只约 **24 分钟**，
    故做成后台线程 + 进度轮询 + 落盘缓存，页面按钮不阻塞。
  - 前端新增 `launcher/ChipRankPage.cs`（原生表格页签，已加进 `build_exe.bat`）：
    列 = 排名 / 代码 / 名称 / SCR90 / 收盘 / 峰位价 / 平均成本 / 获利比例 / 数据日期；
    名称复用 strategies 里「一次拉全市场」的 24h 缓存（拿不到显示「—」，不影响榜本身）。

- **策略：筹码集中度90 · 升序选股**（新增 `strategies/scr90.py`）：每 N 个交易日在池内
  按 SCR90 升序排名，取最集中的前 K 只持有，跌出榜单即卖出。
  参数：持仓数量 / 调仓间隔 / 筹码窗口 / 价格分箱 / 建仓系数。
  - **能回测的关键**：筹码矩阵是**因果**的（第 i 帧只由前 i 天推出），一次算完某只票
    就得到整段 SCR90 序列，可逐日做横截面排序，不存在未来函数；取数区间在 start 之前
    再往前多取约 1.5×window 个自然日养状态，这些帧不参与选股。
  - 与 `/api/chip/rank` 周榜同口径：批量走 `offline=True`（不联网），故**未做锁仓修正**。
  - 已通过策略注册中心 LOAD 五步校验（导入 / 唯一性 / 元数据 / 签名 / 冒烟），
    「大佬策略实验室」的下拉会自动出现，无需改前端。实测 12 只 / 半年 0.4s 出 25 条信号。

- **后端启动日志不再把 uvicorn 的启动横幅标成 `[err]`**：新增
  `backend_fastapi/uvicorn_logging.json`，经 `--log-config` 传给 uvicorn，把默认日志器的
  输出流从 stderr 改成 stdout（访问日志本来就在 stdout）。启动器按**输出流**打标签，
  而 uvicorn 默认把启动横幅写 stderr，于是每次启动都显示 `[err] INFO: ...` 像报错。
  ⚠️ 该 JSON **必须纯 ASCII 无 BOM**：uvicorn 用 `json.load(file)` 读它且**不指定编码**，
  在中文 Windows 上按 GBK 解码，含中文会直接让后端启动崩溃（实测踩到）。
  真正的告警 / 异常仍写 stderr、仍带 `[err]`——那是对的。
  启动器、`run_backend.bat`、`start_backend.bat` 三处均已接上，且都做了文件存在性判断
  （文件缺失则退回 uvicorn 默认配置，不会因此启动失败）。

- **K线周期选项：日线 / 周线 / 月线**（工具栏新增「周期」三按钮）。
  - 日线是**唯一落库口径**，周 / 月由新增的 `periods.py` **读取时现算**（不另存一份，
    与「不存 qfq_factor」同理）：按自然周 / 自然月合样，日期取组内最后一个交易日，
    open 取首、close 取末、high/low 取极值，volume/amount/turnover 取**合计**，
    涨跌幅 / 振幅按聚合后 OHLC 现算；必须在**复权之后**再合样。
  - **新增 `kline_service.py`：全项目唯一的「取数 + 周期合样」出口**。
    此前 `resample_bars` 在个股历史 / 指标 / 筹码三处各调一遍，等于「同一根 K 线定义了三遍」，
    任一处改规则就会出现「蜡烛是周线、指标按日线算」这类**看着正常实则错位**的问题。
    合并后三处共用同一序列（实测日 / 周 / 月下"指标序列 == K线序列"均为 True，
    日换手率合计 == 周换手率合计），且只合样一次并缓存。
    分层单向无环：`history / indicators.data / chip_formulas → kline_service → price_store / periods`。
    **不落盘**：周 / 月 K 是派生口径，落盘就要处理失效与陈旧，与项目「只存不可变数据」相悖。
  - `strategies/core/data.py` 同步改为经 `kline_service` 取数（此前绕道 `indicators.data`，
    让策略为了拿行情而依赖"指标"包，分层不对）；对外仍只给 OHLCV 五列、仍以 2 根为最小可用，
    与旧路径逐值对拍一致。**回测固定日线**并写明原因：`stats.py` 的年化 / 夏普按 `252`
    交易日/年折算，换周期会静默算错，故不开放 period（ARCHITECTURE 已记录这一约束）。
  - **指标与筹码同步换周期**：`GET /api/history/stock/{code}?period=`、
    `POST /api/indicators/batch`（body 加 `period`）、`GET /api/chip/dist?period=`。
    周线蜡烛配周线 MA（MA5 = 5 周，实测日 11.37 / 周 11.49 / 月 10.92），
    周换手率 2.69% ≈ 日换手率 0.53% × 5，口径自洽。图例同步显示 日K / 周K / 月K。
  - 「范围」从**固定根数**改为按周期换算（近1年：日 250 / 周 52 / 月 12），
    否则周线下点「近1年」会画出 5 年，标签是骗人的；滚轮缩放时范围按钮取消高亮。
  - 渲染完 K 线后按当前周期重载指标，顺带修掉「换股后指标残留上一只票」。
  - 已知取舍：入池 / 出池标记按精确日期匹配，周 / 月下只在标记日恰为该周期最后一天时显示。

- **筹码分布（筹码峰）**：个股页 K 线**右侧新增一个窗口**，横轴筹码占比、纵轴价格，
  与 K 线主图共用价格轴（同一 lo/hi 映射到同一纵向区间），并给出平均成本 / 获利比例 /
  峰位价 / 集中度 90。
  - 算法在新增的 `chip_dist_service.py`：**换手率衰减 + 三角形分布**（峰值取当日均价），
    `k=换手率×衰减系数×锁仓系数`，存量 `×=(1-k)`、新增按三角形分布的 CDF 差值精确落到各价格箱。
    接口 `GET /api/chip/dist`（`chip_dist_routes.py`，已挂到 `main.py`）。
  - **不进指标体系**：指标框架输出「与 K 线同 date 的序列」，筹码分布是「按价位的直方图」，
    两者结构不通用，故独立取数、独立渲染，前端按 `panel` 路由的指标面板不受影响。
  - **新增流通股本数据源**（`share_service.py`，腾讯行情市值 ÷ 现价反推）并落库
    `price_store.share_capital`（按生效日存序列，读时前向填充、不足则回退最近已知值）：
    本地通达信日线没有股本，换手率（= 成交量 ÷ 流通股本）算不出来，而衰减完全由它驱动。
    `indicators/data.py` 相应扩展 `get_float_shares` / `get_turnover` / `get_vwap`。
  - **锁仓修正**：`k = 换手率 × 衰减系数 × 锁仓系数`，锁仓系数由前十大流通股东
    「占流通股比例 > 5%」自动算得（见上方「变更」）；无数据时回退 1.0，
    结果带 `lockup_applied` 与 `lockup_ratio / lockup_factor`，界面标注「已/未做锁仓修正」。
  - **光标跟随**：光标在 K 线上左右移动，窗口切到那一天的分布（标题右侧显示该日日期，
    获利 / 套牢按**当日**收盘价着色）。做法是后端**一次算完窗口内每个交易日的快照**
    （`frames.dates/pct/stats` 一一对应，与 `bins` 共用价格轴），前端按日期本地切片，
    光标移动不再发请求。并加 **warmup**：迭代区间在窗口之前再取等长的一段把筹码状态养熟
    （否则窗口首日只累积了一天，光标移到最左会退化成一根尖刺——实测首帧 90% 集中度
    0.009、获利比例 0.88%，加预热后为 0.09 / 38%）。
  - 窗口随个股打开 / 区间切换 / 复权切换重新计算，滚轮缩放**防抖 400ms** 后重算；
    加载失败只影响该窗口，不拖累 K 线。

- **K 线键盘操作**：`↑↓` 缩放（上=放大 / 下=缩小，与滚轮同一步进与同一实现）、
  `←→` 移动光标（每次一根，并显示该根 OHLC 浮窗）。
  - 光标走到可视区**边缘会自动平移窗口**（露出更早 / 更新的 K 线），与行情软件一致；
    缩放后光标越界会自动收回。右侧筹码窗口随光标同步回溯。
  - 复用 `OnPreviewKeyDown` 声明方向键为输入键，避免被外层容器当「移动焦点」吞掉；
    切到 K线 页、点过范围 / 复权按钮后都把焦点还给图表，键盘操作不被打断。
  - 工具栏「范围」行末尾加了 `↑↓ 缩放 · ←→ 移动光标` 提示。

- **K线页精简、图表加高**：移除「个股概况（在榜统计 KPI）」分组（连同 `StockRenderStats`
  与其调用），顶部只保留指标勾选与筹码公式下拉；row1 是 `Percent 100`，
  省下的高度**全部**归 K 线。在榜明细仍可在「记录 · 消息」页的入池出池表查看。
  - 顺带修掉一个被概况分组"遮住"的布局缺陷：K线页顶部那个 `Stack()` 的列宽是
    `Percent 100`，在 `AutoSize` 表里会绕成「列宽 ← 表宽 ← 子项首选宽」的循环，
    指标勾选行（`WrapContents` 的 FlowLayoutPanel）被用 ~98px 测量 → 8 个控件各占一行
    → 首选高度 208px，而 `AutoSize` 行取的正是它，于是 K 线上方空出一大块。
    以前有概况分组的高首选宽度顶着才没暴露。修法：把该列宽改成 `Absolute`
    （测量有确定宽度），实测 520~1400 窗口宽度下**空余均为 0** 且不横向溢出，
    K 线由 293px → 465px。诊断用离屏 `PerformLayout` 打各控件几何，定位后已删除临时程序。
  - 同一原因导致的另一处不一致：`SetData()` 不重置可见根数，加载新股票后"范围按钮高亮"
    与图表实际根数可能对不上（筹码帧窗口也会跟着对不上光标），现在加载后补 `SetRange(_stockRange)` 同步。

- **筹码峰改为「公式」形式载入**（新增 `chip_formulas/`，与 `strategies/` 同构）：
  一个文件一个公式、**文件名即 id**、包加载时自动扫描 + 5 步校验（导入 / 唯一性 /
  元数据 / 签名 / 冒烟），未通过者直接剔出注册表——既不出现在清单也不可被计算，
  运行期不做冗余兜底。现有算法落为默认公式 `tri_decay.py`，结果与原实现完全一致。
  - 公式契约：`(ctx) -> (交易日数, 分箱数)` 的**相对**筹码量矩阵（core 按行归一化到 100%），
    逐日留档以支持光标回溯；价格轴 / 预热由 core 统一负责，公式不碰取数与 bin。
  - 新增 `core/spread.py` 提供 `triangle_cdf` / `uniform_cdf` 摊布原语，供后续公式复用；
    `core/data.py` 是包内唯一取数入口。
  - 接口：`GET /api/chip/dist/formulas`（清单 + 默认 id）、`GET /api/chip/dist?formula=&params=`；
    前端新增公式下拉（≥2 个公式才显示），新增公式无需改前端。

- **筹码峰的延伸量拆成独立指标文件**（`indicators/` 下）：凡是「按日期的序列」就进指标体系，
  非序列的中间量（分布矩阵 / 价格轴 / 流通股本）留在公式包。
  - `chip_cost.py`（panel=main，平均成本 + 峰位价，与价格同量纲故叠加主图）、
    `chip_profit.py`（panel=lower，获利比例%，以**当日**收盘为界避免未来信息）、
    `chip_scr.py`（panel=lower，集中度 90/70）、
    `turnover.py`（panel=**none**，换手率%：能枚举、能算、不显示，供导出 / 内部复用）。
  - 经 `indicators/data.py` 的 `get_chip_frames()` 复用公式包算出的矩阵（带缓存，
    同一票叠加多个筹码指标只算一次）；与公式直算对拍一致（获利比例 92.5197 == 92.5197）。
  - `df.attrs["code"]` 由 `compute()` 挂上，指标需按代码再取数时经 `data.code_of(df)` 取；
    依赖单向：`chip_formulas.core.data` → `indicators.data`，反向只在 `data.py` /
    `registry.py` **函数内延迟 import**，避免模块级循环。

- **数据可信原则写入注释与文档**（`docs/database.md` 第八节 + 相关模块 docstring）：
  确立「**丢数据可重拉，错误数据不可接受；可用性可以让步，正确性不能让步**」为第一原则，
  并派生四条：不展示可疑数据、不静默丢弃数据、可信优先于完整、其余库只报告。
  - 新增 **8.0 可信原则**与**分层防护表**：日K 有按股内容指纹；池快照 / 消息面 / 复权因子
    只有 `_meta` 结构级保护（300 秒容差内无感，且定位不到具体记录）。
    **并点明最大遗留风险**：`pool_members.import_price` 是唯一不可再生的数据
    （数据源没有「那天的选股结果」），却恰好属于无内容 fingerprint 的那一类——
    一旦被改，既发现不了也补不回来。
  - 修正多处**过时 / 错误**描述：容差 `120 → 300` 秒；时间校验**不含 `-shm`** 且
    **必须早于打开库**（否则必然误报，见 8.2）；`stock_detail` 的行为由
    「拒绝返回 + 提示重新抓取」改为**自动全量重抓并整体替换**；
    `_digests` 补上 `version` 列与「参与字段覆盖**全部**行情列」的说明。
  - 补记**「增量写入洗白篡改」**这一踩过的坑、指纹版本号为何必需、
    重抓冷却（60 秒）、**8.8 单实例锁**，以及「整体替换」的刻意取舍
    （数据源返回不全时会删除未覆盖的历史数据——按第一原则，可信优先于完整）。
  - 修正编号重复：末尾的「能力边界」由 8.5 改为 **8.9**。
  - `price_store.py` / `integrity.py` 的模块 docstring 同步写入该原则。

- **代码审核后的加固（数据可信优先于数据完整）**：对整批未提交改动做了一轮审核，按结论修复三处。
  - **`.env` 改为原子写**（新增 `envfile.py`）：原先 `seal_llm_key` 与 `crypto` 写密钥都直接用
    `write_text`（打开即截断），写入中途失败会**损坏整个 `.env`**——那里还有 `LLM_BASE_URL`、
    `DATA_DIR`、`DATA_SECRET_SEALED`，配置全丢比单个密钥丢失严重得多（密钥丢了指纹全失效，
    全库都要重拉）。现统一为「先备份 `.env.bak` + 写临时文件 + `os.replace` 原子替换」。
  - **自动重抓加冷却**（`history_service.REFETCH_COOLDOWN_SECONDS = 60`）：重抓要打数据源
    （东财不可用时还会降级腾讯，一次几秒），若不冷却，反复刷新或批量导入会把请求放大成几百次。
    `force=True`（用户点「重新抓取」）跳过冷却立即执行；冷却期内仍如实报告原因、库中数据保留。
  - **`instance_lock` 锁区偏移 `1024 → 65536`**：实测不同读取方式行为不一致
    （Python `read_text` 能读，按块读取的工具会撞上锁区报错），留足余量让占用者信息稳定可读。
  - **刻意保留项**：`overwrite_bars` 在数据源只返回部分年份时仍**整体替换**
    （实测会删除未被覆盖的历史数据，见审核结论）。取舍是**数据可信优先于数据完整**——
    宁可整体重拉，也不保留来源存疑的数据。

- **单实例锁：同一数据目录只允许拉起一个后端**：新增 `instance_lock.py`，
  在 `main.py` **任何「写数据目录」的动作之前**取排他文件锁（`<数据目录>/instance.lock`），
  取不到则打印中文说明并以**非 0 退出码**结束（`[fatal] 启动被拒绝：程序已在运行…`）。
  - **为什么必须早于初始化**：第二个进程若已开始迁移、建表、刷新指纹，就会与第一个进程
    互相覆盖指纹与签名——属「静默损坏」，事后无法分辨谁是对的，必须在入口拦住。
  - **用操作系统锁，而不是「PID 文件 + 判断进程是否存在」**：进程退出（含崩溃、被强杀）时
    锁由操作系统释放，不会留下陈旧 PID 造成「没人运行却启动不起来」。实测强杀持有者后
    可立即获取；反之持锁期间第二个实例被拒绝并报出占用者 `PID xxx 启动于 …`。
  - **占用者信息可读**：锁区刻意放在内容区**之后**（Windows 文件锁是强制锁，会阻止其他进程
    读写被锁区间），因此后来者仍能读到是谁占着。
  - 新增 `test_instance_lock.py`（4 个用例：跨进程拒绝、强杀后自动释放、同进程幂等、占用者可读）。

- **可信链路强化：指纹覆盖全部字段 + 检测到不可信直接重拉 + 密钥不留明文**：
  在既有「按股指纹 + 读时拒绝」基础上补齐三处薄弱点。
  - **指纹字段从 6 个价格列扩到全部行情列**（`DIGEST_VERSION = 2`）：原先只算
    `trade_date/open/high/low/close/volume`，因此改 `amount`（成交额）、`turnover`（换手率）、
    `amplitude`、`change_pct`、`change_amount` 或 `source`（数据来源）**都不会被发现**。
    现覆盖全部行情字段（仅排除 `code` 查询键与 `fetched_at` 采集时间戳）。
    配套引入**指纹版本号**：字段集合一变，新旧摘要无法比对，会把正常数据整体误判成「被篡改」；
    `_digests` 增 `version` 列，启动时 `ensure_digests()` 自动整体升级（实测重算 15 只）。
  - **检测到不可信后直接重新抓取**（不再只是拒绝 + 提示）：`price_service.refetch_bars()`
    从库中**既有最早日期**开始全量重抓，并由 `price_store.overwrite_bars()` **整体替换**
    （写入后删除不在新日期集合内的旧行）。关键点：原 `sync_bars` 是**增量**的
    （只从最新日期往前重叠 14 天），**碰不到更早的历史行**，所以历史区间被改动时永远修不好——
    这正是原 `POST /api/system/rebuild` 失效的原因，现一并改为全量。
    **先抓取、后写入**：数据源失败时抛错并保留库中原数据，不造成丢失。
  - **密钥不留明文**：DPAPI 原语抽为独立模块 `dpapi.py`（避免 `config` 与 `crypto` 循环导入），
    `LLM_API_KEY` 支持以 `LLM_API_KEY_SEALED` 密封存放（读取时**密封优先、明文回退**）；
    新增 `GET /api/system/llm-key` 查看状态与 `POST /api/system/llm-key/seal` 执行密封。
    **刻意不自动改写 `.env`**：密封值绑定本机，换机器后解不开需重填，故由使用者显式触发。
    **行情数据本身保持明文**（不做全库加密）。
  - **修复完整性校验的另一处同源误报**：此前已排除 `-shm`、只比较主库与 `-wal`，
    但实测**打开 WAL 库（哪怕纯读查询）也会创建 `-wal`**，其 mtime 就是「此刻」；
    而 `check_library` 恰好先连接后取值，于是只要库有年龄就必然报「文件修改时间晚于程序记录」。
    现将取时间提前到**打开库之前**，实测告警消失（`ok=True`，此前稳定报 9~10 分钟偏差）。
  - **实测闭环**：真实篡改 `600519` 一条 close → 请求个股详情 → 自动重拉修复
    （`before=486 after=494 来源=腾讯证券`）→ 修复后读数正确；重抓失败时 `bars=0` 且库中数据完好。
  - **顺带修掉一条历史脏数据**：该股 `2026-01-05` 的 close 此前被写成 `99999.0`
    （早期人工测试残留），一直未被发现，本次由自动重拉修正为真实值 `1426.0`。
  - **修复「增量写入洗白篡改」漏洞**（实测发现，此前会绕过整条防线）：`upsert_bars` 写入后会
    **重算整只股票**的指纹，而增量写入只覆盖「有数据的那几天」——于是
    「改历史行 + 等一次日常同步」就会把**本次没覆盖到的历史改动**一并纳入新基线，
    篡改从此**永久隐身**。现改为**写入前先判定旧指纹，已损坏则不重算**
    （保留旧指纹使问题继续暴露，读取仍报「行数 / 内容不一致」→ 继续触发全量重拉）。
    实测：正常增量同步照常通过、篡改后同步仍被拒、重拉后恢复；已补 2 个回归测试。
  - **加密边界（现状）**：`_meta.hmac` 与 `_digests.checksum` 为 HMAC-SHA256 摘要（不可逆，非明文）；
    日志文件经检查**不含密钥明文**；`DATA_SECRET` 已由 DPAPI 密封。

- **数据目录可配置（与代码分离 + 旧数据自动迁移）**：所有运行期数据集中到一个数据根目录，
  **默认是「程序上一级的 `stockanaly-data`」**，即与仓库并列：

  ```text
  <上一级>/
  ├── stockanaly-main/     ← 程序
  └── stockanaly-data/     ← 默认数据目录
  ```

  做到了数据与代码分离，同时跟随程序自身位置（换机器、整体移动都不受影响），也不依赖盘符；
  需要放到别处时用 `.env` 的 `DATA_DIR` 或环境变量 `STOCK_DATA_DIR` 指定，
  **首页「数据管理」中可查看与修改**。
  - 目录结构：`stock_history.db`（池快照 / 日K / 因子 / 除权 / 消息 / 任务）、`mentor_lab.db`、
    `chip/`（`raw/` 原始导出文件、`processed/` 计算结果、`meta.json`）。
  - **解析顺序**：`STOCK_DATA_DIR` > `.env` 的 `DATA_DIR` > 默认（程序上一级的 `stockanaly-data`）。
    默认值取自程序自身位置，因此不依赖盘符与当前工作目录。
  - **旧数据的自动迁移**：启动时若当前目录缺少数据而项目内旧位置有，则**复制**过去（含 SQLite 的
    `-wal` / `-shm` 附属文件，以及整个 `chip_data/`），**只复制、不删除源文件**，
    避免出现「数据看起来丢了」的情况。
  - 新增 `storage.py`（路径解析、迁移、用量统计）与 `system_routes.py`
    （`GET /api/system/storage` 查询、`POST /api/system/storage` 修改——只写 `.env`，重启后生效，
    并校验目标目录可写）；`.gitignore` 相应收敛为通用规则。
  - `chip_service` 的 `chip_data/` 同步并入数据根目录下的 `chip/`。

- **可信链路：按股指纹 + 读时拒绝 + 密钥绑定本机**：在「错误数据不可接受」的原则下，
  把防线从「能发现库被改过」推进到「**能定位到哪只股票、且拒绝把可疑数据交出去**」。
  - **按股指纹**（`_digests`，每个日K分片库一张，每股一行）：记录行数、日期范围与
    全部原始值的确定性 HMAC。**写入时只重算受影响的那几只**（毫秒级），
    `ensure_digests()` 为既有数据补算一次。
  - **读时拒绝**：`list_bars` / `load_bars` 默认先校验指纹，不一致抛 `UntrustedDataError`，
    **不把可疑数据交给上层计算**（后续已升级为「检测到即自动全量重拉」，见上方条目）。
  - **深校验** `GET /api/system/integrity/deep`：逐股重算指纹，输出不可信清单（可指定单只）。
  - **重新抓取恢复** `POST /api/system/rebuild`：用数据源真实数据覆盖并刷新指纹，
    是「恢复可信」的正规途径；与 `resign`（接受现状）语义明确区分。
    **注**：该接口起初调用的是增量 `sync_bars`，**修不了历史区间**，现已改为全量重抓（见上方条目）。
  - **密钥绑定本机**：改用 Windows DPAPI 密封（`DATA_SECRET_SEALED`），密文只能被
    本机 + 当前用户解开，拷到其他机器即失效。启动时自动把明文密钥改为密封；
    **按设计不提供恢复码**（丢数据重拉即可）。
  - **实测闭环**：改一条 K 线 → 读取被拒（`bars=0 untrusted=True`）→ 深校验定位到
    `600519/bars_2026` → `rebuild` 重抓 → 恢复 `ok`。
  - **修复一个会自伤的缺陷**：`db.connect()` 原先每次都执行 `PRAGMA journal_mode=WAL`，
    而库已是 WAL 时该 PRAGMA 仍会改写文件头（改时间但不算 DML），
    导致**每次启动都误报「被外部改动」**。现改为「只在需要时切换」，容差放宽到 300 秒。
  - 新增 5 个测试：指纹放行正常读取、篡改后拒绝返回、深校验定位、DPAPI 密封往返、
    密钥写入不留明文。

- **数据可信性加固（正确性优先于可用性）**：确立「**丢数据可重拉，错误数据不可接受**」的原则。
  - **修复采集层仍拉三口径**：存储结构早已收敛为只存原始价，但采集仍同步 qfq / hfq / raw 三套，
    导致库里白占约 2/3 空间（与"省 2/3"的目标相悖）。现 `SYNC_ADJUSTS` 只保留 `raw`，
    并新增 `price_store.drop_redundant_adjusts()` 清理已有冗余：实测三个分片 0.692 MB → 0.352 MB，
    删除 1942 行；复权价改由 `hfq_factor` 现算，读数验证 `qfq` 末端恰等于 `raw`（符合前复权定义）。
  - **入库前质量校验**（`price_service.validate_bars`）：对每根 K 线检查 OHLC 是否齐全、
    价格是否为正、最高价是否低于最低价、开收盘是否超出当日区间——**不合格的行拒绝入库**并给出原因。
    宁可该股当日缺数据（可重拉），也不让不可信数据进入后续计算。
    刻意只做**绝对可靠**的日内关系校验，跨日跳变易与除权混淆，不在入库处拦截。
  - **修复完整性校验的误报缺陷**：原先把 SQLite 的 `-shm` 文件也算进"最后修改时间"，
    而 `-shm` 只是共享内存索引、**任何一次打开库（哪怕纯读）都会更新它**，造成误报。
    现只比较主库与 `-wal`。
    **注**：`-wal` 其实有同样性质——打开库即被创建（实测），因此当时并未真正修好，
    残留问题已在后续条目中进一步修复。
  - **`VACUUM` 后同步记录时间**：`VACUUM` 会刷新文件时间但不产生 DML，
    原先会导致记录时间不更新而误报；`db.connect()` 新增 `force_touch` 参数用于这类操作。
  - 新增 `POST /api/system/integrity/resign`：把当前状态**重记为可信基线**，
    并明确标注「这会接受当前数据状态，仅当清楚变动来源时使用」。
  - 新增 `test_validate_bars_rejects_bad_rows`（入库质量校验）。

- **数据完整性保护（入库格式版本化 + HMAC 防篡改）**：
  新增 `_meta` 表记录「结构版本 / 程序最后写入时间」并对内容做 HMAC 签名，
  可发现有人绕过程序直接用数据库工具改数据。
  - **入库格式定版**：`db.SCHEMA_VERSION` 与代码同步递增并写入每个库的 `_meta`；
    启动时比对，不一致即提示需要迁移。
  - **零侵入的写入记录**：更新发生在 `db.connect()` 的事务提交出口——
    任何写事务（`total_changes > 0`）提交前自动刷新元信息，**各 store 无需关心**。
  - **三层检查**（`integrity.check_library`，全程只读）：
    ① `_meta` 签名是否匹配（能发现只改值不改签名）；② 库文件（含 WAL/SHM）的 mtime
    是否明显晚于 `last_write_at`（能发现**程序之外**的写入，容差 120 秒以免误报）；
    ③ 结构版本与期望表是否齐全。
  - **实测检出效果**：直接改数据并推后文件时间 → `tampered`（提示相差 120 分钟）；
    只改 `_meta` 值 → `tampered`（签名不匹配）；恢复后 → `ok`。
  - **密钥**：首次运行自动生成 256 位随机密钥写入 `backend_fastapi/.env`（不进版本库），
    可用 `DATA_SECRET` 环境变量覆盖。`ensure_signed()` 为既有数据补一次签名。
  - **接口**：`/health` 增加 `integrity` 字段（启动校验一次并缓存）；
    新增 `GET /api/system/integrity` 可随时重新校验。
  - **原则：只报告、不自动删除**——数据不该由程序在用户不知情时丢弃，
    「删库重拉」由使用者决定。
  - 新增 `test_integrity.py`（6 个用例：正常通过、签名篡改、外部写入、缺表、不误报、只读性）。

- **行情存储分片化 + 单库调优（为 5000 只 × 10 年规模做基础）**：
  实测单条日K含索引约 203 字节，据此外推 5000 只 × 10 年：三口径需 **7.4 GB**，
  而只存一份原始价只要 **2.5 GB**——**三分之二的体积是自己多存出来的**。
  因此把基础结构一次建对，避免数据长到千万行后再停机迁移：
  - **日K 按年分片**：`bars/bars_YYYY.db`，单文件约 250 MB/年（5000 只），
    便于备份、归档与老年份只读；跨年查询由 `price_store` 按年份逐库读取后合并排序。
  - **复权因子与除权明细独立库**：`bars/factors.db`（全量仅几十 KB）。
  - **只存一份原始价**：删除 qfq / hfq 冗余存储，复权价一律由 `hfq_factor` 现算，
    存储从 7.4 GB 降到 2.5 GB。
  - **单库调优**：行情表改用 `WITHOUT ROWID`（主键即聚簇数据，省一份 rowid 索引，
    约省 20~30% 空间且读更快）；连接启用 `mmap_size=256MB`、`cache_size=64MB`、
    `synchronous=NORMAL`（WAL 下依然安全）。
  - **一次性迁移**（`price_store.migrate_legacy_shards()`）：把旧主库单表里的
    **`adjust='raw'`** 按年拆入分片、因子与除权搬到 `factors.db`；
    老数据只有 qfq（以当时「最新日」为基准烘焙而出，无法还原真实成交价）一律丢弃，
    由访问时自动重新同步补齐。
  - **旧表清理**（`price_store.drop_legacy_tables()`）：迁移验证无误后删除主库中的
    `daily_bars` / `adjust_factors` / `dividends` 三张旧表并 `VACUUM` 回收空间。
    带安全校验——分片库为空或没有日K数据时拒绝执行，避免误删唯一副本。
    实测主库 **5.23 MB → 0.44 MB**（回收 4.80 MB），总占用降至 **1.24 MB**。
  - 迁移完成后清理了历史副本（迁移前备份、D 盘旧目录、项目内 `backend_fastapi/data`
    与 `chip_data`），共释放约 15.8 MB。行情均可按需重新抓取，无需长期保留副本；
    `.gitignore` 中的旧路径规则保留作为兜底。
  - 测试新增 `test_sharded_by_year`（分片写入与跨年合并查询）与
    `test_factors_live_in_separate_db`（因子独立库）。

- **股价模块独立拆分（`price_store` / `price_service` / `db`）**：
  原先行情与「股票池历史 + 消息面」混在 `history_service` / `history_store` 里，职责过载且难以复用。
  现按「存储 / 采集 / 编排」三层拆开：
  - **`price_store.py`** —— 行情存储与**复权计算**：表结构、读写、`load_bars(code, adjust, as_of)`。
    **纯数据模块，不发起任何网络请求**，因此页面、估值、回测都可直接依赖；
  - **`price_service.py`** —— 行情采集：日 K 三口径、复权因子、除权明细的拉取与数据源降级；
  - **`db.py`** —— 共享 SQLite 基础设施（连接、库路径、建表调度、幂等加列），
    避免行情与股票池两个存储模块各自重复一份连接逻辑；
  - `history_service` / `history_store` 相应瘦身，只保留**股票池快照、消息面与后台任务编排**。
  - **表结构与数据零迁移**：表名、主键、库文件均不变，仅代码归属调整；`db.init_db()` 统一建表。
  - 顺带修复：`load_bars` 的复权换算改为**指针单次遍历**（O(bars + factors)），
    避免回测批量读取时逐日重复扫描因子表。
  - 新增 `test_price_store.py`（5 个用例，覆盖中英文列兼容、读写、
    **复权计算与滚动复权**、缺因子退化、不复权直返）。

- **复权因子与除权除息明细独立落盘（「原始价 + 因子」方案 · 阶段 1）**：
  新增 `adjust_factors`（复权因子）与 `dividends`（分红送转明细）两张表，`daily_bars` 幂等补一列 `source`。
  **现有表结构与数据均无需迁移**（`daily_bars` 主键本就含 `adjust`，原始价直接以 `adjust='raw'` 存放）。
  - **只存 `hfq_factor`，不存 `qfq_factor`**：前者以「上市首日」为基准、历史值不随新的除权变化；
    后者以「最新日」为基准、每天都在变，入库等于又引入会变的数据。前复权价读取时现算：
    `raw(t) × hfq_factor(t) ÷ hfq_factor(基准日)`，基准日可取「回测起点」，从而**避免把未来信息烘焙进历史**。
  - **对拍校验通过**：600519 实测 `raw × hfq_factor` 与数据源 hfq 价格的**最大偏差 0.0000%**（浮点误差级），
    口径确认无误；另实测 `stock_zh_a_daily` 默认返回不复权价、`hfq_factor` 单调递增。
  - **跨源一致性**：腾讯与新浪的**不复权原始价完全一致**（4 只股票 × 13 个交易日，差异 0.0000%），
    因此原始价可跨源混用；但**复权价必须同源**（已实测腾讯的 qfq/hfq 收益率并不相等）。
  - 采集策略：复权因子与除权明细只在**首次**采集，已有数据则跳过（`sync_reference`），
    避免每次导入股票池为每只股票多发两次请求；需要补救时用 `force=True`。

- **日 K 三口径落盘（为策略回测做准备）**：`history_service` 一次同步 **qfq / hfq / 不复权（raw）** 三种口径，
  同表并存（`daily_bars` 主键含 `adjust`），各口径独立增量补齐，单口径失败不影响其余口径。
  - 动机：原实现只存前复权。而**前复权以最新日为基准，每发生一次除权就会重写整段历史**，
    导致同一段历史在不同时间回测得到不同结果、无法复现；
    **后复权（hfq）以上市首日为基准，历史数据不随新的除权变化**，因此策略回测应固定使用 hfq。
  - 不复权（raw）用于准确还原当时的成交价与持仓成本（hfq 数值失真，不宜直接展示给用户）。
  - 实测（600519，2024-09-19 ~ 2026-09-18，腾讯源）：三口径各 486 条；
    `qfq` 末日价恰等于 `raw` 末日价（符合"前复权以最新日为基准"的定义）。
  - ⚠️ **注意**：实测 **`qfq` 与 `hfq` 的全区间收益率并不相等**（+8.60% vs +6.74%），
    说明该数据源的两种复权算法并非只差一个常数因子。**回测应固定单一数据源并记录口径与来源，避免混用。**

- **股票池个股 K线与消息面**：每日导入快照同步至 SQLite，后台增量缓存前复权日 K；东方财富行情不可用时自动降级腾讯行情。点击股票可查看 K线、MA5/10/20/60、成交量、进出池标记、公告和新闻时间轴，既有浏览器历史会自动补录到后端。

- **股票估值计算（简化 DCF · 自动拉取参数）**：新增 `frontend/valuation.html` 与 `backend_fastapi/valuation_service.py`，
  从股票池页顶部「📈 股票估值计算」按钮打开；接口 `POST /api/stock/valuation`。
  - 方法口径对齐《股票估值_单文件模板.py》：前 5 年净利润贴现 + 永续增长（贴现率 10%、永续增长率 0）。
  - **三项参数全自动拉取**：股价（新浪行情）、总股本（腾讯行情总市值 ÷ 股价 反推）、
    期初净利润按 **TTM 归母 → 年报扣非 → 年报归母** 三级兜底（东财财务摘要，TTM 由「最新累计 + 上年年报 − 上年同期累计」折算）。
    实测与模板样例一致：巨人网络股本 19.0056、TTM 归母 31.2177 分毫不差。
  - **机构预测净利润为可选项**：提供时按复合增长率推算，三档为乐观 ×1.5 / 中性 ×1 / 悲观固定 5%；
    未提供时按模板兜底 25% / 10% / 5%，不再要求必须填写。
  - 输入代码后自动显示股票名称与当前股价用于确认（新增轻量接口 `GET /api/stock/quote`，只拉行情、不拉财务数据）；
  - **修复「第二次计算数据不更新」**：自动拉取的股价 / 总股本 / 净利润会回填到参数输入框，
    下一次计算时被当作「用户手动输入」回传后端，覆盖新股票的自动拉取（表现为换了代码、名称变了但数据没变）；
    现给自动填入的值加 `data-auto` 标记——自动值不回传后端、换代码时清空，仅用户手改过的值才优先使用。
  - 输出每股价值、股价/价值（低估程度）、预测 N 年价与收益率，并标注各档增长率来源；
    页面含「① 输入参数（机构预测等为可展开选填项）→ ② 计算结果（三情景对比表）→ ③ 计算过程（按情景切换、逐步展示公式与中间值）」。

### 变更（估值计算改为原生内嵌页）

- **股票估值计算由网页改为启动器原生页面**：新增标签页「估值计算」，直接嵌在启动器窗口内，不再新开浏览器窗口；
  布局沿用启动器自有的 `Stack / Group / Row` 体系，随「主题」按钮切换浅色 / 深色（换肤已覆盖 `DataGridView` 与 KPI 卡片）。
- **代码拆分**：估值页独立为 `launcher/ValuationPage.cs`（`MainForm` 改为 `partial class` 的同一份类），
  复用主文件的页面组件与换肤逻辑，主文件 `StockPoolLauncher.cs` 减少约 660 行；`build_exe.bat` 已加入该文件。
- **删除 `frontend/valuation.html`**：原网页版估值页不再使用；页面入口改为启动器标签页，
  「分析工具」页的估值卡片「进入」按钮改为切到该标签页。
- 个股分析页 `stock-analysis.html` 的「打开完整计算过程 ↗」链接随之移除（原指向 `valuation.html?code=xxx`），
  改为页面内提示「完整计算过程见启动器「估值计算」标签页」；该页的估值结果本身仍直接调用 `POST /api/stock/valuation`，不受影响。

### 变更（盘面及板块分析改为原生内嵌页）

- **盘面及板块分析由网页改为启动器原生页面**：新增标签页「盘面及板块」，嵌在启动器窗口内，不再新开浏览器窗口；
  五块数据分区展示：① 外围环境 ② 大盘资金 ③ 板块β ④ 连板梯队 ⑤ 大面股，沿用 `Stack / Group / Row` 布局并跟随主题换肤。
- **数据获取**：并发拉取 `GET /api/market/{global,capital,sectors,limit-up,big-loss}` 五个接口，
  每块有独立状态与失败提示（任一块失败不影响其他块）；顶部可选交易日（勾选按所选日期取数，不勾选为实时）+ 刷新 / 实时按钮。
- **图表改为数据表**：网页里 echarts 画的行业 / 概念资金流 Top10、连板结构条形图，原生版改为同数值的表格展示
  （按净额 / 涨跌幅排序一致）；涨停明细、炸板 / 跌停表支持点击表头排序；涨红跌绿按单元格着色。
- **代码拆分**：盘面页独立为 `launcher/MarketPage.cs`（仍是 `MainForm` 的 `partial class`），
  复用估值页已有的 `VRequest`（`HttpWebRequest`，`Proxy=null` / UTF-8）与 JSON 取值辅助、`AddKpi` / `VKpiRow` 控件辅助；
  `build_exe.bat` 已加入该文件。
- **删除 `frontend/market-sector.html`**：入口改为启动器标签页，「分析工具」页的盘面卡片「进入」按钮改为切到该标签页；
  `README.md` 与 `backend_fastapi/README.md` 中对该页面的引用已同步更新。

## [v0.3.0] - 2026-09-11

### 新增

- **盘面及板块分析页面**：股票池页顶部「📊 盘面及板块分析」按钮打开 `frontend/market-sector.html`，
  含外围环境、大盘资金、板块β、连板梯队、大面股五大模块，数据缺失时按模块降级提示。
- **后端采集服务**：新增 `market_service.py` 与 `GET /api/market/global|capital|sectors|limit-up|big-loss`，
  数据源为新浪行情 / 同花顺 / 乐咕乐股 / 申万 / 东方财富等公开接口；单源失败自动降级并记录 `errors`，
  进程内 60–120 秒缓存，支持 `?force=1` 强制刷新。

### 修复

- **盘面页切换交易日后图表与数据丢失**：图表容器每次渲染都会重建，但 ECharts 实例被复用并绑定在已被移除的旧节点上，
  导致第二次渲染（改日期 / 刷新）图表永远空白；现校验 `inst.getDom() !== el` 时先 `dispose` 再重建。
- **切换交易日改为整页刷新**：选日期即重新加载五个模块（④⑤ 按所选交易日取数），
  刷新期间保留已有内容、失败只在顶部追加提示。日期框除 `change` 外同时监听 `input`，
  键盘改日期无需失焦也会触发刷新。
- **接口无超时导致页面长时间“加载中”**：前端单接口增加 45 秒超时，并显示 `加载中… n/5` 进度与未返回项；
  后端 `_ak()` 调用 akshare 增加 15 秒硬超时，超时按该数据源不可用降级，不再无限阻塞。

### 性能

- **② 大盘资金 20.8s → 6.0s**：成交额 / 涨跌家数 / 主力资金流三个独立数据源由串行改并行；
  同花顺降级链路（个股资金流 + 大单追踪）并行请求，5000+ 行累加改为列向量化，去掉 `iterrows` 开销。
- **③ 板块β 三源并行**（行业 / 概念 / 申万一级），实测 0.6s；④⑤ 分别 1.1s / 0.5s。

### 新增与修复（历史交易日）

- **①②③ 支持按所选交易日取历史数据**：
  - ① 外围环境：道琼斯 / 纳斯达克 / 纳斯达克100 / 费城半导体（新浪美股日线）、恒生指数（新浪港股指数日线）、
    纽约原油 / 黄金 / 白银 / 美铜（外盘期货日线）按该交易日收盘与涨跌幅展示；日经225、韩国综合指数暂无历史数据源。
  - ③ 板块β：申万一级行业（31 个）按该交易日收盘计算涨跌排行；行业 / 概念板块资金流仅提供实时值。
  - ② 大盘资金：**主力资金流已支持按交易日回溯**——东财 `stock_market_fund_flow` 一次返回约 120 个交易日历史，
    原实现只取 `iloc[-1]` 把这段序列浪费了，现按所选交易日取对应行（日期超出区间时回退最新一期并在 errors 中说明）；
    成交额 / 涨跌家数仍无可用历史数据源（东财相关接口失效，新浪/腾讯日线只有成交量不含成交额，乐咕仅提供当日快照），
    历史日期下这两项返回实时快照并在卡片上提示。
  - ② 的进程内缓存键补上日期（`capital_flow:{date|rt}`），避免查询历史交易日时命中实时缓存。
  - 历史数据不再变化，进程内缓存 6 小时；卡片状态标签会显示数据所属交易日。
- **修复 akshare 并发调用崩溃**：`py_mini_racer`（新浪、同花顺系接口依赖）并发初始化会触发
  `FATAL: partition_address_space` 直接杀掉后端进程；现按模块自动识别这类接口并串行化执行，其余接口保持并行。

### 新增（个股分析独立页面）

- **个股分析独立成页面**（`frontend/stock-analysis.html`）：原先只在 index.html 的「个股详情弹窗」里，
  现拆为独立页面，支持通过 URL 参数 `?code=600519` 直接打开与分享。页面内容：前复权日 K
  （含 MA5/10/20/60、成交量、入池/出池与公告/新闻标记点）、在榜统计卡、入池出池轨迹表、消息面时间轴；
  数据来自 `GET /api/history/stock/{code}`，名称与现价来自 `GET /api/stock/quote`。
- **SCR 选股页个股可点击跳转**：三档结果表格中的「代码 / 名称」改为链接，
  点击在新标签页打开该股的个股分析页（`stock-analysis.html?code=xxxxxx`）。
- **个股分析页底部新增「估值计算」区块**：展示三档情景的结果（增长率、每股价值、股价/价值、
  低估/合理/高估判断、预测 N 年价与收益率）与关键输入（现价、期初净利润、复合增速、贴现率）。
  数据**直接调用 `POST /api/stock/valuation`**（与估值页同一接口），本页不复制任何公式或口径说明；
  另提供「打开完整计算过程 ↗」跳转 `valuation.html?code=xxxxxx`。
- **估值页支持 URL 参数直达**：`valuation.html?code=600519` 会自动填入代码、查询名称并计算，
  便于从个股分析页等处直接跳转。
- **个股详情弹窗已从 index.html 彻底移除**：原 `#detailModal` 的 HTML（约 30 行）与详情专用脚本
  （K 线渲染 `renderStockKline`、消息渲染 `renderStockEvents`、历史加载 `loadStockHistory`、
  入池时间轴、状态变量等约 150 行）全部删除，相关 CSS 保留不影响其他弹窗。
  `openStockDetail()` 改为直接跳转独立页面，因此顶栏搜索、三个列表表格、变动分析的入池/出池列表、
  AI 弹窗的「进出池时间轴」共 6 处入口无需改动，行为统一为「新标签页打开个股分析」。

### 重构

- **业务接口全模块化 + 容错挂载**：
  - 盘面及板块分析 → `market_routes.py`（5 个接口）；大佬策略实验室 → `mentor_routes.py`（15 个接口）；
    个股调研与股票估值 → `stock_routes.py`（3 个接口）；LLM 调用抽为公共模块 `llm_client.py`。
    main.py 从约 600 行精简到约 100 行，只剩「应用创建 + 容错挂载 + /health」。
  - 统一分层：`*_routes.py` 只管 HTTP（路由、参数校验、缓存清理），`*_service.py` / `*_store.py`
    管数据采集与计算，`llm_client.py` 管模型调用；后续新增接口不必再改 main.py。
  - **容错挂载**：改用 `importlib` 逐个导入并捕获异常——**单个模块导入失败（依赖缺失、语法错误等）
    只让该模块的接口不可用，不影响主页与其他模块**；失败原因写入 `/health` 的 `route_errors`，
    `modules` 列出各模块可用性，`mentor_lab` / `market_board` 保留以兼容前端（mentor-lab.html）版本检测。
  - 已实测：临时移除 `market_routes.py` 后服务照常启动，`/health` 报 `market_board=false` 与
    `ModuleNotFoundError`，盘面接口 404，而筹码体系 / 大佬实验室 / 估值接口全部正常。
  - 接口路径与参数（`date` / `force` 等）保持不变，前端无需改动。

### 新增（筹码体系 · SCR 选股）

- **SCR 选股页**（`frontend/chip-scr.html`，股票池页「筹码体系 · SCR 选股」进入）：
  顶部导入行情软件导出的多期「临时条件股YYYYMMDD.xls」，文件名中的 8 位日期自动解析为默认日期并**支持手动修改**；
  跨期合并后输出周级三档分类——第一档磨主峰（最新一期在榜 ∩ 连续 5 期全勤 ∩ 流通市值 100–800 亿 ∩ PE>0）、
  第二档向下破位离榜、第三档启动型离榜；阈值可在页面调整，点「🔄 刷新计算」手动触发计算。
- **新增「最少在榜期数」阈值（默认 2）**：只出现过 1 期的标的多为一次性噪音，直接过滤；
  阈值在页面「展开阈值」中可调（设为 1 即不启用）。该门槛对三档统一生效。
- **过滤统计闭环**：结果区分「在榜期数不足」「市值不符」「在榜但未满全勤期数（暂不入档）」三类并各自计数，
  三者之和与跨期去重总数一致（本地实测 6 + 161 + 16 = 183）。
- **三档统一市值过滤（口径可调）**：第二档「向下破位离榜」/ 第三档「启动型离榜」原先未做市值过滤
  （参考脚本仅过滤了第一档），导致中金公司（935 亿）、同花顺（907 亿）等大盘股混入离榜池；
  现三档统一按流通市值区间过滤，区间可在页面「展开阈值」中手动调节（默认 100–800 亿）；
  PE > 0 仅第一档要求，离榜池不排除亏损股。页面会统计因市值不符被排除的标的数。
- **三档表格支持折叠与排序**：点档位标题收起/展开；点表头按列排序（数值列首次降序、文本列首次升序，
  同列再点反序），每档排序状态独立，排序 / 折叠时不丢失页面滚动位置。
- **运行需最近 5 周数据，缺失直接报错**：数据按周归组（同一周内多份文件只取日期最新的一份），
  窗口以「周一 ~ 周日」为单位——**周一~周四及周五收盘前最新一期为上一周，周五 15:00 收盘后及周末为本周**
  （修复「周五盘后仍按上一周计算、要等到周六才切换」的问题：A 股 15:00 收盘后当日数据即已生成，
  数据也是周五盘后导出的，故收盘后立即切换，不必等周末）；
  缺少任一期的数据会拒绝计算并列出缺口周区间与应有窗口，不做静默跳过；窗口之外的更早文件不参与计算。
- **后端**：新增 `chip_service.py`（计算逻辑）与 `chip_routes.py`（HTTP 接口，独立 `APIRouter`，不写在 main.py 内），
  提供 `POST /api/chip/scr/upload|date|delete|analyze`、`GET /api/chip/scr/files`；
  导入的原始文件存放于 `chip_data/raw/`，计算结果（JSON 明细 + CSV 汇总）写入 `chip_data/processed/`，两者独立于其他数据。
- **格式兼容**：行情软件导出的「.xls」实际多为 GBK 制表符文本（直接 `read_excel` 会报 BOF 错误），
  现兼容 GBK 制表符文本、真 Excel（.xls/.xlsx）与 CSV 三种来源。

### 新增（交互）

- **涨停明细表支持点击表头排序**：④ 连板梯队的涨停明细可按 代码 / 名称 / 连板 / 行业 / 涨幅 / 封板资金 /
  换手 / 首封 / 涨停统计 排序；点击同一列切换升序与降序，数字列默认降序、文本列默认升序，空值恒排最后，
  表头用 ▲▼ 标记当前排序，标题同步显示“按 XX 降序，共 N 只”。

### 修复（LLM 配置体验）

- **未配置 API Key 时不再长时间 loading**：`/health` 新增 `llm_problem` 字段，`llm_ready` 改为“真正可用”
  （缺项与占位符均为 false）。前端个股调研入口先查 `/health`（4 秒超时、60 秒缓存、页面打开即预热），
  后端未启动或未配置 LLM 时立即提示具体原因，不再进入等待；调研请求自身也加了 3 分钟上限。
  后端各 LLM 接口在密钥缺失时返回 503 + 明确文案（此前是难以定位的 `latin-1` 编码错误）。
- **`.gitignore` 补充密钥类文件**：新增 `*.key` / `*.pem` / `*.p12` / `*.pfx` / `secrets.*` / `credentials.*` /
  `*apikey*` / `*api_key*`；`.env`、`.env.*` 保持忽略，`.env.example` 模板仍可提交。
- **LLM 报错附带上游原始说明**：`_llm_error_detail()` 解析上游 `error.message` 并追加在 401 / 402 / 429 提示之后，
  可直接判断是哪家平台、什么原因（例如把腾讯云 TokenHub 的 Key 配到混元域名时，会明确提示走错平台并给出控制台地址）。
- **文档补充多平台配置对照**：README 新增 DeepSeek 官方 / 腾讯云 TokenHub（广州 · 新加坡）/ 腾讯混元的
  `LLM_BASE_URL` 与 `LLM_MODEL` 对照表，并注明 BASE_URL、KEY、MODEL 必须来自同一平台，TokenHub 还区分地域。

## [v0.2.0] - 2026-09-02

### 新增与修复

- 新增无未来函数的策略评分引擎与 1/3/5/10 数据日复盘。
- 新增分析快照持久化、备份恢复和 Agent 结构化分析快照。
- 个股调研升级为带证据 ID、来源链接、覆盖状态和事件标签的可审计响应。
- Skill 升级至 v2，修复编辑正文丢失、重复 ID、启动加载竞争和删除绑定残留。
- Markdown 改用 DOMPurify 清洗；FastAPI 增加输入校验与标准状态码。
- 启动脚本移除 `D:\ai` 硬编码，新增后端环境初始化脚本与自动化测试。

## [v0.1.0] - 2026-08-15

### 新增

- **frontend**：股票池追踪单页（Excel 导入、变动分析、连续在榜、ECharts 图表、数据管理、个股详情、个股 AI 调研弹窗）。
- **backend_fastapi**：FastAPI 个股调研后端（巨潮公告 + 机构调研纪要 + 东财新闻 → LLM → markdown），`/api/stock/research` + `/health`，12h 缓存。
- **agent_dsh**：红色小牛问答 Agent（基于 deepseek-harness），两个自定义 Tool（`getStockPoolSnapshot`、`fetchStockResearch`）、Skill 角色规则库、`/api/bull/*` REST 网关。
- 前端红色小牛 UI：悬浮按钮、聊天窗、Skill 管理（新建/编辑/删除/导入/导出）。
- 工程化：目录重构（frontend / backend_fastapi / agent_dsh / docs / examples）、统一 `.gitignore`、Git 规范与文档。
