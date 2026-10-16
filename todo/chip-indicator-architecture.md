# 双分布筹码：作为"指标级"分发布局接入（架构定义）

> 2026-10-16 定调（路径 B 的精确化）。
> 本文是**架构定义**，不含实现。落地前需拍板 §8 的决策点。

---

## 0. 背景与决策

双分布筹码（`pct_fix` 固定筹码 / `pct_hot` 强换手）本质是「每日一个价格直方图」=
向量值时间序列。此前 `chip-peak-variants.md` 走的是路径 (A)（留在右侧筹码窗口
`ChipPanel` 渲染）。现改为路径 (B)：让它**像指标一样可选 / 可调度**，并**共用 K 线价格轴**
渲染。

⚠️ **但 `indicators/base.py:14-26` 的模块 docstring 已刻意把 `right` 面板从
`VALID_PANELS` 删除**，理由正是「筹码分布 x 轴是价格、指标 x 轴是时间，维度不同」。
因此 (B) **不是**「把通用指标契约扩成 2D 然后丢进 lower/main 面板」——那会重蹈被删除的
`right` 的覆辙。正确做法是：**分布仍是专用渲染单元，只是新增一种「共用价格轴」的几何，
并接入指标的可选/调度通道**。契约只在 `kind="dist"` 上做**加法**，绝不污染现有 1D 路径。

---

## 1. 架构分层（最终形态）

```
                         ┌─────────────────────────────────────────┐
   用户勾选/调度          │ 指标列表 & 调度层（list_indicators / compute）│
   （像 KDJ/BOLL 一样可选）│  → 双分布在此以「indicator 身份」出现        │
                         └───────────────┬─────────────────────────┘
                                         │ 按 panel/kind 分发
                 ┌───────────────────────┴───────────────────────┐
                 │                                               │
        kind="line"|"bar"                          kind="dist"（新增）
                 │                                               │
        通用 line/bar 渲染                    价格轴共享分发布局渲染器（新增）
        （lower / main，y=指标值）            （y=价格，复用 K 线 priceToY）
                 │                                               │
                 └───────────────────────┬───────────────────────┘
                                         │ 经稳定接口取数
                         ┌───────────────▼───────────────────────┐
                         │ 稳定接口 get_chip_distribution(...)      │
                         │ （indicators/data.py，延迟 import 断环）  │
                         └───────────────┬───────────────────────┘
                                         │ 内部全 volatile
                         ┌───────────────▼───────────────────────┐
                         │ chip_formulas 包（N / B / 分类 / 衰减律） │
                         │ ChipResult 扩展 pct_fix / pct_hot        │
                         │ tri_decay_dual.py（薄公式）              │
                         └─────────────────────────────────────────┘
```

**三层职责边界（不可越界）**：
1. **指标列表/调度层**：双分布以 indicator 身份进入勾选列表与 `compute()` 调度；
   但**只认 `kind` 与 `panel`，不关心分布内部算法**。
2. **渲染层**：`kind="dist"` 走**专用**价格轴共享渲染器，**绝不**进 `LowerPanel` /
   `SetMainIndicators` 的通用 line/bar 分支。
3. **筹码计算层（volatile）**：`chip_formulas` 内所有动荡（N 定义、B 阈值、三分类、
   衰减路线①/②）都封死在包内，对外只暴露稳定接口。

---

## 2. 稳定接口（回应「筹码部分大量反复修改」的核心）

> 这是本文最重要的约束：**筹码内部会反复改，但指标/渲染层必须稳定。**
> 手段 = 在 `indicators/data.py` 暴露一个**窄而稳定**的取数接口，把 churn 关在后面。

```python
# indicators/data.py 新增（仍是延迟 import chip_formulas 断环，复用既有 seam）
@dc.dataclass
class ChipDistFrames:
    dates: list[str]          # 与 get_ohlcv 同序
    centers: np.ndarray       # 价格轴中心（分箱），双分布两侧共用同一价格轴
    pct_fix: np.ndarray       # (days, bins)，固定筹码（左 / 负侧）
    pct_hot: np.ndarray       # (days, bins)，强换手（右 / 正侧）

def get_chip_distribution(code: str, formula_id: str | None = None,
                          days: int | None = None, bins: int = 80,
                          adjust: str = "qfq") -> ChipDistFrames:
    """双分布的唯一对外入口。返回前一次性算好两侧，调用方只读。"""
    ...
```

- 所有「反复修改」（阈值、衰减、B 口径、EMA 路线、N 分布统计）**只在 `chip_formulas`
  内发生**；`get_chip_distribution` 的签名与返回结构**冻结**。
- 指标文件（如未来的 `chip_dual.py`）和渲染器都只依赖 `ChipDistFrames`，
  因此筹码算法再怎么改，这两层都不用动。
- 与现有 `get_chip_frames` / `chip_rows_of`（供 WIN/COST/SCR 标量指标）**并列共存**，
  不改动既有标量路径。

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
**x 轴 = 时间**（与 K 线共享时间轴），筹码% 表现为**宽度/色温**（镜像：fix 向左、hot 向右）。
即「时间 × 价格」的筹码分布剖面/热力，价格刻度与蜡烛严格对齐。

```
  价格↑
   │        ╱│╲          ← 某日的固定筹码（左，fix）
   │      ╱  │  ╲        ← 某日的强换手（右，hot）
   │    ╱    │    ╲
   ├────┼────┼────┼──→ 时间
```

实现要点：
- **新增专用渲染方法**（如 `KLineChart.SetDistributionIndicators(distPanels)`），
  **不**复用 `SetMainIndicators`/`SetIndicators(LowerPanel)` 的通用 line/bar 绘制。
- 复用 K 线已有的 `priceToY`（价格→像素）与 `dateToX`（时间→像素）映射，
  因此价格轴天然与蜡烛对齐——这正是「共用价格轴」的含义，也是它与被删 `right`
  （独立 x=价格 面板）的本质区别。
- `maxPct = max(|pct_hot|, |pct_fix|)` 决定满刻度宽度；正负镜像以中轴（价格轴竖线）为基准。
- 与现有 `ChipPanel`（右侧价格×占比 窗口）**并存不替换**：两者是同一数据的两种视图
  （ChipPanel 是单日纵深剖面；本渲染器是时间×价格演化）。可后续择一，但首版都留。

---

## 5. 调度/列表层（让它「像指标一样可选」）

- `chip_dual` 在 `indicators/` 下注册 `@indicator(id="CHIPDUAL", name="筹码双分布",
  panel="main", ...)` —— `panel="main"` 仅表示「共用价格轴」（与 MA/BOLL 同义），
  **渲染仍由 §4 的专用渲染器接管**，不会进 line/bar 分支。
- `list_indicators` / 前端勾选列表**自动包含**它（沿用现有自动扫描），用户勾选体验与
  KDJ/BOLL 无差别。
- `compute()` 调度链：识别 `kind="dist"` 后，由渲染层走专用单元。

---

## 6. 不动的部分（明确边界，防范围蔓延）

- 所有现有 1D 指标（MA/EMA/BOLL/KDJ/RSI/WR/MACD/WIN/COST/SCR）：零改动。
- `chip_formulas` 的**单分布**链路（`tri_decay` → `ChipResult.pct` → 现有 ChipPanel）：
  **保持不动**（决策 #4：双分布由指标内自实现，不依赖 `chip_formulas`）；
  若后续想让单分布窗口也复用双分布数据，再考虑增量 `pct_fix`/`pct_hot` 字段。
- web 前端（`frontend/`）：筹码消费方仍为 0，本次不涉及。
- `VALID_PANELS` 枚举**不新增** `right`；分布通过 `kind="dist"` + 现有 `panel="main"`
  表达，避免重蹈被删 `right` 的覆辙。

---

## 7. 落地顺序（churn 隔离原则）

1. **先冻结稳定接口**（§2）：`ChipDistFrames` + `get_chip_distribution` —— 这道墙立起来，
   后面筹码怎么改都伤不到指标/渲染层。
2. **指标内自实现**（决策 #4）：new `chip_dual.py` 内部完成 N/B/三分类/衰减，复用
   `data.get_ohlcv`/`get_turnover`/`get_float_shares`/`get_vwap`，**不新增** `chip_formulas`
   公式、不依赖 `chip_formulas` 包；`chip_formulas` 单分布链路保持不动。
3. **契约扩展**（§3）：`VALID_KINDS` + `series_dist` + `_normalize_series` 的 dist 分支。
4. **渲染器**（§4）+ **调度层**（§5）：价格轴共享分发布局 + 勾选接入。
5. 统计前置（§8 决策点 1）：先跑 N 分布 / VOL-B 分布，定阈值与衰减路线，再收敛参数。

---

## 8. 需拍板的决策点

| # | 决策 | 选项 | 影响 |
|---|---|---|---|
| 1 | 双分布几何 | ✅ **(b) 时间×价格热力图（色温表强度）**（已定）<br>(a) 时间×价格镜像剖面（备选） | 渲染器实现不同 |
| 2 | 与现有 `ChipPanel` 右侧窗口关系 | ✅ **二选一：新热力图取代右侧 ChipPanel 窗口**（已定）/ 并存（备选） | 决定首版是否双视图 |
| 3 | `panel="main"` 语义 | ✅ **沿用 `panel="main"`（=共用价格轴），枚举不动**（已定）/ 新增 `panel="price"`（备选） | 枚举是否动 |
| 4 | 衰减/分类实现位置 | ✅ **指标内自己实现**（new `chip_dual` 内部完成 N/B/三分类/衰减，不依赖 `chip_formulas.tri_decay_dual`）（已定）；具体衰减路线①/②与阈值仍按 chip-peak-variants.md §3/§1.4 在实现时定 | — |
| 5 | 分类阈值首版 | 三分类 `<B / [B,2B) / ≥2B`，判别量=瞬时 VOL | 见 chip-peak-variants.md §1.4 |

---

## 9. 与 chip-peak-variants.md 的关系

- 筹码**算法侧**（N/B/分类/衰减/残差法）两路径完全一致，本文不再重复，直接引用该文档。
- 本文只解决：**算法产物如何以「指标级、共用价格轴」的方式被调度与渲染**。
- 该文档 §5 列的「C# 渲染要改 `StockPage.cs:1325-1361`」在 (B) 下**改为**在新建的
  价格轴共享渲染器里实现，原 ChipPanel 那段由新渲染器直接取代（决策 #2：二选一）。
