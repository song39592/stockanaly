# 指标模块架构说明（新增指标前必读）

本目录实现技术分析指标。**任何人在此新增/修改指标前，必须通读本文件。**

---

## 1. 目标与范围（本期试点）

- **范围**：技术面指标（基于行情序列 OHLCV 派生）。基本面指标（PE/PB/ROE/分红/估值）本期**不做**，后续再扩。
- **计算方式**：实时计算、不落库。每次请求现算现返，无状态，不写 `indicators_store`。
- **呈现方式**：指标接口返回与 K 线同 `date` 序列的**标准化输出**，并声明自己放在哪个**面板**（主图叠加 / 下方副图 / 右侧副图），前端按面板分配渲染层。

---

## 2. 目录结构

```
backend_fastapi/indicators/
    __init__.py      包入口：暴露 compute() / list_indicators()
    ARCHITECTURE.md  本文件（新增指标前必读）
    data.py          【唯一数据入口】封装 price_store 取数，指标只能从这里取行情
    base.py          指标注册机制（@indicator 装饰器）+ 统一计算入口 compute() + 标准化输出
    registry.py      把 REGISTRY 转成前端清单 list_indicators()
    ma.py            均线 MA（主图叠加）            ← 每个指标一个文件，文件名即 id
    macd.py          MACD（下方副图）              ← 每个指标一个文件，文件名即 id
    rsi.py           RSI（下方副图）               ← 每个指标一个文件，文件名即 id
```

> 本期**每个指标独立成文件，文件名即指标 id**（如 `ma.py` / `macd.py` / `rsi.py`）。
> 新增指标时新建 `<id>.py` 并在 `__init__.py` 里 `from . import <id>` 触发注册即可；
> `category` 仅作为注册元数据（前端分组用），不再是文件边界。

---

## 3. 核心约束（⚠️ 最重要）

1. **唯一数据入口**：`data.py` 是指标包内**唯一**允许 `import price_store`（以及将来任何外部源/akshare/网络）的文件。
2. **指标文件禁止直连外部**：`ma.py / macd.py / rsi.py` 等「**每个指标一个文件、文件名即指标 id**」的实现文件**只能**
   `from .data import get_ohlcv`，不得 `import price_store`、`import akshare`、读取文件或发起请求。
   所有取数、复权、缺失处理、口径对齐都在 `data.py` 一次性解决。
3. **为什么这样设计**：取数与算数解耦 → 口径统一（复权/对齐/缺失）、便于测试（mock `get_ohlcv`）、
   且新增指标时不必关心数据从哪来。Python 不强制封装，此约束靠代码审查与本文件保证。
4. **指标不落库**：计算无状态，不新增 `indicators_store.py`；若未来需要回测缓存，再单独评估。
5. **面板声明 + 标准化输出**：每个指标必须声明 `panel`（见 §5），且函数返回必须符合 §6 的标准化结构，
   前端只消费该结构，不写死任何算法。

---

## 4. 数据入口 `data.py`

- `get_ohlcv(code, start=None, end=None, adjust="qfq") -> pd.DataFrame`
  - 返回 DataFrame：**索引为日期字符串 `YYYY-MM-DD`**，列固定 `open/high/low/close/volume`，按日期升序。
  - `adjust` 默认 `"qfq"`（前复权，用于展示型技术指标）；回测用途调用方应显式传 `"hfq"`。
  - 数据不足（< 2 行）或无数据时抛 `RuntimeError`。
- 内部 `_load(...)` 带 `lru_cache`，同一 `(code, 区间, 口径)` 只解析一次，指标常反复取同一段数据时可显著提速。
- 若指标需要成交额/换手率等额外列，只在 `data.py` 扩展，实现文件无需改动。

---

## 5. 指标注册机制 `base.py`

每个指标是一个**普通函数**，用 `@indicator(...)` 装饰即完成登记。装饰时声明
**面板位置 `panel`**，取值只能是 `"main"` / `"lower"` / `"right"`（非法值在模块导入时即报错）：

- `"main"`   主图叠加（共用主图价格坐标轴），如 MA / EMA / BOLL
- `"lower"`  主图下方副图（与主图共享时间轴、独立 y 轴，可多个堆叠），如 MACD / RSI / KDJ
- `"right"`  主图右侧副图（与主图等高并列、独立 y 轴），如需要独立纵轴的指标

```python
import pandas as pd
from .base import indicator, ParamSpec, series_line, series_bar
from .data import get_ohlcv   # 仅此一处取数（实现文件内若需取数也走这里）

@indicator(
    id="ma",
    name="均线 MA",
    category="trend",
    panel="main",                        # 主图叠加
    params=[
        ParamSpec("periods", "choice", [5, 10, 20, 60], label="周期"),
    ],
)
def ma(df: pd.DataFrame, periods=None) -> dict:
    periods = periods or [5, 10, 20, 60]
    # 每条序列用 series_line / series_bar 构造；kind 缺省 "line"
    return {"series": [series_line(f"MA{p}", df["close"].rolling(p).mean())
                       for p in periods]}
```

- `REGISTRY`：id → `IndicatorMeta`（含函数与参数规格）。id **必须唯一**，重复会抛 `ValueError`。
- `IndicatorMeta`：id / name / category / panel / params / func。
- `series_line(name, data)` / `series_bar(name, data)`：构造标准化序列单元，`kind` 分别为 `"line"` / `"bar"`。
- `ParamSpec`：参数规格（见 §9），供前端渲染与后端校验。

### 指标函数约定
- 入参 `df` 由 `compute()` 经 `data.get_ohlcv` 提供（绝不自行取数）。
- 返回 `dict`：`{"series": [series_line(...), series_bar(...), ...]}`。
- 每条 Series **必须沿用 `df` 的 date 索引**（直接基于 `df` 计算即可），
  **不得裁掉前段索引**；不足周期的位点用 `NaN` 表示（pandas 滚动/移位自然产生）。
- 数值为 float；`NaN` 由 `compute()` 序列化为 `null`，前端按 date 对齐叠加。

---

## 6. 标准化输出接口（前后端契约）⚠️ 核心

无论哪个指标、哪个面板，后端 `compute()` 都输出**同一套结构**；前端按 `panel` 字段路由到
三个渲染区。下分**后端输出**与**前端输出**两部分。

### 6.1 后端 API 输出

**(a) 指标清单** `GET /api/indicators`
```json
[
  {"id":"ma","name":"均线 MA","category":"trend","panel":"main",
   "params":[{"name":"periods","type":"choice","default":[5,10,20,60],"label":"周期"}]}
]
```
每项含 `panel`，前端据此把指标放入对应面板的「可选列表」。

**(b) 单指标计算** `GET /api/indicators/compute?code=600000&id=ma&params={...}`
```json
{
  "id": "ma",
  "name": "均线 MA",
  "panel": "main",
  "dates": ["2024-01-02","2024-01-03","..."],
  "series": [
    {"name":"MA5","kind":"line","data":[null,10.1,10.2,"..."]},
    {"name":"MA20","kind":"line","data":[null,null,"...",10.5]}
  ]
}
```

**(c) 批量计算（建议，避免前端多次请求）** `POST /api/indicators/batch`
请求：`{"code":"600000","items":[{"id":"ma","params":{}},{"id":"macd","params":{}}]}`
返回：`{"items":[ <标准化计算响应>, <标准化计算响应> ]}`

**(d) 错误响应**（任一接口失败均返回统一错误体）
```json
{"error":{"code":"NO_DATA","message":"行情数据不足: 600000 (adjust=qfq)"}}
```
常见 code：`UNKNOWN_INDICATOR`（指标 id 不存在）、`NO_DATA`（无行情）、`BAD_PARAM`（参数非法）。

**(e) 标准化计算响应字段表**

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string | 指标 id |
| `name` | string | 指标中文名 |
| `panel` | "main"\|"lower"\|"right" | 渲染面板 |
| `dates` | string[] | 交易日，与 K 线完全同序 |
| `series` | array | 该指标的 1..N 条序列 |
| `series[].name` | string | 序列名（如图例 MA5） |
| `series[].kind` | "line"\|"bar" | 线 / 柱（前端据此选渲染方式） |
| `series[].data` | number[]\|null | 与 `dates` 等长，NaN→null |

### 6.2 三个 panel 的常用数据需求

| panel | 典型指标 | series 构成（name / kind） |
|---|---|---|
| **main** 主图叠加 | MA | MA5/MA10/MA20/MA60，均为 `line` |
| | EMA | EMA12/EMA26，`line` |
| | BOLL | 中轨/上轨/下轨，均为 `line` |
| **lower** 下方副图 | MACD | DIF `line`、DEA `line`、MACD柱 `bar` |
| | RSI | RSI(14) `line`（0–100） |
| | KDJ | K/D/J 三条 `line` |
| **right** 右侧副图 | （预留） | 需要独立纵轴、与主图等高并列的指标；具体由前端定布局 |

> 设计要点：MACD 同时含 `line`（DIF/DEA）与 `bar`（柱），靠 `kind` 区分，前端无需特判指标算法。
> 一条指标可输出多条 series，且分属不同 kind，全部由 §6.1(e) 统一承载。

### 6.3 前端输出方式（前端向图表层暴露的标准结构）

前端**不写死任何指标算法**，只把后端标准化响应映射为三个面板数据集容器，供图表组件消费：

```
mainPanel:   Series[]            // 叠加在主图，共用价格轴
lowerPanels: Series[][]          // 每个 lower 指标一组，纵向堆叠成多个副图
rightPanel:  Series[]            // 右侧并列面板
```
其中每个 `Series`（= 一个指标实例）结构为：
```
{ id, name, dates, series: [{name, kind, data}] }
```

**路由与渲染规则**：
1. 按响应的 `panel` 字段分发：`main`→`mainPanel`；`lower`→`lowerPanels` 追加一组；`right`→`rightPanel`。
2. 按 `series[].kind` 选渲染图元：`line` 画折线，`bar` 画柱（如 MACD 柱）。
3. `data` 按 `dates` 与主图 K 线对齐（同序等长，null 留空）。
4. 新增指标**无需改前端渲染代码**，只要后端注册并标好 `panel`、输出符合 §6.1(e) 的结构。

---

## 7. 复权与口径

- 技术指标默认 **前复权（qfq）**，由 `get_ohlcv` 默认口径保证，全包统一。
- 成交量不受复权影响（已在 `price_store.load_bars` 处理）；指标如需成交量直接用 `df["volume"]`。
- 北交所（920/8/4 段）行情经新浪源入库，`get_ohlcv` 透明取用，指标无需特殊处理。

---

## 8. 新增指标步骤（清单）

1. **新建一个文件，文件名即指标 id**（如 `ma.py`、`rsi.py`），顶部
   `from .base import indicator, ParamSpec, series_line, series_bar`。
   一个文件只放一个指标；`category` 仅作为注册元数据（前端分组用），不再是文件边界。
2. **写指标函数**：入参 `df`，返回 `{"series": [series_line(...), ...]}`，见 §5 示例。
3. **加 `@indicator` 注册**：填唯一 `id`、中文 `name`、`category`、**`panel`**（main/lower/right）、以及 `params` 规格。
4. **（若是新文件）**在 `indicators/__init__.py` 里 `from . import <id>`（如 `from . import rsi`）触发注册
   （已注册的文件保持 import 即可）。
5. **本地验证**：
   ```python
   import indicators
   print(indicators.list_indicators())                 # 应出现新指标，含 panel 字段
   r = indicators.compute("600000", "ma", {"periods": [5, 20]})
   print(r["panel"], r["series"])                      # series 含 name/kind/data
   ```
   确认 `panel` 正确、`dates` 与行情一致、前段为 `null`、结构符合 §6.1(e) 后再联调前端。

---

## 9. 参数规范 `ParamSpec`

| 字段 | 含义 |
|---|---|
| `name` | 参数名（函数关键字参数名） |
| `type` | `"int"` / `"float"` / `"choice"` |
| `default` | 默认值；`choice` 可为列表（多线）或单值 |
| `min` / `max` | 数值范围（含），前端做输入限制 |
| `choices` | `choice` 类型的可选项 |
| `label` | 前端展示的中文标签 |

---

## 10. 禁止事项

- ❌ 指标实现文件 `import price_store` / `import akshare` / 读文件 / 发网络请求。
- ❌ 指标函数内自行连接数据库或缓存数据库写入。
- ❌ 在 `series` 里裁掉前段索引（导致与 K 线错位）。
- ❌ 重复 `id`、或把多个指标塞进一个文件；每个指标必须独立成文件且**文件名即其 id**（如 `ma.py`）。
- ❌ 用错 `panel`（主图指标误标 lower/right，或反之）导致前端渲染错位。
- ❌ 破坏 §6.1(e) 的标准化输出结构（前端只认该结构）。

---

## 11. 与现有架构的关系

- `price_store.py` 是底层存储层（SQLite 分片，提供 `load_bars` 等）。本包**不碰存储**，
  只通过 `data.py` 这一个窄接口取数。
- 后端 HTTP 层（`indicators_routes.py`，放 `backend_fastapi` 顶层并挂到 `main.py`）
  只调用 `indicators.compute / list_indicators`，不直接引用 `data.py` 底层——与 §3 约束一致。
- 本包与 `valuation_service.py` 等既有服务互不依赖，可独立演进。
