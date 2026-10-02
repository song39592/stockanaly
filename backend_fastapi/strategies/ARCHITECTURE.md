# 策略回测模块架构

与 `indicators/` 同源的「统一文件接口」思想：**一个策略一个文件、文件名即 id、自动扫描注册**。
区别在于策略是**事件驱动**而非逐日数值：策略输出「买入/卖出时点 + 仓位系数」，
净值与统计由独立的统计引擎计算。

目录约定（**具体策略 vs 基础框架分离**）：
  strategies/
    <id>.py        具体策略（如 macd.py），留在这一层，可任意新增
    core/          基础执行框架（base/data/stats/backtest/registry），策略不应修改
    _validated.json 校验缓存（自动生成）
具体策略导入框架一律走 `from .core import ...`。

## 三层核心思想：策略主动拉取

每个策略需要的数据列不同（有的只要 `close`，有的要 `close`+`volume`，有的要 `high/low`），
因此**由策略自己声明 `inputs` 并在函数体内调用 `data.load_bars` 主动拉取**，
而不是被动接收一个"统一输入对象"。回测层只负责：读范围 → 把范围交给策略 → 把收盘价交给统计。

数据流：

```
回测范围 (codes / start / end)
        │
        ▼
backtest.py   ① 读范围 + 抽样上限；预检取数（data.load_bars，缓存）
        │      ② 过滤数据不足的标的 → 构造 StrategyContext
        ▼
<id>.py(策略)  ③ 主动拉取：data.load_bars(ctx.codes, fields=inputs)
        │      ④ 算信号 → list[Signal]（每条带 code）
        ▼
backtest.py   ⑤ signals_to_positions → {code: 目标仓位序列}
        │
        ▼
stats.py      ⑥ 用收盘价序列算净值与指标（与策略无关）
```

> `data.load_bars` 按 (code, start, end, adjust) **逐标的缓存**底层全量 OHLCV，
> 因此「回测预检 / 策略拉数 / 统计拉数」多次调用（即使字段或 code 集合不同）只触发一次真实取数。
> 底层 K 线由 `kline_service` 提供（与个股页 K 线 / 指标 / 筹码同一份实现），
> 但**回测固定日线**：`stats.py` 的年化与夏普按 **252 交易日/年**折算，
> 换成周 / 月会让这两个指标静默算错，故 `load_bars` 不开放 period
> （真要做周期回测，须先改 stats 的年化口径并重定义 T+1 与持仓天数的语义）。

## 分层（各层职责单一，互不越界）

| 文件 | 角色 | 职责 | 允许依赖 |
| --- | --- | --- | --- |
| `data.py` | **数据层** | `load_bars(codes, fields) -> {code: DataFrame}`、`list_universe_codes()`；取数走 **`kline_service`**（全项目唯一的「取数 + 周期合样」出口）；包内**唯一**允许 `import price_store` 的地方 | `kline_service`、`price_store` |
| `base.py` | **契约层** | `@strategy` / `ParamSpec` / `Signal` / `StrategyContext`；`run()` 调策略、`signals_to_positions()` 信号→仓位 | `data`（仅类型）、`pandas` |
| `stats.py` | **统计引擎** | 目标仓位序列 + 收盘价 → 净值曲线与指标；**与策略完全解耦** | 仅 `numpy/pandas` |
| `backtest.py` | **编排层** | 范围 → 策略信号 → 统计，串起来并规范化返回 | `data/base/stats` |
| `<id>.py` | **策略实现** | 主动拉数（`data.load_bars`）+ 算信号 | `base`、`data` |

> 策略文件**不得**直接读写数据库/文件/网络；取数只能走 `data`，统计只能走 `stats`。
> `stats.py` 不 import 任何策略，也不直接取数，可单独复用与单元测试。

## 策略校验门槛（`registry.py`）

策略 `.py` 文件**不能**直接信任地导入即用。包加载时（`__init__` 调 `registry.scan()`）
对每个策略文件做「输入输出接口检查」，通过才登记进 `base.REGISTRY` 并对外可选；
未通过的记录错误、不可选中、不可回测。

校验内容（`registry._validate_module`）：
1. 文件可被导入（异常被捕获并记录，不拖垮整个包）；
2. 恰好用 `@strategy` 注册 1 个策略，且 `id == 文件名(去 .py)`；
3. 元数据完整：`name`/`category`/`outputs` 非空，`inputs` 为 str 列表，`params` 为 `ParamSpec` 列表（`type ∈ {int,float,choice}`）；
4. 函数签名：首参必须为 `ctx`，其余参数名与 `ParamSpec` 一一对应；
5. **冒烟测试**：用假数据替换 `data.load_bars` 跑一次策略，确认其返回可标准化为「带 `code` 的 `Signal`」。

通过的文件写入 `_validated.json` 缓存，记录**文件名 + 最后修改时间 + 状态 + 元数据/错误**；
文件 `mtime` 变化则下次扫描自动完整重验，`mtime` 未变且曾通过时仅做轻量复检（跳过冒烟）。
HTTP 接口：`GET /api/strategies/validation` 看报告，`POST /api/strategies/refresh` 强制重验。

## 标准策略格式

```python
from .core.base import strategy, ParamSpec, Signal
from .core import data

@strategy(
    id="macd",                                   # 文件名即 id（新增文件即注册）
    name="MACD 金叉/死叉",
    category="趋势",
    description="DIF 上穿 DEA 买入，下穿卖出。",
    inputs=["close"],                            # 本策略实际会去拉的字段
    outputs="buy/sell 信号 + 仓位系数(weight)",
    params=[                                     # 向文件请求的额外参数（前端据此渲染表单）
        ParamSpec("fast", "int", 12, min=2, max=60, label="快线 EMA"),
        ParamSpec("position", "float", 1.0, min=0.1, max=1.0, label="建仓系数"),
    ],
)
def macd(ctx, fast: int = 12, position: float = 1.0) -> list[Signal]:
    # 主动拉取本策略所需字段；缓存命中，无额外 I/O
    bars = data.load_bars(ctx.codes, ctx.start, ctx.end, fields=["close"], adjust=ctx.adjust)
    signals = []
    for code, df in bars.items():
        close = df["close"].astype(float)
        ...
        signals.append(Signal(code=code, time="2024-01-05", action="buy",  weight=1.0))
        signals.append(Signal(code=code, time="2024-03-11", action="sell", weight=0.0))
    return signals
```

### 契约细节
- **主动取数**：策略通过 `data.load_bars(codes, start, end, fields, adjust)` 拉数，返回 `{code: DataFrame}`；`fields` 仅声明本策略要用的列。
- **额外参数**：`ParamSpec(name, type, default, min, max, choices, label)`，`type ∈ {int, float, choice}`；前端据此动态生成控件；`base.run` 会用默认值填充缺失项。
- **输出**：`list[Signal]`（也容忍 `dict` / `(code, time, action, weight, reason)` 元组 / `DataFrame`）。每条信号**必须带 `code`**（标明属于哪只标的）。
  - `Signal.code` 股票代码（必填）；
  - `Signal.time` 事件日（`YYYY-MM-DD`）；
  - `Signal.action` `buy` / `sell`；
  - `Signal.weight` 仓位系数 `0..1`，`sell` 恒置 0；
  - `Signal.reason` 可选说明。
  - 归一化由 `base._as_signals` 完成：方向合法化、裁剪到 `[0,1]`、按 `(code, time)` 排序、丢弃无 `code` 的信号。
- 信号 → 仓位：`base.signals_to_positions` 按 `code` 把事件日设为该仓位，其余前向填充（首个事件前为空仓），序列对齐到该 code 自身的行情日期。

## 统计口径（`stats.py`）
- 输入：{code: 目标仓位序列} + {code: 收盘价序列}（均由编排层从 `data` 取好后传入）。
- **日线口径**：年化与夏普按 `252` 交易日/年折算（`stats.py` 中的 `252.0`），
  夏普的分母也用它做年化。因此**序列必须是日线**——这也是 `data.load_bars` 不开放
  period 的原因；若将来支持周 / 月回测，这里要改成按实际周期数/年折算。
- 次日生效：`w_{t-1}` 承担第 `t` 日收益（当日收盘算信号）。
- 交易成本：按目标仓位变化量 × `commission`（单边）。
- 组合：各标的**等权**，每只分到 `initial_capital / N` 一条资金带；未建仓即现金（收益 0）。
- 指标：总收益、年化、最大回撤、夏普、胜率、交易次数、标的数、交易日数；可选基准（买入持有）对比。

## 新增一个策略
1. 在 `strategies/` 下新建 `<id>.py`（**文件名即 id**）；
2. 用 `@strategy(...)` 声明 `inputs` / `params` / `outputs`；函数签名首参为 `ctx: StrategyContext`，其余为各 `ParamSpec` 同名参数：`def f(ctx, p1=.., p2=..) -> list[Signal]`；
3. 函数体内用 `data.load_bars(ctx.codes, fields=inputs)` 主动拉取所需字段，循环 `bars.items()` 逐标的计算，返回带 `code` 的 `Signal`；
4. 保存即生效（包加载时自动扫描注册，无需改任何其它文件）；
5. 前端「策略选择与编辑」页会自动出现该策略及其参数表单。

## HTTP 接口（`strategies_routes.py`，前缀 `/api/strategies`）
- `GET  ""` 策略清单（含 `inputs/outputs/params`）
- `GET  /codes` 本地已下载股票代码
- `POST /backtest` 运行回测；`use_all=True` 且超过上限（800）时自动随机抽样并回传 `scope.sampled`
