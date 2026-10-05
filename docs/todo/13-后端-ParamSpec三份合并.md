# 13 · 后端：`ParamSpec` dataclass 三份合并（只合并它，Meta/装饰器保持独立）

> 状态：待办　|　优先级：中　|　收益 ★★★☆☆ / 风险 ★★☆☆☆

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
