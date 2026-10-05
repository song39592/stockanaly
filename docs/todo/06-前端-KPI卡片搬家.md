# 06 · 前端：KPI 卡片 / 卡片行 搬家（零行为改动）

> 状态：待办　|　优先级：中　|　收益 ★★★☆☆ / 风险 ★☆☆☆☆

## 问题
`AddKpi` / `VKpiRow` 定义在 `ValuationPage.cs`，但被 **3 个页面共 24 处**调用（跨文件调私有静态方法）。
它事实上已经是公共件，只是**放错了文件**，造成「估值页的东西被个股页/盘面页依赖」的耦合错觉。

## 证据

### 卡片 a：`AddKpi`（三行卡）—— ValuationPage.cs:213
- 形态：230×66，`Tag="kpi"`，无边框；三行：标签(10,7) / 值(10,23, Consolas 12.5f) / 副标题(10,46)
- 调用 24 处，跨 3 页：
  - ValuationPage.cs:474,475,476,481,486,490
  - StockPage.cs:1118,1120,1122（基本信息）、1136,1138,1141（涨停）、1964,1965,1966,1970,1972（估值）
  - MarketPage.cs:652,654,656,657（大盘资金）、993,994,995（连板）

### 行容器：`VKpiRow()` —— ValuationPage.cs:185
- `WrapContents=false`、`Margin=(0,0,0,6)`
- 调用 7 处，跨 3 页：ValuationPage:108,109；StockPage:371,393,445；MarketPage:250,307

### 卡片 b：`StKpiCard` —— StrategyPage.cs:1210（**不建议并入 a**）
- 150×66，`BorderStyle.FixedSingle`（**有边框**），值在上 / 标签在下（**排版方向相反**）
- 调用 18 处：StrategyPage.cs:547-551、669-675、873-881

### 行容器：`StCardRow()` —— StrategyPage.cs:742
- `WrapContents=true`、`MaximumSize=(900,0)`、Margin 底 8 —— 与 `VKpiRow` 的差异是**有意为之**（策略页卡片多要换行）

### 其它形态（建议不动）
- MarketPage.cs:601-626：卡片内多行（只有 1 个调用点，性价比低）
- `MktRow` MarketPage.cs:463：单行「名称…值」（5 处调用）

## 建议做法
1. 新建 `launcher/Cards.cs`（或并入现有公共文件），把 `AddKpi` + `VKpiRow` **原样搬过去**（零行为改动）。
2. `StKpiCard` / `StCardRow` **保持独立**，不要并入（见下）。

## 注意（踩坑点）
1. `Skin()`（StockPoolLauncher.cs:990）按 `tag == "kpi"` 上卡片底色 —— 搬家不要丢 `Tag="kpi"`。
2. **不要把 `StKpiCard` 并入 `AddKpi`**：它有边框、排版方向相反，会让原本无边框的 24 张卡长出边框、42 处调用点出现视觉回归，收益不抵风险。
3. 两个行容器的 `WrapContents` / `MaximumSize` 差异是有意的，合并时若统一要保留参数。

## 验收
- [ ] `AddKpi` / `VKpiRow` 已迁出 `ValuationPage.cs`，调用点全部仍正常编译
- [ ] 估值页 / 个股页基本信息 / 涨停 KPI / 估值情景 / 盘面资金 / 连板 —— 卡片外观与搬家前一致
- [ ] 浅色/深色切换后卡片底色正常
