# 02 · 前端：JSON 解析小工具收敛（约 20 个函数，4 组逐字重复）

> 状态：**已完成**（2026-10-05）　|　优先级：高　|　收益 ★★★★★ / 风险 ★★☆☆☆

> **回填实际改动**
> 新建 `launcher/J.cs`（20 个方法），**删除 27 个重复定义、替换 829 处调用点**。
>
> | 原函数 | 归到 | 处数 |
> |---|---|---|
> | `VSafe` `StGet` `DictVal` | `J.Get` | 332+3+7 |
> | `DlVal` | `J.GetFrom` | 33 |
> | `VMap` `DlDict` | `J.Map` | 24+2 |
> | `VArr` | `J.Arr` | 50 |
> | `VNum` | `J.NumOrNull` | 84 |
> | `StNum` | `J.NumOr` | 10 |
> | `ToDbl` `DlDbl` | `J.Num` | 24+4 |
> | `ScrNum` `ChipNum` | `J.NumAt` | 16+3 |
> | `ToInt` `DlInt` | `J.Int` | 10+10 |
> | `VStr` | `J.Str` | 142 |
> | `StStr` | `J.StrOr` | 25 |
> | `ScrStr` | `J.StrAt` | 6 |
> | `VFmtPct` / `VRate` `Pct` / `MktPct` | `J.Pct100` / `J.Pct100Plain` / `J.PctRaw` | 7 / 5+14 / 7 |
> | `VFmt` `F2` | `J.Fmt` `J.FmtNum` | — |
> | `Money` `FmtYi` `MktYi` | `J.Money` `J.Yi` `J.YiSigned` | 7 |
>
> 逐文件替换数：StockPage 320、MarketPage 206、ValuationPage 132、StrategyPage 88、
> DownloadPage 45、ChipRankPage 32、StockPoolLauncher 6。
>
> **`VArr` 也一并搬了**：待办原写「已收敛好的别动」，指的是**不要再去和别的副本合并**
> （它只有 1 份）。但它当时住在 `ValuationPage.cs` 里却被 `ChipRankPage` 等 4 个文件用，
> 属于第 26 项说的「公共件住在页面文件里」。本项把它搬到 `J.Arr`（纯搬家，逻辑未变）。
>
> **三处踩坑点都按要求处理**
> 1. `NumOrNull`（`double?`，失败 null）与 `NumOr` / `Num`（默认值 / 0）**三个名字并存** ——
>    估值页靠 null 显示「—」、策略页靠默认值，强行合并会让其中一页显示错。
> 2. `PctRaw` **不乘 100**，并在注释里写明原因（盘面接口已是百分数，乘 100 会静默放大 100 倍）。
> 3. `VNum` 对 `decimal` 的显式分支**原样保留**（JavaScriptSerializer 把 JSON 小数解成 decimal）。
>
> **一处有意的口径统一**：转 double 统一用 `InvariantCulture`（原先只有 `DlDbl` 这么做）。
> JSON 数字与数字字符串一律以 `.` 作小数点、与区域性格式无关；zh-CN 下两者行为完全相同，
> 所以这是「本来就对」的写法，不改变本机表现。

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

## 验收（2026-10-05）
- [x] 逐字重复的取键 4 份已删除 ✅ `VSafe` / `StGet` / `DictVal` / `DlVal` → `J.Get` / `J.GetFrom`
- [x] `ToInt` 3 份已删除 ✅ `ToInt` / `DlInt` → `J.Int`（`MktInt` / `VIntOf` 是不同口径，保留在页面里）
- [x] 百分比 4 份已合并为 3 个口径函数 ✅ `Pct100` / `Pct100Plain` / `PctRaw`
- [x] 转 double 6 份 → 3 个 ✅ `NumOrNull` / `NumOr` / `Num`（+ `NumAt` 组合版）
- [x] 转 string 4 份 → 3 个 ✅ `Str` / `StrOr` / `StrAt`
- [x] 旧函数名全仓无残留 ✅ 逐个检索 27 个名字，除 `J.cs` 自身定义外均为 0
- [x] `csc` 编译 exit 0 ✅ 一次通过（829 处替换），无 lint 报错
- [x] 三处「故意不合并」都保留 ✅ `NumOrNull` 的 null 语义、`PctRaw` 不乘 100、`decimal` 显式分支
- [ ] 实际界面核对（估值页「—」、策略页默认值、盘面涨跌幅数量级）—— **未做**（需人工过一眼）
