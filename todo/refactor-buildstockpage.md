# 后期待办 1：把 `BuildStockPage()` 重构为子构建方法

## 背景
在 `StockPage.cs` 的"整方法搬运"拆分之后，各二级页的 **Load / Render 逻辑**（`StockLoadXxx` / `StockRenderXxx` 等）
已挪到对应的 partial 文件（`StockPage.Profile/Records/Valuation/Saolei/AI/Overlays.cs`）。
但 `StockPage.cs` 里仍保留一个单体总装方法 `BuildStockPage()`（约 459 行），
它把每个二级页的 UI **内联**构建在同一方法体内，仅以 `// ---- 二级页 ① K线 …` 等注释分节。

这导致：
- `StockPage.cs` 仍偏大（约 2143 行）；
- 想再加二级页时，要在 `BuildStockPage()` 中间插入大段代码，可读性差。

## 目标
把 `BuildStockPage()` 的每个内联分节抽成独立的 `BuildStockXxxPanel()` 子构建方法，
让 `BuildStockPage()` 退化为"新建容器 + 调用子构建 + 组装 + 接线"的薄壳。
后续可再把这些子方法挪到对应的 partial 文件（彻底按二级页分文件）。

## 具体步骤
1. 按 `// ---- 二级页 ① K线` … 等分节，识别每个分节用到的 **局部变量 / 控件字段**
   （如 `_stockKline`、`_stockStrip`、`root`、`mainCol` 等）。
2. 逐个分节抽成方法，例如：
   - `BuildStockKlinePanel()` → ① K线
   - `BuildStockBasicPanel()` → 基本信息 + 所属板块
   - `BuildStockChipPanel()` → 筹码
   - `BuildStockRecordsPanel()` → 记录·消息
   - `BuildStockValuationPanel()` → 估值
   - `BuildStockSaoleiPanel()` → 扫雷
   - `BuildStockAiPanel()` → AI 分析 + 偏好
3. 分节间共享的局部变量：
   - 若是"已构建好的控件引用"且后续还要用 → 提升为 `MainForm` 的 `private` 字段；
   - 若是临时布局容器 → 作为参数传入子构建方法，或让子方法返回 `Control` 由外壳接线。
4. `BuildStockPage()` 只保留：建 `root`/`_stockStrip`、调用各 `BuildStockXxxPanel()`、把返回值 `Add` 到对应 Tab、挂事件。

## 验证
- 用 `csc` 直接编译（绕过 `build_exe.bat` 的 `pause`）确认零错误；
- 重建 `股票池追踪系统.exe` 并冒烟启动；
- 逐一打开个股页的 9 个二级页，核对 UI 与之前完全一致（行为保持，无功能变化）。

## 风险 / 注意
- 低：本质是行为保持的轻量重构，不改逻辑；
- 主要坑在**局部变量耦合**：抽取时务必把"后续还要引用的控件"正确提升为字段或返回值，
  否则会出现"对象引用未设置"或编译器报字段不存在。建议每抽一个分节就编译一次。
