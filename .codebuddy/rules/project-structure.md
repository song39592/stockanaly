---
description: 目录结构权威地图：四个部件、backend 分层与路由前缀、单一来源清单、改动同步矩阵、launcher 与指标层约定
alwaysApply: true
enabled: true
---

# 文件结构与构建清单

> 本文是**目录与权威位置的唯一来源**：读完本文件即可定位代码，不必扫仓库。
> 展开细节（表结构 / 接口清单 / 口径推导）**不在这里**，只给指针。
> 纪律类内容各自成篇：新增指标 `add-indicator-strategy.md`、可移植性 `cross-device-portability.md`、
> Git `git-safety.md`、启动器交互 `launcher-ux.md`、脚本写法 `powershell-authoring.md`、
> 动手前评估 `engineering-discipline.md`、文档同步 `docs-sync.md`。

## 一、全景：四个部件

| 部件 | 位置 | 端口 | 入口文件 |
|---|---|---|---|
| 原生启动器 | `launcher/*.cs` → 仓库根 `stockpool.exe`（C# WinForms，`csc.exe` 离线编译） | — | `launcher/StockPoolLauncher.cs`（`partial class MainForm`） |
| 后端 | `backend_fastapi/`（FastAPI + akshare + SQLite） | 8000 | `backend_fastapi/main.py` |
| 前端 | `frontend/`（原生 HTML/JS + CDN） | file:// | `frontend/home/index.html` |
| AI Agent | `agent_dsh/`（Node 22 + `@deepseek-ai/dsh`） | 3080 | `agent_dsh/plugins/bull-http.mjs` |

启动器负责拉起后端与 Agent、状态灯、托盘常驻、退出时收尾子进程。
**前端不直接调大模型**：LLM 调用一律由后端（:8000）或 dsh（:3080）托管。


## 二、目录地图

```
launcher/              32 个 .cs → 一个 exe（stockpool.exe）
  StockPoolLauncher.cs   主窗体 partial class MainForm + Program / ServiceItem / WebWindow / Win32
  StockPage*.cs          个股页及 6 个分部（Kline/Profile/Records/Valuation/Saolei/AI/Overlays）
  MarketPage / ValuationPage / DownloadPage / MentorLabPage / RedBullPage / RpsPoolPage
  ChipPanel / ChipRankPage / IndicatorIndexPage / StrategyPage
  公共件：Http.cs / J.cs / Palette.cs / AsyncKit.cs / UiKit.cs / Cards.cs / GridKit.cs / ChartKit.cs
          KLineChart.cs / BootOverlay.cs / SubTabStrip.cs / QuickSearch.cs / UserSettings.cs
  build_exe.bat（显式源文件清单，无通配符）/ install_env.ps1（带 # INTEGRITY 标记）
  test-launcher-conventions.ps1（静态约定自检，退出码 0 = 通过）

backend_fastapi/
  main.py              入口：单实例锁 → 迁移/建表/指纹/签名 → 容错挂载 11 组路由 → /health
  apiutil.py           统一错误体 {ok, detail, error:{code,message}}（全仓生效）
  indicators_routes.py / strategies_routes.py（顶层，未搬）
  core/                21 个基础设施模块 —— 依赖图底部，不依赖任何业务包
  features/            7 个业务子包：chip/download/history/market/mentor/stock/system
                       子包内按 routes.py（HTTP）/ service.py（业务）/ store.py（持久化）分层
  indicators/          插件包 @indicator —— 扫目录自动注册，**id 与文件名无关**
  strategies/          插件包 @strategy  —— **id 必须 == 文件名**
  chipformulas/        插件包 @chip_formula —— **id 必须 == 文件名**
  formulas/            中立纯计算内核（无注册）—— 依赖方向恒为「指标 → formulas ← 策略」
  <39 个顶层转发壳>      旧 import 路径的别名（sys.modules[__name__] = _impl），**不是实现**

frontend/              home/（股票池主页面）、chip-scr/（SCR 选股）、mentor-lab/（实验室）
                       shared/analysis-engine.js（评分与回测引擎）、shared/theme.js（主题引擎）
                       shared/stock-handoff.js（网页 → 原生的个股详情移交）
                       ⚠ 网页与原生窗口**无命令通道**：个股详情只有原生页，网页点代码只能
                         「复制代码 + 提示去启动器个股分析」，别再写死网页链接
agent_dsh/             plugins/（bull-http 网关 + bull-store 共享状态 + 2 个 Tool + role-skills）
                       skills_library/<skillId>.skill、cordis.patch.yml（模板）/ .runtime.yml（产物）
scripts/               check-docs.ps1 —— 文档一致性自检（链接 / 路径 / API 前缀 / 规则区预算）
```

**转发壳**：`config.py` / `db.py` / `price_store.py` … 共 39 个顶层文件是
`core/` 与 `features/` 的别名。用 `sys.modules` 别名而非 `import *` 是必须的——
`market_service._ak` 是私有名（`import *` 不导出会 AttributeError），
且 `patch.object(config, ...)` 打在副本上会**补丁静默失效**。

## 三、路由前缀速查（后端对外 HTTP）

| 前缀 | 业务 |
|---|---|
| `/api/indicators` | 技术指标（清单 / compute / batch / diagnose） |
| `/api/strategies` | 策略回测（清单 / 校验 / 推荐 / 回测 / 报告 / 成交） |
| `/api/history` | 股票池历史与 K 线 |
| `/api/history/download` | 历史数据下载（任务 / 队列 / 通达信） |
| `/api/chip/scr` | SCR 选股 |
| `/api/chip/rank` | SCR90 周榜 |
| `/api/chip/dist` | 筹码分布（筹码峰） |
| `/api/market` | 盘面及板块（global / capital / sectors / limit-up / big-loss） |
| `/api/mentor` | 大佬策略实验室 |
| `/api/stock` | 个股调研 / 估值 / 档案 / 板块 |
| `/api/system` | 数据目录 / 完整性 / 诊断包 |

路由文件：前两个在顶层，其余在 `features/<子包>/routes.py`；`/api/bull` 由 `agent_dsh/plugins/bull-http.mjs` 提供。

> ⚠️ `/api/system/storage` 的前缀**一字都不能改** —— Windows 启动器直接探这个地址
> （`launcher/StockPoolLauncher.cs` 的 `RefreshDataDir` / `SaveDataDir`）。

## 四、单一来源清单（改这里，别处自动生效）

| 事实 | 唯一权威 | 其它引用点 |
|---|---|---|
| 日线标准列 + 复权缩放 | `core/bars_schema.py` | `indicators/data.py`、`strategies/core/data.py` |
| 三套注册的机械骨架 | `core/registry_scan.py` | 三个插件包各自的 `registry.py` |
| `ParamSpec` 定义 | `core/paramspec.py` | 三个插件包的 `base.py` |
| 行情取数 + 周期合样 | `core/kline_service.py` | `indicators/data.py`、`strategies/core/data.py` |
| 日→周/月 合样 | `core/periods.py` | `core/kline_service.py`、`indicators/rps_data.py` |
| 行情存储与复权现算 | `core/price_store.py` | `core/price_service.py`、`core/kline_service.py` |
| 通达信本地日线读取 | `core/tdx_reader.py` | `core/price_service.py`、`features/download/service.py` |
| 数据目录解析 / 迁移 | `core/config.py` / `core/storage.py` | `main.py`、`features/system/routes.py` |
| 完整性签名 + 密钥密封 | `core/crypto.py` + `core/dpapi.py` | `core/db.py`、`features/system/integrity.py` |
| 日志 | `core/logutil.py` | 全仓 |
| 指标 / 策略共用的纯计算 | `formulas/` | `indicators/*`、`strategies/*` |
| 指标数值基准 | `indicators/_golden.json` | `test_indicators_golden.py` |
| 数据库表结构 | `docs/database.md` | 建表代码在 `core/db.py` / `core/price_store.py` |
| 用户本地设置 | `launcher/UserSettings.cs` | `launcher/StockPoolLauncher.cs` |
| 语义色调色板 | `launcher/Palette.cs` | `launcher/KLineChart.cs` 等 |
| 前端评分 / 回测引擎 | `frontend/shared/analysis-engine.js` | `frontend/home/`、`frontend/mentor-lab/` |

## 五、改动同步矩阵（改 X 必同步 Y）

完整矩阵在 `docs-sync.md` 第四节「触发矩阵」。最容易踩的三条：

| 你改了什么 | 必须同步 | 漏了会怎样 |
|---|---|---|
| `launcher\*.cs`（新增/删除顶层文件） | `build_exe.bat` 清单 + 重跑生成 `stockpool.exe` | 新类不进 exe（「改了没生效」） |
| 后端接口的**字段 / 返回结构** | `launcher/*.cs` 的消费端 | 前端读到旧字段**静默降级**（不报错，最难发现） |
| 指标 / 策略 / 筹码公式的**计算逻辑** | `python -m indicators.golden --update` + `_golden.json` 入库 | `test_indicators_golden` 失败 |

## 六、launcher\：一个主类一个文件 + 同步 build_exe.bat

- 主窗体是分部的（`StockPoolLauncher.cs` + `StockPage.*.cs` 等）；**新抽出来的控件 / 面板类放独立顶层文件**
  （如 `launcher\ChipPanel.cs`、`launcher\KLineChart.cs`），不要塞回 `MainForm` 里当嵌套类。
- 嵌套类只在「外部完全用不到」时保留；一旦别的顶层类要用，就必须提升为顶层 `internal` 类型，
  并**一起提升它的基类**（如 `ChartControl`）与依赖的其它嵌套类型（如 `KBar` / `KMark`），
  否则编译期报 CS0246（类型找不到）。顶层类放 `namespace StockPool` 下、标 `internal`。
- **新增或删除顶层 .cs 后必须同步改 `build_exe.bat`** —— 它是**显式源文件清单**（不是通配符），
  否则新类不会进 exe（表现为「改了没生效」）。改完重跑 `build_exe.bat` 重新生成 `stockpool.exe`；
  **发布版 exe 要入库**（见 git-safety 规则）。

## 七、单一来源模块的坑（对应第四节的行）

- `core/bars_schema.py` —— ⚠️ 新增量纲为「价」的列（如成交额）时，必须同时加进 `PRICE_SCALED_COLUMNS`。
- `core/registry_scan.py` —— **判据各包自理**（指标的 id 与文件名无关、策略与公式必须相同），
  机械动作共用这一份；新增第四套注册机制时复用它，不要照抄。
- `core/tdx_reader.py` —— ⚠️ `detect()` 必须保持**轻量**（秒级）：它被「目录是否可用」的热路径反复调用
  （`_tdx_is_valid`、设置保存校验）。要全量覆盖度统计请走 `coverage_stats()`
  （并发 8 线程全扫、结果按目录缓存 5 分钟），**不要**把全扫塞进 `detect()`。
  ⚠️ 覆盖度里的「最新日」以**众数**（`last_mode`）为准，**不用**最大日期（`latest`）：
  通达信刚下载完日线时常见「只有 1 只更新到今天、绝大多数停在昨天」，
  按 max 显示会变成「最新 11-10（0.0% 的股票到这一天，5588 只更早）」—— 既难看又完全误导。
  「本机比库里落后几个交易日」的缺口判定**同样**要按众数比，否则会误报「没有缺口」。
- `formulas/` —— ⚠️ 不要把共用内核塞进 `chipformulas/core/`（那是筹码语义）——
  那会让「非筹码的东西住在筹码包里」变成默认惯例，下一个新增的人照抄。
- `indicators/_golden.json` —— 改了指标计算逻辑或新增指标后**必须** `python -m indicators.golden --update`，
  否则 `test_indicators_golden` 失败；基准文件入库。

## 八、backend_fastapi\indicators\：跨层上下文必须显式

- 指标函数只允许从**签名**拿到「按代码 / 周期再取数」这类上下文：
  `def xxx(df, *, ctx=None, ...)`，框架经 `context.CalcContext` 注入。
- **禁止**再用 `df.attrs["code"]` 这类藏在数据里的通道 —— 签名里看不见 = 契约是假的，
  单元测试还得伪造 attrs 才能跑。
- `ctx` 走 `base.INJECTED_PARAMS` 白名单、必须是**关键字参数**且有默认值、不进用户参数表（ParamSpec）；
  `ctx=None` 表示「没有上下文」（合成数据自检），需要 code 的指标据此抛「取不到股票代码」，
  由 `scan` 判为需外部数据跳过。详见 `indicators\ARCHITECTURE.md` §5。
- 颜色 / 线宽由 `@indicator(styles=...)` 声明并随计算结果下发（`series[].style`），
  **新增指标不必改前端**；前端保留硬编码规则作兼容回退，用户覆盖过的语义色优先。
- ⚠️ `indicators/` 下的 .py 会被 `__init__.py` **自动 import**：非指标模块（如 `golden.py`）
  必须列进 `_EXCLUDE`，否则会在包加载时顺手执行一遍。

## 九、文档分工（谁说了算）

同一事实只允许有**一处**权威表述，其余位置只写指针。「哪份文档说了算 / 何时同步」
见 `docs-sync.md` 第二节与第四节。
