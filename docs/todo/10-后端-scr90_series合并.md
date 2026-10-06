# 10 · 后端：SCR90 序列计算合并（2 份逐字符雷同 + 修 1 个判空缺陷）

> 状态：**已完成**（2026-10-06）　|　优先级：高　|　收益 ★★★★☆ / 风险 ★☆☆☆☆
> 前置：第 00 项已完成（结论：`rows` NameError 不复现，无需改代码）→ 周榜可验证
> 位置提示：`chip_formulas/` 已由第 34 项搬进 `features/chip/formulas/`，故内核落在
> **`backend_fastapi/features/chip/formulas/core/scr.py`**（与建议一致）

## 回填实际改动

**新增内核** `features/chip/formulas/core/scr.py`（107 行，纯 numpy + 一层 pandas 包装）：

| 函数 | 语义 | 供谁用 |
|---|---|---|
| `scr_series(pct, centers, q_lo, q_hi)` | **NaN** | 周榜、策略（两次调 5/95 与 15/85 的副图） |
| `scr_frame(pct_row, centers, q_lo, q_hi)` | **None** | `base._stats_of`（要 JSON 序列化，`null` 比 NaN 诚实） |
| `scr_value(p_lo, p_hi)` | NaN | 已取好分位时的比值 |
| `scr_series_pd(res)` | pandas Series | 周榜与策略的共同用法（索引取 `res.dates`） |

**三处调用点改用内核，两份雷同函数删除**：

| 文件 | 变化 |
|---|---|
| `features/chip/rank_service.py` | 删 `_scr90_series`（21 行）→ `scr.scr_series_pd(res)` |
| `strategies/scr90.py` | 删 `_scr90_series`（21 行）→ `scr.scr_series_pd(res)` |
| `features/chip/formulas/core/base.py` | 删本地 `_percentile` → `_stats_of` 改用 `scr.scr_frame` |
| `indicators/chip_scr.py` | 删本地 `_percentile` + `_scr` → 两次 `scr.scr_series`（5/95 与 15/85） |

**函数体净删约 45 行**，算法只剩一份。

## 踩坑点三条的处置

1. ✅ **只合并算法内核，调用参数一律不动** —— 内核 docstring 用表格写明四类调用方的
   「有无锁仓修正」差异（周榜/策略 = `offline=True` 无修正；副图/单帧 = qfq 带修正），
   并注明「这是业务口径差异，不是该 unify 掉的重复；合并时把参数也统一会让数值整体偏移」。
2. ✅ **判空缺陷已修，属行为变更并已量化** —— `if p5 and p95` → `den == 0`。
   对照实验（同一输入、旧逻辑逐步复刻 vs 新内核）：

   | 输入 | 旧逻辑 | 新内核 |
   |---|---|---|
   | 价格分箱含 0，`P5=0.0`、`P95=1.8` | **`None`**（p5=0 被当成空筹码） | **`1.0`**（正确值） |
   | `den == 0`（P5=P95=0） | `None` | `None`（一致） |
   | 正常样本 | 正常值 | 正常值（一致） |

   **只影响「分位价格恰为 0」的样本**（分箱下沿正好落在 0 的低价股）；
   实测 1252 日的 `chip_scr` 副图序列（SCR90 0.031→0.109、SCR70 0.021→0.067）
   与四个极端样本的 `None` 语义**全部与修复前一致**，没有新增 `null`。
3. ✅ `chip_rank_service` 原本已 `import chip_formulas`，本项只多一行
   `from features.chip.formulas.core import scr`（同包内聚）。

## 验收（2026-10-06 实测）

- [x] 三处 SCR90 计算共用同一内核；两份逐字符雷同的函数消失 ✅
      全仓检索：`_scr90_series` 0 处定义/0 处调用；`_percentile` 只剩内核里 1 份
- [x] 周榜结果与合并前一致 ✅ **`/api/chip/rank?weeks=1` 100 条逐行逐值零差异**
      （含 code / scr90 / rank 三列全等）；另在进程内对 600519 / 000002 / 000001
      三个票做了 120 帧序列的**首 5 / 末 5 / NaN 数 / nansum** 对比，全部一致
- [x] 策略 `scr90` 跑同样池子正常 ✅ `/api/strategies/recommend`（8 只票、top_n=3）
      `ok=true`、持仓 3 只、买入 0、卖出 0，价格与盈亏均为合理值
- [x] `if p5 and p95` 已修；分位为 0 的极端样本能产出非 None 的 scr90 ✅
      对照表见上（`None` → `1.0`）
- [x] `chip_formulas.validation_report()` 中 invalid 为 0 ✅
      `total=1 valid=1 invalid=0`、`FORMULAS=['tri_decay']`
      （`scr.py` 在 `core/` 下，被注册器的 `_EXCLUDE={"__init__","core"}` 正确排除，
      不会被误当成一个公式）
- [x] `/health` 的 `_module_errors` 为空 ✅ `ok=True`、11/11 路由、`integrity.ok=True`
- [x] `chip_scr` 副图真实入口正常 ✅ `POST /api/indicators/batch`
      （`{"code":"600519","items":[{"id":"chip_scr"}]}`）→ 1252 点、全部非空，
      SCR90 与 SCR70 两条序列的值域与方向都合理（越集中值越小）
- [x] 52 个单元测试全绿 ✅

## 记录

- 2026-10-06 执行，位置为阶段 D 第 24 步。
- 本项执行期间**顺带发现并修掉了 bug-04 的续集污染**（000001 有 177 行
  `close=8888.0` 的字段错位、factors 表 4 行夹具），详见
  `docs/todo/bug-04-*.md` 的「续集」章节；入库校验同步加严为
  「开收盘必须落在当日高低区间内」。