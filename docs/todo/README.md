# 待办事项（复用/优化审计产出）

用途：一次「还能复用什么 / 结构怎么优化」的**只读审计**结果，**一项一个文件**。
你逐个把某个文件投喂给我，我就只做那一项，做完在文件里把状态改成「已完成」并回填实际改动。

## 使用方式
- 想做哪一项，就说「做 05」或直接把文件内容贴给我。
- 每项文件里都写清了：问题 / 证据（文件:行号）/ 建议做法 / 踩坑点 / 验收方式。
- **推荐执行顺序见 👉 [执行顺序.md](执行顺序.md)**（含**独立性矩阵**：每项能否单独执行/验证）。
- **收尾约定（每次改完必做）**：① 该项文件的「状态」改成「已完成」并回填实际改动；
  ② **同步更新 [执行顺序.md](执行顺序.md)**（独立性矩阵 + 阶段表都打上 ✅已完成）；③ 提交一次。

> **提交原则**：**生成物也要提交**。
> 使用环境不该「每次拉完都重新编译一遍」。所以 `股票池追踪系统.exe` 一类构建产物
> 虽非手写源码，**仍须跟踪入库**；`.gitignore` 只忽略**可重建**的东西
> （构建缓存 / 虚拟环境 / 依赖目录 / 日志 / 凭据真值）。

> **优先级：结构先行**（第二批 19~35 排在前面）。
> 原因：部分去重项会**新建文件**（01→`Palette.cs`、02→`J.cs`、06→`Cards.cs`、09→`jsonutil.py`、10→`chip_formulas/core/scr.py`），
> 目录结构没先定好的话，这些新文件会先落在旧位置、之后还要再搬一次。
> 唯一例外：**00（阻断 bug）优先级最高**，随时可先修。

---

# 一、复用优化（第一批，18 项）

| 编号 | 文件 | 领域 | 一句话 | 收益/风险 |
|---|---|---|---|---|
| **00** ✅ | [00-阻断bug-chip_rank_service-rows未定义.md](00-阻断bug-chip_rank_service-rows未定义.md) | 后端 | 周榜跑完必抛 NameError，结果永不落盘 | ★★★★★ / ★☆☆ —— **已复核：不复现，无需改码** |
| 01 ✅ | [01-前端-色彩常量集中.md](01-前端-色彩常量集中.md) | 前端 | 146 处硬编码颜色 → 一个 `C` 静态类 | ★★★★★ / ★☆☆ —— **已完成** |
| 02 | [02-前端-JSON工具收敛.md](02-前端-JSON工具收敛.md) | 前端 | 4 份逐字等价的取键函数 + 6 份转 double | ★★★★★ / ★★☆ |
| 03 | [03-前端-HTTP补齐三行.md](03-前端-HTTP补齐三行.md) | 前端 | `GetText`/`PostJson` 补 Proxy/ReadWriteTimeout/Expect100 | ★★★★ / ★☆☆ |
| 04 ✅ | [04-前端-股票名称获取合一.md](04-前端-股票名称获取合一.md) | 前端 | 个股页与估值页同一段取名逻辑抄两遍 | ★★★★ / ★☆☆ —— **已完成** |
| 05 ✅ | [05-前端-SubTab机制抽取.md](05-前端-SubTab机制抽取.md) | 前端 | 3 份近乎逐字的二级页机制 → `SubTabStrip` | ★★★★ / ★★★ —— **已完成** |
| 06 | [06-前端-KPI卡片搬家.md](06-前端-KPI卡片搬家.md) | 前端 | `AddKpi`/`VKpiRow` 住在 ValuationPage 却被 3 页调用 | ★★★ / ★☆☆ |
| 07 ✅ | [07-前端-图表基类.md](07-前端-图表基类.md) | 前端 | 双缓冲 SetStyle 4 份逐字 + 抗锯齿不统一 | ★★★ / ★★☆ —— **已完成** |
| 08 ◐ | [08-前端-小样板合集.md](08-前端-小样板合集.md) | 前端 | IsOk / ErrOf / SetErr / ToggleVisible / Debounce / FlatBtn | ★★ / ★☆☆ —— **部分完成 3/6** |
| 09 ✅ | [09-后端-json_safe公共化.md](09-后端-json_safe公共化.md) | 后端 | 唯一写对的 NaN 清洗只服务 1 个模块 | ★★★★★ / ★☆☆ —— **已完成** |
| 10 ✅ | [10-后端-scr90_series合并.md](10-后端-scr90_series合并.md) | 后端 | 2 份逐字符雷同的 SCR90 序列 + 1 个判空缺陷 | ★★★★ / ★☆（须在 00 之后）—— **已完成** | |
| 11 ✅ | [11-后端-错误体形状统一.md](11-后端-错误体形状统一.md) | 后端 | 4 种路由错误风格 → 统一 error 形状（不动状态码） | ★★★★ / ★★ —— **已完成** |
| 12 ✅ | [12-后端-阈值常量与chip_service对齐.md](12-后端-阈值常量与chip_service对齐.md) | 后端 | 三档阈值靠注释同步 → 改成代码引用 | ★★★ / ★ —— **已完成**（1 处判定不该对齐） |
| 13 | [13-后端-ParamSpec三份合并.md](13-后端-ParamSpec三份合并.md) | 后端 | 三处 `ParamSpec` 字段逐字符相同 | ★★★ / ★☆ |
| 14 ✅ | [14-后端-ensure_dirs复用.md](14-后端-ensure_dirs复用.md) | 后端 | 已 import 却自写一行 makedirs | ★★ / ☆ —— **已完成** |
| 15 ✅ | [15-后端-httpclient统一.md](15-后端-httpclient统一.md) | 后端 | raw HTTP **6 份**（原记 4 份）+ UA 四个变体 | ★★★ / ★★ —— **已完成** |
| 16 ✅ | [16-后端-market_symbol三份口径.md](16-后端-market_symbol三份口径.md) | 后端 | 3 份市场前缀规则集互相矛盾 | ★★ / ★★★ —— **已完成：评估后判定不统一**，加锁 + 注释 |
| 17 ✅ | [17-后端-kline_service出口收口.md](17-后端-kline_service出口收口.md) | 后端 | 自称唯一出口但有 4 处绕过，含自写后复权 | ★★★ / ★★★ —— **已完成**：宣传语改准 + 2 处例外登记 + 后复权/分桶各收敛为一份 |
| 18 ✅ | [18-前端-异步加载骨架.md](18-前端-异步加载骨架.md) | 前端 | 20+ 处 Task.Run+Invoke，守卫各不相同 | ★★★ / ★★★★ —— **已完成**：`AsyncKit.RunUi`，三类守卫各迁一个代表点 |

---

# 二、仓库结构优化（第二批，17 项）

> 背景：源码 / 依赖声明与锁文件 / 可重建的构建缓存 / 配置与凭据 / 日志与持久化状态，五类分开；
> 前端按页面、后端按功能；数据盘里用户配置文件放一起；新增 dumplog 与定时清理。

| 编号 | 文件 | 一句话 | 收益/风险 |
|---|---|---|---|
| 19 ✅ | [19-仓库-gitignore按五类重写.md](19-仓库-gitignore按五类重写.md) | 按源码/依赖/缓存/凭据/日志五类重写忽略规则 | ★★★★ / ★☆☆ —— **已完成** |
| 20 ✅ | [20-仓库-依赖锁文件.md](20-仓库-依赖锁文件.md) | 只有 requirements.txt，缺**锁文件** | ★★★ / ★☆☆ —— **已完成** |
| 21 ✅ | [21-数据盘-目录结构重排.md](21-数据盘-目录结构重排.md) | `config/ state/ logs/ cache/` + 用户配置归位 | ★★★★★ / ★★☆ —— **已完成** |
| 22 ✅ | [22-后端-日志落盘到data-logs.md](22-后端-日志落盘到data-logs.md) | 现在没有日志目录，`print` 到 stderr | ★★★★ / ★★☆（依赖 21）—— **已完成** |
| 23 ✅ | [23-新增-dumplog诊断包.md](23-新增-dumplog诊断包.md) | 一键打包日志+完整性+**脱敏**配置 | ★★★★ / ★☆☆（依赖 21/22）—— **已完成** |
| 24 ✅ | [24-新增-定时清理.md](24-新增-定时清理.md) | 接进**已有**的 download_service 调度循环 | ★★★ / ★★☆（依赖 21/22/23）—— **已完成** |
| 25 ✅ | [25-前端-网页按页面分子目录.md](25-前端-网页按页面分子目录.md) | 3 个 HTML + 共享 js → 按页面分目录 | ★★★ / ★★☆ —— **已完成** |
| 26 ✅ | [26-前端-启动器公共件归位.md](26-前端-启动器公共件归位.md) | GridKit/Cards/J/C 等公共件集中，别散在各页 | ★★★ / ★☆☆ —— **已完成** |
| 27 ✅ | [27-后端-core子包.md](27-后端-core子包.md) | 基础设施层先归位（后端搬家**第一步**） | ★★★★ / ★★★（依赖 19）—— **已完成** |
| 28 ✅ | [28-后端-features-market.md](28-后端-features-market.md) | 盘面/板块搬到 `features/market/` | ★★★ / ★★★（依赖 27）—— **已完成** |
| 29 ✅ | [29-后端-features-stock.md](29-后端-features-stock.md) | 个股/估值/板块索引搬到 `features/stock/` | ★★★ / ★★★（依赖 27）—— **已完成** |
| 30 ✅ | [30-后端-features-history.md](30-后端-features-history.md) | 股票池历史搬到 `features/history/` | ★★★ / ★★☆（依赖 27）—— **已完成** |
| 31 ✅ | [31-后端-features-download.md](31-后端-features-download.md) | 下载与调度搬到 `features/download/` | ★★★ / ★★★（依赖 27）—— **已完成** |
| 32 ✅ | [32-后端-features-system.md](32-后端-features-system.md) | 系统设置/完整性/修复搬到 `features/system/` | ★★ / ★★☆（依赖 27）—— **已完成** |
| 33 ✅ | [33-后端-features-mentor.md](33-后端-features-mentor.md) | 大佬策略实验室搬到 `features/mentor/` | ★★ / ★★☆（依赖 27）—— **已完成** |
| 34 ✅ | [34-后端-features-chip.md](34-后端-features-chip.md) | 筹码体系（**被依赖最多，最后搬**） | ★★★★ / ★★★★（依赖 27~33）—— **已完成** |
| 35 | [35-后端-目录改名与路径同步.md](35-后端-目录改名与路径同步.md) | 可选：`backend_fastapi` 改名 → 同步启动器/脚本/文案 | ★★ / ★★★★（**依赖全部**） |

---

## 审计时明确「不建议做」的项（记录在此，避免以后重复提出）

**复用类**
1. `indicators/data` 的 `lru_cache` 与 TTL 缓存合并 —— lru 无时间维度，不是同一类东西。
2. `board_service`（DB 持久化 TTL）与 `strategies/core/registry`（JSON 文件缓存）与进程内缓存合并 —— 跨进程语义不同。
3. `recent_trading_days` 与 `expected_weeks` 合并 —— 一个是日线日历最后 N 天，一个是周节点，语义不同。
4. 三个注册装饰器（`indicator`/`strategy`/`chip_formula`）与三个 Meta 合并 —— 字段与生命周期不同，硬合会变成一堆可选 None。
5. 各后台 daemon 线程（12+ 处）抽象 —— 进度/停止/错误处理各不相同。
6. `MktPct`（不乘 100）与其它百分比函数归一 —— 那是盘面接口的业务口径，统一会静默放大 100 倍。
7. `ChipPanel` 的流式布局与 `Stack()` 合并 —— 是有意分叉。
8. `StKpiCard` 并入 `AddKpi` —— 边框与排版方向相反，会导致 42 处视觉回归。

**结构类**
9. 移动 `agent_dsh/` —— 含 `node_modules` **32843 个文件**，且启动器按固定路径引用 dsh bin.js。
10. 把 `venv` 从 `backend_fastapi/` 挪走 —— 安装脚本与启动器的 Python 探测路径、以及安装失败文案都写死了该位置（除非与第 35 项一起做）。

## 缺陷（独立于 00~35 编号序列）

> 用 `bug-NN` 前缀而非数字编号，是为了不打乱「35 必须最后」的排序语义。
> 这类项不进阶段表，按下表的位置插入执行。

| 编号 | 文件 | 状态 | 说明 |
|---|---|---|---|
| **bug-01** | [bug-01-筹码接口偶发无响应.md](bug-01-筹码接口偶发无响应.md) | ✅ 已完成 | 报告期回退无总预算，8×20=160s 撞上启动器 30s |
| **bug-02** ✅ | [bug-02-多源串行回退硬化.md](bug-02-多源串行回退硬化.md) | bug-01 的同类模式 | **已完成** —— 实测 13 处，**必修 3 处**已修 |

**建议执行位置**：bug-02 排在**阶段 C 第 16 步（09）之后、第 17 步（28 features/market）之前** ——
它要改的 `market_service` / `price_service` / `download_service` 正是 28/29 要搬走的文件，
先修完再搬，动的文件少、回归面小。

## 已完成的项
- **16**（评估后判定**不统一**）：三份 `market_symbol` 规则集刻意保持并存。
  ⭐ 关键前提是**分歧段在业务上不可达**——真实股票池 5585 只的 2 位前缀只有
  `00/30/60/68/92`五种，三份在这五种上完全一致，**全量 0 处分歧**；
  出现分歧的段（沪市可转债 `11`、B 股 `900`/`200`、北交所白名单外新号段）
  都不在池子里。实测两个分歧段代码（`110059`/`900901`）在 0.1s 内以 `ok=false`
  结束 —— **不是取到了错市场的数据，而是压根没走到判市场那一步**。
  ⭐ 待办原文低估了分歧范围（只说北交所，实测 70 个 2 位段两两不一致），
  而且**没有一份在所有类别上都对**，所以「以谁为准」没有唯一答案——
  这正是不能取并集的原因。
  ⭐ 唯一真实未来风险：`valuation._market_prefix` 是三份里唯一用
  「北交所白名单 + **兜底 sh**」的，北交所开新号段时它会判错
  （另两份判 bj）→ 个股页估值空白。真要修应改**它**的兜底值，不是动另两份。
  落地：`test_market_symbol.py` 5 个用例（含**全量池一致性**，
  将来池里若纳入可转债/B股/北交所新段会先失败并提示重新评估）
  + 三处 docstring 写明「为何不统一、改时该改哪份」。
- **12 + 14**（同文件同批提交）：`rank_service` 的阈值常量与内联 `makedirs` 清理。
  `DEFAULT_WEEKS`/`DEFAULT_LAUNCH` 改为**直接引用** `scr_service`（改上游会联动，实测验证）。
  ⭐ **`DEFAULT_CHG_DAYS` 判定「不该对齐」**——待办建议在 `scr_service` 补一个
  `CHG_DAYS_DEFAULT` 再引用，**照做会造一个骗人的常量**：本侧的 30 是**交易日窗口**
  （`s.iloc[-1-days]` 取交易日序列），`scr_service` 的 `chg30` 是**导出文件的列名**
  「30日涨幅%」（行情软件的口径，本项目无从控制，且它根本没有「天数」这个参数）。
  数值同为 30 纯属巧合。该统一的「涨幅阈值百分比」已由 `DEFAULT_LAUNCH` 接管。
  按待办建议只做复用、不改名（`_ensure_dirs` 改公开名会动6 个调用点，留给后续）。
  ⚠️ **本项实测教训（比改动更重要）**：验证「删掉 processed 能否自动重建」时，
  我的脚本**真删了生产数据盘**的 `<data>/chip/processed/`，清空了用户的周榜缓存 ——
  这正是 `bug-04`（测试夹具污染生产数据库）同形态的错误，自己犯了。已触发
  `force=true` 重算恢复。正确做法应是把 `DATA_DIR` 指到临时目录
  （`test_support.require_data_isolation` 就是干这个的）。
  **教训：验证「目录能自动重建」时，先问「删的是谁的数据」。**
- **11** 路由错误体形状统一：新增 `apiutil.py`（`ok/fail/soft_fail/guard/http_error/install`），
  形状统一为 `{"ok":false,"detail":…,"error":{"code","message"}}`。
  ⭐ 主力杠杆是**全局异常处理器**—— 全仓 90 余处 `raise HTTPException(detail=…)`
  **一行未改**即生效（原文档只列了 4~5 个路由文件，实际遍布 5 个 features 包）。
  ⭐ `detail` 必须保留：前端 8 处直接读它，它是**向后兼容锚点**，不是冗余字段。
  ⭐ 必须注册 `starlette.exceptions.HTTPException`（**不是** fastapi 那个）——
  404/405 抛的是 starlette 版，只注册 fastapi 的会漏出去又是裸 `{"detail":"Not Found"}`。
  ⭐ 真实踩坑：`raise apiutil.fail(...)` 是错的，`fail()` 返回 JSONResponse 不是 Exception，
  raise 它会回 `500 Internal Server Error`（比改前更糟）；正确是 `return apiutil.fail(...)`。
  ⭐ 前端必须同步适配：`J.Str()` 拿到 Dictionary 会输出**类型名垃圾串**，所以新增
  `J.ErrMsg()` 先判类型再取值，兼容三种形状；`ChipRankPage` 那处改前读字符串改后读对象，
  不改就会显示垃圾。`guard` 的 `code` 支持 str/dict/None，dict 用于保住 `UNKNOWN_STRATEGY`
  这类更具体的码（用默认会降级成通用 `UNKNOWN_KEY`）。
  验证：8 类失败全部带 `ok:false` + `error{code,message}`；成功路径 5 条响应体零变化；
  `csc` exit 0；52 个单元测试全绿。
- **10** SCR90 序列计算收成单一内核 `features/chip/formulas/core/scr.py`：
  周榜 / 策略 / 副图 / 单帧统计**四处共用**（原先 2 份逐字符雷同 + 2 份各自实现），
  函数体净删约 45 行。`scr_series` 用 NaN 语义（序列）、`scr_frame` 用 None 语义（JSON）——
  **空值语义差异是刻意保留的**。顺手修掉判空缺陷 `if p5 and p95` → `den == 0`
  （分位价格**可以是 0**，旧写法会把「分位恰为 0」误判成空筹码），并用对照实验量化影响。
- **34** 筹码体系归位 `features/chip/`（**阶段 C 收官**）：`scr_service` / `rank_service` /
  `scr_routes` / `dist_routes` / `rank_routes` + **整包** `formulas/`（含 ARCHITECTURE.md）。
  5 个模块**改名**以避免三个 routes 撞名，**转发名保持原样**故 `main.py` 的字符串引用零改动。
  ⭐ **发现「转发做不到的事」**：`sys.modules` 别名转发能覆盖「import 后用公开名」，
  但**覆盖不了「按包名动态导入子模块」**。`formulas/core/registry.py` 原本硬编码
  `_FORMULA_PACKAGE = "chip_formulas"` 供 `importlib.import_module` 拼子模块名 ——
  硬编码在搬家后会 import 一个不存在的命名空间，而靠转发会让**同一文件被当两个模块加载两次**。
  改为 `__package__.rsplit(".", 1)[0]` **自动推导**，以后再搬也不必手工同步。
  验证用**加权校验和**（`pct.hash` 等 13 个统计量）而非抽样，避免「形状对、数值错」漏网。
- **33** 大佬策略实验室归位 `features/mentor/`（**4 个**模块）：**函数体零改动**（20 行 import 差异）。
  ⭐ **文档漏列了 `store.py`**（`mentor_store`，321 行，被 `main.py:30/:175`、`mentor_routes`、
  `test_mentor_store` 依赖）—— 按 4 模块搬，否则顶层会留一个孤立文件。
  ⭐ **`collectors` 与 `llm_client` 是跨功能共享的**（个股页/股票池历史/盘面页都用），
  3 个**已搬**文件共 4 处引用一并改到规范位置 —— 清掉了「新架构内部反向依赖顶层转发」。
  ⭐ **原文档踩坑点 1 写错了位置**：`_ai_cache`/`AI_CACHE_TTL=600` 在 `features/market/routes.py`
  （盘面页），**不在** `mentor_routes`；实验室页根本没有 AI 缓存。
  验收里「`force` 绕过缓存」改为**对第 28 项做回归**（0.0s 命中 / 54.8s 绕过，语义钉死）。
- **32** 系统设置/完整性/修复归位 `features/system/`（`routes` / `integrity` / `repair`）：
  **函数体零改动**（difflib 逐行核对：18 行差异全是 import）。
  ⭐ `repair_digests.py` 是**独立 CLI**（全仓无人 import），别名转发**额外带 `__main__` 守卫**，
  保住了它 docstring 里记载的 `python repair_digests.py [--apply]` 那条用法 ——
  否则只做转发会让已写进文档的调用方式**静默失效**。
  另排查出 `integrity` 一处**既有**误报（`-wal` mtime 与 `_meta` 记录的时效敏感点，
  它自己 docstring 就有记载），已用 `git stash` 回到搬家前复现，证明**与本项无关**。
- **31** 下载与调度归位 `features/download/`（`service` / `store` / `routes`）：**函数体零改动**，
  `git diff` 只有 9 行 import。全仓最有状态的一块（自带调度线程 + 任务引擎 + 落库断点续跑）。
  ⭐ 踩坑点 1「模块级 `_start_background()`」用**两条独立证据**验住：
  ① 转发链导入后进程内出现 `Thread-1 (_scheduler_loop)` 线程；
  ② 把 `last_cleanup_date` 清空再起后端，**85 秒内被写回今天** —— 不只是线程在，
  而是真的跑完一轮并触发了定时清理（第 24 项链路一并证通）。
  另**先查后搬**确认三个文件无任何 `__file__` 路径推导（第 27 项 `PROG_DIR` 踩过的坑），
  落库路径落在数据盘、搬家前后一致。
- **30** 股票池历史归位 `features/history/`（`service` / `store` / `routes`）：只改 import，函数体未动。
  ⭐ 用**源码断言**验住了踩坑点 1 —— `load_trusted_bars` 仍直连 `price_store.load_bars`、
  仍捕获 `UntrustedDataError`、**没有**被改成走 `kline_service.get_bars`，
  「脏数据自动重抓」能力完整保留。顶层 3 个别名转发，`main.py` 的字符串引用照旧可用。
- **29** 个股/估值/板块归位 `features/stock/`（`profile` / `routes` / `valuation` / `board_index`）：
  只改因搬家失效的 import，**函数体一行未动**（TTL 缓存、`_cached` 与 `lockup_ratio` 的矛盾语义、
  三份 market 前缀规则、board_index 的 DB 持久化 TTL 全部原样，留给各自的后续项）。
  顶层 4 个**别名转发**——除私有名外，`stock_profile._CACHE` 也必须是**同一份**，否则 `clear_cache()` 清不到。
- **28** 盘面/板块归位 `features/market/`：`service.py` 是**纯 rename**（内容零改动，
  TTL 缓存与盘后延长原样保留）、`routes.py` 只改 1 行 import。顶层留**别名转发**，
  7 处 `from market_service import _ak` 与 `main.py` 的字符串 `"market_routes"` **一行未改**。
  **HTTP 路径不变**（`prefix` 仍是 `/api/market`），启动器与网页端无需改动。
- **bug-02** 多源串行回退硬化：新增 `core/fallback.py` 的 `first_ok`（总时间预算）。逐处读控制流实测 13 处 —— **必修从 10 处缩到 3 处**：`_universe_codes`(180s) / `fetch_daily`(65s) 已加预算；`sector_beta` 早已有 35s deadline；另有 3 处是 AST 误报（互斥分支 / 读本地库）。
- **09** `_json_safe` 公共化 → `core/jsonutil.py`。**关键发现**：原 `_json_safe` 对 dict/list 是
  **恒等映射**，照待办原样在出口清洗等于**空操作**（`out is body` 为 True），故新增递归版
  `json_safe_deep`；7 个出口接入（估值 / 报价 / 盘面 5 个）。另统一 2 处语义等价的半成品，
  并说明 2 处**刻意不统一**的理由（`stats._num` 带 rounding；`chip_service` 4 处是输入守卫）。
- **bug-01** 筹码分布偶发「筹码接口无响应」：`stock_profile` 往回试 8 个报告期、
  每期 `_ak(timeout=20)` **串行累加最坏 160s**，而启动器只等 30s → 判「无响应」。
  新增 `_walk_report_periods` 给遍历加**总时间预算**（6s），卡死场景 160s → 6.0s，
  并按既有约定回退「不做锁仓修正」。同形态的 `top_holders` 一并修掉。
  编号用 `bug-` 前缀而非 00~35，是为避免打乱「35 必须最后」的排序语义。
- **19** `.gitignore` 按五类重写（源码 / 依赖 / 构建缓存 / 配置凭据 / 日志状态）；
  并确立**提交原则**：生成物也要提交（exe 属运行必需产物，必须入库），只忽略可重建项。
- **15** raw HTTP 收敛为 `core/httpclient.py`：**6 处**调用点接入（待办只记了 4 处），
  UA 从 4 个变体（120 / 120.0.0.0 / 124.0.0 ×2）统一为 1 份，进程内共用 Session。
  `retries` 默认 1（不重试）以免放大页面耗时，只有盘面那处显式 `retries=3`；
  `post` 的 `timeout` 必填无默认值 —— LLM 是分钟级语义，不能共用行情默认值。
- **27** 后端 `core/` 子包（**搬家的第一步，转发模式已验证**）：14 个基础设施模块 `git mv` 进
  `core/`（比清单多收 `crypto`），内部 17 处改相对导入；旧路径留 **14 个 `sys.modules` 别名式转发**
  （不是 `import *` —— 那会让 `patch.object` 静默失效，曾致 26 个测试失败并把临时路径写进真实 `.env`）。
  `config.py` 新增 `PROG_DIR` 收敛「多退一级」，`BASE_DIR` 与 `ENV_PATH` 都由它派生。
- **05** 二级页机制收成 `SubTabStrip.cs` 控件：个股 / 盘面 / 策略三份 Add + Select + 换肤循环
  与标签栏搭建合并为 1 套，三页各持一个实例（5 字段组 ×3 → 1 字段 ×3）。
  两处懒加载（盘面 AI 子页、个股 K 线焦点）靠 `OnSelected` 钩子保住；
  顺带**修掉策略页换肤漏挂**的 bug；策略页按钮紧凑形态与容器滚动条两处差异用开关保留。
- **04** 股票名称获取合一：新增 `Http.FetchStockName(code, onOk, onFail)` + `PriceSuffix`，
  `StockLoadName` / `ValuationLookupName` 各由 32 行降到 15 行。竞态守卫、失败兜底文案、
  个股页 `StockAddHistory` 副作用三处差异**全部留在调用方**。
- **08** 小样板合集（**3/6 类**）：`J.IsOk`(12 处) + `J.IsFailed`(3 处，取反语义**刻意不合并**)、
  `UiKit.SetErr`(20 处严格同构形态)、`UiKit.Debounce`(2 处)。
  **另 3 类判定不该做**：`ErrOf`（前提被 02 项改变，抽取会改变行为）、
  `ToggleVisible`（收益低、控件类型不同）、`FlatBtn`（3 处形态确实不同，且第 05 项会一并吃掉）。
- **07** 图表基类：新建 `launcher/ChartKit.cs`（`ChartControl : Control` 基类），4 个自绘控件改为继承它。
  `SetStyle` 4 份 → 1、`GetPreferredSize` 3 份 → 1、抗锯齿**5 个表面全开**（原先 3 个没开）、
  `StringFormat` 全部改用基类缓存（`StockPage` 每帧 new 5 处 → 0）。`StEquityPaint` 是
  PictureBox 事件处理器、进不了基类，手工设一次。价格轴协作未动。
- **03** HTTP 补齐：新建 `launcher/Http.cs`（`Probe`×3 / `GetText` / `PostJson` 原样搬入），
  并补齐 `GetText` 的 `ReadWriteTimeout`（**真 bug**：大响应体读取阶段原本无超时保护）、
  `Proxy = null`、`Expect100Continue = false`；`PostJson` 补后两项。
  **未统一「抛异常 vs 返回 bool」**（改动面 20 余处，需单列评估）。
- **06** KPI 卡片搬家：新建 `launcher/Cards.cs`，`AddKpi` / `VKpiRow` 从 ValuationPage 原样搬入
  （零行为改动、调用点一行未改，调用数 24 / 7 与搬家前一致）。`StKpiCard`/`StCardRow`/`MktRow`
  **刻意不并入**（有边框 / 排版相反 / 形态不同，合并就是视觉回归）。
- **02** JSON 工具收敛：新建 `launcher/J.cs`（20 个方法），**删除 27 个重复定义、
  替换 829 处调用点**。三处「故意不合并」保留：`NumOrNull` 的 null 语义、`PctRaw` 不乘 100
  （盘面已是百分数）、`VNum` 的 decimal 分支。
- **01** 色彩常量集中：新建 `launcher/Palette.cs`（`internal static class C`，31 个常量），
  硬编码 `Color.FromArgb` **171 → 37 处**。**同语义多套值刻意未合并**（合并 = 改设计），
  也未纳入 `BuildPalette()` 换肤。
- **26** 启动器公共件归位：新建 `launcher/UiKit.cs`，从骨架文件搬出 11 个页面骨架/控件工厂方法
  （`NewPage/Stack/Group/Row/AddRow/MiniBtn/Lbl/Mute/Dot/Check`）；`StockPoolLauncher.cs` 2778→2606 行。
  `Palette/J/Http/Cards` **故意不建空壳**（分别是第 01/02/03/06 项的落点）。
  另：把只存在于 `refactor/ui-grid-kit` 分支的 `GridKit.cs` 合并进本分支（按「所有改动一个分支」）。
- **25** 网页按页面分目录：`frontend/{home,mentor-lab,chip-scr}/index.html` + `shared/`；
  同步改启动器 4 处路径、`.gitignore`、`启动系统.bat`、`package.json` 测试 glob。exe 已重编。
- **24** 清理：新增 `cleanup.py`（**零业务依赖**，后端起不来也能跑）+ `清理日志与缓存.bat`
  手动入口；定时只是 `download_service._scheduler_tick()` 里每天调一次 `schedule_tick()`。
  保留天数统一 7 天；`bars/*.db.bak`(1.02 GB) 默认不动，仅 `--bak` 显式清。
- **23** dumplog 诊断包：新增 `dumplog.py` + `POST /api/system/dump`，一键导出
  `<data>/logs/dump-<时间戳>.zip`（日志 / 系统 / 完整性 / 脱敏配置 / 依赖 / 周榜）；
  打包前做密钥扫描兜底，命中即拒绝生成整个包。启动器按钮等第 26/35 项编译 exe 时再加。
- **22** 日志落盘：新增 `logutil.py`（stderr 双写 + `<data>/logs/backend.log` 按天轮转保留 7 天）；
  `main.py` 11 处 `print` 换成 logger，异常堆栈随之进文件；启动器零改动（旧路径兼容）。
- **21** 数据盘按用途分区（`config/ state/ logs/ cache/`，`bars/` 留在根）+ 一次性非破坏自动迁移；
  顺带修掉 `mentor_store` 把库写在**项目内**、导致大佬实验室数据被孤立的 bug。
- **20** 补依赖锁文件 `backend_fastapi/requirements.lock.txt`（venv 内 `pip freeze`，44 个包精确版本）；
  `requirements.txt` 顶部加「声明 vs 锁定」注释。未动安装脚本（避免死代码）。
- **00** 阻断 bug（`rows` 未定义）—— **复核后确认已不复现**：工作区与 HEAD 均无该变量
  （随「离榜改口径」那次 `_worker()` 重构一并消失）。40 只样本实测 `state=ready`、
  结果文件正常落盘，`result()` 返回 `ok=True`。**未改动代码**。
- 表格显示收敛 → `launcher/GridKit.cs`（`StockGrid` + `GridColumn`），13 张表统一，删除 9 份重复实现。
- 修复 `BackgroundColor = Color.Transparent`（DataGridView 不支持透明背景，构造期抛异常导致「打不开」）。
- 修复 SCR90 周榜名称列全空（名称改计算前取 + 与计算解耦，可单独补齐）。
