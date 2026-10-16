# 筹码峰（EMA 锁仓模型）：作为"指标级"分发布局接入（架构定义）

> 2026-10-16 定调（路径 B 的精确化）；同日把筹码峰**模型**切换为 **EMA 锁仓（方案③，
> 见 `chip-ema-lock-model.md`）**，取代早先的"双分布残差法"（`chip-peak-variants.md`，已弃用）。
> 本文是**架构定义**，不含实现。传输层（dist 契约 + 价格轴共享渲染器）与具体模型无关，沿用。

---

## 0. 背景与决策

筹码峰本质是「每日一个价格直方图」= 向量值时间序列。现采用 **EMA 锁仓模型（方案③）**：
- **红（强换手分布）**：仅换手率 > M_high×B 的交易日，把 (turnover−B) 按三角分布铺到当日
  [low,high]（峰=vwap），EMA 递归堆积；衰减全靠 EMA 时间权重，B 与记忆长度随换手周期自适应。
- **灰（锁仓分布）**：仅换手率 ≤ M_low×B 的交易日，从红色按 R 比例**染灰**（红→灰），灰不超过红。

走**路径 (B)**：把它**当指标一样可选/可调度**，并**共用 K 线价格轴**渲染（时间×价格热力图）。

⚠️ `indicators/base.py:14-26` 的模块 docstring 已刻意把 `right` 面板从 `VALID_PANELS` 删除，
理由正是「筹码分布 x 轴是价格、指标 x 轴是时间，维度不同」。因此 (B) **不是**「把通用指标契约
扩成 2D 然后丢进 lower/main 面板」，而是：**分布仍是专用渲染单元（`kind="dist"`），只在契约上做加法**，
复用 K 线价格轴。

---

## 1. 架构分层（最终形态）

```
                         ┌─────────────────────────────────────────┐
   用户勾选/调度          │ 指标列表 & 调度层（list_indicators / compute）│
   （像 KDJ/BOLL 一样可选）│  → 筹码峰在此以「indicator 身份」出现         │
                         └───────────────┬─────────────────────────┘
                                         │ 按 panel/kind 分发
                 ┌───────────────────────┴───────────────────────┐
                 │                                               │
        kind="line"|"bar"                          kind="dist"（新增）
                 │                                               │
        通用 line/bar 渲染                    价格轴共享分发布局渲染器（新增）
        （lower / main，y=指标值）            （y=价格，复用 K 线 priceToY）
                 │                          （red=强换手右 / gray=锁仓左）│
                 └───────────────────────┬───────────────────────┘
                                         │ 经稳定接口取数
                         ┌───────────────▼───────────────────────┐
                         │ compute_ema_lock_frames(...)            │
                         │ （features.chip.formulas，封装算法 churn）│
                         └───────────────┬───────────────────────┘
                                         │ 内部全 volatile
                         ┌───────────────▼───────────────────────┐
                         │ chip_formulas/ema_lock.py              │
                         │ （EMA 递归堆积红 + 锁仓染灰；N/B/分类/衰减）│
                         └─────────────────────────────────────────┘
```

**三层职责边界（不可越界）**：
1. **指标列表/调度层**：筹码峰以 indicator 身份进入勾选列表与 `compute()` 调度；
   但**只认 `kind` 与 `panel`，不关心分布内部算法**。
2. **渲染层**：`kind="dist"` 走**专用**价格轴共享渲染器，**绝不**进 `LowerPanel` /
   `SetMainIndicators` 的通用 line/bar 分支。
3. **筹码计算层（volatile）**：`ema_lock.py` 内所有动荡（M_low/M_high/R/k、EMA 记忆、B 口径、
   N 反推、死筹淡出）都封死在模块内，对外只暴露 `compute_ema_lock_frames`。

---

## 2. 稳定接口（回应「筹码部分大量反复修改」的核心）

> 这是本文最重要的约束：**筹码内部会反复改，但指标/渲染层必须稳定。**
> 手段 = 在 `features.chip.formulas` 暴露一个**窄而稳定**的取数入口，把 churn 关在后面。

```python
# features/chip/formulas/__init__.py 已暴露（复用 ChipContext/data 装配，封装算法 churn）
from features.chip.formulas import compute_ema_lock_frames

res = compute_ema_lock_frames(code, params={...}, days=None, bins=80, adjust="qfq")
# res: dates, centers(价格轴分箱中心), red(days×bins 强换手), gray(days×bins 锁仓),
#      lock_ratio(days×bins 锁仓浓度 gray/(red+gray)), close
```

- 所有「反复修改」（M_low/M_high/R/k、EMA 记忆、B 口径、N 反推、死筹淡出）**只在 `ema_lock.py`
  内发生**；`compute_ema_lock_frames` 的签名与返回结构**冻结**。
- 指标文件（`chip_peak.py`）和渲染器都只依赖 `res.red / res.gray / res.centers / res.lock_ratio`，
  因此筹码算法再怎么改，这两层都不用动。
- 与现有 `get_chip_frames` / `chip_rows_of`（供 WIN/COST/SCR 标量指标）**并列共存**，
  不改动既有标量路径；`ema_lock` 同时作为 `@chip_formula(id="ema_lock")` 注册，可直接在
  现有筹码窗口选用（返回红单矩阵，core 归一到 100）。

---

## 3. 标准化输出扩展（kind="dist"，加法、向后兼容）

现状（`base.py:_normalize_series` 135-156）：每个 series 是
`{name, kind:"line"|"bar", data:number[]}`，`data` 经 `[json_safe(v) for v in data.values]`
拍成一维。`VALID_KINDS` 当前仅 `{"line","bar"}`。

扩展方案（**只加不删**）：

| 项 | 改动 | 影响 |
|---|---|---|
| `VALID_KINDS` | 增加 `"dist"` | 仅新增枚举值，现有 line/bar 零影响 |
| `series_dist(name, axis, data, side)` 构造器 | 新增（对标 `series_line`/`series_bar`） | 新构造器，不动旧两个 |
| `_normalize_series` | `kind=="dist"` 走独立分支：`axis`/`side` 透传，`data` 作为 `(days,bins)` 二维直接 `json_safe` 逐元素，**不做一维拍平、不做行归一化**（筹码% 已在 `chip_formulas` 归一到 100） | 旧分支完全不动 |
| 响应结构 | `series` 元素兼容两种形态：`{name,kind:"line",data:[..]}` 或 `{name,kind:"dist",axis:[..],data:[[..]],side:"left"|"right"}` | 旧指标零改动 |

`side` 字段表达镜像方向：`left`=固定筹码（负宽）、`right`=强换手（正宽），
渲染器据此决定向价格轴哪侧画。

> 不采用「在 `series` 外另起 `distributions` 顶层字段」的方案：那样会让前端多一套
> 渲染分支，且偏离「distribution 就是一种 series（kind=dist）」的直观模型。
> 若后续分布类变多，再考虑提升为顶层字段不迟。

---

## 4. 价格轴共享渲染器（新几何，复用 priceToY）

这是 (B) 与 (A) 的唯一实质差异，也是「共用价格轴」的落点。

**几何定义**：渲染单元 **y 轴 = 价格**（与 K 线蜡烛共用同一 `priceToY` 映射），
**x 轴 = 时间**（与 K 线共享时间轴），筹码% 表现为**色温（时间×价格热力图）**——
红=强换手、灰=锁仓，二者共用同一价格轴、各自以色温强度表占比。
即「时间 × 价格」的筹码分布演化，价格刻度与蜡烛严格对齐。

```
  价格↑
   │   ░▒▓ 强换手(red)  │  ░▒▓ 锁仓(gray)    ← 某日：右侧红、左侧灰，共用价格轴
   │   ░▒▓       ▓▒░   │  ░▒▓       ▓▒░
   │   ▓▒░       ░▒▓   │  ▓▒░       ░▒▓
   ├────────────┼──────┼────────────┼──→ 时间
```

实现要点：
- **新增专用渲染方法**（如 `KLineChart.SetDistributionIndicators(distPanels)`），
  **不**复用 `SetMainIndicators`/`SetIndicators(LowerPanel)` 的通用 line/bar 绘制。
- 复用 K 线已有的 `priceToY`（价格→像素）与 `dateToX`（时间→像素）映射，
  因此价格轴天然与蜡烛对齐——这正是「共用价格轴」的含义，也是它与被删 `right`
  （独立 x=价格 面板）的本质区别。
- 两条 `dist` series（`side="right"`=强换手红、`side="left"`=锁仓灰）共用同一 `axis`（价格轴）；
  `maxPct = max(red, gray)` 决定满刻度色温强度。
- 取代现有 `ChipPanel`（右侧价格×占比 窗口，决策 #2：二选一）——本渲染器是时间×价格演化视图，
  原 ChipPanel 那段由新渲染器直接取代。

---

## 5. 调度/列表层（让它「像指标一样可选」）

- `chip_peak` 在 `indicators/` 下注册 `@indicator(id="CHIPPEAK", name="筹码峰(EMA锁仓)",
  panel="main", ...)` —— `panel="main"` 仅表示「共用价格轴」（与 MA/BOLL 同义），
  **渲染仍由 §4 的专用渲染器接管**，不会进 line/bar 分支。其 `compute()` 调用
  `compute_ema_lock_frames`（见 §2）拿到 red/gray，返回两条 `dist` series（红=side right、灰=side left）。
- `list_indicators` / 前端勾选列表**自动包含**它（沿用现有自动扫描），用户勾选体验与
  KDJ/BOLL 无差别。
- `compute()` 调度链：识别 `kind="dist"` 后，由渲染层走专用单元。

---

## 6. 不动的部分（明确边界，防范围蔓延）

- 所有现有 1D 指标（MA/EMA/BOLL/KDJ/RSI/WR/MACD/WIN/COST/SCR）：零改动。
- `chip_formulas` 的**单分布**链路（`tri_decay` → `ChipResult.pct` → 现有 ChipPanel 默认）：
  **保持不动**，仍作筹码窗口默认公式。`ema_lock` 作为**新增** `@chip_formula` 与之并列共存，
  用户可在筹码窗口直接选 `formula_id=ema_lock` 看新模型（零前端改动）；两者互不影响。
- `indicators/data.py` 的 `chip_rows_of` / `get_chip_frames`（WIN/COST/SCR 标量指标）不变；
  新增的 `chip_peak` 指标经同一延迟-import 桥接 `features.chip.formulas` 调
  `compute_ema_lock_frames`（与 `chip_profit` 等既有桥接同款 seam）。
- web 前端（`frontend/`）：筹码消费方仍为 0，本次不涉及（渲染器属 §4 未来步骤）。
- `VALID_PANELS` 枚举**不新增** `right`；分布通过 `kind="dist"` + 现有 `panel="main"`
  表达，避免重蹈被删 `right` 的覆辙。
- **对决策 #4 的务实修订**：当初写"双分布不依赖 chip_formulas、指标内自实现"，现改为
  EMA 锁仓模型**实现在 `chip_formulas/ema_lock.py`**（复用 `ChipContext`/`data` 装配），
  指标 `chip_peak` 经既有桥接调用之（与 `chip_profit` 同模式）。算法仍封装在自己模块内、
  指标/渲染层只依赖 `compute_ema_lock_frames` 的稳定返回，churn 隔离不变。

---

## 7. 落地顺序（churn 隔离原则）

1. **先冻结稳定接口**（§2）：`compute_ema_lock_frames` —— 这道墙已立起来，后面筹码怎么改都
   伤不到指标/渲染层（已在 `chip_formulas/ema_lock.py` 实现并通过 LOAD 校验）。
2. **模型实现**（已落地）：`chip_formulas/ema_lock.py` 完成 EMA 递归堆积 + 锁仓染灰，并注册
   `@chip_formula(id="ema_lock")`；同时暴露 `compute_ema_lock_frames` 供指标侧用。
3. **契约扩展**（§3，已落地）：`VALID_KINDS` + `series_dist` + `_normalize_series` 的 dist 分支。
4. **指标 `chip_peak`**（§5，待做）：调 `compute_ema_lock_frames` 返回两条 `dist` series。
5. **渲染器**（§4，待做+前端确认）：价格轴共享分发布局 + 勾选接入。
6. **参数收敛**：用真实数据跑 §8 决策点的阈值网格（M_low/M_high/R/k），定最终默认。

---

## 8. 需拍板的决策点

| # | 决策 | 选项 | 影响 |
|---|---|---|---|
| 0 | 筹码峰**模型** | ✅ **EMA 锁仓模型（方案③，`chip-ema-lock-model.md`）**（已定）/ 双分布残差法（`chip-peak-variants.md`，已弃用） | 算法与字段形态（red/gray vs pct_fix/pct_hot） |
| 1 | 分布几何 | ✅ **时间×价格热力图（色温表强度）**（已定）/ 镜像剖面（备选） | 渲染器实现不同 |
| 2 | 与现有 `ChipPanel` 右侧窗口关系 | ✅ **二选一：新热力图取代右侧 ChipPanel 窗口**（已定） | 决定首版是否双视图 |
| 3 | `panel="main"` 语义 | ✅ **沿用 `panel="main"`（=共用价格轴），枚举不动**（已定） | 枚举是否动 |
| 4 | 分类阈值默认 | ✅ **M_low=0.7 / M_high=2.0 / R=0.5 / k=0.5**（已定，见下注） | 见 `ema_lock.py` ParamSpec |
| 5 | 死筹淡出（§7 模型） | 显示层按价格距现价 **>30% 淡出/灰阶**（显示层处理，后端只给 red/gray/lock_ratio） | 前端 |

> ⚠️ **参数实测纠偏（§10 验证结果）**：方案③文档 §9 默认 `M_low=1.0` 实测锁仓日占比高达 ~66%，
> 远超其 §10 自估的 25~40%；`M_low=0.7` 实测中位数 ~35% 命中目标，故默认改为 **0.7**。
> 换手日占比实测仅 ~4~5%（因 `B=均值`、换手率右偏，仅极端放量日 >2×均值），方案③ §10 估的
> "~20%" 达不到且不改语义（`M_high` 降到 ~1.0 会失去"异常放量=强换手"含义），故 `M_high=2.0` 保留。

---

## 9. 与两份方案文档的关系

- **`chip-ema-lock-model.md`（方案③，现用）**：筹码**算法侧**（EMA 递归、N/B 反推、三分类、锁仓染灰、
  死筹淡出）的权威定义，本文不再重复，直接引用。本文只解决：**算法产物如何以「指标级、共用价格轴」
  的方式被调度与渲染**。
- **`chip-peak-variants.md`（双分布残差法，已弃用）**：早期双分布方案；其模型已被方案③取代，但其
  §5 列的「C# 渲染要改 `StockPage.cs:1325-1361`」在 (B) 下**改为**在新建的价格轴共享渲染器里实现，
  原 ChipPanel 那段由新渲染器直接取代（决策 #2：二选一）。
