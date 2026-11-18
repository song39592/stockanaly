---
description: 后端约定：分层与依赖方向（硬约束）、插件包注册契约、口径的唯一实现处、改后端后的验证纪律
alwaysApply: true
enabled: true
---

# 后端约定

> 展开细节在各包的 `ARCHITECTURE.md`（**权威**），本文只留纪律与指针。

## 一、分层与依赖方向（硬约束）

- `core/`（基础设施，依赖图底部，不依赖任何业务包）→ `features/`（业务子包）
  → 插件包（`indicators/` `strategies/` `chipformulas/`）→ 顶层路由壳。
- 依赖**单向**：底层不 import 上层。跨层要用的东西下沉到 `core/`；
  共用的纯计算放**中立包** `formulas/`（依赖方向恒为「指标 → formulas ← 策略」）。
- ⚠️ **不要把共用内核塞进语义专属包**（如 `chipformulas/core/`）——
  那会让「非筹码的东西住在筹码包里」变成默认惯例，下一个新增的人照抄。
- ⚠️ 顶层那些转发壳（`config.py` / `db.py` / `price_store.py` …）是**旧 import 路径的别名**，
  **不是实现**：用 `sys.modules` 别名而非 `import *` 是必须的
  （私有名不会被 `import *` 导出，且 `patch.object` 打在副本上会**补丁静默失效**）。

## 二、插件包（指标 / 策略 / 筹码公式）

- 三套注册共用 `core/registry_scan.py` 的机械骨架（遍历 / 强制重导入 / 单注册校验 /
  报告组装 / 缓存）；**判据各包自理**（指标的 id 与文件名无关，策略与筹码公式必须相同）。
  新增第四套注册机制时**复用它**，不要照抄一遍。
- 详细契约见各包文档（**权威**）：
  - 指标 → `backend_fastapi/indicators/ARCHITECTURE.md`（**§8 新增指标步骤**是权威清单）
  - 策略 → `backend_fastapi/strategies/ARCHITECTURE.md`
  - 筹码 → `backend_fastapi/chipformulas/ARCHITECTURE.md`
- 新增 / 修改指标的计算逻辑后**必做**：`python -m indicators.golden --update`，
  `indicators/_golden.json` 入库；否则 `test_indicators_golden` 直接失败。

## 三、口径的唯一实现处

同一口径只允许**一份实现**；有副本 = 将来两边算出两个数、两边都不报错。

| 口径 | 唯一权威 | 备注 |
|---|---|---|
| 范围口径（板块 / ST / 上市日 / 市值 / 判定基准日） | `strategies/core/universe.py` | spec 构造走 `universe.spec_from_request`；改口径的允许改动面 = 该文件（+ 需要新取数能力时的 `strategies/core/data.py`），前端不用动；报告也不用手改（口径版本自动带，见下） |
| 指标 / 策略共用的纯计算 | `formulas/` | 禁止在策略里把指标算法重写一遍 |
| 日线标准列 / 复权缩放 | `core/bars_schema.py` | 新增量纲为「价」的列必须同时进 `PRICE_SCALED_COLUMNS` |
| 涨跌停 / 涨停开盘应对（三档） | `core/limit_price.py` | 回测范围页选、**全局生效**；撤单（策略主动放弃）与一字板买不进（物理约束）**分开归因**，禁止在策略层另写一份 |
| 剔除是否真的生效（名单可用性） | `strategies/core/universe.py` | 勾了剔除但外部数据取不到 → **中止**（见下），并给 `pool_signature` 池子结果指纹 |

**自检**：`cd backend_fastapi && python -m unittest test_conventions`
（阈值唯一 / spec 唯一入口 / 分层不反向依赖 / 池子入口唯一）。

### 口径与算法改了 → bump 版本（否则报告没法比）

口径改一次（阈值 / 剔除特征 / 判定基准日），**同一区间、同一策略的结果能差 10~30 倍**
（实测同区间 3839 笔 → 174645% 与 3418 笔 → 5569%）。报告不记版本 → 两份报告
摆在一起无法判断能不能比，只能靠猜。

- 统计口径 → 手工 bump `universe.SPEC_VERSION`；`universe.spec_signature()` 自动算指纹
  （防「改了参数却忘了 bump」）。两者随报告落盘在 `scope.exclusion`。
- 策略算法 / 参数默认值 → bump 该策略的 `version`（`@strategy(version=...)`），
  落盘在 `strategy.version`。
- **两份报告口径或策略版本不同 → 不可直接对比**，先对齐再说数字。

### 口径项依赖外部数据 → **取不到就中止**，不许静默降级

「取不到」= **不知道**，**不等于**「没有」：

- **中止**并给可操作的下一步（恢复网络 / 取消该剔除项后重跑），绝不降级成
  「一只都没剔」—— 降级后报告的「剔除明细」只是**没有那一项**，看起来像
  「本来就没有 ST」，口径指纹也算不出差别。
- 佐例（实测）：ST 名单一次没取到 → 两份报告 `spec_signature` **完全相同**，
  池子却差 126 只、收益差 5% → 结论直接失效。
- 报告里要**显式声明**「本次未剔除某特征」，让人分得清「没勾」与「勾了但没取到」。
- 权威实现：`strategies/core/universe.py::ScopeDataUnavailable`（400 + 下一步文案）。

## 四、改后端代码之后

- 验证前**先结束占用 8000 端口的旧 uvicorn 进程**再重启。
  启动器检测到后端已在运行会**不抢占、不重启**；不清旧进程，测到的仍是旧代码，
  结论失真。
- 临时诊断（后端 trace / `print`、前端 Log 标签）必须标注「待清理」，
  **验证通过后立即删除、再提交**；不得把诊断代码随业务修复长期留存。
- BUG 排查：**无法从源码静态定位根因时，先用日志 / 诊断手段取证再改**——
  禁止「改代码试一试、看运行结果」的试错式修改。

## 五、指针

- 目录地图 / 路由前缀 / 单一来源全表 → `project-structure.md`
- 实现方式的选择优先级 / 抽象判据 → `engineering-standards.md`
- 回测 / 策略比对的可信度纪律（三道闸 + 两个必做对照）→ `backtest-credibility.md`
- 接口字段变更必须同步前端消费端 → `docs-sync.md` 触发矩阵
