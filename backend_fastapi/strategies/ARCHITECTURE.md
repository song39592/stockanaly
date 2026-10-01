# 策略回测模块架构说明（新增策略前必读）

本目录实现**策略回测**。设计上完全对齐 `indicators/` 模块：**统一文件接口、自动扫描注册、单一数据入口**。

---

## 1. 目标与范围

- **范围**：以「目标仓位序列（0/1）」为统一契约的技术型策略（趋势 / 反转 / 动量）。策略本身只决定「买不买」，回测引擎负责把仓位合成为组合收益与指标。
- **计算方式**：实时计算、不落库；每次请求现算现返。
- **呈现方式**：后端返回组合净值曲线（`equity[]`）+ 指标（`metrics`）+ 个股明细（`per_stock`），前端按此渲染，不写死任何策略算法。

---

## 2. 目录结构

```
backend_fastapi/strategies/
    __init__.py      包入口：暴露 list_strategies() / run() / REGISTRY，并自动扫描注册
    ARCHITECTURE.md  本文件（新增策略前必读）
    base.py          注册机制（@strategy 装饰器）+ 计算入口 run() + 参数规格 ParamSpec
    data.py          【唯一数据入口】封装 get_ohlcv（后复权）与 list_universe_codes
    registry.py      把 REGISTRY 转成前端清单 list_strategies()
    backtest.py      回测引擎：多标的等权组合、净值、指标、基准对比
    ma_cross.py      双均线交叉（趋势）        ← 每个策略一个文件，文件名即 id
    macd_cross.py    MACD 金叉（趋势）        ← 每个策略一个文件，文件名即 id
    rsi_reversal.py  RSI 均值回归（反转）     ← 每个策略一个文件，文件名即 id
    breakout.py      N 日新高突破（动量）     ← 每个策略一个文件，文件名即 id
```

> **每个策略独立成文件，文件名即策略 id**（如 `ma_cross.py`）。新增策略只需新建 `<id>.py`，
> `__init__.py` 会在包加载时**自动扫描目录**并 import，触发 `@strategy` 注册，无需手动登记。

---

## 3. 核心约束

1. **唯一数据入口**：`data.py` 是策略包内**唯一**允许 `import price_store`（以及列本地代码）的文件。
2. **策略文件禁止直连外部**：`ma_cross.py` 等实现文件**只能** `from .data import get_ohlcv`，
   不得 `import price_store` / `import akshare` / 读文件 / 发网络请求。取数、复权、对齐都在 `data.py` 解决。
3. **为什么**：取数与算数解耦 → 口径统一（后复权/对齐/缺失）、便于测试（mock `get_ohlcv`）、新增策略不必关心数据来源。
4. **不落库**：计算无状态，不写任何存储；组合净值仅在请求内计算。

---

## 4. 数据入口 `data.py`

- `get_ohlcv(code, start=None, end=None, adjust="hfq") -> pd.DataFrame`
  - 默认**后复权（hfq）**，使收益率反映真实持仓盈亏；技术指标展示用的前复权（qfq）**不适用**回测。
  - 返回索引为日期字符串（`YYYY-MM-DD`），列固定 `open/high/low/close/volume`，升序。数据不足（<2 行）抛 `RuntimeError`。
- `list_universe_codes() -> list[str]`：本地已下载（有日 K）的全部股票代码，供「回测范围」选股票池。

---

## 5. 策略注册机制 `base.py`

每个策略是一个**普通函数**，用 `@strategy(...)` 装饰即完成登记：

```python
import pandas as pd
from .base import strategy, ParamSpec

@strategy(
    id="ma_cross",
    name="双均线交叉",
    category="趋势",
    description="快均线上穿慢均线时持有，下穿时空仓。",
    params=[
        ParamSpec("fast", "int", 5, min=2, max=60, label="快线周期"),
        ParamSpec("slow", "int", 20, min=5, max=250, label="慢线周期"),
    ],
)
def ma_cross(df: pd.DataFrame, fast: int = 5, slow: int = 20) -> pd.Series:
    ma_f = df["close"].rolling(fast).mean()
    ma_s = df["close"].rolling(slow).mean()
    return (ma_f > ma_s).astype(float)
```

- `REGISTRY`：id → `StrategyMeta`（含函数与参数规格）。id **必须唯一**，重复会抛 `ValueError`。
- `StrategyMeta`：id / name / category / description / params / func。
- `ParamSpec`：参数规格（见 §8），供前端渲染表单与后端校验。

### 策略函数约定（⚠️ 最重要）

- 入参 `df` 由 `run()` 经 `data.get_ohlcv` 提供（绝不自行取数），索引为 date，列含 OHLCV。
- **返回「目标仓位序列」**：任意非零值视为 `1`（满仓持有），`0` 视为空仓。
  `run()` 会把它归一化为与 `df` 对齐的 0/1 序列（NaN→0）。
- 每条序列**必须沿用 `df` 的 date 索引**（直接基于 `df` 计算即可），不得裁掉前段索引；
  前段不足周期的位点用 `NaN` 表示（pandas 滚动自然产生），`run()` 会转成 0。

---

## 6. 回测引擎 `backtest.py`

`run_backtest(strategy_id, params, codes, start, end, initial_capital, commission, benchmark)`：

1. 对 `codes` 中每只标的：取后复权日 K → `run()` 得到每日 0/1 仓位 →
   以「信号当日收盘算、次日收益计入」近似（`shift(1)`），按换手扣减双边佣金（`turnover * commission`）。
2. 把各标的日收益**等权**加总为组合日收益，累积得到组合净值（`equity[]`）。
3. 计算指标：总收益、年化、最大回撤、夏普、胜率、交易次数、标的数量、交易日数，可选基准买入持有收益。
4. 各标的对齐到**共同交易日（交集）**，保证组合口径一致；数据不足/失败的标的记入 `data_errors`。

> 模型简化：满仓/空仓二值仓位、次日开盘近似、等权组合、固定比例佣金。**非实盘级**回测，
> 用于策略思路的快速验证与对比。

---

## 7. 标准化输出接口（前后端契约）

### (a) 策略清单 `GET /api/strategies`
```json
[{"id":"ma_cross","name":"双均线交叉","category":"趋势",
  "description":"...","params":[{"name":"fast","type":"int","default":5,"min":2,"max":60,"label":"快线周期"}]}]
```

### (b) 本地代码 `GET /api/strategies/codes`
```json
{"codes":["000001","000002","600000","..."]}
```

### (c) 运行回测 `POST /api/strategies/backtest`
请求：
```json
{"strategy_id":"ma_cross","params":{"fast":5,"slow":20},
 "use_all":false,"codes":["600000","000001"],
 "start":"2020-01-01","end":"2024-01-01",
 "initial_capital":100000,"commission":0.0003,"benchmark":"000300"}
```
返回（成功 `ok:True`，失败 `ok:False` + `error`）：
```json
{
  "ok": true,
  "strategy": {"id":"ma_cross","name":"双均线交叉","params":{...}},
  "scope": {"codes_count":2,"start":"2020-01-01","end":"2024-01-01",
            "initial_capital":100000,"commission":0.0003,"benchmark":"000300"},
  "dates": ["2020-01-02", "..."],
  "equity": [100000, 100320.5, "..."],
  "equity_benchmark": [100000, "..."] | null,
  "metrics": {"total_return":0.18,"annual_return":0.042,"max_drawdown":-0.12,
              "sharpe":0.9,"win_rate":0.52,"num_trades":14,
              "num_stocks":2,"trading_days":960,"benchmark_return":0.05},
  "per_stock": [{"code":"600000","total_return":0.21,"trades":7}],
  "data_errors": []
}
```

---

## 8. 参数规范 `ParamSpec`

| 字段 | 含义 |
|---|---|
| `name` | 参数名（函数关键字参数名） |
| `type` | `"int"` / `"float"` / `"choice"` |
| `default` | 默认值；`choice` 可为单值或列表 |
| `min` / `max` | 数值范围（含），前端做输入限制 |
| `choices` | `choice` 类型的可选项 |
| `label` | 前端展示的中文标签 |

---

## 9. 新增策略步骤（清单）

1. **新建一个文件，文件名即策略 id**（如 `momentum.py`），顶部 `from .base import strategy, ParamSpec`。
   一个文件只放一个策略。
2. **写策略函数**：入参 `df`，返回 0/1 仓位序列（或任意非零/零序列，会被归一化）。
3. **加 `@strategy` 注册**：填唯一 `id`、中文 `name`、`category`、`description` 与 `params` 规格。
4. **（新文件）无需手动登记**：`__init__.py` 自动扫描并 import，触发注册。
5. **本地验证**：
   ```python
   import strategies
   print(strategies.list_strategies())                 # 应出现新策略
   r = strategies.run("ma_cross", {"fast":5,"slow":20}, strategies.data.get_ohlcv("600000"))
   print(r.value_counts())                              # 0/1 分布
   out = strategies.backtest.run_backtest("ma_cross", {}, ["600000"], "2020-01-01", "2024-01-01")
   print(out["metrics"])
   ```

---

## 10. 禁止事项

- ❌ 策略实现文件 `import price_store` / `import akshare` / 读文件 / 发网络请求。
- ❌ 在策略函数内裁掉 `df` 的前段索引（导致与行情错位）。
- ❌ 重复 `id`、或把多个策略塞进一个文件；每个策略必须独立成文件且**文件名即其 id**。
- ❌ 用错复权口径（回测须后复权 hfq，勿用 qfq）。
- ❌ 在策略函数里混入未来函数（如用当日收盘价判断当日仓位又计入当日收益）——仓位次日生效由引擎统一处理。
