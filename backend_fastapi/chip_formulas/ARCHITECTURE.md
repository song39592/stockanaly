# 筹码峰公式包架构说明（新增公式前必读）

本目录实现**筹码分布（筹码峰）公式**。**任何人在此新增/修改公式前，必须通读本文件。**

与 `strategies/`（回测策略）同构：一个文件一个公式、文件名即 id、包加载时自动校验。
与 `indicators/`（技术指标）**不同构**：指标输出「与 K 线同 date 的序列」，
筹码分布输出「按**价格**分箱的直方图」，横轴不是时间——这正是它单独成包的原因。

---

## 1. 目录结构

```
backend_fastapi/chip_formulas/
    __init__.py        包入口：暴露 compute / compute_matrix / list_formulas / FORMULAS
    ARCHITECTURE.md    本文件（新增公式前必读）
    core/base.py       注册机制（@chip_formula）+ ChipContext + 统一入口 compute()
    core/data.py       【唯一数据入口】装配 ChipInput（行情 / 换手率 / 均价 / 价格轴）
    core/spread.py     筹码摊布的**分布原语**（triangle_cdf / uniform_cdf）
    core/registry.py   LOAD 时校验（导入 / 唯一 / 元数据 / 签名 / 冒烟）
    tri_decay.py       三角形分布 + 换手率衰减（默认公式）  ← 文件名即 id
```

> 新增公式**只需新建 `<id>.py`**：`__init__.py` 会扫描目录并校验，无需手动 import。
> `category` 只是分组元数据，不再是文件边界。

---

## 2. 核心约束（⚠️ 最重要）

1. **唯一数据入口**：`core/data.py` 是包内**唯一**允许 import 外部取数模块
   （`indicators.data` / `price_service`）的文件。
2. **公式实现文件禁止直连外部**：不得 `import price_store` / `akshare` / 读文件 / 发请求。
   实际上公式**通常不需要取数**——输入全在 `ctx` 里，公式只管「筹码怎么摊、怎么衰减」。
   `core/data.py` 取数时也不自己合样，而是经 `indicators.data` → `kline_service`
   （全项目唯一的「取数 + 周期合样」出口），因此筹码与 K 线、指标用的是同一根 K 线。
3. **不自行归一化**：返回**相对**筹码量（非负即可），core 统一按行归一化到 100%。
4. **逐日演进、逐日留档**：返回 `(交易日数, 分箱数)` **矩阵**，第 i 行即「第 i 日收盘时的
   筹码分布」。光标回溯就是按日期取一行，因此**不能只返回最后一天**。
5. 价格轴（edges / centers）由 core 统一构造，覆盖**整个迭代区间**（含预热段），
   公式不得自行改 bin——否则各帧之间无法比较。

---

### 周期（day / week / month）

`compute(..., period=)` 与 `core/data.load_input(..., period=)` 都带周期：
周线的一根 = 一周的成交量合计 + 一周高低区间，因此
**换手率（成交量 ÷ 流通股本）自然就是周换手率**，衰减按周期步进，公式无需感知周期。
必须在**复权之后**再合样（先 `load_bars(adjust=...)` 再 resample），
否则除权当周的 OHLC 会跨价格台阶。

## 3. 公式契约

```python
@chip_formula(
    id="tri_decay",                       # 必须等于文件名
    name="三角形分布 · 换手率衰减",
    category="chip",
    description="……",
    params=[ParamSpec("decay", "float", 1.0, min=0.0, max=5.0, label="衰减系数")],
)
def tri_decay(ctx: ChipContext, decay: float = 1.0) -> np.ndarray:
    ...
    return np.zeros((ctx.days, ctx.bins))     # 相对筹码量，非负
```

- 首参必须是 `ctx`（`ChipContext`）；其余参数**都要有默认值**且与 `ParamSpec` 一一对应。
- `ChipContext` 字段：`dates / open / high / low / close / volume(手) / turnover(小数) /
  vwap(当日均价) / float_shares(股) / edges / centers / p_lo / p_hi / adjust`。
- 返回值：二维 `np.ndarray`，形状严格等于 `(ctx.days, ctx.bins)`，元素有限且非负。

### 迭代区间 = 窗口 + 预热（core 负责，公式无感）

`days` 是**输出帧数**（= K 线可见根数）。core 在窗口之前再取等长的一段（`WARMUP_RATIO`）
一并喂给公式，只为把筹码状态养熟——否则窗口首日的筹码只累积了一天，光标移到最左会
退化成一根尖刺（实测首帧 90% 集中度 0.009、获利比例 0.88%，加预热后为 0.09 / 38%）。
公式照常输出全部行，core 只切最后 `days` 行。窗口选「全部」时没有更早数据，预热为 0。

---

## 4. LOAD 时校验（唯一闸门，共 5 步）

见 `core/registry.py::_validate_module`：

1. 文件可被导入；
2. 恰好用 `@chip_formula` 注册 1 个，且 **id == 文件名**；
3. 元数据完整（name / category 非空，params 是 `ParamSpec` 列表且 type ∈ int/float/choice）；
4. 签名：首参 `ctx`，其余参数有默认值且与 ParamSpec 对齐；
5. **冒烟测试**：用确定性假 `ChipContext`（40 日 × 32 箱）跑一次，经严格
   `base._as_matrix` 校验返回值形状 / 有限性 / 非负性，且要求确实产生了筹码。

**未通过者直接剔出 `FORMULAS`**：既不出现在 `/api/chip/dist/formulas` 清单，也不可被计算。
因此运行期**不做任何冗余兜底**——`_as_matrix` 遇到非法返回直接抛错。
与策略注册中心的一个差异：**不做 mtime 缓存**，公式少、冒烟仅毫秒级，保证改完文件立刻生效。

---

## 5. 标准化输出（HTTP 契约）

`GET /api/chip/dist?code=&formula=&days=&bins=&adjust=` 返回：

| 字段 | 说明 |
|---|---|
| `formula` | {id, name, params} 实际生效的公式与参数 |
| `period` | K 线周期 day / week / month（与 K 线展示一致） |
| `bars` / `warmup_bars` | 输出帧数 / 预热根数（预热不输出） |
| `bins[]` | {lo, hi, price, pct} 最新一天的筹码分布（价格轴在此） |
| `frames` | {dates[], bins, pct[][], stats[]} **逐日快照**，三者一一对应 |
| `stats` | 最新一天的统计：avg_cost / peak_price / profit_ratio / scr90 / close |
| `lockup_applied` | 锁仓修正是否已启用（当前固定 false） |

> `frames.pct[i]` 与 `bins` 共用同一价格轴；前端光标左右移动只切索引，不再回服务端。

---

## 6. 与指标体系的分工（重要）

筹码峰的**延伸量**里，凡是「按日期的序列」都应独立成指标文件（在 `indicators/` 下），
由 `indicators/data.py::get_chip_frames()` 复用本包的矩阵，**不重复计算**：

| 量 | 形态 | 归属 |
|---|---|---|
| 分布矩阵、价格轴、流通股本 | 非序列 / 二维 | **留在本包**（进不了指标体系） |
| 获利比例、平均成本、峰位价、集中度 | **按日期的序列** | `indicators/` 下的指标文件 |
| 换手率 | 按日期的序列 | `indicators/turnover.py`（panel="none"，内部/导出复用） |

指标侧经 `indicators/data.py` 的 `get_chip_frames()` 拿矩阵，**函数内延迟 import 本包**
以打断 `indicators.data ↔ chip_formulas.core.data` 的循环依赖；指标实现文件本身
不得 import 本包（保持 §2 的单向依赖）。

---

## 7. 新增公式步骤

1. 新建 `<id>.py`（如 `uniform_decay.py`），`from .core.base import chip_formula, ParamSpec, ChipContext`。
2. 写公式函数，返回 `(交易日数, 分箱数)` 相对筹码量矩阵；需要摊布时用 `core/spread.py` 的原语。
3. 加 `@chip_formula(...)`：id **等于文件名**、中文 name、params 规格。
4. **无需手动登记**：包加载时自动扫描校验。
5. 本地验证：
   ```python
   import chip_formulas
   print(chip_formulas.list_formulas())
   print(chip_formulas.validation_report())          # 看是否被剔出、为什么
   r = chip_formulas.compute("600000", "tri_decay", {"decay": 1.5}, days=250)
   print(r["formula"], r["bars"], len(r["frames"]["dates"]))
   ```
6. 想让延伸指标（获利比例等）跟着新公式走：给指标传 `formula` 参数即可。
