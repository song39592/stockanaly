# bug-02 · 系统性排查并硬化「多数据源串行回退」（bug-01 的同类模式）

> 状态：**已完成**（2026-10-06）　|　类型：**潜伏缺陷类**　|　优先级：高
> 收益 ★★★★☆ / 风险 ★★☆☆☆
> 原建议位置：阶段 C 第 16 步（09）之后、第 17 步（28 features/market）之前 —— **已按此执行**

## 结论先说：排查后，必修项从 10 处缩到 3 处

立项时的清单是 AST 扫出来的**候选**（「同一函数里有 >=2 个 `_ak` 调用」）。
逐处读代码后才发现**其中一半不是串行回退**，而我的初始表格把它们的超时也**算错了**。
待办里写过「待查项必须实测确认，不许照抄估算」—— 这次自己必须先遵守。

## 实测后的完整表（2026-10-06）

单次上限用 `_uses_v8()` **实测**判定：依赖 py_mini_racer 的接口走 `AK_V8_TIMEOUT=60`，
其余走 `AK_TIMEOUT=15`。**实测 19 个接口里 6 个走 V8**，且 `stock_fund_flow_*` 全是 V8。

| # | 位置 | 结构 | 串行最坏 | 已有预算 | 调用方 | 调用方超时 | 判定 |
|---|---|---|---|---|---|---|---|
| 1 | `stock_profile.free_top_holders` | 8 报告期**串行** | 160s | 6s | `/api/chip/dist` | **30s** | 已修（bug-01） |
| 2 | `stock_profile.top_holders` | 8 报告期**串行** | 160s | 6s | `/api/stock/profile` | 90s | 已修（bug-01） |
| 3 | `download_service._universe_codes` | 2 源**串行**（各 90s） | **180s** | 无 | `/api/history/download/*` **同步路径** | 有限 | **必修 -> 已修** |
| 4 | `core/price_service.fetch_daily` | 2 源**串行**（35/30） | **65s** | 无 | `sync_bars` -> 下载/重取（同步） | 90s | 低于阈值但**已加预算** |
| 5 | `stock_profile.company_info` | 2 源**串行**（20/20） | 40s | 无 | `/api/stock/profile` | 90s | **低于阈值，不改**（仅补注释） |
| 6 | `market_service.sector_beta` | 3 源**线程** | — | **已有 35s deadline** | `/api/market/sectors` | 90s | 别人已修过 |
| 7 | `market_service._ths_market_flow` | 2 x V8 线程 | 40s（2 x join(20)） | 部分 | `/api/market/capital` | 90s | 低于阈值，不改 |
| 8 | `market_service._sw_first_sectors_hist` | 1 + N 线程（31） | 约 15s（并行） | join(45)/个 | `/api/market/sectors` | 90s | 低于阈值，不改 |
| 9 | `market_service.limit_up_ladder` | 2 源**串行**（15+15） | 30s | 无 | `/api/market/limit-up` | 90s | 低于阈值，不改 |
| 10 | `market_service.big_loss` | 2 源**串行**（15+15） | 30s | 无 | `/api/market/big-loss` | 90s | 低于阈值，不改 |
| 11 | `market_service._hist_frame` | **互斥分支**（us/hk/ff），**不在循环里** | 1x60s | — | `/api/market/global` | 90s | **不是串行回退**（AST 误报） |
| 12 | `board_service._sync_worker` | N 指标 x M 板块嵌套循环 | 理论极长 | 无 | **后台线程**，不阻塞 HTTP | — | 见下方「另发现」 |
| 13 | `strategies/data.list_universe_codes` | **读本地库**，不联网 | 0 | — | — | — | **不是数据源回退**（AST 误报） |

### 初始表格的三处错误（记下来，别重犯）

1. **`_hist_frame` 当成「3 源 x 默认 = 45~180s」** —— 实际是 `source` 的**互斥分支**，
   每次只跑一个，且**不在循环里**（唯一调用点 `market_service.py:435`）。
2. **`fetch_daily` 当成 4 源累加 130s** —— 实际北交所 / 其余是**互斥分支**，
   每次只跑 2 个源 -> 最坏 **65s**。
3. **漏判 `strategies/core/data.list_universe_codes` 与 `board_service._sync_worker` 的性质** ——
   前者读本地库（不是数据源回退），后者在后台线程（不在 HTTP 路径）。

> AST 只能回答「函数里有几个 `_ak` 调用」，**回答不了「它们会不会串行执行」**。
> 判「串行回退」必须读控制流。
## 改动

**新增公共件 `core/fallback.py`** —— `first_ok(sources, budget, per_call, is_ok)`：
- `sources` 是 `(标签, fetch)` 序列，`fetch(本次允许阻塞的秒数)`；
- **总耗时上限 = `budget`，与源数量无关**；每次实际传 `min(per_call, 剩余预算)`；
- 预算用尽立即停，取不到由调用方按**既有约定**降级（本次未改变任何降级语义）；
- 单源抛异常跳过而非崩溃；`is_ok` 可自定义（空 DataFrame 视为失败）。

接入 3 处：

| 位置 | 改前最坏 | 改后 | 预算 |
|---|---|---|---|
| `download_service._universe_codes` | 180s | **20s** | `_UNIVERSE_BUDGET` |
| `core/price_service.fetch_daily` | 65s | **45s** | `_DAILY_BUDGET` |
| `stock_profile.free_top_holders` / `top_holders` | 160s | 6s（bug-01） | `_HOLDERS_BUDGET`，**本次改为调用公共件**，删掉手写那份 |

`company_info`（#5）**只补注释不改代码**：40s < 90s 阈值，按判据不属于必修，
与其余低于阈值的项保持一致；注释写明「若以后要压冷启动时间，这里就是入口」。

## 踩坑（验证时自己踩的）

**第一版单元测试没抓住核心不变量。** 只测了「总耗时 <= budget」，
但没测「**每次调用收到的 timeout 是否递减**」—— 而这正是预算能传导到底层 `_ak` 的唯一途径。
补上第 6 项测试（断言依次收到 `[0.5, 0.5, 0.1]`）后才算覆盖完整。

## 验收

- [x] 逐行填上「串行最坏」与「调用方超时」两个实测值 ✅ 上表 13 行（含 3 处误报修正）
- [x] 串行最坏 > 调用方超时的**全部**改为带总预算，一条不留 ✅
      唯二超阈值的 `_universe_codes`(180s) 与 `fetch_daily`(65s) 均已加；
      `free_top_holders`/`top_holders` 已在 bug-01 加过，本次并入公共件
- [x] 低于阈值的逐处写明理由 ✅ #5~#10、#12、#13 全部有理由，无空白
- [x] 公共件有单元测试 ✅ 6 项：8源x20s 被 budget 兜住（2.0s / 6.0s）、
      首个源成功、顺序回退、异常跳过、空 DataFrame 判定、**timeout 递减**
- [x] 正常路径回归 ✅ 真实上游：`free_top_holders` 0.37s、`lockup_ratio` 0.36s
      （ratio 0.5450 / factor 2.1978）、`top_holders` 0.36s、`company_info` 0.38s（巨潮、有行业）、
      `_universe_codes` 10.7s 拿到 5572 个代码、`fetch_daily` 1.10s 源=腾讯 / 21 根 bars
- [x] 接口冒烟 8 条全 `ok=true` ✅ 筹码分布 0.8s、基本信息 1.0s、K线 0.3s、报价 0.4s、
      外围环境 0.4s、板块β 0.9s、下载股票池 11.2s、任务列表 0.1s
- [x] `/health` 无模块错误、stderr 无告警 ✅
- [x] 单元测试 52 个全绿 ✅ 真实 `.env` 未被改动

## 另发现，未修（需要独立决策，不适合塞进本项）

1. **`board_service._sync_worker` 的后台重建可能跑很久**：`_BOARD_SOURCES` x 每个板块两层循环，
   每层各一次 `_ak`（15s），量随数据规模走（板块约 340 个）-> 理论可达数十分钟。
   它在**后台线程**里跑（`sync_boards` 立即返回 `syncing=True`，不阻塞 HTTP），
   所以**不是超时问题**；但会导致 `_syncing` 长时间为 true、期间 `/api/stock/boards` 一直返回旧索引。
   加静态预算会**截断索引构建**（改变行为），要治得改成可中断 / 增量 / 断点续传 —— 属于设计变更。
2. **V8 全局锁让「并行」退化**：`market_service._ak` 对 V8 接口要抢 `_V8_LOCK`，
   所以 `_ths_market_flow` / `sector_beta` 里「并行发起 2 个 V8 接口」实际仍排队串行。
   `sector_beta` 靠 `join` 的 35s deadline 兜住，`_ths_market_flow` 靠逐个 `join(20)` 兜住，
   但**这是两个地方各写一遍的土办法**，没有公共件。
   -> 若要统一，需要一个「带总 deadline 的线程组 join」小工具。**本项未做**。
3. **`first_ok` 只解决「串行」**。#7 / #8 那种「多线程 + 逐个 join」的形态不在它能力范围内。

## 记录

- 2026-10-06 执行。清单是 AST 扫出来的**候选**，`待查` 项均已逐个读控制流确认，
  未照抄初始估算 —— 初始表格有 3 处错误，已在文中列出并修正。