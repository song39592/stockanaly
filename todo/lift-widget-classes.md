# 后期待办 2：把 `ChipPanel` / `KLineChart` 提升为顶层类

## 背景
`StockPage.cs` 仍是文件偏大的主要剩余来源，其中包含两个**自包含的嵌套控件类**：
- `KLineChart`（约 850 行）—— K 线图表控件；
- `ChipPanel`（约 970 行，含 `StockHistoryItem` 等小嵌套类）—— 筹码分布控件。

二者合计约 1800 行，占 `StockPage.cs` 当前体量（约 2143 行）的绝大部分。
它们是独立控件，理论上可以整体搬到各自文件，让 `StockPage.cs` 进一步瘦身到约 300 行级别。

## 目标
将 `KLineChart`、`ChipPanel` 从 `MainForm` 的嵌套类**提升为顶层 `internal class`**，
分别放到新文件 `launcher/KLineChart.cs`、`launcher/ChipPanel.cs`（仍在 `namespace StockPool`），
并从 `StockPage.cs` 中删除，从而显著缩小 `StockPage.cs`。

## 前置验证（必须，做完才能动手）
确认这两个类**不访问 `MainForm` 的私有成员**。
- 当前证据（指向自包含）：
  - `KLineChart` 由 `BuildStockPage()` 用 `new KLineChart()` 创建；
  - 数据通过 `SetData` / `SetCursorDate` / `SetStatus` / `SetAdjust` 等方法**注入**；
  - 交互通过 `OnRangeChanged` / `FocusForKeys` 等**回调 / 公开方法**暴露回 `MainForm`。
  - `ChipPanel` 同理，靠 `SetData` 注入、公开方法对外。
- 验证方法：在 `KLineChart` / `ChipPanel` 的类体内搜索是否引用了 `_stockXxx` 字段或
  `StockXxx` / `MainForm.` 等仅属于 `MainForm` 的标识符。
  若存在引用，需先把这些耦合改成：
  - 构造函数 / `SetXxx` 方法参数传入；
  - 或 `event` / `Action` 回调；
  才能安全提升。

## 具体步骤
1. 在 `launcher/` 下新建 `KLineChart.cs`、`ChipPanel.cs`；
2. 把对应类定义（连同其小嵌套类，如 `StockHistoryItem` 若只被 `ChipPanel` 用）复制到新文件；
3. 类修饰符由 `private class` 改为 `internal class`（仍是 `namespace StockPool`，
   因此 `MainForm` 中 `new KLineChart()` / `_stockKline` 等引用无需改动）；
4. 加 `using` 指令 + `namespace StockPool { ... }` 包裹（与现有 partial 文件头部一致）；
5. 从 `StockPage.cs` 中删除这两个类的全部定义；
6. 在 `build_exe.bat` 的 `csc` 文件列表里加入 `KLineChart.cs`、`ChipPanel.cs`；
7. 编译（直接 `csc`）+ 重建 EXE + 冒烟。

## 验证
- 编译零错误；
- 重建后冒烟启动，打开任意个股页，确认 K 线图、筹码分布窗口显示与交互正常
  （滚轮缩放、十字光标、复权/周期切换等）。

## 风险 / 注意
- 中：核心风险是**误判自包含**——若有对 `MainForm` 私有成员的隐式引用，
  提升为顶层类后会编译失败或运行时报空引用。务必先完成"前置验证"。
- 嵌套类可见性变化：`private class` → `internal class` 后，类在程序集内可见，
  对 `MainForm` 的使用无影响，但属于内部 API 暴露面扩大（可接受）。
- 建议 KLineChart、ChipPanel **分开**搬，搬一个编译验证一个，降低排错成本。
