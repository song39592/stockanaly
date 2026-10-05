# 02 · 前端：JSON 解析小工具收敛（约 20 个函数，4 组逐字重复）

> 状态：待办　|　优先级：高　|　收益 ★★★★★ / 风险 ★★☆☆☆

## 问题
各页各自写了一套从 `Dictionary<string,object>`（JavaScriptSerializer 产物）取值/转型的小函数，
大量是**逐字等价**的复制品。

## 证据

### 「按 key 取 object」—— 4 份等价
- `VSafe` — ValuationPage.cs:659
- `StGet` — StrategyPage.cs:1237
- `DictVal` — StockPoolLauncher.cs:1857
- `DlVal` — DownloadPage.cs:596（先 `as` 再取，等价于 `VSafe(DlDict(c), k)`）
- 另有 `ScrTier`(ChipRankPage.cs:315) / `ScrParams`(:307) 同模式的第 5、6 份

→ `VSafe` / `StGet` / `DictVal` **三份逐字等价**（仅参数名不同）

### 「转字典」—— 2 份逐字等价
- `VMap` ValuationPage.cs:640 ≡ `DlDict` DownloadPage.cs:604

### 「转 double」—— 6 份
| 函数 | 位置 | 返回 | 失败值 | 特点 |
|---|---|---|---|---|
| `VNum` | ValuationPage.cs:667 | `double?` | `null` | **唯一显式处理 decimal**（:671 注释，踩过坑） |
| `StNum` | StrategyPage.cs:1238 | `double` | 传入默认值 | |
| `ToDbl` | StrategyPage.cs:1245 | `double` | 0 | ≡ `StNum(o, 0)` |
| `DlDbl` | DownloadPage.cs:615 | `double` | 0 | **唯一指定 InvariantCulture** |
| `ScrNum` | ChipRankPage.cs:356 | `double` | 0 | |
| `ChipNum` | StockPage.cs:1464 | `double` | 0 | |

### 「转 int」—— 3 份
- `ToInt` StrategyPage.cs:1244 ≡ `DlInt` DownloadPage.cs:609（**逐字等价**）
- `VIntOf` StockPage.cs:1896；`MktInt` MarketPage.cs:1182

### 「转 string」—— 4 份
- `VStr` ValuationPage.cs:684（null→""）／`StStr` StrategyPage.cs:1236（null→传入默认值）
- `ScrStr` ChipRankPage.cs:349 ≡ `VStr(VSafe(d,k))`；`MktText` MarketPage.cs:1203（空串→"—"）

### 「百分比」—— 3 份只差一个 `+`
- `VFmtPct` ValuationPage.cs:696（带符号×100）／`VRate` :703 ≡ `Pct` StrategyPage.cs:1246（不带符号×100）
- ⚠️ `MktPct` MarketPage.cs:1190（**不乘 100**，盘面接口已是百分数 —— 业务口径，绝对不能归一）

### 「定点小数/金额」—— 5 份
`VFmt`(ValuationPage:690) / `F2`(StrategyPage:773) / `Money`(:778，唯一) /
`MktYi`(MarketPage:1197，带符号) / `FmtYi`(StockPage:1083，不带符号) / `FmtShares`(StockPage:1089，唯一)

### 已收敛好的，别动
`VArr`（ValuationPage.cs:645，兼容 ArrayList 与 object[]）—— 全仓共用，只有 1 份。

## 建议做法
建 `launcher/J.cs`（partial class MainForm 里的静态工具组）：
- `J.Get(d, k)` ← 统一 4 份取键
- `J.Map(o)` ← 统一 2 份转字典
- `J.Num(o, def)` 与 `J.NumOrNull(o)` ← **必须保留两个名字**（见下）
- `J.Int(o)`、`J.Str(o, def)`、`J.Str(o)`（null→""）
- `J.Pct100(v)`（×100）与 `J.PctRaw(v)`（不乘）← **两个都要**
- `J.Fmt(double?, d)`、`J.Money(v)`、`J.Yi(v, sign)`

## 注意（踩坑点）
1. **`double?` vs `double` 不能强并** —— 估值页依赖 `null` 显示「—」，策略页依赖默认值。必须留 `NumOrNull` / `NumOrDefault` 两个名字。
2. **`MktPct` 不乘 100 是业务口径**，统一成一个会静默把盘面涨跌幅放大 100 倍。
3. **`DlDbl` 的 `InvariantCulture` 是唯一正确的区域性写法**，被 `Convert.ToDouble(o)` 覆盖会在非 zh-CN/en-US 下解析错。
4. **`VNum` 对 `decimal` 的显式分支是踩过坑的**（:671 注释：JavaScriptSerializer 把 JSON 小数解成 decimal），合并时不能丢。

## 验收
- [ ] 逐字重复的 4 份取键 / 3 份 ToInt / 3 份百分比 已删除
- [ ] 估值页「—」显示、策略页默认值、盘面涨跌幅数量级均与改动前一致
- [ ] `csc` 编译 exit 0
