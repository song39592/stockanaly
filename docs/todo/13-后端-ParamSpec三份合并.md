# 13 · 后端：`ParamSpec` dataclass 三份合并（只合并它，Meta/装饰器保持独立）

> 状态：**已完成**（2026-10-06）　|　优先级：中　|　收益 ★★★☆☆ / 风险 ★★☆☆☆

## 实际落地

新增 `backend_fastapi/core/paramspec.py` 作为**唯一权威定义**，三处旧定义删除：

| 原位置 | 现在 |
|---|---|
| `indicators/base.py` | `from core.paramspec import ParamSpec` |
| `strategies/core/base.py` | 同上 |
| `features/chip/formulas/core/base.py` | 同上 |

三份定义**逐字段完全相同**（`name/type/default/min/max/choices/label`），
只有 docstring 不同 —— 合并收益不在省 21 行，而在于**校验规则只需改一处**
（`type` 白名单、默认值落法、越界处理这套规则会持续演进，三份各改一次必然漏）。

**⭐ 放 `core/` 而不是待办建议的 `common/spec.py`，因为它必须零依赖**：
本模块只 import 标准库（`dataclasses` / `typing`），不 import 任何项目模块。
这正是合并可行的前提 —— 若它反过来 import 三个注册表任一个，
`indicators` / `strategies` / `features.chip.formulas` 三边 import 它
就会构成循环依赖。

**⭐ 注意两个同名子包**：`strategies/core/` 与 `features/chip/formulas/core/`
里的 `core` 指各自的子包，不是顶层 `core`。所以三处 import 一律用**绝对路径**
`from core.paramspec import ...`；写相对导入 `from . import paramspec`
会去各自的子包里找一个不存在的模块。

### 三处再导出按待办要求保留（兼容锚点）

合并的是**定义**，不是**名字**，下列位置继续 `import ParamSpec` 行为不变：

- `indicators/__init__.py`（从 `.base` 再导出）
- `strategies/__init__.py` 与 `strategies/core/__init__.py`（从 `.core.base` 再导出）

另清掉两处失效导入：删除定义后 `typing.Any` 在 `indicators/base.py` 与
`strategies/core/base.py` 里已无引用，删掉；筹码那份的 `Any` 仍被 `ChipContext` 用着，保留。

## 验收
- [x] `ParamSpec` 只有 1 份定义；三处通过重新导出对外提供
- [x] 7 个 import 路径（`core.paramspec` / `indicators.base` / `indicators` 包 /
      `strategies.core.base` / `strategies.core` 包 / `strategies` 包 /
      `features.chip.formulas.core.base`）拿到的是**同一个类对象**（`is` 为真）
- [x] dataclass 字段与顺序不变（7 个字段）
- [x] 两种调用形式都正常：位置传 min/max（`indicators` 式
      `ParamSpec("period","int",14,2,100,...)`）与关键字传（`strategies` 式
      `ParamSpec("top_n","int",100,min=1,max=200,...)`）
- [x] 三套注册中心 LOAD 校验全部通过：指标 8 个（params 数 1/1/1/1/1/1/3/0）、
      策略 2 个（5/4），策略校验 `invalid=0`，筹码公式校验 `invalid=0`
- [x] 全仓 `class ParamSpec` **只剩 1 处**（`core/paramspec.py`）
- [x] 52 个单元测试全绿
- [x] 后端启动 `/health` 的 `route_errors` 为空、11 个模块全 `true`

## 问题
`ParamSpec` 在三个「插件体系」里各写了一份，字段**逐字符相同**。
但配套的 Meta dataclass 与注册装饰器**不该合并**（字段与生命周期不同）。

## 证据

### 可合并：3 份逐字符相同的 `ParamSpec`
- `indicators/base.py:42-50`
- `strategies/core/base.py:47-55`
- `chip_formulas/core/base.py:34-42`

字段完全一致：`name, type, default, min, max, choices, label`，注释都是 `# "int" | "float" | "choice"`，纯数据类、零逻辑。

### ❌ 不建议合并（明确列出，避免误伤）
| | 差异 |
|---|---|
| 三个 Meta | `IndicatorMeta`（有 `panel`）/ `StrategyMeta`（有 `inputs, outputs`）/ `ChipFormulaMeta`（两者都无） |
| 三个装饰器 | `indicator(id,name,category,panel,params)`（panel 是**必填位置参数**）/ `strategy(id,name,category,description,inputs,outputs,params)` / `chip_formula(id,name,category,description,params)` |

硬合成一个 `PluginSpec` 会引入大量可选 `None` 字段，比现在更难维护 —— **收益为负**。

## 建议做法
1. 新建 `backend_fastapi/common/spec.py`（或顶层 `spec.py`），放入唯一的 `ParamSpec`。
2. 三处改为 `from spec import ParamSpec` 并**重新导出**（`ParamSpec = spec.ParamSpec`），
   这样各包原有的 `from .core.base import ParamSpec` 写法不用改，也能被外部（策略文件/公式文件）继续导入。

## 注意（踩坑点）
- 必须保留三处的**重新导出**，否则所有指标文件（`indicators/*.py`）、策略文件（`strategies/*.py`）、
  公式文件（`chip_formulas/*.py`）都要改 import，改动面会爆炸。
- 三个包的 `registry.py` 在 LOAD 时都会做「签名与 ParamSpec 对齐」校验（如 `chip_formulas/core/base.py:156-159`、
  `strategies/core/registry.py`），合并后要确认这些校验仍能通过（字段集合未变，理论上无影响）。
- dataclass 的字段顺序不要动（可能有位置参数构造的调用点）。

## 验收
- [ ] `ParamSpec` 只有 1 份定义；三处通过重新导出对外提供
- [ ] 三套注册中心的 LOAD 校验全部通过：
      `indicators.list_indicators()` 与 `strategies.list_strategies()` 与 `chip_formulas.list_formulas()` 均正常
- [ ] `validation_report()` 中 invalid 为 0
- [ ] 后端启动 `/health` 的 `_module_errors` 为空
