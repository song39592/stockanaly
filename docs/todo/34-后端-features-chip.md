# 34 · 后端：`features/chip/`（筹码体系）—— **被依赖最多，最后搬**

> 状态：**已完成**（2026-10-06）　|　优先级：高　|　收益 ★★★★☆ / 风险 ★★★★☆
> 依赖 27~33（全部已完成）

## 回填实际改动

```
backend_fastapi/features/chip/
├─ __init__.py
├─ scr_service.py    ← 原 chip_service.py       导入 xls → 多期合并 → 三档分类（529 行）
├─ rank_service.py   ← 原 chip_rank_service.py  SCR90 周级三档（439 行）
├─ scr_routes.py     ← 原 chip_routes.py        /api/chip/scr/*（88 行）
├─ dist_routes.py    ← 原 chip_dist_routes.py   /api/chip/dist/*（84 行）
├─ rank_routes.py    ← 原 chip_rank_routes.py   /api/chip/rank/*（73 行）
└─ formulas/         ← 原 chip_formulas/ 整包迁移（含 ARCHITECTURE.md，7 个 .py）
```

顶层保留 **6 个别名转发**（`sys.modules[__name__] = _impl`）：
`chip_service.py` / `chip_rank_service.py` / `chip_routes.py` /
`chip_dist_routes.py` / `chip_rank_routes.py` / `chip_formulas.py`。

### 文件被改名了（三个 routes 否则会撞名）

`chip_service → scr_service`、`chip_rank_service → rank_service`、
`chip_routes → scr_routes`、`chip_dist_routes → dist_routes`、`chip_rank_routes → rank_routes`。
**转发名一律保持原样**，所以 `main.py` 的 `ROUTE_MODULES` 字符串引用与
`cleanup.py:140` / `dumplog.py:141` 等既有调用方**一行都不用改**。

### 函数体零改动 —— difflib 逐文件核对过

12 个搬入文件里 **11 个零差异**；唯一有改动的是 `formulas/core/registry.py` 的 **1 个常量**：

| 文件 | 差异 |
|---|---|
| `formulas/core/registry.py` | **6 行**（4 行注释 + 2 行常量，见下） |
| `formulas/core/base.py` / `data.py` / `spread.py` / `core/__init__.py` / `__init__.py` / `tri_decay.py` | **0 行** |
| `scr_service.py` | 2 行 import |
| `rank_service.py` | 4 行 import |
| `scr_routes.py` / `rank_routes.py` | 各 2 行 import |
| `dist_routes.py` | 4 行 import（`chip_formulas` + `periods`） |

## 踩坑点六条的处置

1. ⭐ **周口径权威定义方仍是 `scr_service`，结果逐项一致**：
   `MARKET_CLOSE_HOUR=15`、`_as_moment`、`week_start`、`expected_weeks(weeks=5, now=None)`。
   实测 `week_start` 5 个样本、`_as_moment` 5 个样本、`expected_weeks(3)` / `(5)`、
   函数签名 —— **全部与基线一致**。
   `rank_service` 的薄封装 `_week_nodes` **仍然走 `scr_service.expected_weeks`**
   （已用源码断言），未改成各算一份。
   *文档小误*：它说 `_as_date` 也是薄封装，实际 `_as_date` 只是 `datetime→date` 转换器
   （`dt.date.fromisoformat`），从不调 `_as_moment` —— 这是原有实现，未改动。
2. ⭐ **`rank_service` 的三方依赖无循环导入**：
   - `:51` `from features.chip import scr_service as chip_service`（同包）
   - `:52` `from strategies.core.data import list_universe_codes`（`strategies` 留在顶层）
   - `:54` **模块级** `PROCESSED_DIR = chip_service.PROCESSED_DIR` —— 实测与
     `scr_service.PROCESSED_DIR` **同一值**（`E:\stockanaly-data\chip\processed`）
   - `:140` **函数内** `from strategies.core.backtest import _load_names as remote`
     —— **刻意没提到顶层**（`strategies.scr90` 依赖 `chip_formulas`，
     而本模块又依赖 `strategies.core.data`，提到顶层会立刻成环）

   逐个单独导入验证（11 个引用方全部 OK，零循环导入）：
   `chip_rank_service` / `features.chip.rank_service` / `features.chip.scr_service` /
   `chip_service` / `chip_formulas` / `strategies.scr90` / `strategies.core.data` /
   `indicators.registry` / `indicators.data` / `cleanup` / `dumplog`
3. ⭐ **`chip_formulas` 的动态导入命名空间已改为自动推导**（本项最关键的一处改动）：
   `formulas/core/registry.py:34` 原本**硬编码** `_FORMULA_PACKAGE = "chip_formulas"`，
   而 `:69` 用 `importlib.import_module(f"{_FORMULA_PACKAGE}.{mod_name}")` 拼出公式子模块名。
   硬编码在搬家后会去 import 一个已不存在的顶层命名空间。

   改为 `_FORMULA_PACKAGE = __package__.rsplit(".", 1)[0]`
   （本文件位于 `<包>.core`，去掉最后一段即包名）。

   **为什么不能只留顶层转发**：`sys.modules` 别名转发**无法转发「按包名导入子模块」** ——
   `importlib.import_module("chip_formulas.tri_decay")` 在父模块被换成别名对象后，
   Python 会把**同一个文件当成两个模块各加载一次**（`sys.modules` 里出现两个键），
   公式注册表与 `__module__` 都会变乱。改成按 `__package__` 推导后**以后再搬也不用手工同步**。

   实测：公式 `__module__` 由 `chip_formulas.tri_decay` 变为
   `features.chip.formulas.tri_decay` —— **这正是修复生效的证据**；
   `validation_report()` 仍 `total=1 valid=1 invalid=0`。
4. ✅ **`formulas/` 整包迁移，结构未打散** —— `ARCHITECTURE.md` 一并搬过来，
   7 个 .py 原样迁移（6 个零差异），注册机制（`@chip_formula` / 文件名即 id /
   LOAD 五步校验）完全未动，`FORMULAS` 键仍是 `["tri_decay"]`。
5. ✅ **延迟 import 保持原样**：`indicators/data.py:190` 的函数内 `import chip_formulas`
   **一个字没动**（走顶层转发）。若提到顶层会立刻触发它与 `formulas/core/data.py` 的循环依赖。
6. ✅ **第 00 项已完成**（结论：`rows` NameError 在当前代码中不复现，无需改代码），
   所以周榜可以验证 —— 实测 `state == "ready"`（见验收）。

## 验收（2026-10-06 实测）

- [x] `features/chip/` 建立（含 `formulas/` 整包）✅ 6 个模块 + formulas 包，
      顶层 6 个别名转发
- [x] 周榜：小样本跑通，`state == "ready"`，三档与迁移前一致 ✅
      `GET /api/chip/rank/status` → **`state=ready`**；
      `GET /api/chip/rank?weeks=1` → 100 条，字段完整（`code`/`name`/`scr90`/`close`/
      `chg`/`weeks_on`/`last_week`/`as_of`/`rank`），`week=2026-09-28`。
      `rank` 模块的 `PROCESSED_DIR` / `_week_nodes(3)` / `_week_nodes(1)` / `_as_date`
      **与基线零差异**
- [x] 周节点计算结果与迁移前一致（打印 `expected_weeks` 对比）✅
      `expected_weeks(3)` = `['2026-09-28','2026-09-21','2026-09-14']`、
      `(5)` 五项、`rank._week_nodes(3)` 三项 —— **全部与基线逐项一致**；
      `MARKET_CLOSE_HOUR` / `FULL_WEEKS` / 9 个模块级常量（含 `MIN_MARKET`/`LAUNCH_THRESHOLD`）
      也全部一致。*唯一"差异"是 `_as_moment(None)` 返回当前时刻（12:55:16 → 12:58:13），
      是时钟前进而非代码差异；`week_start(None)` 两者同为 `2026-10-05`*
- [x] 个股页 K 线右侧筹码分布正常 ✅ `GET /api/chip/dist?code=600519` → `ok=true`：
      公式 `tri_decay`、`as_of=2026-09-29`、**80 个分箱**、**1252 帧**、
      峰位 `1299.84`（在收盘 `1235.58` 之上 = 三角形峰形合理）、
      峰占比 `6.51%`、获利盘 `11.59%`、scr90 `0.1094`、**锁仓已应用**
      （`ratio=0.544991` / `factor=2.197758`）
- [x] 策略 `scr90` 仍能跑通 ✅ `strategies.scr90` 导入成功，公开名含 `scr90` / `Signal` /
      `strategy` / `ParamSpec`；源码里 `import chip_formulas` 保留、`chip_formulas.` 引用 1 处
- [x] `/health` 的 `_module_errors` 为空 ✅ `ok=True`、**11/11** 路由模块挂载、
      `integrity.ok=True`、stderr **无任何告警**；
      `/api/chip/*` 实测 **12 条路由全部挂上**（三组 prefix 各自不变）
- [x] `chip_formulas.validation_report()` 中 invalid 为 0 ✅
      `total=1 valid=1 invalid=0`，`tri_decay.py` `status=ok`

## 额外验证

- [x] **筹码矩阵数值 13 个统计量与基线逐项一致** ✅ `pct.shape` `(1252, 80)` /
      `pct.min` / `pct.max` `21.1388` / `pct.sum` `125200.0` / **`pct.hash` 加权校验和** /
      `centers.hash` / `close.hash` / `turnover.hash` / `float_shares` / `lockup_ratio` /
      `lockup_factor` / `warm` / `dates.len` —— **共 0 处差异**。
      用加权校验和而非抽样，是为了避免"形状对、数值错"的漏网
- [x] `FORMULAS` 键 / `list_formulas()`（含 params 规格）/ `DEFAULT_FORMULA_ID`
      与基线**完全一致**
- [x] 6 个转发与实现**全部是同一对象**（`is` 为 True）
- [x] 全仓 **132 个** `.py` 三引号成对 + `ast.parse` **全部通过**；
      52 个单元测试全绿；`.env` 未被改动

## 执行方式（按原建议的顺序，每步都验证）

严格按原建议的 4 步走，**每步搬完立刻验证再继续**：

| 步 | 内容 | 验证结果 |
|---|---|---|
| 1 | 整包搬 `chip_formulas/` + 改 `_FORMULA_PACKAGE` | `invalid=0`、注册表一致、`__module__` 已指向新命名空间 |
| 2 | 搬 `chip_service` → `scr_service` | 周口径四函数 + 9 个常量**零差异** |
| 3 | 搬 `chip_rank_service` → `rank_service` | 周节点零差异、**11 个引用方零循环导入** |
| 4 | 搬三个 routes | 12 条路由 + 3 prefix + 3 tags **零差异** |

## 记录

- 2026-10-06 执行，位置为**阶段 C 第 23 步（最后一步）**。至此阶段 C 全部完成。
- 原建议「全部迁完并确认无外部 import 后，才删转发」—— **本项不删转发**：
  `indicators/data.py` / `indicators/registry.py` / `strategies/scr90.py`
  三个**尚未搬**的模块仍 import `chip_formulas`，转发还要继续服役。
  等这三个包各自搬家时改指 `features.chip.formulas` 后再删。
- ⚠️ **本项暴露了一个「转发做不到的事」，值得记住**：
  `sys.modules` 别名转发能覆盖「import 该模块后用它的公开名」，
  但**覆盖不了「按包名动态导入它的子模块」**。凡是**包内部有 `importlib.import_module`
  按自身包名拼子模块名**的代码（`chip_formulas.core.registry` 就是），
  搬家时必须把那个包名常量改成跟着包走，不能指望转发。


## 问题
筹码体系是**耦合面最大**的一块：既被路由调用，又被 `indicators` 与 `strategies` 反向依赖，
还跨包复用 `chip_service` 的目录常量与周口径工具。所以它必须**最后搬**。

## 建议目标
```
backend_fastapi/features/chip/
├─ __init__.py
├─ scr_service.py   ← 原 chip_service.py（导入 xls → 多期合并 → 三档分类；含 week_start / expected_weeks）
├─ scr_routes.py    ← 原 chip_routes.py（/api/chip/scr/*）
├─ dist_routes.py   ← 原 chip_dist_routes.py（/api/chip/dist/*）
├─ rank_service.py  ← 原 chip_rank_service.py（SCR90 周级三档，本地自算）
├─ rank_routes.py   ← 原 chip_rank_routes.py（/api/chip/rank/*）
└─ formulas/        ← 原 chip_formulas/（整包迁移，它已有独立的 ARCHITECTURE.md 与注册机制）
```

## 注意（踩坑点）—— 本项风险最高，逐条看
1. **`chip_service` 是「周口径」的权威定义方**：
   `week_start`（:299）/ `expected_weeks`（:305）/ `_as_moment`（:279）/ `MARKET_CLOSE_HOUR`（:276）。
   `chip_rank_service` 通过薄封装复用它（`_as_date` / `_week_nodes`，是**正确的复用姿势**）。
   → 移动后**必须**确认 `chip_rank_service` 仍能 import 到，且周节点计算结果不变。
2. **`chip_rank_service` 依赖 `chip_service.PROCESSED_DIR`**（:54）与 `strategies.core.data.list_universe_codes`——
   三方依赖，搬家顺序错了会循环导入风险。
3. **`strategies/scr90.py` 与 `chip_rank_service` 都 import `chip_formulas`** —— `chip_formulas` 移进 `features/chip/` 后，
   要确认 `import chip_formulas` 仍能解析（或保留顶层 `chip_formulas.py` 转发）。
4. `chip_formulas/` 是**独立注册体系**（`@chip_formula`、文件名即 id、LOAD 时五步校验）。
   它有 `ARCHITECTURE.md` 明确规定「core/data.py 是唯一数据入口」—— 整包迁移时**不要打散它的内部结构**。
5. `chip_formulas/core/data.py` 与 `indicators/data.py` 之间有**刻意用延迟 import 打断的循环依赖**
   （见 `indicators/data.py` 相关注释）—— 搬家时若把 import 改成顶层，会立刻触发循环导入。
6. 本项涉及 `chip_rank_service` 时，**必须先完成第 00 项**（`rows` NameError），否则无法验证。

## 建议做法
1. 先整包 `git mv chip_formulas/ → features/chip/formulas/`，并在顶层留 `chip_formulas.py` 转发包。
2. 再迁 `chip_service` / `chip_rank_service`（**先 service 后 routes**，routes 只是 HTTP 层）。
3. 每迁一个就重启后端 + 跑一次小样本周榜验证。
4. 全部迁完并确认无外部 import 后，才删转发。

## 验收
- [ ] `features/chip/` 建立（含 `formulas/` 整包）
- [ ] 周榜：小样本跑通，`state == "ready"`，三档与迁移前一致
- [ ] 周节点计算结果与迁移前一致（打印 `expected_weeks` 对比）
- [ ] 个股页 K 线右侧筹码分布正常（三角形峰形 + 横轴刻度 + 锁仓提示）
- [ ] 策略 `scr90` 仍能跑通（它 import `chip_formulas`）
- [ ] `/health` 的 `_module_errors` 为空
- [ ] `chip_formulas.validation_report()` 中 invalid 为 0
