# 策略回测模块架构

与 `indicators/` 同源的「统一文件接口」思想：**一个策略一个文件、文件名即 id、自动扫描注册**。
区别在于策略是**事件驱动**而非逐日数值：策略输出「**目标权重向量**」，
净值与统计由独立的统计引擎计算，买卖流水由引擎对权重向量做差分派生。

目录约定（**具体策略 vs 基础框架分离**）：
```
  strategies/
    <id>.py        具体策略（如 macd.py），留在这一层，可任意新增
    core/          基础执行框架（base/data/stats/backtest/registry），策略不应修改
    _validated.json 校验缓存（自动生成）
```
具体策略导入框架一律走 `from .core import ...`。

> 🆕 **与指标共用同一份计算，不得各写一遍**：策略若需要某个「同时也是副图指标」的口径
> （如筹码峰簇占比 PEAKB、集中度 SCR 等），**必须复用 `chip_formulas/core/` 下的计算内核**
> （`core/peak.py` 的 `peak_band` / `peak_band_series`、`core/scr.py` 的 `scr_series`），
> 而不是在策略里把指标算法再实现一遍。对应指标文件（`indicators/chip_peak_band.py` 等）
> 也只做展示包装、import 同一个内核。这样「策略选股」与「副图显示」同源、可逐日直接对照，
> 不会因为两处各算各的而漂移。策略取行情/框架仍只走 `from .core import ...`，纯计算口径
> 则统一从 `chip_formulas.core` 取，不落地到策略文件内。

---

## 本次修订摘要（重点修改点）

> 以下为相对上一版的**破坏性 / 新增**改动，正文中用 `🆕` 标出同名小节。
> 触发原因：全市场 5559 只回测实测发现资金利用率仅 **1.72%**，`metrics` 全部失真。

| # | 修改点 | 位置 | 严重度 |
|---|---|---|---|
| 1 | **资金模型**：`initial_capital / N` 分带 → **组合权重（Σw ≤ 1）** | 契约细节 / 统计口径 | 🔴 破坏性 |
| 2 | **`Signal.action` 移除**，买卖由引擎 diff 派生 | 契约细节 | 🔴 破坏性 |
| 3 | **`Signal.weight` 语义**：带内系数 → **占组合净值比例** | 契约细节 | 🔴 破坏性 |
| 4 | **新增 `Signal.meta`**：结构化因子值，落进交易明细 | 契约细节 / 明细日志 | 🟢 新增 |
| 5 | **A 股交易约束**：整手 / 涨跌停 / 停牌 / 退市 → **只进明细，不进净值** | 统计口径 | 🔴 缺失 |
| 6 | **成本分层**：比例成本进净值，离散约束出明细 | 统计口径 | 🔴 缺失 |
| 7 | **胜率口径拆分**：日胜率 vs 交易胜率 + profit_factor | 统计口径 | 🟠 易误读 |
| 8 | **交易明细日志**：`trades` 字段扩展 | 明细日志 | 🟢 新增 |
| 9 | **抽样可复现**：随机抽样 → 规则化 + seed | 编排层 | 🟠 不可复现 |
| 10 | **`reason` 不再丢弃**，随信号进明细 | 契约细节 | 🟠 归因失效 |
| 11 | **`universe_mode`**：区间内有行情的代码即入选 + 强制偏差告警 | 编排层 | 🔴 偏差 |
| 12 | **前端 / 报告适配**（`schema_version: 2`） | 改动波及面 | 🔴 破坏性 |

> **一次性迁移，不保留 `allocation` 兼容层。** 现存 3 个策略（`macd` / `scr90` / `rps_score`）
> 本来就要为「全量重发」重写（见契约细节④），迁移成本已经付了；
> 再留两套 weight 语义只是长期技术债。所有策略统一为**组合权重**。

---

## 三层核心思想：策略主动拉取

每个策略需要的数据列不同（有的只要 `close`，有的要 `close`+`volume`，有的要 `high/low`），
因此**由策略自己声明 `inputs` 并在函数体内调用 `data.load_bars` 主动拉取**，
而不是被动接收一个"统一输入对象"。回测层只负责：读范围 → 把范围交给策略 → 把行情交给统计。

数据流：

```
回测范围 (codes / start / end / universe_mode)
        │
        ▼
backtest.py   ① 读范围 + 抽样上限；预检取数（data.load_bars，缓存）
        │      ② 过滤数据不足/不可交易的标的 → 构造 StrategyContext
        ▼
<id>.py(策略)  ③ 主动拉取：data.load_bars(ctx.codes, fields=inputs)
        │      ④ 算信号 → list[Signal]（每条带 code + 目标权重 + meta）
        ▼
backtest.py   ⑤ signals_to_positions → {code: 目标权重序列}
        │      ⑤' diff → 交易流水（buy/sell/add/trim）   🆕
        ▼
stats.py      ⑥ hfq 收盘价 → 净值曲线与指标（与策略无关）
        │      ⑥' raw OHLCV（**仅权重变动的 code**）→ 整手/涨跌停/停牌可行性   🆕
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
| `data.py` | **数据层** | `load_bars(codes, fields) -> {code: DataFrame}`、`list_universe_codes(universe_mode)`、`load_bars_raw_for(codes)`；原始分类数据 `load_names()` / `code_first_dates()` / `market_caps()`；取数走 **`kline_service`**（全项目唯一的「取数 + 周期合样」出口）；包内**唯一**允许 `import price_store` 的地方 | `kline_service`、`price_store` |
| `universe.py` | **股票池层** | `build(spec)` 按规则（板块前缀/ST/上市日/市值）把基池筛成回测代码；`summary(spec)` 给前端预览；**小型键值缓存**（最多 8 种范围），范围不变直接复用；**不依赖 backtest** | `data` |
| `base.py` | **契约层** | `@strategy` / `ParamSpec` / `Signal` / `StrategyContext`；`run()` 调策略、`signals_to_positions()` 信号→权重序列、**`diff_trades()` 权重→流水** | `data`（仅类型）、`pandas` |
| `stats.py` | **统计引擎** | 目标权重序列 + hfq 收盘价 → 净值与指标；raw OHLCV → 可行性指标；**与策略完全解耦** | 仅 `numpy/pandas` |
| `backtest.py` | **编排层** | 股票池 `codes` → 策略信号 → 统计，串起来并规范化返回（只消费 codes，**不感知范围规则**） | `data/base/stats` |
| `<id>.py` | **策略实现** | 主动拉数（`data.load_bars`）+ 算信号 | `base`、`data` |

> 策略文件**不得**直接读写数据库/文件/网络；取数只能走 `data`，统计只能走 `stats`。
> `stats.py` 不 import 任何策略，也不直接取数，可单独复用与单元测试。
> 🆕 **策略层不含「下单/成交/成本」语义**：策略只表达"我想要什么样的持仓"。

### 股票池层（`universe.py`）

回测范围到代码列表的翻译**只发生在这里**，路由与编排层都不重复实现规则：

```python
spec = {"universe_mode": "interval_data", "start": ..., "end": ...,
        "exclude": ["chinext", "star", "bse", "st", "star_st", "ipo", "sub_new", "micro"]}
codes = universe.build(spec)      # 命中缓存直接返回；范围变了才重建
```

- 规则按「**排除**」语义：基池 = `data.list_universe_codes()`，勾选的特征从基池剔除；
  各特征独立计算取并集；外部数据（名称/市值）不可用时**静默降级**（不剔除），绝不把池子搞空。
- 键值缓存 key = 规范化 spec（模式/区间/基准日/排除项/阈值），最多 8 种范围，TTL 1h；
  `invalidate()` 可手动清空（如刚下载完新股数据后希望重建）。
- 阈值默认：新股 ≤90 自然日、次新 90~365 自然日、微盘 ≤30 亿总市值，均可在 spec 覆盖。

## 策略校验门槛（`registry.py`）

策略 `.py` 文件**不能**直接信任地导入即用。包加载时（`__init__` 调 `registry.scan()`）
对每个策略文件做「输入输出接口检查」，通过才登记进 `base.REGISTRY` 并对外可选；
未通过的记录错误、不可选中、不可回测。

校验内容（`registry._validate_module`）：
1. 文件可被导入（异常被捕获并记录，不拖垮整个包）；
2. 恰好用 `@strategy` 注册 1 个策略，且 `id == 文件名(去 .py)`；
3. 元数据完整：`name`/`category`/`outputs` 非空，`inputs` 为 str 列表，`params` 为 `ParamSpec` 列表（`type ∈ {int,float,choice}`）；`requires_pool` 为 bool；
4. 函数签名：首参必须为 `ctx`，其余参数名与 `ParamSpec` 一一对应；
5. **冒烟测试**：用假数据替换 `data.load_bars` 跑一次策略，确认其返回可标准化为「带 `code` 的 `Signal`」；
   🆕 假 `codes` 必须给 **3~5 只**（单只时 `Σweight` 恒 ≤ 1，测不出超额，第 6 项形同虚设）；
6. **权重合法性**：任一事件日 `Σweight ≤ 1 + 1e-6`，且所有 `weight ≥ 0`（负权重 = 做空，A 股不支持）；
7. **meta 可序列化**：对每条信号的 `meta` 做一次 `json.dumps`，失败或含 `nan/inf` 则登记失败
   ——必须在冒烟阶段拦下，不能等到回测结束落盘时才炸。

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
    outputs="目标权重向量 + 因子值(meta)",
    requires_pool=False,                         # 依赖股票池历史时置 True（编排层据此预检）
    params=[
        ParamSpec("fast", "int", 12, min=2, max=60, label="快线 EMA"),
        ParamSpec("top_n", "int", 20, min=1, max=200, label="持仓数量"),
        ParamSpec("position", "float", 1.0, min=0.1, max=1.0, label="总仓位系数"),
    ],
)
def macd(ctx, fast: int = 12, top_n: int = 20, position: float = 1.0) -> list[Signal]:
    bars = data.load_bars(ctx.codes, ctx.start, ctx.end, fields=["close"], adjust=ctx.adjust)
    signals = []
    held: set[str] = set()                      # 🆕 必须自己维护持仓集合
    for day in rebalance_days:
        picks = rank_and_pick(day, top_n)
        held = set(picks)                       # 🆕 决定本期的完整目标组合
        w = position / len(held) if held else 0.0
        for code in sorted(held):               # 🆕 输出【全部】持仓票，不只变化的
            signals.append(Signal(
                code=code, time=day, weight=w,
                reason="DIF 上穿 DEA",
                meta={"dif": 0.0312, "dea": 0.0287, "rank": 3},
            ))
    return signals
```

> ⚠️ **`held` 不可省略。** 引擎在每个调仓日把「未出现的 code」记为 0，
> 所以少发一条 = 静默清仓。策略必须自己维护「当前持有集合」并整组重发。

### 契约细节（本版核心改动）

- **主动取数**：策略通过 `data.load_bars(codes, start, end, fields, adjust)` 拉数，返回 `{code: DataFrame}`；`fields` 仅声明本策略要用的列。
- **额外参数**：`ParamSpec(name, type, default, min, max, choices, label)`，`type ∈ {int, float, choice}`；前端据此动态生成控件；`base.run` 会用默认值填充缺失项。
- **输出**：`list[Signal]`（也容忍 `dict` / 元组 / `DataFrame`）。每条信号**必须带 `code`**。

```python
@dataclass
class Signal:
    code: str                                  # 股票代码（必填）
    time: str                                  # 事件日 YYYY-MM-DD
    weight: float = 0.0                        # 目标权重 = 占【组合净值】比例，>= 0
    reason: str = ""                           # 人读说明（进交易明细，用于归因）
    meta: dict = field(default_factory=dict)   # 机器读因子值（进交易明细，用于 IC/分层）
```

#### 修改点 ①：`action` 字段移除

旧版 `Signal.action ∈ {buy, sell}` 由策略手写，但**买卖是"变化"而非"状态"**，
策略手写变化会导致漏发 sell 就永久错仓。新契约下：

> **策略只输出非负目标权重；引擎对相邻两个调仓日的权重向量做 diff，自动派生买卖。**

| 层 | 语义 | 符号 | 谁产生 |
|---|---|---|---|
| 策略层 | 目标权重（绝对量，状态） | **恒 ≥ 0** | 策略文件 |
| 流水层 | 权重变化 delta（事件） | 正=买/加仓，**负=卖/减仓** | 引擎 `diff_trades()` |

负号**只出现在流水层**。若策略层允许负权重，引擎无法区分「卖出」与「做空」，而 A 股融券标的不全、门槛高，默认不支持。

#### 修改点 ②：`weight` 语义从「带内系数」改为「组合占比」

| | 旧 | 新 |
|---|---|---|
| 语义 | 该资金带内的建仓系数 | **占总组合净值的比例** |
| 约束 | `0..1`（带内） | `≥ 0` 且 **`Σweight ≤ 1`** |
| 分配 | `initial_capital / N` × weight | `组合净值 × weight` |
| 差额 | 无概念 | **差额即现金**（收益 0） |

🔴 **为什么必须改**：旧口径下 `N = 回测池股票数`，实测全市场 5559 只时每带仅 `100000/5559 = 17.99` 元，
平均占用资金 1721 元、**资金利用率 1.72%**，98.28% 本金全程闲置，
总收益 -0.36% 是假象（折算到动用资金约 **-25%**）。
且利用率随池子大小漂移——同一策略在 500 只池子与 5559 只池子结果**不可横向对比**，
直接违背"回测用于对比策略"的用途。新口径与池子大小彻底解耦。

> 代码依据（旧口径如何产生利用率塌陷）：
> `stats.compute` 里 `sret = w_prev * ret - turnover * commission`，再 `port = Σsret / n`（`n` = 有数据的股票数）。
> 于是组合日收益 = 各票 `w×ret` 的**均值**，某日只有 `top_n` 只有仓时，
> 收益幅度天然被压成 `top_n / n` —— 这就是 1.72% 的来源。

> **为什么不用「池子做分母」**（曾讨论后否决）：保留池子分母有两个硬伤。
> ① 跨池仍不可比：同一策略持 50 只，500 只池利用率 10%、5559 只池 0.899%，**差 11 倍**——
>    正是上面要废掉的那个漂移。② 对低 breadth 策略有系统性偏袒：
>    同为 N=5559，持 20 只的收益是 `(1/N)(20×avg)`、持 100 只的是 `(1/N)(100×avg)`，
>    per-name alpha 完全相同却差 5 倍——**测的是 breadth，不是选股能力**。
> 想要「每单位动用资金」的横向口径，用下面的 `return_per_utilized_capital`，而不是池子分母。

#### 修改点 ③：`meta` 结构化因子值（新增）🆕

- **用途**：把信号发生时点的因子值带进交易明细，供 **IC 分析 / 分层回测 / 阈值优化**。
- **与 `reason` 分工**：`reason` 给人读（调试、前端展示），`meta` 给机器算。**不要合并**——
  把因子值拼进 `reason` 字符串后无法做数值运算，且格式一改就崩。
- **取值约束**：value 必须是 `str/int/float/bool/None` 标量；`np.float64` 需 `.item()` 转换；
  `nan` / `inf` 在 `_as_signals` 归一化时**丢弃该字段并计 WARNING**（不拖垮整批）。
- **结构**：**扁平优先，最多一层嵌套**。`{"dif": 0.03, "rank": 3}` 可以，
  `{"indicators": {"macd": {...}}}` 不要——下游分组聚合会很痛。
- **空值**：用 `{}` 而非 `None`。下游 `for k, v in meta.items()` 少一层判空分支。
- **体积**：**信号侧远大于成交侧**——全市场日频调仓 × 100 只 × 700 天 ≈ **7 万条信号**，
  每条 3 字段 × 20 B ≈ 4.2 MB，是 3831 笔成交明细（+224 KB）的近 20 倍。
  `include_meta: bool = True` 开关同时作用于 `buy_meta`/`sell_meta` 与信号层，大批量任务可关闭。

#### 修改点 ④：`reason` 不再丢弃 🆕

旧链路中 `reason` 在 `signals → trades` 环节被丢掉，明细里完全没有。
新契约下 `reason` 与 `meta` **一起落进交易明细**（`buy_reason` / `sell_reason`）。

- 归一化由 `base._as_signals` 完成：`weight` 裁剪到 `≥0`、按 `(code, time)` 排序、**丢弃无 `code` 的信号并计 WARNING**、
  `meta` 清洗。
- 🔴 **`Σweight > 1 + 1e-6` 直接抛 `ValueError`，不再按比例归一化。**
  静默归一化会把"策略想满仓但算错了"变成"半仓运行"，结果不可解释且极难排查。
  报错信息须带上：超额总量、`Σweight` 实测值、贡献最大的前 5 个 `code`。
- 信号 → 权重：`base.signals_to_positions` 按 `code` 把事件日设为该权重，其余前向填充（首个事件前为空仓），
  序列对齐到该 code 自身的行情日期。
- **「调仓日」与「缺失即清零」（定稿）**：
  - `rebalance_dates = sorted({s.time for s in signals})`（全部信号日期的并集）。
  - 任一调仓日未出现的 `code`，该日权重记为 **0**。
  - **推论：策略无法表达"沉默持有"**，想继续持有就必须显式重发该票（哪怕权重与上期相同）。
  - 因此每个调仓日必须输出**完整目标组合**，这样幂等、可重放、任一天可直接读出完整持仓。
- 🆕 **保护：持仓骤降告警。** 某调仓日非零持仓数较上一调仓日下降超过 `position_drop_warn`
  （默认 50%）时记 WARNING。漏发一条信号 = 静默清仓，这个守卫能把 bug 从"结果诡异"变成"可定位告警"。

## 统计口径（`stats.py`）

- 输入：{code: 目标权重序列} + **{code: hfq 收盘价序列}** + **{code: raw OHLCV（仅权重变动过的 code）}**，均由编排层取好传入。
  - **净值与成本**只用 hfq close：收益消除除权跳空；成本 `notional = NAV × |Δw|` 本身
    就是真实金额，不依赖价格，故无需 raw。
  - **可行性判定**（整手 / 涨跌停 / 停牌）必须用 **raw**：hfq 会平滑除权跳空，可能掩盖真实涨跌停。
  - 🆕 raw OHLCV **只对权重实际变动的 code 取**（实测全市场仅百量级），
    这是本方案不与既有内存优化冲突的关键——`run_recommend` 的 `_IdxOnly` 思路继续有效，
    不会重演 5585 只把常驻内存推到 2GB 的老问题。
- **日线口径**：年化与夏普按 `252` 交易日/年折算（`stats.py` 中的 `252.0`），
  夏普的分母也用它做年化。因此**序列必须是日线**——这也是 `data.load_bars` 不开放
  period 的原因；若将来支持周 / 月回测，这里要改成按实际周期数/年折算。
- 次日生效：`w_{t-1}` 承担第 `t` 日收益（当日收盘算信号）。
- **现金语义**：`Σweight < 1` 的差额是现金（收益 0），**不告警**。
  由于 `Σweight > 1` 已改为抛错，`capital_utilization` 只会 ≤ 1；
  要提高利用率必须显式调 `position`，**不会被引擎自动改写**。

### 修改点 ④：资金分配模型 🔴

```
   分配_i(t) = 组合净值(t) × weight_i(t)
   现金(t)   = 组合净值(t) × (1 − Σ_i weight_i(t))     # 收益 0
```

| 项 | 说明 |
|---|---|
| 与池子大小解耦 | 500 只池子与 5559 只池子，同一 top_n 结果一致、可比 |
| 现金 | `Σweight < 1` 的差额，收益 0，由 `position` 参数显式产生 |
| `Σweight > 1` | **抛错**（校验阶段已拦，此处兜底） |
| 必报指标 | `capital_utilization` = `mean_t(Σ_i weight_i(t))`，**应接近 1.0，远低于 1 说明配置有问题** |
| 必报指标 | `return_per_utilized_capital` = `total_return / max(capital_utilization, 1e-9)`，**横向比策略用这个** |

### 修改点 ⑤：A 股交易约束（旧版完全缺失）🔴

> **定稿：约束只影响 `trades` 明细与可行性指标，不影响 `equity`。**
> 理由：本框架的主要用途是**横向比较多策略的信号质量**，`equity` 需要一个干净、
> 与执行细节无关的口径；执行可行性单独成一组指标。代价是 `equity` 偏乐观，
> 报告**必须**同时给出下表右侧的可行性指标，且标注 `equity_model: "weight_idealized"`。

| 约束 | 判定（用 **raw**） | 处理 | 是否进 `equity` |
|---|---|---|---|
| **整手** `lot_size=100` | `shares = int(notional // (price × 100)) × 100` | 算出 0 股 → 记 `feasible: false`，不写有效成交 | ❌ |
| **涨跌停不可成交** | 一字板 `high == low` 且 `\|close/prev_close-1\| ≥ 9.8%`（ST 4.9% / 创业板·科创板 19.8%） | 记 `blocked_by: "limit_up" / "limit_down"` | ❌ |
| **停牌** | `volume == 0` | 记 `blocked_by: "suspended"` | ❌ |
| **退市** | 行情终止且不在当前代码表 | 按最后收盘价计提，标 `delisted: true` | ✅（末值仍在序列内） |
| **新股/ST** | 上市日期、ST 标识 | 上市 N 日内过滤；ST 涨跌幅单独判定 | ❌ |

**可行性汇总指标**（必报，与 `equity` 并列）：

```
tradable_ratio      = 可成交笔数 / 总成交笔数
blocked_limit_up / blocked_limit_down / blocked_suspended   = 各自笔数
lot_feasible_ratio  = 整手约束下能买到的笔数比例
```

⚠️ **实测提示**：整手约束下，10 万本金 + top_n=100（每只 1000 元，买 1 手需股价 ≤10 元）
**可成交率仅 38.9%**（实际成交价中位 13.08 元）。10 万本金建议 **top_n ≤ 30**（可成交率 85%）。
该数字是**可行性体检**，不是净值影响——净值侧不受整手约束。

**幸存者偏差（定稿）** 🆕：新增 `universe_mode`，三档：

| 模式 | 口径 | 说明 |
|---|---|---|
| `interval_data`（默认） | `universe = {code : 在 [start,end] 内有 ≥ _MIN_BARS 根行情}` | 含已退市但行情仍在库的票；仍不含"从未下载过"的票 |
| `pool_snapshot` | 用 `pool_members` 的**时点**成分 | 最严格，`requires_pool=True` 的策略必须用这档 |
| `index_constituent` | 历史指数成分股（中证全指等） | 最严格，**待接入**，见待办 |

🔴 **强制告警**：`interval_data` / `index_constituent` 模式下，报告的 `scope.warnings`
**必须**含一条幸存者偏差声明（写明模式、纳入只数、是否含退市票）。
**任何模式都不允许静默**——偏差存在是事实，藏起来才是问题。

### 修改点 ⑥：交易成本分层 🔴

> **定稿：按"能否用 `Δw × NAV` 精确表达"分层，而不是按"重要不重要"分层。**

| 项 | 参数 | 默认 | 能否用权重表达 | 归属 |
|---|---|---|---|---|
| 佣金 | `commission` | 0.0003 双边 | ✅ `notional × rate` | **进 `equity`** |
| **印花税** | `stamp_duty` | 0.001 卖出单边 | ✅ `sell_notional × rate` | **进 `equity`** |
| **滑点** | `slippage` | 0.0005 双边 | ✅ `notional × rate` | **进 `equity`** |
| **最低佣金** | `min_commission` | 5.0 元/笔 | ✅ `max(notional×rate, 5.0)` | **进 `equity`** |
| 整手 / 涨跌停 / 停牌 | `lot_size` 等 | — | ❌ 需股数与价格粒度 | **只进明细** |

```
# 权重口径下逐笔、逐侧计算（t 时刻净值 NAV_t，Δw = w_t − w_{t-1}）
buy_notional  = NAV_t × max(Δw_i, 0)
sell_notional = NAV_t × max(-Δw_i, 0)
cost_t = Σ_i [ (buy_notional_i + sell_notional_i) × (commission + slippage)
             + sell_notional_i × stamp_duty
             + (buy_notional_i>0 ? max(buy_notional_i×commission, min_commission) : 0)
             + (sell_notional_i>0 ? max(sell_notional_i×commission, min_commission) : 0) ]
```

最低佣金**不需要股数**（`notional` 已含金额），因此可以留在净值里——
这一点很关键，否则修改点⑥的成本模型会整体退化成"只影响明细的摆设"。

**同口径要求**：trade 级的 `pnl` / `win_rate_trade` / `profit_factor` / `expectancy_pct`
必须与 `equity` **同口径**（权重口径、含上述比例成本），否则明细里 N 笔盈亏加总与
`total_return` 对不上，报告无法自证。整手可行性另成一组指标，不混进这批口径。

研究对比可用 `min_commission=0` 保持纯净，但报告须标注 `mode: research`。

### 修改点 ⑦：指标口径拆分与边界 🆕

| 指标 | 旧 | 新 |
|---|---|---|
| 胜率 | 单字段 `win_rate`（实为**日胜率** 53.86%，被误当交易胜率） | `win_rate_daily` 与 `win_rate_trade` **分开输出** |
| 盈亏比 | 无 | `profit_factor`（金额口径，**必看**） |
| 单笔期望 | 无 | `expectancy_pct` |
| 交易次数 | 含 0 股幽灵单、未平仓 | `weight_change_count`（权重变动次数）/ `num_trades_eff`（实际成交且已平仓） |
| 持仓数 | `num_stocks`（回测池大小，误导） | `avg_positions` / `max_positions` |
| 换手 | 无 | `turnover_annual`、`avg_holding_days` |
| 资金利用 | 无 | `capital_utilization`、`return_per_utilized_capital` |
| 基准 | 常为 `null` | 默认**等权全池买入持有**，可选指数 → `alpha`/`beta`/`IR`/`tracking_error` |

**指标定义（定稿）**：

```python
closed_eff = [t for t in trades if t.shares > 0 and t.sell_date and t.pnl is not None]
win_rate_trade   = mean(t.pnl > 0) over closed_eff
profit_factor    = Σ(pnl>0) / |Σ(pnl<0)|
turnover_annual  = 252 × mean_t( Σ_i |Δw_i(t)| )                  # 倍/年
expectancy_pct   = mean(t.pnl) / mean(t.buy_notional)             # 单笔期望收益率
avg_holding_days = mean(t.holding_days) over closed_eff          # 笔数加权
capital_utilization = mean_t( Σ_i w_i(t) )
```

**边界与空值约定（定稿）** 🔴 —— 一律返回 `None`，**绝不返回 `0` 或 `inf`**：

| 情形 | 返回 |
|---|---|
| `closed_eff` 为空 | `win_rate_trade` / `profit_factor` / `expectancy_pct` / `avg_holding_days` = `None` |
| `profit_factor` 分母为 0（全胜） | `None` + `flags += ["no_losing_trades"]` |
| `profit_factor` 分子为 0（全亏） | `None` + `flags += ["no_winning_trades"]` |
| `capital_utilization == 0` | `return_per_utilized_capital` = `None` |
| 基准不可用 | `alpha`/`beta`/`IR`/`tracking_error` = `None` + `flags += ["benchmark_unavailable"]` |

> `json.dumps(float("inf"))` 会产出 `Infinity`，**不是合法 JSON**，前端 `JSON.parse` 直接抛异常。
> 这是把 `inf` 排除在外的硬理由。

⚠️ **笔数盈亏比与金额盈亏比会背离**（实测 1.192 vs 0.944），说明亏损单平均持仓金额更大。
**只看笔数会误判为正期望，必须同时看 `profit_factor`。**

**老指标去留（定稿）** 🔴：**不保留 `win_rate` / `num_stocks` 的兼容别名**。
三者（`stats.py`、`report.py`、`launcher/StrategyPage.cs`）在同一提交内改完；
留兼容层只是把一次性的同步成本换成长期技术债。`metrics` 增加
`schema_version: 2`，报告落盘时据此标注口径版本。

**基准口径（定稿）** 🆕：

- **默认基准 = 等权全池买入持有**：`r_t = (1/N) × Σ_i ret_i(t)`。
  零数据依赖（直接复用已有 close）、永远可得、且与策略**同池**——
  排除了「池子选得不一样」这个干扰项，比挂中证1000 更适合横向对比。
- 若本地有指数行情（000852 / 000905 / 000300），**额外**输出对指数的
  `alpha` / `beta` / `IR` / `tracking_error`。
- 两者都取不到时：按上表返回 `None` + `benchmark_unavailable` flag，
  🔴 **不允许静默**。

## 交易明细日志 🆕

`trades` 数组需支持**交易级归因**，单条结构：

```json
{
  "code": "000004", "name": "...",
  "buy_date": "2023-11-06", "buy_price": 16.73, "shares": 600,
  "buy_notional": 10038.0,
  "buy_reason": "DIF 上穿 DEA",
  "buy_meta":  {"dif": 0.0312, "dea": 0.0287, "rank": 3},
  "sell_date": "2023-11-13", "sell_price": 19.52, "sell_notional": 11712.0,
  "sell_reason": "跌出前20",
  "sell_meta": {"dif": 0.0244, "dea": 0.0301, "rank": 47},
  "ret": 0.1668, "pnl": 1674.0, "holding_days": 5, "open": false,
  "cost": 14.2,
  "feasible": true, "blocked_by": null, "delisted": false
}
```

要点：

- **买卖两端都要带 `meta`** 🆕：买入值算「因子 → 后续收益」（IC），
  **卖出值同样重要**——两者相减才是因子变化量，才能回答"是因为因子发散才卖、还是被别的票挤出去的"。
- `reason` 与 `meta` 并存，前者人读、后者机器读。
- **未平仓交易需 mark-to-market** 🆕：期末按最后收盘价虚拟平仓并标记 `"open": true`。
  ⚠️ 注意口径：`win_rate_trade` / `profit_factor` 只统计 `closed_eff`（**已平仓**），
  这部分浮盈浮亏**不进这两个指标**——此口径须写进报告。
- **可行性字段**（`feasible` / `blocked_by` / `delisted`）与 `pnl` **口径不同**：
  `pnl` 是权重口径的理想值，`feasible=false` 的那些笔**不改变净值**。
  报告里两组数必须分区展示，避免被读成"实际能赚到的钱"。
- `per_stock` **双向截断**：旧版只给 `total_return` 降序头部 500 条（共 5559），
  看不到亏损端，归因做不了；改为 top N + bottom N 各取。
- `data_errors` 与 `scope.skipped` 内容重复，去重。

## 改动波及面 🔴

资金口径与指标改名会外溢到框架之外，以下位置**必须同一次提交内改完**，
否则报告/页面静默变空：

| 位置 | 现状 | 需要做什么 |
|---|---|---|
| `strategies/core/stats.py` | `port = Σsret / n`；`metrics.win_rate` / `num_stocks`；`trade_detail` / `all_trades_detail` 带 `band` 参数 | 改为组合权重口径；删除全部 `band`；新增 ⑦ 指标组与边界约定；`schema_version: 2` |
| `strategies/core/backtest.py` | `run_backtest` 与 `run_recommend` **各一处** `band = initial_capital / len(usable)`，并透传给 `all_trades_detail`；`run_recommend` 返回体含 `band` 字段、`amount = band * cur` | 删除 `band`；改 `amount = target_capital × w`；返回体 `band` 字段删除、改为 `target_capital` |
| `strategies/core/backtest.py` | `_prepare` 只取 `fields=["close"]`；`run_recommend` 用 `_IdxOnly` 省内存 | 新增 `universe_mode` 与 `load_bars_raw_for(changed_codes)`，**保持 `_IdxOnly` 思路不变** |
| `strategies/report.py` | 摘要读 `metrics.win_rate`；`per_stock` 只留前 500 | 改读新指标名；`per_stock` 双向截断；写入 `scope.warnings` |
| `launcher/StrategyPage.cs` | 13 处引用指标 / 资金字段 | 逐处对齐新字段名与金额口径 |
| `strategies_routes.py` | 入参含 `commission` / `initial_capital` / `benchmark` | 新增 `stamp_duty` / `slippage` / `min_commission` / `lot_size` / `universe_mode` 入参 |
| `strategies/macd.py` / `scr90.py` / `rps_score.py` | 逐票独立发信号，`weight=1.0` | **全部重写为组合式**：维护 `held` 集合、每调仓日全量重发（见下节） |

> ⚠️ **三个现存策略必须全部重写，这不是可选项。**
> 「缺失即清零」+「`Σweight>1` 抛错」叠加后，逐票独立发信号的策略会自我摧毁：
> macd 在 D1 只有 A 金叉 → 发 A，Σw=1 → **100% 砸进 A**；
> D2 只有 B 金叉 → 发 B，A 缺失记 0 → **清掉 A，100% 砸进 B**。
> 每个发信号日就是一次全组合对倒。组合再平衡的语义本身没错（不在目标组合里就该卖），
> 但策略必须自己维护 `held` 并整组重发。

## 新增一个策略
1. 在 `strategies/` 下新建 `<id>.py`（**文件名即 id**）；
2. 用 `@strategy(...)` 声明 `inputs` / `params` / `outputs` / `requires_pool`；
   函数签名首参为 `ctx: StrategyContext`，其余为各 `ParamSpec` 同名参数：`def f(ctx, p1=.., p2=..) -> list[Signal]`；
3. 函数体内用 `data.load_bars(ctx.codes, fields=inputs)` 主动拉取所需字段，逐标的计算；
4. **自己维护 `held` 持仓集合**，每个调仓日输出**完整目标组合**
   （`weight ≥ 0`、`Σweight ≤ 1`），并附 `reason` / `meta`；
5. 保存即生效（包加载时自动扫描注册，无需改任何其它文件）；
6. 前端「策略选择与编辑」页会自动出现该策略及其参数表单。

## HTTP 接口（`strategies_routes.py`，前缀 `/api/strategies`）
- `GET  ""` 策略清单（含 `inputs/outputs/params/requires_pool`）
- `GET  /codes` 本地已下载股票代码（按 `universe_mode` 过滤）
- `POST /backtest` 运行回测；`use_all=True` 且超过上限（800）时抽样并回传 `scope.sampled`

  🆕 **抽样必须可复现**：旧版"自动随机抽样"导致同一请求两次结果不同。
  改为**规则化抽样**（按成交额/市值取头部）或固定 `seed`，并在 `scope` 回传抽样方式与 seed。

## 已知待办（P2/P3）

- **幸存者偏差收口**：接入历史指数成分股（`index_constituent` 模式）。
  在此之前，所有非 `pool_snapshot` 模式的报告都带强制偏差告警。
- **并行加速**：全市场 5559 只约 25 分钟；逐标的计算彼此独立，可按 code 并行。
- **周期回测**：需先改 `stats` 的年化口径并重定义 T+1 与持仓天数语义。
- **`range_is_effective`**：请求区间被裁剪时（如请求 2023-10-07 实际 10-09 起）当前静默，应显式 WARN。

---

## 决策记录（2026-10-14）

本轮评审已定稿的项，附代码依据。诊断部分全部经核对成立。

| 议题 | 决定 | 依据 |
|---|---|---|
| 整手/涨跌停/停牌是否进净值 | **不进**，只出明细与可行性指标 | 横向比较要的是信号质量口径；执行可行性单独成组指标 |
| 成本模型 | **比例成本 + 最低佣金进净值**，离散约束出明细 | 最低佣金 `max(notional×rate, 5.0)` 无需股数即可精确表达 |
| 调仓日判定 | `rebalance_dates = union(信号日期)`，强制全量重发 | 组合再平衡语义要求；代价是三个策略全部重写（已记入波及面） |
| weight 分母 | **组合权重，废弃 `allocation` / `band`** | 池子分母会重引入跨池漂移，并对低 breadth 策略有系统性偏袒 |
| `Σweight > 1` | **抛错**，不再静默归一化 | 静默归一化会把策略 bug 变成半仓运行，极难排查 |
| 现金来源 | 只由 `position` 显式产生 | 归一化会吃掉现金语义，使 `capital_utilization` 恒为 1 |
| 涨跌停/停牌判定价 | **raw**（净值用 hfq） | hfq 平滑除权跳空，会掩盖真实涨跌停 |
| 幸存者偏差 | `universe_mode` 三档 + **强制告警** | 偏差存在是事实，藏起来才是问题 |
| 基准 | **默认等权全池买入持有** | 零数据依赖、与策略同池，排除池子选择差异 |
| 空值约定 | 一律 `None`，禁 `0` / `inf` | `json.dumps(inf)` → `Infinity`，非合法 JSON，前端解析直接抛异常 |
| 老指标兼容 | **不留别名**，同仓三处同步改 | 一次性同步成本 < 长期技术债 |

**核对通过、无需改动**：`meta` 与 `reason` 分工、日/交易胜率拆分、未平仓 mark-to-market、
`per_stock` 双向截断、印花税与最低佣金的必要性、抽样可复现、"历史结果作废重跑"的诚实声明。