using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Diagnostics;
using System.Text;
using System.Text.RegularExpressions;
using System.Windows.Forms;
using System.Web.Script.Serialization;
using System.IO;

namespace StockPool
{
    /// <summary>
    /// 个股分析（原生内嵌标签页）：MainForm 的拆分文件，替代 frontend/stock-analysis.html。
    /// 数据来自本机后端：GET /api/history/stock/{code}（K线 / 入池出池轨迹 / 消息面），
    /// GET /api/stock/quote（名称与现价），POST /api/stock/valuation（估值），
    /// POST /api/stock/research（AI 个股调研，返回 markdown，需配置 LLM_API_KEY）。
    /// 呈现贴近原网页：在榜统计 KPI + 自绘日 K 蜡烛图（MA5/10/20/60 + 成交量 + 入池/出池/事件标记）
    /// + 入池出池表 + 消息面时间轴 + 内联估值（完整过程见「估值计算」标签页）。
    /// </summary>
    internal sealed partial class MainForm
    {
        // ---- 个股分析页 ----
        private int _stockTabIndex = -1;
        private TextBox _stockCode;
        private Button _stockOpen, _stockRefresh;
        private Label _stockName, _stockHint, _stockStatus, _stockValStatus, _stockIndStatus;
        private KLineChart _stockKline;
        private ChipPanel _stockChip;                      // K 线右侧的筹码分布窗口
        private ComboBox _stockChipFormula;                // 筹码公式下拉（后端 /api/chip/dist/formulas 枚举）
        private string _stockChipFormulaId = null;         // 当前选中的公式 id（null = 后端默认）
        private bool _chipFormulaInit = false;             // 填充下拉时抑制 SelectedIndexChanged
        private System.Windows.Forms.Timer _chipTimer;     // 滚轮缩放的防抖重算
        private int _stockRange = 250;                     // 0 = 全部
        private string _stockAdjust = "qfq";               // qfq 前复权 / raw 不复权
        private readonly Dictionary<Button, string> _stockAdjustMap = new Dictionary<Button, string>();
        private readonly Dictionary<Button, string> _stockPeriodMap = new Dictionary<Button, string>();
        private string _stockPeriod = "day";                  // K线周期：day / week / month
        private int _stockRangeIdx = 1;                       // 范围档位：0=近6月 1=近1年 2=全部（-1=滚轮自定义）
        private Label _stockBasicStatus;                      // 基本信息状态行
        private FlowLayoutPanel _stockBasicKpi;               // 行业 / 市值 / 股本 KPI
        private FlowLayoutPanel _stockBasicLimitKpi;          // 涨停 / 连板 KPI
        private Label _stockBasicBus;                         // 主营业务
        private StockGrid _stockBasicHolders;                // 前十大股东表
        private Label _stockBlocks;                           // 基本信息页 · 所属板块（行业行）
        private Label _stockCptBlocks;                        // 所属板块（概念行）
        private Label _stockRgnBlocks;                        // 所属板块（地域行）
        private System.Windows.Forms.Timer _boardsTimer;      // 板块索引重建后的重试
        private int _stockBasicRound = 0;
        private StockGrid _stockTimeline;                   // 入池 / 出池记录
        private TableLayoutPanel _stockEvents;              // 消息面时间轴
        private System.Collections.ArrayList _stockEventData = new System.Collections.ArrayList();
        private FlowLayoutPanel _stockValKpi;               // 估值 KPI
        private StockGrid _stockValGrid;                    // 估值情景表
        private Label _stockSaoleiStatus;                   // 扫雷状态行
        private TableLayoutPanel _stockSaoleiList;          // 扫雷 · 风险清单 + 个股亮点
        private string _stockCurrent = null;
        private readonly HashSet<string> _stockIndicators = new HashSet<string>();   // 已勾选的指标 id（跨面板）
        private FlowLayoutPanel _stockIndFlow;                                       // 指标勾选区（动态生成）
        // AI 分析（调 /api/stock/research）
        private Label _stockAiStatus;                 // AI 分析状态
        private RichTextBox _stockAiBox;              // AI 分析 markdown 渲染框
        private string _stockAiMarkdown = "";         // 当前 AI 分析文本（换肤时重绘）
        // AI 分析偏好（作为 /api/stock/research 的投喂变量，持久化到本地 JSON）
        private string _aiDepth = "normal";           // concise | normal | detailed
        private string _aiHorizon = "mid";            // short | mid | long
        private List<string> _aiFocus = new List<string> { "板块", "估值", "走势", "大盘" };
        private List<CheckBox> _aiFocusCbs = new List<CheckBox>();   // 偏好页关注点勾选框
        private int _stockRound = 0;
        private readonly Dictionary<Button, int> _stockRangeMap = new Dictionary<Button, int>();

        // 二级导航：K线 / 记录·消息 / 估值 各一页（K线页不滚动，图表撑满，避免滚轮缩放与翻页冲突）
        private FlowLayoutPanel _stockSubBar;
        private Panel _stockSubBody;
        private readonly List<Button> _stockSubBtns = new List<Button>();
        private readonly List<Panel> _stockSubPages = new List<Panel>();
        private int _stockSubIndex;

        // 历史查看记录（最多 10 条，先进先出滚动覆盖）
        // 持久化到启动器自己的 %APPDATA%\StockPoolLauncher\history_stock_view.json：
        // 这是界面状态，不该写进行情数据目录（数据目录可能在别的盘、也随时会被改）。
        private class StockHistoryItem
        {
            public string Code = "";
            public string Name = "";
            public long Ts = 0;
        }
        private List<StockHistoryItem> _stockHistory = new List<StockHistoryItem>();
        private string _stockHistoryPath = null;
        private TableLayoutPanel _stockHistoryRoot = null;   // 个股页根（2 列），用于切换列宽
        private TableLayoutPanel _stockHistoryList = null;   // 历史项列表容器（可滚动）
        private Label _stockHistoryHeader = null;
        private Button _stockHistoryToggle = null;           // 折叠/展开按钮
        private bool _stockHistoryExpanded = true;

        #region 个股分析页（原生内嵌标签页）

        private Panel BuildStockPage()
        {
            var p = NewPage("个股分析");
            _stockTabIndex = _tabPages.Count - 1;
            p.AutoScroll = false;   // 内容分到二级页，整页不滚（K线页图表撑满）

            var root = new TableLayoutPanel();
            root.Dock = DockStyle.Fill;
            root.Margin = new Padding(0);
            root.Padding = new Padding(0);
            root.ColumnCount = 2;
            root.RowCount = 1;
            root.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 170f));   // 历史导航（可折叠）
            root.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100f));    // 主内容
            root.RowStyles.Add(new RowStyle(SizeType.Percent, 100f));
            p.Controls.Add(root);
            _stockHistoryRoot = root;

            // 主内容列（标题 / 查询 / 二级标签栏 / 二级页内容）
            var mainCol = new TableLayoutPanel();
            mainCol.Dock = DockStyle.Fill;
            mainCol.Margin = new Padding(0);
            mainCol.Padding = new Padding(16, 8, 16, 12);
            mainCol.ColumnCount = 1;
            mainCol.RowCount = 4;
            mainCol.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100f));
            mainCol.RowStyles.Add(new RowStyle(SizeType.AutoSize));       // 标题
            mainCol.RowStyles.Add(new RowStyle(SizeType.AutoSize));       // 查询工具区
            mainCol.RowStyles.Add(new RowStyle(SizeType.AutoSize));       // 二级标签栏
            mainCol.RowStyles.Add(new RowStyle(SizeType.Percent, 100f));  // 二级页内容

            // 历史查看导航（左侧，可折叠）
            root.Controls.Add(BuildStockHistoryPanel(), 0, 0);

            // ---- 标题 ----
            // 原先这里还有一行「前复权/不复权日K…｜K线页：滚轮缩放 · 拖动平移」的说明，
            // 已按需求移除：内容早已过时（现在有周/月周期与键盘操作），
            // 且省下的高度归 K 线（标题行是 AutoSize）。
            var head = Stack();
            var title = Lbl("📊 个股分析");
            title.Font = new Font("Microsoft YaHei UI", 14f, FontStyle.Bold);
            title.Margin = new Padding(0, 0, 0, 2);
            AddRow(head, Row(title));
            mainCol.Controls.Add(head, 0, 0);

            // ---- 查询工具区（始终可见，不随二级页滚动）----
            TableLayoutPanel b0;
            var g0 = Group("查询", out b0);
            _stockCode = new TextBox();
            _stockCode.Width = 130;
            _stockCode.MaxLength = 6;
            _stockCode.Margin = new Padding(0);
            _stockOpen = MiniBtn("打开", delegate { StockOpen(); }, 80);
            _stockName = Mute(Lbl(""));
            AddRow(b0, Row(Lbl("代码"), _stockCode, _stockOpen, _stockName));

            _stockHint = Mute(Lbl("输入 6 位代码后点「打开」；数据来自本机后端 /api/history/stock/{code}"));
            _stockHint.AutoSize = true;
            _stockHint.MaximumSize = new Size(740, 0);
            AddRow(b0, Row(_stockHint));

            // 范围按钮 + 刷新 + 状态
            // 「范围」按**时间**定义（近6月 / 近1年 / 全部），存的是档位而非根数——
            // 换周期后根数要跟着变：日线近1年≈250根，周线≈52根，月线≈12根。
            // 若直接存根数，周线下按「近1年」会画出 5 年，标签就骗人了。
            var rangeLabels = new string[] { "近6月", "近1年", "全部" };
            var rangeVals = new int[] { 0, 1, 2 };
            for (int i = 0; i < rangeVals.Length; i++)
            {
                int r = rangeVals[i];
                var btn = new Button();
                btn.Text = rangeLabels[i];
                btn.Tag = "stock-range";
                btn.AutoSize = false;
                btn.Height = 26;
                btn.Width = Math.Max(72, TextRenderer.MeasureText(rangeLabels[i], Font).Width + 22);
                btn.FlatStyle = FlatStyle.Flat;
                btn.FlatAppearance.BorderSize = 0;
                btn.Font = new Font("Microsoft YaHei UI", 9f);
                btn.TabStop = false;
                btn.Margin = new Padding(0, 0, 8, 0);
                int captured = r;
                btn.Click += delegate
                {
                    _stockRangeIdx = captured;
                    StockApplyRange();
                    if (_stockCurrent != null) StockLoadChip(_stockCurrent);   // 窗口变了，筹码重新算
                };
                _stockRangeMap[btn] = r;
            }
            _stockRefresh = MiniBtn("↻ 更新行情/消息", delegate { if (_stockCurrent != null) StockLoadHistory(_stockCurrent, true); }, 140);
            _stockStatus = Mute(Lbl("待加载"));
            _stockIndStatus = Mute(Lbl(""));

            // 复权切换（前复权 / 不复权）——先建按钮，再与「范围」并入同一行
            var adjustLabels = new string[] { "前复权", "不复权" };
            var adjustVals = new string[] { "qfq", "raw" };
            for (int ai = 0; ai < adjustVals.Length; ai++)
            {
                string a = adjustVals[ai];
                var btn = new Button();
                btn.Text = adjustLabels[ai];
                btn.Tag = "stock-adjust";
                btn.AutoSize = false;
                btn.Height = 26;
                btn.Width = Math.Max(72, TextRenderer.MeasureText(adjustLabels[ai], Font).Width + 22);
                btn.FlatStyle = FlatStyle.Flat;
                btn.FlatAppearance.BorderSize = 0;
                btn.Font = new Font("Microsoft YaHei UI", 9f);
                btn.TabStop = false;
                btn.Margin = new Padding(0, 0, 8, 0);
                string cap = a;
                btn.Click += delegate
                {
                    _stockAdjust = cap;
                    StockSetAdjustActive();
                    if (_stockKline != null)
                    {
                        _stockKline.SetAdjust(_stockAdjust);
                        _stockKline.FocusForKeys();
                    }
                    if (_stockCurrent != null) StockLoadChip(_stockCurrent);   // 价格口径变了，筹码跟着变
                };
                _stockAdjustMap[btn] = a;
            }

            // ---- 周期（日 / 周 / 月）----
            // 周线 / 月线由后端在读取时把日线合样（日线是唯一落库口径），
            // 指标与筹码也必须按同一周期重算，否则周线蜡烛会配上日线 MA。
            var periodLabels = new string[] { "日线", "周线", "月线" };
            var periodVals = new string[] { "day", "week", "month" };
            for (int pi = 0; pi < periodVals.Length; pi++)
            {
                string pv = periodVals[pi];
                var btn = new Button();
                btn.Text = periodLabels[pi];
                btn.Tag = "stock-period";
                btn.AutoSize = false;
                btn.Height = 26;
                btn.Width = Math.Max(52, TextRenderer.MeasureText(periodLabels[pi], Font).Width + 18);
                btn.FlatStyle = FlatStyle.Flat;
                btn.FlatAppearance.BorderSize = 0;
                btn.Font = new Font("Microsoft YaHei UI", 9f);
                btn.TabStop = false;
                btn.Margin = new Padding(0, 0, 8, 0);
                string cap = pv;
                btn.Click += delegate { StockSetPeriod(cap); };
                _stockPeriodMap[btn] = pv;
            }

            // 范围 + 周期 + 复权 + 刷新同处一行
            var rangeRow = Row(Mute(Lbl("范围")));
            foreach (Button b in _stockRangeMap.Keys) rangeRow.Controls.Add(b);
            rangeRow.Controls.Add(Mute(Lbl("周期")));
            foreach (Button b in _stockPeriodMap.Keys) rangeRow.Controls.Add(b);
            rangeRow.Controls.Add(Mute(Lbl("复权")));
            foreach (Button b in _stockAdjustMap.Keys) rangeRow.Controls.Add(b);
            rangeRow.Controls.Add(_stockRefresh);
            rangeRow.Controls.Add(_stockStatus);
            rangeRow.Controls.Add(_stockIndStatus);
            rangeRow.Controls.Add(Mute(Lbl("  ↑↓ 缩放 · ←→ 移动光标")));
            AddRow(b0, rangeRow);
            mainCol.Controls.Add(g0, 0, 1);

            // ---- 二级标签栏 ----
            _stockSubBar = new FlowLayoutPanel();
            _stockSubBar.Dock = DockStyle.Top;
            _stockSubBar.Height = 30;
            _stockSubBar.FlowDirection = FlowDirection.LeftToRight;
            _stockSubBar.WrapContents = false;
            _stockSubBar.Margin = new Padding(0, 0, 0, 2);
            _stockSubBar.Padding = new Padding(0);
            mainCol.Controls.Add(_stockSubBar, 0, 2);

            _stockSubBody = new Panel();
            _stockSubBody.Dock = DockStyle.Fill;
            _stockSubBody.Margin = new Padding(0);
            mainCol.Controls.Add(_stockSubBody, 0, 3);

            // 主内容列挂到根布局的右列
            root.Controls.Add(mainCol, 1, 0);

            // ---- 二级页 ① K线：图表撑满、不滚动（滚轮只缩放，不与翻页冲突）----
            var klinePage = new Panel();
            klinePage.AutoScroll = false;
            var kLayout = new TableLayoutPanel();
            kLayout.Dock = DockStyle.Fill;
            kLayout.Margin = new Padding(0);
            kLayout.ColumnCount = 2;
            kLayout.RowCount = 2;
            kLayout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100f));   // K 线
            kLayout.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 178f));  // 右侧筹码分布窗口
            kLayout.RowStyles.Add(new RowStyle(SizeType.AutoSize));       // 指标勾选 / 筹码公式下拉
            kLayout.RowStyles.Add(new RowStyle(SizeType.Percent, 100f));  // 图表撑满
            klinePage.Controls.Add(kLayout);
            var kTop = Stack();
            // ⚠️ 这里的列宽必须给**具体值**，不能沿用 Stack() 的 Percent 100。
            // 在 AutoSize 表里 Percent 会绕成「列宽 ← 表宽 ← 子项首选宽」的循环，
            // 指标勾选那行 FlowLayoutPanel（WrapContents=true）会被用 ~98px 去测量 →
            // 8 个控件各占一行 → 首选高度 208px，而 AutoSize 行取的正是它，
            // 于是 K 线上方空出一大块（实测：Percent 时 kTop=208/图表=517，
            // Absolute 时 kTop=36/图表=689，且 520~1400 宽度下空余均为 0）。
            kTop.ColumnStyles.Clear();
            kTop.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 300f));

            // 指标勾选：从后端 /api/indicators 自动枚举，按 panel 分组（主图 / 副图），none 类型不显示
            _stockIndFlow = new FlowLayoutPanel();
            _stockIndFlow.WrapContents = true;
            _stockIndFlow.AutoSize = true;
            _stockIndFlow.AutoSizeMode = AutoSizeMode.GrowAndShrink;
            _stockIndFlow.Margin = new Padding(0, 0, 0, 6);
            _stockIndFlow.Padding = new Padding(0);
            AddRow(kTop, _stockIndFlow);
            StockRefreshIndicatorList();   // 异步拉取指标清单并动态生成勾选项

            // 「更新至 …」与「个股概况（在榜统计 KPI）」已按需求移除，kTop 只留指标勾选；
            // 省下的高度全部归 K 线——row1 是 Percent 100，会自动吃满剩余空间。

            _stockKline = new KLineChart();
            _stockKline.Dock = DockStyle.Fill;
            _stockKline.Tag = "kline";
            _stockKline.TabStop = true;
            _stockKline.OnRangeChanged = delegate (int r)
            {
                _stockRange = r;
                _stockRangeIdx = -1;                  // 滚轮自定义根数：范围按钮取消高亮
                StockSetRangeActive();
                ScheduleChipReload();     // 滚轮缩放：防抖后重算（帧窗口 = 新的可见根数）
            };
            _stockKline.BackColorChanged += delegate { _stockKline.Invalidate(); };

            // 筹码分布窗口：与 K 线等高并列在右侧，价格轴与 K 线主图严格对齐
            _stockChip = new ChipPanel();
            _stockChip.Dock = DockStyle.Fill;
            _stockChip.Owner = _stockKline;
            // 光标跟随：光标在 K 线上左右移动 → 筹码窗口切到那一天的分布
            _stockKline.OnHoverDate = delegate (string d) { if (_stockChip != null) _stockChip.SetCursorDate(d); };
            _stockKline.PriceAxisChanged += delegate { if (_stockChip != null) _stockChip.Invalidate(); };
            _stockChip.BackColorChanged += delegate { _stockChip.Invalidate(); };

            // 公式下拉放在右侧列的**第 0 行**（不能压在筹码窗口上——筹码窗口必须与
            // K 线同顶同高，价格轴才对得齐）。直接放控件、不套 Panel：
            // 普通 Panel 默认高 100px，会把 AutoSize 行撑出一大块空白。
            _stockChipFormula = new ComboBox();
            _stockChipFormula.DropDownStyle = ComboBoxStyle.DropDownList;
            _stockChipFormula.Width = 170;
            _stockChipFormula.Margin = new Padding(0, 3, 0, 0);
            _stockChipFormula.TabStop = false;
            _stockChipFormula.Visible = false;             // 只有 ≥2 个公式才值得选
            _stockChipFormula.SelectedIndexChanged += delegate
            {
                if (_chipFormulaInit) return;
                var it = _stockChipFormula.SelectedItem as ChipFormulaItem;
                if (it == null) return;
                _stockChipFormulaId = it.Id;
                if (_stockCurrent != null) StockLoadChip(_stockCurrent);
            };

            kLayout.Controls.Add(kTop, 0, 0);                  // 指标勾选
            kLayout.Controls.Add(_stockChipFormula, 1, 0);     // 筹码公式下拉
            kLayout.Controls.Add(_stockKline, 0, 1);       // 图表占满剩余高度
            kLayout.Controls.Add(_stockChip, 1, 1);        // 右侧筹码分布窗口
            StockAddSubTab("K线", klinePage);

            // ---- 二级页 ② 基本信息：行业 / 市值 / 前十大股东 / 涨停与连板（/api/stock/profile）----
            var basicPage = new Panel();
            basicPage.AutoScroll = true;
            var basicStack = Stack();

            TableLayoutPanel bb;
            var gb = Group("公司概况", out bb);
            _stockBasicStatus = Mute(Lbl("打开个股后自动加载"));
            _stockBasicKpi = VKpiRow();
            _stockBasicBus = Mute(Lbl(""));
            AddRow(bb, Row(_stockBasicStatus));
            AddRow(bb, _stockBasicKpi);
            AddRow(bb, _stockBasicBus);
            AddRow(basicStack, gb);

            // 所属板块（通达信 HYBLOCK/GNBLOCK/DYBLOCK 风格）：行业黄 / 概念绿 / 地域青，
            // 每类第一个（成分股最少的「最相关」板块）加 ★。数据来自 /api/stock/boards。
            TableLayoutPanel bk;
            var gbk = Group("所属板块（行业 / 概念 / 地域）", out bk);
            _stockBlocks = BoardLine(C.Flat);
            _stockBlocks.Text = "板块标注加载中…";
            _stockCptBlocks = BoardLine(C.UpSoft);
            _stockRgnBlocks = BoardLine(C.LineCyan);
            AddRow(bk, _stockBlocks);
            AddRow(bk, _stockCptBlocks);
            AddRow(bk, _stockRgnBlocks);
            AddRow(basicStack, gbk);

            TableLayoutPanel bl;
            var gl = Group("涨停与连板（本地日线现算）", out bl);
            _stockBasicLimitKpi = VKpiRow();
            AddRow(bl, _stockBasicLimitKpi);
            AddRow(basicStack, gl);

            TableLayoutPanel bh;
            var gh = Group("前十大股东", out bh);
            _stockBasicHolders = NewGrid(new List<GridColumn> {
                new GridColumn("名次", "rank", true),
                new GridColumn("股东名称"),
                new GridColumn("股份类型"),
                new GridColumn("持股数", "shares", true),
                new GridColumn("占总股本", "pct", true),
                new GridColumn("增减", "change", true),
                new GridColumn("变动", "change_ratio", true),
            });
            AddRow(bh, _stockBasicHolders);
            AddRow(basicStack, gh);

            basicPage.Controls.Add(basicStack);
            StockAddSubTab("基本信息", basicPage);

            // ---- 二级页 ③ 记录 · 消息 ----
            var recPage = new Panel();
            recPage.AutoScroll = true;
            var recStack = Stack();
            TableLayoutPanel b3;
            var g3 = Group("入池 / 出池记录", out b3);
            _stockTimeline = NewGrid(new List<GridColumn> {
                new GridColumn("入池日期", "start"),
                new GridColumn("出池日期", "end"),
                new GridColumn("在榜天数", "days", true),
                new GridColumn("状态", "state"),
            });
            _stockTimeline.SetSortable(true);       // 该表保留排序（按日期/天数排）
            AddRow(b3, _stockTimeline);
            AddRow(recStack, g3);

            TableLayoutPanel b4;
            var g4 = Group("消息面时间轴", out b4);
            _stockEvents = Stack();
            AddRow(b4, _stockEvents);          // 全部消息直接展示，不再折叠
            AddRow(recStack, g4);
            recPage.Controls.Add(recStack);
            StockAddSubTab("记录 · 消息", recPage);

            // ---- 二级页 ③ 估值（完整过程见估值计算标签页）----
            var valPage = new Panel();
            valPage.AutoScroll = true;
            var valStack = Stack();
            TableLayoutPanel b5;
            var g5 = Group("估值计算（结果来自 /api/stock/valuation）", out b5);
            _stockValStatus = Mute(Lbl("打开个股后自动计算"));
            _stockValKpi = VKpiRow();
            AddRow(b5, Row(_stockValStatus));
            AddRow(b5, _stockValKpi);
            _stockValGrid = NewGrid(new List<GridColumn> {
                new GridColumn("情景", "label"),
                new GridColumn("增长率", "growth", true),
                new GridColumn("每股价值", "value_per_share", true),
                new GridColumn("股价/价值", "undervalued_ratio", true),
                new GridColumn("判断", "verdict"),
                new GridColumn("预测价", "target_price", true),
                new GridColumn("收益率", "return_rate", true),
            });
            AddRow(b5, _stockValGrid);
            AddRow(b5, Row(MiniBtn("在估值页打开完整计算", delegate
            {
                if (_stockCurrent != null) StockJumpValuation(_stockCurrent);
            }, 180)));
            AddRow(valStack, g5);
            valPage.Controls.Add(valStack);
            StockAddSubTab("估值", valPage);

            // ---- 二级页 ③.5 扫雷（通达信「扫雷宝 · 个股亮点」，来自 /api/stock/saolei）----
            var slPage = new Panel();
            slPage.AutoScroll = true;
            var slStack = Stack();
            TableLayoutPanel bsl;
            var gsl = Group("扫雷 · 通达信风险清单（来自 /api/stock/saolei）", out bsl);
            _stockSaoleiStatus = Mute(Lbl("打开个股后自动获取"));
            AddRow(bsl, Row(_stockSaoleiStatus, MiniBtn("↻ 刷新", delegate
            {
                if (_stockCurrent != null) StockLoadSaolei(_stockCurrent);
            }, 90)));
            _stockSaoleiList = Stack();
            AddRow(bsl, _stockSaoleiList);
            AddRow(slStack, gsl);
            slPage.Controls.Add(slStack);
            StockAddSubTab("扫雷", slPage);

            // ---- 二级页 ④ AI 分析（调 /api/stock/research）----
            var aiPage = new Panel();
            aiPage.AutoScroll = true;
            var aiStack = Stack();
            TableLayoutPanel b6;
            var g6 = Group("AI 个股分析（来自 /api/stock/research，需配置 LLM_API_KEY）", out b6);
            _stockAiStatus = Mute(Lbl("打开个股后自动分析"));
            AddRow(b6, Row(_stockAiStatus, MiniBtn("↻ 重新分析", delegate
            {
                if (_stockCurrent != null) StockLoadAi(_stockCurrent, true);
            }, 120)));
            _stockAiBox = new RichTextBox();
            _stockAiBox.Tag = "doc-ai";
            _stockAiBox.ReadOnly = true;
            _stockAiBox.BorderStyle = BorderStyle.None;
            _stockAiBox.Height = 460;
            _stockAiBox.Anchor = AnchorStyles.Left | AnchorStyles.Right | AnchorStyles.Top;
            _stockAiBox.ScrollBars = RichTextBoxScrollBars.Vertical;
            _stockAiBox.Font = new Font("Microsoft YaHei UI", 9.5f);
            _stockAiBox.BackColor = _cPanel;
            _stockAiBox.ForeColor = _cText;
            AddRow(b6, _stockAiBox);
            AddRow(aiStack, g6);
            aiPage.Controls.Add(aiStack);
            StockAddSubTab("AI 分析", aiPage);

            // ---- 二级页 ⑤ AI 分析偏好（作为投喂变量，自动存本地）----
            StockLoadAiPrefs();   // 先加载已保存偏好，供控件初始选中
            var prefPage = new Panel();
            prefPage.AutoScroll = true;
            var prefStack = Stack();
            TableLayoutPanel bp;
            var gp = Group("AI 分析偏好（作为投喂变量，自动保存到本地）", out bp);
            // ① 详细程度
            TableLayoutPanel bpd;
            var gpd = Group("① 详细程度（AI 反馈字数）", out bpd);
            StockRadioGroup(bpd, new[] { "精简", "适中", "详细" }, new[] { "concise", "normal", "detailed" }, _aiDepth,
                v => { _aiDepth = v; StockSaveAiPrefs(); StockAiPrefChanged(); });
            AddRow(bp, gpd);
            // ② 时间范围
            TableLayoutPanel bph;
            var gph = Group("② 时间范围（投喂新闻窗口与条数）", out bph);
            StockRadioGroup(bph, new[] { "短期", "中期", "长期" }, new[] { "short", "mid", "long" }, _aiHorizon,
                v => { _aiHorizon = v; StockSaveAiPrefs(); StockAiPrefChanged(); });
            AddRow(bp, gph);
            // ③ 关注点（可多选）
            TableLayoutPanel bpf;
            var gpf = Group("③ 关注点（可多选，作为额外关注维度）", out bpf);
            foreach (var f in new[] { "板块", "估值", "走势", "大盘" })
            {
                var cb = new CheckBox();
                cb.Text = f;
                cb.AutoSize = true;
                cb.Checked = _aiFocus.Contains(f);
                _aiFocusCbs.Add(cb);
                cb.CheckedChanged += delegate
                {
                    var lst = new List<string>();
                    foreach (var c in _aiFocusCbs) if (c.Checked) lst.Add(c.Text);
                    _aiFocus = lst;
                    StockSaveAiPrefs();
                    StockAiPrefChanged();
                };
                AddRow(bpf, cb);
            }
            AddRow(bp, gpf);
            AddRow(prefStack, gp);
            prefPage.Controls.Add(prefStack);
            StockAddSubTab("偏好设置", prefPage);

            StockSubSelect(0);

            _stockCode.KeyDown += delegate(object s, KeyEventArgs e)
            {
                if (e.KeyCode == Keys.Enter) StockOpen();
            };
            _stockCode.TextChanged += delegate
            {
                string digits = Regex.Replace(_stockCode.Text, "[^0-9]", "");
                if (digits.Length > 6) digits = digits.Substring(0, 6);
                if (_stockCode.Text != digits)
                {
                    int sel = _stockCode.SelectionStart;
                    _stockCode.Text = digits;
                    _stockCode.SelectionStart = Math.Min(sel, digits.Length);
                }
            };

            StockApplyRange();
            StockSetAdjustActive();
            StockSetPeriodActive();

            // 历史查看记录：从 stockanaly-data 加载并渲染左侧导航
            StockLoadHistoryFile();
            StockRenderHistoryNav();
            return p;
        }

        /// <summary>建一个二级页：按钮进标签栏，页面进内容区（由 StockSubSelect 只挂载当前页）。</summary>
        private void StockAddSubTab(string title, Panel page)
        {
            int idx = _stockSubPages.Count;

            var b = new Button();
            b.Text = title;
            b.Tag = "stock-subtab";
            b.AutoSize = false;
            b.Height = 28;
            b.Width = Math.Max(96, TextRenderer.MeasureText(title, Font).Width + 24);
            b.FlatStyle = FlatStyle.Flat;
            b.FlatAppearance.BorderSize = 0;
            b.Font = new Font("Microsoft YaHei UI", 9f);
            b.TabStop = false;
            b.Margin = new Padding(0, 0, 2, 0);
            b.Click += delegate { StockSubSelect(idx); };
            _stockSubBtns.Add(b);
            _stockSubBar.Controls.Add(b);

            page.Dock = DockStyle.Fill;
            page.Visible = false;
            page.Margin = new Padding(0);
            page.Tag = "tabpage";
            _stockSubPages.Add(page);
        }

        private void StockSubSelect(int index)
        {
            if (index < 0 || index >= _stockSubPages.Count) return;
            _stockSubIndex = index;
            // 容器里只挂当前页，避免多个 Dock=Fill 面板叠放导致布局错乱
            _stockSubBody.Controls.Clear();
            var page = _stockSubPages[index];
            page.Visible = true;
            page.Dock = DockStyle.Fill;
            _stockSubBody.Controls.Add(page);
            Skin(page);                 // 懒挂载的页首次显示前按当前主题上色
            page.Invalidate(true);
            SkinStockSubTabs();
            // 切到 K线 页就把焦点交给图表，↑↓ 缩放 / ←→ 移光标立刻可用
            if (index == 0 && _stockKline != null) _stockKline.FocusForKeys();
        }

        /// <summary>二级标签配色（跟随主题）：选中用卡片色，未选中用窗口底色。</summary>
        private void SkinStockSubTabs()
        {
            if (_stockSubBtns == null) return;
            for (int i = 0; i < _stockSubBtns.Count; i++)
            {
                bool sel = (i == _stockSubIndex);
                _stockSubBtns[i].BackColor = sel ? _cPanel : _cBg;
                _stockSubBtns[i].ForeColor = sel ? _cText : _cSub;
                _stockSubBtns[i].Invalidate();
            }
        }

        // ---- 打开 ----
        private void StockOpen()
        {
            string code = _stockCode.Text.Trim();
            if (!Regex.IsMatch(code, "^[0-9]{6}$"))
            {
                _stockHint.Text = "股票代码必须是 6 位数字（如 600519）";
                _stockHint.Tag = "bad";
                _stockHint.ForeColor = C.UpErr;
                _stockCode.Focus();
                return;
            }
            if ((_stockHint.Tag as string) != "bad")
            {
                _stockHint.Tag = "muted";
                _stockHint.ForeColor = C.Flat;
                _stockHint.Text = "数据加载中…";
            }
            _stockCurrent = code;
            StockRefreshIndicatorList();   // 每次打开同步后端指标清单（后端可能刚启动）
            StockAddHistory(code, null);   // 记录查看历史（名称稍后补全）
            StockLoadName(code);
            StockLoadHistory(code, false);
            StockLoadChipFormulas();   // 每次打开同步公式清单（后端可能刚加过公式）
            StockLoadChip(code);
            StockLoadBasic(code);      // 基本信息（行业 / 市值 / 前十大股东 / 涨停连板）
            StockLoadBoards(code);     // K 线上方板块标注（行业/概念/地域）
            StockLoadValuation(code);
            StockLoadSaolei(code);
            StockLoadAi(code);
        }

        private void StockLoadName(string code)
        {
            System.Threading.Tasks.Task.Run(delegate
            {
                try
                {
                    string resp = VRequest("http://127.0.0.1:8000/api/stock/quote?code=" + code, null);
                    var j = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(resp);
                    Invoke((Action)delegate
                    {
                        if (_stockCode.Text.Trim() != code) return;
bool ok = J.IsOk(j);
                        if (ok && j.ContainsKey("name"))
                        {
                            double? pr = J.NumOrNull(J.Get(j, "price"));
                            string nm = J.Str(J.Get(j, "name"));
                            _stockName.Text = nm + (pr != null ? "  " + pr.Value.ToString("F2") + " 元" : "");
                            StockAddHistory(code, nm);   // 补全历史记录中的名称
                        }
                        else
                        {
                            _stockName.Text = J.Str(J.Get(j, "error"));
                        }
                    });
                }
                catch (Exception ex)
                {
                    try { Invoke((Action)delegate { _stockName.Text = "名称查询失败：" + ex.Message; }); }
                    catch (Exception) { }
                }
            });
        }

        // ---- 历史（K线 / 轨迹 / 消息）----
        private void StockLoadHistory(string code, bool refresh)
        {
            int round = ++_stockRound;
            _stockStatus.Text = refresh ? "正在更新真实行情与消息…" : "正在读取K线与消息缓存…";
            _stockStatus.Tag = "muted";
            System.Threading.Tasks.Task.Run(delegate
            {
                try
                {
                    string url = "http://127.0.0.1:8000/api/history/stock/" + code + "?refresh=" + (refresh ? "true" : "false")
                           + "&period=" + (string.IsNullOrEmpty(_stockPeriod) ? "day" : _stockPeriod);
                    string resp = VRequest(url, null);
                    var j = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(resp);
                    Invoke((Action)delegate
                    {
                        if (round == _stockRound) StockRenderHistory(j);
                    });
                }
                catch (Exception ex)
                {
                    string msg = "请求失败：" + ex.Message;
                    try { Invoke((Action)delegate { if (round == _stockRound) StockFail(msg); }); }
                    catch (Exception) { }
                }
            });
        }

        private void StockFail(string msg)
        {
            _stockStatus.Text = "加载失败：" + msg;
            _stockStatus.Tag = "bad";
            _stockStatus.ForeColor = C.UpErr;
            _stockKline.SetData(new List<KBar>(), new List<KBar>(), new List<KMark>());
            // 右侧筹码窗口同步清空，避免还留着上一只股票的分布
            if (_stockChip != null) _stockChip.SetStatus("筹码未加载", false);
        }

        // ---- 副图指标（lower 面板）：勾选 → 后端计算 → 按日期对齐 → 叠加到 K 线下方 ----
        private void StockSetIndicator(string id, bool on)
        {
            if (on) _stockIndicators.Add(id); else _stockIndicators.Remove(id);
            if (_stockCurrent != null) StockLoadIndicators(_stockCurrent);
        }

        // 从后端自动枚举指标清单，按 panel 分组动态生成勾选框（none 类型不显示）
        private void StockRefreshIndicatorList()
        {
            if (_stockIndFlow == null) return;
            System.Threading.Tasks.Task.Run(delegate
            {
                try
                {
                    string body;
                    if (!GetText("http://127.0.0.1:8000/api/indicators", 15000, out body) || string.IsNullOrEmpty(body))
                        return;
                    var list = new JavaScriptSerializer().Deserialize<System.Collections.ArrayList>(body);
                    var groups = new System.Collections.Generic.SortedList<string, List<Dictionary<string, object>>>();
                    if (list != null)
                    {
                        foreach (Dictionary<string, object> it in list)
                        {
                            string panel = J.Str(J.Get(it, "panel"));
                            if (panel == "none") continue;                   // 不显示类型：不进勾选列表
                            string grp = (panel == "main") ? "主图" : "副图";
                            if (!groups.ContainsKey(grp)) groups[grp] = new List<Dictionary<string, object>>();
                            groups[grp].Add(it);
                        }
                    }
                    Invoke((Action)delegate { BuildIndicatorToggles(groups); });
                }
                catch { }
            });
        }

        private void BuildIndicatorToggles(System.Collections.Generic.SortedList<string, List<Dictionary<string, object>>> groups)
        {
            if (_stockIndFlow == null) return;
            _stockIndFlow.Controls.Clear();
            foreach (var kv in groups)
            {
                _stockIndFlow.Controls.Add(Lbl(kv.Key + "："));
                foreach (var it in kv.Value)
                {
                    string id = J.Str(J.Get(it, "id"));
                    string name = J.Str(J.Get(it, "name"));
                    var cb = Check(name, _stockIndicators.Contains(id));
                    cb.CheckedChanged += delegate { StockSetIndicator(id, cb.Checked); };
                    _stockIndFlow.Controls.Add(cb);
                }
            }
        }

        private void StockLoadIndicators(string code)
        {
            if (_stockIndicators.Count == 0)
            {
                if (_stockIndStatus != null) _stockIndStatus.Text = "";
                if (_stockKline != null)
                {
                    _stockKline.SetIndicators(new List<LowerPanel>());
                    _stockKline.SetMainIndicators(new List<ChartSeries>());
                }
                return;
            }
            // 计算前先给个提示：RPS 这类横截面指标首次要预热「全市场日 K 面板」，可能要十几秒；
            // 没有这行的话，勾选后界面毫无动静，用户会以为指标没出来。
            if (_stockIndStatus != null)
            {
                bool slow = _stockIndicators.Contains("rps");
                _stockIndStatus.Text = slow
                    ? "指标计算中…（RPS 首次约 15 秒预热全市场）"
                    : "指标计算中…";
                _stockIndStatus.Tag = "muted";
            }
            System.Threading.Tasks.Task.Run(delegate
            {
                try
                {
                    var items = new System.Collections.ArrayList();
                    foreach (var id in _stockIndicators)
                    {
                        var it = new Dictionary<string, object>();
                        it["id"] = id;
                        it["params"] = new Dictionary<string, object>();
                        items.Add(it);
                    }
                    var req = new Dictionary<string, object>
                    {
                        { "code", code },
                        { "items", items },
                        { "period", string.IsNullOrEmpty(_stockPeriod) ? "day" : _stockPeriod },
                    };
                    string json = new JavaScriptSerializer().Serialize(req);
                    string body;
                    if (!PostJson("http://127.0.0.1:8000/api/indicators/batch", json, 120000, out body) || string.IsNullOrEmpty(body))
                        return;
                    var j = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(body);
                    var itemsResp = J.Arr(J.Get(j, "items"));
                    var dates = (_stockKline != null) ? _stockKline.GetDates() : new List<string>();
                    var panels = new List<LowerPanel>();
                    var mainSeries = new List<ChartSeries>();
                    if (itemsResp != null)
                    {
                        foreach (Dictionary<string, object> it in itemsResp)
                        {
                            string id = J.Str(J.Get(it, "id"));
                            string name = J.Str(J.Get(it, "name"));
                            string panel = J.Str(J.Get(it, "panel"));
                            if (J.Get(it, "error") != null) continue;
                            var sArr = J.Arr(J.Get(it, "series"));
                            if (sArr == null) continue;
                            if (panel == "main")
                            {
                                // 主图叠加：序列直接叠加到价格轴
                                foreach (Dictionary<string, object> s in sArr)
                                {
                                    string sname = J.Str(J.Get(s, "name"));
                                    string kind = J.Str(J.Get(s, "kind"));
                                    var dArr = J.Arr(J.Get(s, "data"));
                                    var data = AlignSeries(dates, J.Get(s, "dates"), dArr);
                                    var cs = new ChartSeries { Name = sname, Kind = kind, Data = data };
                                    ApplySeriesStyle(cs);
                                    mainSeries.Add(cs);
                                }
                            }
                            else
                            {
                                // lower / right：作为下方副图面板
                                var lp = new LowerPanel { Id = id, Title = name, Weight = 1, Series = new List<ChartSeries>() };
                                foreach (Dictionary<string, object> s in sArr)
                                {
                                    string sname = J.Str(J.Get(s, "name"));
                                    string kind = J.Str(J.Get(s, "kind"));
                                    var dArr = J.Arr(J.Get(s, "data"));
                                    var data = AlignSeries(dates, J.Get(s, "dates"), dArr);
                                    var cs = new ChartSeries { Name = sname, Kind = kind, Data = data };
                                    ApplySeriesStyle(cs);
                                    lp.Series.Add(cs);
                                }
                                panels.Add(lp);
                            }
                        }
                    }
                    Invoke((Action)delegate
                    {
                        if (_stockKline != null)
                        {
                            _stockKline.SetIndicators(panels);
                            _stockKline.SetMainIndicators(mainSeries);
                        }
                        if (_stockIndStatus != null) _stockIndStatus.Text = "";
                    });
                }
                catch { if (_stockIndStatus != null) _stockIndStatus.Text = ""; }
            });
        }

        // ---- 筹码分布（K 线右侧窗口）----
        // 与指标体系的「按日期对齐的序列」不同，筹码分布是「按价位分布的直方图」，
        // 因此不走指标面板，单独向 /api/chip/dist 取数、单独渲染。
        // ---- 所属板块（基本信息页，通达信 HYBLOCK/GNBLOCK/DYBLOCK 风格）----
        // 数据来自 /api/stock/boards：新浪板块体系预计算索引（行业 / 概念 / 地域），
        // 每类内按成分股数量升序——成分越少越「专属」，第一个即最相关板块（加 ★）。
        // 用 Label 而非 FlowLayoutPanel：后者的 AutoSize 首选高度在表格里测量不稳，
        // 会把 Group 撑出一大块空白；Label + MaximumSize 限宽换行的高度计算是可靠的。
        private Label BoardLine(Color color)
        {
            var l = new Label();
            l.AutoSize = true;
            l.MaximumSize = new Size(960, 0);       // 超宽自动换行
            l.ForeColor = color;
            l.Margin = new Padding(0, 1, 0, 3);
            l.TabStop = false;
            return l;
        }

        private void StockLoadBoards(string code)
        {
            StockLoadBoards(code, false);
        }

        private void StockLoadBoards(string code, bool refresh)
        {
            if (string.IsNullOrEmpty(code) || _stockBlocks == null) return;
            int round = _stockRound;
            string url = "http://127.0.0.1:8000/api/stock/boards?code=" + code
                       + (refresh ? "&refresh=1" : "");
            System.Threading.Tasks.Task.Run(delegate
            {
                try
                {
                    string resp = VRequest(url, null);
                    var j = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(resp);
                    Invoke((Action)delegate { if (round == _stockRound) StockRenderBoards(j); });
                }
                catch (Exception)
                {
                    try
                    {
                        Invoke((Action)delegate
                        {
                            if (round != _stockRound || _stockBlocks == null) return;
                            _stockBlocks.Text = "板块标注不可用";
                            _stockBlocks.ForeColor = C.Flat;
                        });
                    }
                    catch (Exception) { }
                }
            });
        }

        /// <summary>索引正在后台重建时，隔几秒重查一次直到建好（重建约 1 分钟）。</summary>
        private void ScheduleBoardsRetry()
        {
            Debounce(ref _boardsTimer, 6000, delegate
            {
                if (_stockCurrent != null) StockLoadBoards(_stockCurrent);
            });
        }

        private void StockRenderBoards(Dictionary<string, object> j)
        {
            if (_stockBlocks == null) return;
            Color muted = C.Flat;
            _stockCptBlocks.Text = "";
            _stockRgnBlocks.Text = "";
bool ok = J.IsOk(j);
            if (!ok)
            {
                string emsg = J.Str(J.Get(j, "detail"));
                _stockBlocks.Text = string.IsNullOrEmpty(emsg) ? "板块标注不可用" : emsg;
                _stockBlocks.ForeColor = muted;
                return;
            }

            // 索引还没建好：提示 + 定时重查（后台重建约 1 分钟，重建完成前先不强求）
            bool syncing = false;
            object syv;
            if (j.TryGetValue("syncing", out syv) && syv is bool) syncing = (bool)syv;
            var boardsObj = J.Get(j, "boards") as Dictionary<string, object>;
            bool empty = (boardsObj == null
                          || J.Arr(J.Get(boardsObj, "industry")) == null
                          || J.Arr(J.Get(boardsObj, "industry")).Count == 0);
            if (empty)
            {
                if (syncing)
                {
                    _stockBlocks.Text = "板块索引建立中（约 1 分钟）…";
                    _stockBlocks.ForeColor = C.LineGold;
                    ScheduleBoardsRetry();
                    return;
                }
                _stockBlocks.Text = "板块索引未建立，点此 ↻ 重建索引";
                _stockBlocks.ForeColor = C.LineCyan;
                _stockBlocks.Cursor = Cursors.Hand;
                _stockBlocks.Click -= StockRebuildBoards;      // 防重复挂接
                _stockBlocks.Click += StockRebuildBoards;
                return;
            }
            _stockBlocks.Cursor = Cursors.Default;

            // 通达信配色：行业黄 / 概念绿 / 地域青；最相关（成分股最少）加 ★
            _stockBlocks.Text = BoardLineText("行业：", J.Arr(J.Get(boardsObj, "industry")), 6);
            _stockBlocks.ForeColor = C.RpsYellowLight;
            _stockCptBlocks.Text = BoardLineText("概念：", J.Arr(J.Get(boardsObj, "concept")), 12);
            _stockCptBlocks.ForeColor = C.UpSoft;
            _stockRgnBlocks.Text = BoardLineText("地域：", J.Arr(J.Get(boardsObj, "region")), 3);
            _stockRgnBlocks.ForeColor = C.LineCyan;
        }

        private void StockRebuildBoards(object sender, EventArgs e)
        {
            if (_stockCurrent != null) StockLoadBoards(_stockCurrent, true);
        }

        /// <summary>拼一行「标题： ★最相关 其余…」。板块很多时截断，超出部分显示省略。</summary>
        private static string BoardLineText(string title, System.Collections.ArrayList items, int maxShow)
        {
            if (items == null || items.Count == 0) return "";
            var names = new List<string>();
            int n = Math.Min(items.Count, maxShow);
            for (int i = 0; i < n; i++)
            {
                var d = items[i] as Dictionary<string, object>;
                if (d == null) continue;
                string name = Convert.ToString(J.Get(d, "board"));
                if (string.IsNullOrEmpty(name)) continue;
                names.Add((i == 0 && items.Count > 1 ? "★" : "") + name);
            }
            if (names.Count == 0) return "";
            string extra = items.Count > n ? ("  …共" + items.Count) : "";
            return title + string.Join("  ", names.ToArray()) + extra;
        }

        // ---- 基本信息（/api/stock/profile）：行业 / 市值 / 前十大股东 / 涨停与连板 ----
        private void StockLoadBasic(string code)
        {
            if (string.IsNullOrEmpty(code) || _stockBasicKpi == null) return;
            int round = ++_stockBasicRound;
            _stockBasicStatus.Text = "正在加载基本信息…";
            _stockBasicStatus.Tag = "muted";
            _stockBasicStatus.ForeColor = C.Flat;
            System.Threading.Tasks.Task.Run(delegate
            {
                try
                {
                    string resp = VRequest("http://127.0.0.1:8000/api/stock/profile?code=" + code, null);
                    var j = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(resp);
                    Invoke((Action)delegate { if (round == _stockBasicRound) StockRenderBasic(j); });
                }
                catch (Exception ex)
                {
                    try
                    {
                        Invoke((Action)delegate
                        {
                            if (round == _stockBasicRound)
                            {
                                _stockBasicStatus.Text = "加载失败：" + ex.Message;
                                _stockBasicStatus.Tag = "bad";
                                _stockBasicStatus.ForeColor = C.UpErr;
                            }
                        });
                    }
                    catch (Exception) { }
                }
            });
        }

        private static string FmtShares(object o)
        {
            double? v = J.NumOrNull(o);
            if (v == null) return "—";
            if (v >= 1e8) return (v.Value / 1e8).ToString("F2") + " 亿股";
            if (v >= 1e4) return (v.Value / 1e4).ToString("F2") + " 万股";
            return v.Value.ToString("F0") + " 股";
        }

        private void StockRenderBasic(Dictionary<string, object> j)
        {
            if (_stockBasicKpi == null) return;
            _stockBasicKpi.Controls.Clear();
            _stockBasicLimitKpi.Controls.Clear();
            _stockBasicHolders.Rows.Clear();

bool ok = J.IsOk(j);
            if (!ok)
            {
                string emsg = J.Str(J.Get(j, "detail"));
                if (string.IsNullOrEmpty(emsg)) emsg = "加载失败";
                _stockBasicStatus.Text = emsg;
                _stockBasicStatus.Tag = "bad";
                _stockBasicStatus.ForeColor = C.UpErr;
                return;
            }

            string industry = J.Str(J.Get(j, "industry"));
            AddKpi(_stockBasicKpi, "所属行业", string.IsNullOrEmpty(industry) ? "—" : industry,
                "上市 " + (string.IsNullOrEmpty(J.Str(J.Get(j, "listing_date"))) ? "—" : J.Str(J.Get(j, "listing_date"))));
            AddKpi(_stockBasicKpi, "总市值", J.Yi(J.Get(j, "total_mv_yi")),
                "总股本 " + FmtShares(J.Get(j, "total_shares")));
            AddKpi(_stockBasicKpi, "流通市值", J.Yi(J.Get(j, "float_mv_yi")),
                "流通股本 " + FmtShares(J.Get(j, "float_shares")));

            string bus = J.Str(J.Get(j, "main_business"));
            _stockBasicBus.Text = string.IsNullOrEmpty(bus) ? "" : ("主营业务：" + bus);

            var lu = J.Get(j, "limit_up") as Dictionary<string, object>;
            if (lu != null)
            {
                double? lpct = J.NumOrNull(J.Get(j, "limit_pct"));
                string last = J.Str(J.Get(lu, "last_date"));
                string range = "";
                var rArr = J.Arr(J.Get(lu, "max_range"));
                if (rArr != null && rArr.Count >= 2) range = J.Str(rArr[0]) + " ~ " + J.Str(rArr[1]);
                AddKpi(_stockBasicLimitKpi, "上次涨停", string.IsNullOrEmpty(last) ? "—" : last,
                    lpct != null ? ("涨跌幅限制 " + lpct.Value + "%") : "");
                AddKpi(_stockBasicLimitKpi, "历史最大连板",
                    (J.NumOrNull(J.Get(lu, "max_streak")) ?? 0) + " 板",
                    string.IsNullOrEmpty(range) ? "" : range);
                AddKpi(_stockBasicLimitKpi, "涨停次数",
                    (J.NumOrNull(J.Get(lu, "total")) ?? 0).ToString(), "历史累计");
            }

            var holders = J.Arr(J.Get(j, "holders"));
            if (holders != null)
            {
                foreach (Dictionary<string, object> h in holders)
                {
                    double? pct = J.NumOrNull(J.Get(h, "pct"));
                    _stockBasicHolders.Rows.Add(
                        J.Str(J.Get(h, "rank")),
                        J.Str(J.Get(h, "name")),
                        J.Str(J.Get(h, "share_type")),
                        FmtShares(J.Get(h, "shares")),
                        pct == null ? "—" : pct.Value.ToString("F2") + "%",
                        J.Str(J.Get(h, "change")),
                        J.Str(J.Get(h, "change_ratio")));
                }
                _stockBasicHolders.Fit(200, 620);
            }

            var errs = J.Arr(J.Get(j, "errors"));
            bool hasErr = errs != null && errs.Count > 0;
            _stockBasicStatus.Text = hasErr ? ("部分数据不可用：" + JoinErrs(errs)) : "加载完成";
            _stockBasicStatus.Tag = "muted";
            _stockBasicStatus.ForeColor = hasErr ? C.Warn : C.Flat;
        }

        // ---- 筹码公式下拉：后端 /api/chip/dist/formulas 枚举 ----
        // 公式是「一个文件一个公式、文件名即 id」，新增公式无需改前端，这里自动列出。
        private void StockLoadChipFormulas()
        {
            if (_stockChipFormula == null) return;
            System.Threading.Tasks.Task.Run(delegate
            {
                try
                {
                    string body;
                    if (!GetText("http://127.0.0.1:8000/api/chip/dist/formulas", 15000, out body)
                        || string.IsNullOrEmpty(body))
                        return;
                    var j = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(body);
                    var items = J.Arr(J.Get(j, "items"));
                    var list = new List<ChipFormulaItem>();
                    if (items != null)
                    {
                        foreach (Dictionary<string, object> it in items)
                        {
                            string fid = J.Str(J.Get(it, "id"));
                            if (string.IsNullOrEmpty(fid)) continue;
                            list.Add(new ChipFormulaItem
                            {
                                Id = fid,
                                Name = J.Str(J.Get(it, "name")) ?? fid,
                            });
                        }
                    }
                    string def = J.Str(J.Get(j, "default"));
                    Invoke((Action)delegate { StockRenderChipFormulas(list, def); });
                }
                catch { }
            });
        }

        private void StockRenderChipFormulas(List<ChipFormulaItem> list, string def)
        {
            if (_stockChipFormula == null || list == null || list.Count == 0) return;
            _stockChipFormula.Visible = list.Count > 1;     // 单公式时保持界面干净
            int sel = 0;
            for (int i = 0; i < list.Count; i++)
            {
                if (!string.IsNullOrEmpty(def) && list[i].Id == def) { sel = i; break; }
            }
            _chipFormulaInit = true;
            _stockChipFormula.Items.Clear();
            foreach (ChipFormulaItem it in list) _stockChipFormula.Items.Add(it);
            if (_stockChipFormula.Items.Count > 0) _stockChipFormula.SelectedIndex = sel;
            _chipFormulaInit = false;
        }

        /// <summary>滚轮缩放会连续改变可见根数，防抖后再重算筹码，避免每滚一格就发一次请求。</summary>
        private void ScheduleChipReload()
        {
            Debounce(ref _chipTimer, 400, delegate
            {
                if (_stockCurrent != null) StockLoadChip(_stockCurrent);
            });
        }

        private void StockLoadChip(string code)
        {
            if (string.IsNullOrEmpty(code) || _stockChip == null) return;
            int round = _stockRound;
            _stockChip.SetStatus("筹码计算中…", false);
            string adjust = string.IsNullOrEmpty(_stockAdjust) ? "qfq" : _stockAdjust;
            string url = "http://127.0.0.1:8000/api/chip/dist?code=" + code
                       + "&adjust=" + adjust
                       + "&days=" + (_stockRange > 0 ? _stockRange.ToString() : "0")
                       + "&bins=80";
            if (!string.IsNullOrEmpty(_stockChipFormulaId))
                url += "&formula=" + Uri.EscapeDataString(_stockChipFormulaId);
            url += "&period=" + (string.IsNullOrEmpty(_stockPeriod) ? "day" : _stockPeriod);
            System.Threading.Tasks.Task.Run(delegate
            {
                try
                {
                    string body;
                    if (!GetText(url, 30000, out body) || string.IsNullOrEmpty(body))
                    {
                        Invoke((Action)delegate
                        {
                            if (round == _stockRound) _stockChip.SetStatus("筹码接口无响应", true);
                        });
                        return;
                    }
                    var j = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(body);
bool ok = J.IsOk(j);
                    if (!ok)
                    {
                        string emsg = J.Str(J.Get(j, "error"));
                        if (string.IsNullOrEmpty(emsg)) emsg = "筹码数据不可用";
                        string cap = emsg;
                        Invoke((Action)delegate
                        {
                            if (round == _stockRound) _stockChip.SetStatus(cap, true);
                        });
                        return;
                    }
                    // 解析放工作线程：逐日快照可达上千帧 × 80 个分箱，不能卡 UI
                    var bins = StockParseChipBins(j);
                    var dates = new List<string>();
                    var frames = new List<double[]>();
                    var fstats = new List<ChipStats>();
                    StockParseChipFrames(j, bins, dates, frames, fstats);
                    // 口径提示：锁仓修正 = 前十大流通股东「占流通股比例 > 5%」合计 r，
                    // 换手率按 1/(1-r) 放大（对齐通达信 / 东财口径，见 chip_formulas/tri_decay.py）。
                    object lockObj;
                    bool lockup = (j.TryGetValue("lockup_applied", out lockObj) && lockObj is bool && (bool)lockObj);
                    string note = "未做锁仓修正";
                    if (lockup)
                    {
                        double lr = J.NumAt(j, "lockup_ratio");
                        double lf = J.NumAt(j, "lockup_factor");
                        note = string.Format("已做锁仓修正（占流通股 {0:F1}%，系数 {1:F3}）", lr * 100.0, lf);
                    }
                    Invoke((Action)delegate
                    {
                        if (round != _stockRound) return;
                        _stockChip.SetData(bins, dates, frames, fstats, note);
                        // 重算后若光标仍停在 K 线上，立刻回到光标那一天
                        if (_stockKline != null) _stockChip.SetCursorDate(_stockKline.CursorDate);
                    });
                }
                catch (Exception ex)
                {
                    try
                    {
                        Invoke((Action)delegate
                        {
                            if (round == _stockRound) _stockChip.SetStatus("筹码加载失败：" + ex.Message, true);
                        });
                    }
                    catch (Exception) { }
                }
            });
        }

        private static List<ChipBin> StockParseChipBins(Dictionary<string, object> j)
        {
            var bins = new List<ChipBin>();
            var arr = J.Arr(J.Get(j, "bins"));
            if (arr == null) return bins;
            foreach (Dictionary<string, object> d in arr)
            {
                var b = new ChipBin();
                b.Lo = J.NumOrNull(J.Get(d, "lo")) ?? 0;
                b.Hi = J.NumOrNull(J.Get(d, "hi")) ?? 0;
                b.Price = J.NumOrNull(J.Get(d, "price")) ?? 0;
                b.Pct = J.NumOrNull(J.Get(d, "pct")) ?? 0;
                bins.Add(b);
            }
            return bins;
        }

        /// <summary>
        /// 解析逐日快照 frames：dates[i] / pct[i] / stats[i] 一一对应，
        /// 光标左右移动时前端只切索引，不再回服务端。
        /// 后端没给 frames（老版本 / 异常）时，用最后一帧兜底成单帧，不至于开天窗。
        /// </summary>
        private static void StockParseChipFrames(Dictionary<string, object> j, List<ChipBin> bins,
            List<string> dates, List<double[]> frames, List<ChipStats> fstats)
        {
            var frObj = J.Get(j, "frames") as Dictionary<string, object>;
            if (frObj != null)
            {
                var dArr = J.Arr(J.Get(frObj, "dates"));
                if (dArr != null)
                    foreach (object o in dArr) dates.Add(Convert.ToString(o));

                var pArr = J.Arr(J.Get(frObj, "pct"));
                if (pArr != null)
                {
                    foreach (object row in pArr)
                    {
                        var rl = row as System.Collections.ArrayList;
                        if (rl == null) continue;
                        var vals = new double[rl.Count];
                        for (int k = 0; k < rl.Count; k++) vals[k] = J.NumOrNull(rl[k]) ?? 0;
                        frames.Add(vals);
                    }
                }

                var sArr = J.Arr(J.Get(frObj, "stats"));
                if (sArr != null)
                {
                    foreach (object row in sArr)
                    {
                        var d = row as Dictionary<string, object>;
                        var s = new ChipStats();
                        if (d != null)
                        {
                            s.Close = J.NumOrNull(J.Get(d, "close")) ?? 0;
                            s.AvgCost = J.NumOrNull(J.Get(d, "avg_cost"));
                            s.PeakPrice = J.NumOrNull(J.Get(d, "peak_price"));
                            s.ProfitRatio = J.NumOrNull(J.Get(d, "profit_ratio"));
                            s.Scr90 = J.NumOrNull(J.Get(d, "scr90"));
                        }
                        fstats.Add(s);
                    }
                }
            }
            if (frames.Count == 0 && bins.Count > 0)
            {   // 兜底：单帧（最新一天）
                var vals = new double[bins.Count];
                for (int i = 0; i < bins.Count; i++) vals[i] = bins[i].Pct;
                frames.Add(vals);
                dates.Add(J.Str(J.Get(j, "as_of")));
                var s = new ChipStats();
                var sObj = J.Get(j, "stats") as Dictionary<string, object>;
                if (sObj != null)
                {
                    s.Close = J.NumOrNull(J.Get(j, "last_close")) ?? 0;
                    s.AvgCost = J.NumOrNull(J.Get(sObj, "avg_cost"));
                    s.PeakPrice = J.NumOrNull(J.Get(sObj, "peak_price"));
                    s.ProfitRatio = J.NumOrNull(J.Get(sObj, "profit_ratio"));
                    s.Scr90 = J.NumOrNull(J.Get(sObj, "scr90"));
                }
                fstats.Add(s);
            }
        }

        // 把后端返回的指标序列按 K 线日期对齐为等长数组（缺失填 NaN）
        private static List<double> AlignSeries(List<string> dates, object datesObj, System.Collections.ArrayList dataArr)
        {
            var data = new List<double>();
            for (int i = 0; i < dates.Count; i++) data.Add(double.NaN);
            if (dataArr == null) return data;
            var srcDates = new List<string>();
            var dl = datesObj as System.Collections.ArrayList;
            if (dl != null)
                foreach (object d in dl) srcDates.Add(Convert.ToString(d));
            if (srcDates.Count == dataArr.Count)
            {
                var map = new Dictionary<string, double>();
                for (int k = 0; k < srcDates.Count; k++)
                {
                    object v = dataArr[k];
                    map[srcDates[k]] = (v == null) ? double.NaN : Convert.ToDouble(v);
                }
                for (int i = 0; i < dates.Count; i++)
                {
                    double val;
                    if (map.TryGetValue(dates[i], out val)) data[i] = val;
                }
            }
            else
            {
                for (int i = 0; i < dates.Count && i < dataArr.Count; i++)
                {
                    object v = dataArr[i];
                    data[i] = (v == null) ? double.NaN : Convert.ToDouble(v);
                }
            }
            return data;
        }

        // RPS 副图高亮阈值 M：本项目约定 M = 90（RPS ≥ 90 = 全市场前 10% 强势）。
        private const double RpsHiThreshold = 90.0;
        private static readonly Color RpsHiRed = C.Up;

        /// <summary>
        /// 按通达信 RPS 副图公式给序列配色：120 绿 / 250 白 / 50 黄 / 20 灰 / 10 浅青，
        /// 并开启「数值 ≥ M(90) 转红」高亮；其余指标沿用 <see cref="LineColor"/>。
        /// 对齐公式：RPS120:...,COLORGREEN; IF(RPS120&gt;=M,RPS120,DRAWNULL),COLORRED;
        /// </summary>
        private static void ApplySeriesStyle(ChartSeries s)
        {
            switch (s.Name)
            {
                case "RPS120": s.Color = C.Down;   break;   // COLORGREEN
                case "RPS250": s.Color = C.RpsWhite; break;   // COLORWHITE
                case "RPS50": s.Color = C.RpsYellow;  break;    // COLORYELLOW
                case "RPS20": s.Color = C.Flat; break;    // COLORGRAY
                case "RPS10": s.Color = C.RpsCyan; break;    // COLORLICYAN
                default: s.Color = LineColor(s.Name); return;
            }
            s.Width = 2f;                       // LINETHICK2
            s.HasHi = true;
            s.HiThreshold = RpsHiThreshold;
            s.HiColor = RpsHiRed;
        }

        private static Color LineColor(string name)
        {
            if (name != null)
            {
                if (name.Contains("DIF")) return C.LineDif;
                if (name.Contains("DEA")) return C.LineDea;
            }
            return C.LineViolet;
        }

        private void StockRenderHistory(Dictionary<string, object> j)
        {
bool ok = J.IsOk(j);
            if (!ok)
            {
                string emsg = J.Str(J.Get(j, "detail"));
                if (emsg == "") emsg = J.Str(J.Get(j, "error"));
                if (emsg == "") emsg = "加载失败";
                StockFail(emsg);
                return;
            }

            // K 线（前复权）
            var bars = new List<KBar>();
            var arr = J.Arr(J.Get(j, "bars"));
            if (arr != null)
            {
                foreach (Dictionary<string, object> d in arr)
                {
                    var b = new KBar();
                    b.Date = J.Str(J.Get(d, "trade_date"));
                    b.O = J.NumOrNull(J.Get(d, "open")) ?? 0;
                    b.C = J.NumOrNull(J.Get(d, "close")) ?? 0;
                    b.L = J.NumOrNull(J.Get(d, "low")) ?? 0;
                    b.H = J.NumOrNull(J.Get(d, "high")) ?? 0;
                    b.V = J.NumOrNull(J.Get(d, "volume")) ?? 0;
                    bars.Add(b);
                }
            }

            // K 线（不复权原始价）
            var barsRaw = new List<KBar>();
            var arrRaw = J.Arr(J.Get(j, "bars_raw"));
            if (arrRaw != null)
            {
                foreach (Dictionary<string, object> d in arrRaw)
                {
                    var b = new KBar();
                    b.Date = J.Str(J.Get(d, "trade_date"));
                    b.O = J.NumOrNull(J.Get(d, "open")) ?? 0;
                    b.C = J.NumOrNull(J.Get(d, "close")) ?? 0;
                    b.L = J.NumOrNull(J.Get(d, "low")) ?? 0;
                    b.H = J.NumOrNull(J.Get(d, "high")) ?? 0;
                    b.V = J.NumOrNull(J.Get(d, "volume")) ?? 0;
                    barsRaw.Add(b);
                }
            }

            // 入池/出池轨迹
            var pool = J.Map(J.Get(j, "pool"));
            var spans = J.Arr(pool != null ? J.Get(pool, "spans") : null);
            var spanList = new List<Dictionary<string, object>>();
            if (spans != null) foreach (Dictionary<string, object> s in spans) spanList.Add(s);

            // 消息面
            var events = J.Arr(J.Get(j, "events"));

            // 标记（入池/出池/事件）只取落在 K 线上的日期
            var dateSet = new HashSet<string>();
            foreach (KBar b in bars) dateSet.Add(b.Date);
            var marks = new List<KMark>();
            foreach (Dictionary<string, object> s in spanList)
            {
                string st = J.Str(J.Get(s, "start"));
                if (dateSet.Contains(st))
                    marks.Add(new KMark { Date = st, Kind = "in", Color = C.Up, Text = "入" });
                object openv = J.Get(s, "open");
                bool open = (openv is bool) && (bool)openv;
                if (!open)
                {
                    string en = J.Str(J.Get(s, "end"));
                    if (dateSet.Contains(en))
                        marks.Add(new KMark { Date = en, Kind = "out", Color = C.FlatWarm, Text = "出" });
                }
            }
            if (events != null)
            {
                foreach (Dictionary<string, object> ev in events)
                {
                    string dt = J.Str(J.Get(ev, "published_at"));
                    if (!dateSet.Contains(dt)) continue;
                    string kind = J.Str(J.Get(ev, "kind"));
                    Color c = kind == "announcement" ? C.Warn : C.LineDea;
                    marks.Add(new KMark { Date = dt, Kind = "event", Color = c, Text = kind == "announcement" ? "告" : "闻" });
                }
            }

            _stockKline.Period = _stockPeriod;      // 图例显示 日K / 周K / 月K
            _stockKline.SetData(bars, barsRaw, marks);
            // SetData 不重置可见根数，这里按当前选中的「范围」按钮同步一次：
            // 否则按钮高亮与图表实际根数会不一致，筹码帧窗口也会跟着对不上光标。
            _stockKline.SetRange(_stockRange);
            // 指标随周期重算（周线要配周线 MA，不能沿用日线序列）；
            // 换股票 / 刷新 / 切周期都会走到这里，顺带也避免了指标残留上一只票。
            if (_stockCurrent != null) StockLoadIndicators(_stockCurrent);
            StockRenderTimeline(spanList);
            StockRenderEvents(events);

            // 状态：「更新至 …」栏与「前复权日K · …」统计文字已按需求移除，
            // 仅当有同步失败时在状态位提示，否则清空。
            var sync = J.Map(J.Get(j, "sync"));
            var syncErrs = J.Arr(sync != null ? J.Get(sync, "errors") : null);
            string errs = (syncErrs != null && syncErrs.Count > 0) ? "；部分失败：" + JoinErrs(syncErrs) : "";
            if (errs.Length > 0)
            {
                _stockStatus.Text = "部分数据同步失败" + errs;
                _stockStatus.Tag = "muted";
                _stockStatus.ForeColor = C.Flat;
            }
            else
            {
                _stockStatus.Text = "";
            }
        }

        private static string JoinErrs(System.Collections.ArrayList list)
        {
            var parts = new List<string>();
            foreach (object o in list) parts.Add(J.Str(o));
            return string.Join("；", parts.ToArray());
        }

        private void StockRenderTimeline(List<Dictionary<string, object>> spans)
        {
            _stockTimeline.Rows.Clear();
            if (spans.Count == 0)
            {
                _stockTimeline.Rows.Add("—", "—", "—", "暂无记录");
                _stockTimeline.Fit(180, 620);
                return;
            }
            // 倒序：最近的在前面
            for (int i = spans.Count - 1; i >= 0; i--)
            {
                Dictionary<string, object> s = spans[i];
                object openv = J.Get(s, "open");
                bool open = (openv is bool) && (bool)openv;
                string end = open ? "至今" : J.Str(J.Get(s, "end"));
                int days = MktInt(J.Get(s, "days"));
                int row = _stockTimeline.Rows.Add(
                    J.Str(J.Get(s, "start")), end, days + " 天", open ? "在榜" : "已出池");
                _stockTimeline.Rows[row].Cells[2].Style.ForeColor = days >= 7 ? C.Up
                    : (days >= 3 ? C.WarnMid : C.Flat);
                _stockTimeline.Rows[row].Cells[3].Style.ForeColor = open ? C.Up : C.Flat;
            }
            _stockTimeline.Fit(180, 620);
        }

        private void StockRenderEvents(System.Collections.ArrayList events)
        {
            if (events != null) _stockEventData = events;
            _stockEvents.Controls.Clear();
            int total = _stockEventData.Count;
            if (total == 0)
            {
                _stockEvents.Controls.Add(Mute(Lbl("暂未采集到公告或新闻")));
                return;
            }
            // 不再折叠：全部消息直接展示
            for (int i = 0; i < total; i++)
            {
                Dictionary<string, object> ev = _stockEventData[i] as Dictionary<string, object>;
                if (ev == null) continue;
                AddRow(_stockEvents, MakeEventPanel(ev));
            }
        }

        private Control MakeEventPanel(Dictionary<string, object> ev)
        {
            var panel = new Panel();
            panel.Dock = DockStyle.Top;
            panel.AutoSize = true;
            panel.AutoSizeMode = AutoSizeMode.GrowAndShrink;
            panel.Margin = new Padding(0, 0, 0, 10);
            panel.Padding = new Padding(0);

            string kind = J.Str(J.Get(ev, "kind"));
            string meta = J.Str(J.Get(ev, "published_at")) + " · "
                + (kind == "announcement" ? "公告" : "新闻") + " · " + J.Str(J.Get(ev, "source"));
            var m = Mute(Lbl(meta));
            m.Dock = DockStyle.Top;
            m.Margin = new Padding(0, 0, 0, 2);
            panel.Controls.Add(m);

            string url = J.Str(J.Get(ev, "source_url"));
            string title = J.Str(J.Get(ev, "title"));
            bool goodUrl = Uri.IsWellFormedUriString(url, UriKind.Absolute);
            var t = goodUrl ? (Control)new LinkLabel() : (Control)new Label();
            t.Text = title;
            t.AutoSize = true;
            t.MaximumSize = new Size(720, 0);
            t.Dock = DockStyle.Top;
            t.Margin = new Padding(0, 2, 0, 2);
            if (goodUrl)
            {
                var link = (LinkLabel)t;
                link.LinkClicked += delegate(object s2, LinkLabelLinkClickedEventArgs e2)
                {
                    try { Process.Start(url); } catch (Exception) { }
                };
            }
            else
            {
                ((Label)t).ForeColor = _cText;
            }
            panel.Controls.Add(t);

            string summary = J.Str(J.Get(ev, "summary"));
            if (summary != "")
            {
                var sm = Mute(Lbl(summary));
                sm.AutoSize = true;
                sm.MaximumSize = new Size(720, 0);
                sm.Dock = DockStyle.Top;
                sm.Margin = new Padding(0, 2, 0, 0);
                panel.Controls.Add(sm);
            }
            return panel;
        }

        // ---- 扫雷（通达信个股亮点，内联）----
        private void StockLoadSaolei(string code)
        {
            _stockSaoleiStatus.Text = "获取中…";
            _stockSaoleiStatus.Tag = "muted";
            _stockSaoleiStatus.ForeColor = C.Flat;
            _stockSaoleiList.Controls.Clear();
            System.Threading.Tasks.Task.Run(delegate
            {
                try
                {
                    string resp = VRequest("http://127.0.0.1:8000/api/stock/saolei/" + code, null);
                    var j = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(resp);
                    Invoke((Action)delegate
                    {
                        if (code == _stockCurrent) StockRenderSaolei(j);
                    });
                }
                catch (Exception ex)
                {
                    string msg = "获取失败：" + ex.Message;
                    try { Invoke((Action)delegate { if (code == _stockCurrent) StockSaoleiFail(msg); }); }
                    catch (Exception) { }
                }
            });
        }

        private void StockRenderSaolei(Dictionary<string, object> j)
        {
            _stockSaoleiList.Controls.Clear();

            int total = VIntOf(J.Get(j, "total"));
            int risk = VIntOf(J.Get(j, "risk"));
            int safe = VIntOf(J.Get(j, "safe"));
            string date = J.Str(J.Get(j, "date"));
            var cats = J.Arr(J.Get(j, "categories"));

            _stockSaoleiStatus.Text = "总检查 " + total + " 项 · 风险项 " + risk + " 项 · 安全项 " + safe + " 项"
                + (date != "" ? "（数据日期 " + date + "）" : "");
            _stockSaoleiStatus.Tag = "muted";
            _stockSaoleiStatus.ForeColor = risk > 0
                ? C.UpErr
                : C.Flat;

            // ---- 四大类风险清单（财务 / 市场 / 交易 / ST，并排展示）----
            if (cats != null && cats.Count > 0)
            {
                var grid = new TableLayoutPanel();
                grid.ColumnCount = cats.Count;
                grid.RowCount = 1;
                grid.AutoSize = true;
                grid.AutoSizeMode = AutoSizeMode.GrowAndShrink;
                grid.Dock = DockStyle.Top;
                grid.Margin = new Padding(0, 0, 0, 10);
                grid.Padding = new Padding(0);
                grid.RowStyles.Add(new RowStyle(SizeType.AutoSize));

                for (int c = 0; c < cats.Count; c++)
                {
                    grid.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
                    var cat = J.Map(cats[c]);
                    if (cat == null) continue;
                    var blk = SaoleiCategory(J.Str(J.Get(cat, "name")), J.Arr(J.Get(cat, "items")));
                    blk.Margin = new Padding(0, 0, 18, 0);
                    grid.Controls.Add(blk, c, 0);
                }
                _stockSaoleiList.Controls.Add(grid);
            }
            else
            {
                _stockSaoleiList.Controls.Add(Mute(Lbl("该股暂无风险清单数据")));
            }

            // ---- 个股亮点（辅）----
            var arr = J.Arr(J.Get(j, "highlights"));
            int n = arr == null ? 0 : arr.Count;
            var head = Lbl("个股亮点" + (n > 0 ? "（" + n + " 项）" : ""));
            head.Font = new Font("Microsoft YaHei UI", 9.5f, FontStyle.Bold);
            head.Margin = new Padding(0, 6, 0, 6);
            _stockSaoleiList.Controls.Add(head);

            if (n == 0)
            {
                _stockSaoleiList.Controls.Add(Mute(Lbl("该股暂无亮点记录")));
                return;
            }

            for (int i = 0; i < n; i++)
            {
                var ev = J.Map(arr[i]);
                if (ev == null) continue;
                string name = J.Str(J.Get(ev, "name"));
                string desc = J.Str(J.Get(ev, "desc"));

                var panel = new Panel();
                panel.AutoSize = true;
                panel.AutoSizeMode = AutoSizeMode.GrowAndShrink;
                panel.Dock = DockStyle.Top;
                panel.Margin = new Padding(0, 0, 0, 8);

                var t = Lbl("· " + name);
                t.Font = new Font("Microsoft YaHei UI", 10f, FontStyle.Bold);
                t.AutoSize = true;
                t.MaximumSize = new Size(760, 0);
                t.Dock = DockStyle.Top;
                panel.Controls.Add(t);

                if (desc != "")
                {
                    var d = Mute(Lbl(desc));
                    d.AutoSize = true;
                    d.MaximumSize = new Size(760, 0);
                    d.Dock = DockStyle.Top;
                    d.Margin = new Padding(0, 2, 0, 0);
                    panel.Controls.Add(d);
                }
                _stockSaoleiList.Controls.Add(panel);
            }
        }

        /// <summary>扫雷清单里的一个分类列：标题 + 条目（名称 / 有·无）。</summary>
        private static TableLayoutPanel SaoleiCategory(string title, System.Collections.ArrayList items)
        {
            var ok = new List<Dictionary<string, object>>();
            if (items != null)
            {
                foreach (object o in items)
                {
                    var m = J.Map(o);
                    if (m != null) ok.Add(m);
                }
            }

            var t = new TableLayoutPanel();
            t.ColumnCount = 1;
            t.AutoSize = true;
            t.AutoSizeMode = AutoSizeMode.GrowAndShrink;
            t.Margin = new Padding(0);
            t.Padding = new Padding(0);
            t.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));

            var head = Lbl(title);
            head.Font = new Font("Microsoft YaHei UI", 9.5f, FontStyle.Bold);
            head.Margin = new Padding(0, 0, 0, 6);
            t.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            t.Controls.Add(head, 0, 0);

            var body = new TableLayoutPanel();
            body.ColumnCount = 2;
            body.RowCount = Math.Max(1, ok.Count);
            body.AutoSize = true;
            body.AutoSizeMode = AutoSizeMode.GrowAndShrink;
            body.Margin = new Padding(0);
            body.Padding = new Padding(0);
            body.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
            body.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
            for (int i = 0; i < body.RowCount; i++) body.RowStyles.Add(new RowStyle(SizeType.AutoSize));

            if (ok.Count == 0)
            {
                body.Controls.Add(Mute(Lbl("—")), 0, 0);
            }
            else
            {
                for (int i = 0; i < ok.Count; i++)
                {
                    bool trig = J.Str(J.Get(ok[i], "trig")) == "1";
                    var nm = Lbl(J.Str(J.Get(ok[i], "name")));
                    nm.Margin = new Padding(0, 0, 12, 3);
                    var st = Lbl(trig ? "有" : "无");
                    st.Margin = new Padding(0, 0, 0, 3);
                    if (trig)
                    {
                        st.ForeColor = C.UpErr;
                        st.Font = new Font("Microsoft YaHei UI", 9f, FontStyle.Bold);
                    }
                    else
                    {
                        st.Tag = "muted";
                    }
                    body.Controls.Add(nm, 0, i);
                    body.Controls.Add(st, 1, i);
                }
            }

            t.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            t.Controls.Add(body, 0, 1);
            return t;
        }

        /// <summary>把 JSON 里的整数（可能是 int / decimal / string）安全转成 int。</summary>
        private static int VIntOf(object o)
        {
            int v;
            return int.TryParse(J.Str(o), out v) ? v : 0;
        }

        private void StockSaoleiFail(string msg)
        {
            _stockSaoleiList.Controls.Clear();
            _stockSaoleiStatus.Text = msg;
            _stockSaoleiStatus.Tag = "bad";
            _stockSaoleiStatus.ForeColor = C.UpErr;
        }

        // ---- 估值（内联）----
        private void StockLoadValuation(string code)
        {
            _stockValStatus.Text = "计算中…";
            _stockValStatus.Tag = "muted";
            _stockValStatus.ForeColor = C.Flat;
            System.Threading.Tasks.Task.Run(delegate
            {
                try
                {
                    var o = new Dictionary<string, object>();
                    o["code"] = code;
                    string body = new JavaScriptSerializer().Serialize(o);
                    string resp = VRequest("http://127.0.0.1:8000/api/stock/valuation", body);
                    var j = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(resp);
                    Invoke((Action)delegate { StockRenderValuation(j); });
                }
                catch (Exception)
                {
                    try
                    {
                        Invoke((Action)delegate
                        {
                            SetErr(_stockValStatus, "计算失败");
                        });
                    }
                    catch (Exception) { }
                }
            });
        }

        private void StockRenderValuation(Dictionary<string, object> j)
        {
            _stockValKpi.Controls.Clear();
            _stockValGrid.Rows.Clear();

bool ok = J.IsOk(j);
            if (!ok)
            {
                string emsg = J.Str(J.Get(j, "error"));
                if (emsg == "") emsg = J.Str(J.Get(j, "detail"));
                if (emsg == "") emsg = "计算失败";
                SetErr(_stockValStatus, emsg);
                return;
            }

            var m = J.Map(J.Get(j, "market"));
            var f = J.Map(J.Get(j, "finance"));
            var g = J.Map(J.Get(j, "growth"));
            var a = J.Map(J.Get(j, "assumptions"));

            AddKpi(_stockValKpi, "股票", J.Str(J.Get(j, "name")), J.Str(J.Get(j, "code")));
            AddKpi(_stockValKpi, "当前股价", J.Fmt(J.NumOrNull(J.Get(m, "price"))) + " 元", J.Str(J.Get(m, "price_source")));
            AddKpi(_stockValKpi, "期初净利润", J.Fmt(J.NumOrNull(J.Get(f, "net_profit_base"))) + " 亿", J.Str(J.Get(f, "indicator")));
            double? cagr = J.NumOrNull(J.Get(g, "cagr"));
            object cons;
            bool fromC = (g != null && g.TryGetValue("from_consensus", out cons) && cons is bool && (bool)cons);
            AddKpi(_stockValKpi, "复合增长率", cagr != null ? J.Pct100(cagr) : "走兜底",
                fromC ? "机构预测" : "按模板兜底");
            AddKpi(_stockValKpi, "贴现率", J.Pct100Plain(J.NumOrNull(J.Get(a, "discount_rate"))), "两段法 DCF");

            var scenarios = J.Arr(J.Get(j, "scenarios"));
            if (scenarios != null)
            {
                foreach (Dictionary<string, object> s in scenarios)
                {
                    string verdict = J.Str(J.Get(s, "verdict"));
                    string cls = (verdict == "低估" || verdict == "高估") ? verdict : "合理";
                    double? rr = J.NumOrNull(J.Get(s, "return_rate"));
                    int row = _stockValGrid.Rows.Add(
                        J.Str(J.Get(s, "label")),
                        J.Pct100(J.NumOrNull(J.Get(s, "growth"))),
                        J.Fmt(J.NumOrNull(J.Get(s, "value_per_share"))) + " 元",
                        J.Fmt(J.NumOrNull(J.Get(s, "undervalued_ratio")), 4),
                        cls,
                        J.Fmt(J.NumOrNull(J.Get(s, "target_price"))) + " 元",
                        J.Pct100(rr));
                    _stockValGrid.Rows[row].Cells[4].Style.ForeColor = verdict == "低估" ? C.Down
                        : (verdict == "高估" ? C.Up : C.Flat);
                    if (rr != null)
                        _stockValGrid.Rows[row].Cells[6].Style.ForeColor = rr.Value > 0 ? C.Up : (rr.Value < 0 ? C.Down : C.Flat);
                }
            }
            _stockValGrid.Fit(150, 620);

            _stockValStatus.Text = J.Str(J.Get(j, "name")) + "（" + J.Str(J.Get(j, "code")) + "）已计算";
            _stockValStatus.Tag = "muted";
            _stockValStatus.ForeColor = C.DownDeep;
        }

        // ---- AI 分析（调 /api/stock/research，返回 AI 生成的 markdown）----
        private void StockLoadAi(string code, bool force = false)
        {
            if (_stockAiStatus != null)
            {
                _stockAiStatus.Text = force ? "AI 强制重新分析中…" : "AI 分析中…";
                _stockAiStatus.Tag = "muted";
                _stockAiStatus.ForeColor = C.Flat;
            }
            _stockAiMarkdown = "";
            if (_stockAiBox != null) FillAiDoc(_stockAiBox, _stockAiMarkdown);
            System.Threading.Tasks.Task.Run(delegate
            {
                try
                {
                    string body = new JavaScriptSerializer().Serialize(new Dictionary<string, object> {
                        { "code", code }, { "force", force },
                        { "depth", _aiDepth }, { "horizon", _aiHorizon }, { "focus", _aiFocus }
                    });
                    string resp = VRequest("http://127.0.0.1:8000/api/stock/research", body);
                    var j = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(resp);
                    Invoke((Action)delegate { StockRenderAi(j); });
                }
                catch (Exception)
                {
                    try
                    {
                        Invoke((Action)delegate
                        {
                            if (_stockAiStatus != null)
                            {
                                SetErr(_stockAiStatus, "分析失败（网络/超时，确认后端已启动且配置了 LLM）");
                            }
                        });
                    }
                    catch (Exception) { }
                }
            });
        }

        // ---- AI 分析偏好：本地持久化（JSON，存于 ApplicationData\StockPoolLauncher）----
        private static string AiPrefsPath()
        {
            var dir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "StockPoolLauncher");
            try { if (!Directory.Exists(dir)) Directory.CreateDirectory(dir); } catch { }
            return Path.Combine(dir, "ai_prefs.json");
        }

        private void StockLoadAiPrefs()
        {
            try
            {
                string p = AiPrefsPath();
                if (File.Exists(p))
                {
                    var d = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(p, Encoding.UTF8));
                    if (d != null)
                    {
                        if (d.ContainsKey("depth"))
                        {
                            var ds = d["depth"] as string;
                            if (ds != null && (ds == "concise" || ds == "normal" || ds == "detailed")) _aiDepth = ds;
                        }
                        if (d.ContainsKey("horizon"))
                        {
                            var hs = d["horizon"] as string;
                            if (hs != null && (hs == "short" || hs == "mid" || hs == "long")) _aiHorizon = hs;
                        }
                        if (d.ContainsKey("focus"))
                        {
                            var fa = d["focus"] as System.Collections.ArrayList;
                            if (fa != null)
                            {
                                var lst = new List<string>();
                                foreach (var x in fa) { var s = x as string; if (s != null) lst.Add(s); }
                                _aiFocus = lst;
                            }
                        }
                    }
                }
            }
            catch { }
        }

        private void StockSaveAiPrefs()
        {
            try
            {
                var d = new Dictionary<string, object> { { "depth", _aiDepth }, { "horizon", _aiHorizon }, { "focus", _aiFocus } };
                File.WriteAllText(AiPrefsPath(), new JavaScriptSerializer().Serialize(d), Encoding.UTF8);
            }
            catch { }
        }

        private void StockAiPrefChanged()
        {
            if (_stockCurrent != null) StockLoadAi(_stockCurrent, true);
        }

        private void StockRadioGroup(TableLayoutPanel b, string[] labels, string[] values, string current, Action<string> onPick)
        {
            for (int i = 0; i < labels.Length; i++)
            {
                var rb = new RadioButton();
                rb.Text = labels[i];
                rb.AutoSize = true;
                rb.Checked = (values[i] == current);
                var v = values[i];
                rb.CheckedChanged += delegate { if (rb.Checked) onPick(v); };
                AddRow(b, rb);
            }
        }

        private void StockRenderAi(Dictionary<string, object> j)
        {
            if (_stockAiStatus == null || _stockAiBox == null) return;
bool ok = J.IsOk(j);
            if (!ok)
            {
                string emsg = J.Str(J.Get(j, "error"));
                if (emsg == "") emsg = J.Str(J.Get(j, "detail"));
                if (emsg == "") emsg = "分析失败";
                SetErr(_stockAiStatus, emsg);
                _stockAiMarkdown = "";
                FillAiDoc(_stockAiBox, _stockAiMarkdown);
                return;
            }
            string md = J.Str(J.Get(j, "markdown"));
            _stockAiMarkdown = md;
            object cached;
            bool isCached = (j.TryGetValue("cached", out cached) && cached is bool && (bool)cached);
            string asOf = J.Str(J.Get(j, "data_as_of"));
            string d = J.Str(J.Get(j, "depth"));
            string h = J.Str(J.Get(j, "horizon"));
            var fobj = J.Get(j, "focus") as System.Collections.ArrayList;
            string f = "";
            if (fobj != null)
            {
                var tmp = new List<string>();
                foreach (var x in fobj) { var s = x as string; if (s != null) tmp.Add(s); }
                f = string.Join("、", tmp.ToArray());
            }
            string prefs = "";
            if (d != "" || h != "" || f != "")
                prefs = "  [偏好：" + (d != "" ? d : "?") + "/" + (h != "" ? h : "?") + (f != "" ? "/" + f : "") + "]";
            _stockAiStatus.Text = (isCached ? "（缓存）" : "已生成") + (asOf != "" ? " 数据截至 " + asOf : "") + prefs;
            _stockAiStatus.Tag = "muted";
            _stockAiStatus.ForeColor = C.DownDeep;
            FillAiDoc(_stockAiBox, _stockAiMarkdown);
        }

        /// <summary>把 AI 调研的 markdown 渲染进只读框；换肤时由 Skin 再调一次，用新配色重排。</summary>
        private void FillAiDoc(RichTextBox rt, string md)
        {
            if (_docFont == null) _docFont = new Font("Microsoft YaHei UI", 9.5f);
            if (_docBold == null) _docBold = new Font(_docFont, FontStyle.Bold);
            var normal = _docFont;

            rt.Clear();
            rt.BackColor = _cPanel;
            rt.ForeColor = _cText;
            rt.SelectionFont = normal;
            rt.SelectionColor = _cText;

            if (string.IsNullOrEmpty(md) || md.Trim() == "")
            {
                rt.SelectionColor = _cSub;
                rt.AppendText("（暂无 AI 分析。配置好 LLM_API_KEY 并启动后端后，点击「生成分析」即可生成。）");
                rt.SelectionStart = 0; rt.SelectionLength = 0;
                return;
            }

            var lines = md.Replace("\r\n", "\n").Split('\n');
            bool first = true;
            foreach (string rawLine in lines)
            {
                string line = rawLine.TrimEnd();
                if (line.Trim() == "") continue;

                var hm = Regex.Match(line, @"^(#{1,6}\s+|[0-9]+[、.)]\s+)(.*)$");
                if (hm.Success)
                {
                    if (!first) rt.AppendText(Environment.NewLine);
                    rt.SelectionFont = _docBold;
                    rt.SelectionColor = _cText;
                    rt.AppendText(hm.Groups[2].Value.Trim() + Environment.NewLine + Environment.NewLine);
                    first = false;
                    continue;
                }
                if (Regex.IsMatch(line, @"^[-*_]{3,}$"))
                {
                    if (!first) rt.AppendText(Environment.NewLine);
                    rt.SelectionFont = normal;
                    rt.SelectionColor = _cSub;
                    rt.AppendText("────────────────────────────" + Environment.NewLine + Environment.NewLine);
                    first = false;
                    continue;
                }
                var qm = Regex.Match(line, @"^>\s?(.*)$");
                if (qm.Success)
                {
                    rt.SelectionFont = normal;
                    rt.SelectionColor = _cSub;
                    rt.AppendText("　　" + qm.Groups[1].Value + Environment.NewLine);
                    first = false;
                    continue;
                }
                var lm = Regex.Match(line, @"^[-*]\s+(.*)$");
                if (lm.Success)
                {
                    rt.SelectionFont = normal;
                    rt.SelectionColor = _cText;
                    rt.AppendText("· ");
                    AppendInline(rt, lm.Groups[1].Value, normal, _docBold);
                    rt.AppendText(Environment.NewLine);
                    first = false;
                    continue;
                }
                rt.SelectionFont = normal;
                rt.SelectionColor = _cText;
                AppendInline(rt, line, normal, _docBold);
                rt.AppendText(Environment.NewLine + Environment.NewLine);
                first = false;
            }
            rt.SelectionStart = 0;
            rt.SelectionLength = 0;
        }

        /// <summary>按 **加粗** 标记分段写入 RichTextBox（行内加粗）。</summary>
        private static void AppendInline(RichTextBox rt, string text, Font normal, Font bold)
        {
            var re = new Regex(@"\*\*(.+?)\*\*");
            int last = 0;
            foreach (Match m in re.Matches(text))
            {
                if (m.Index > last)
                {
                    rt.SelectionFont = normal;
                    rt.AppendText(text.Substring(last, m.Index - last));
                }
                rt.SelectionFont = bold;
                rt.AppendText(m.Groups[1].Value);
                last = m.Index + m.Length;
            }
            if (last < text.Length)
            {
                rt.SelectionFont = normal;
                rt.AppendText(text.Substring(last));
            }
        }

        /// <summary>跳到「估值计算」标签页并把代码填进去重算（完整过程在那一页）。</summary>
        private void StockJumpValuation(string code)
        {
            _vCode.Text = code;
            ValuationCalc();
            SelectTab(_valTabIndex);
        }

        // ---- 范围按钮配色（跟随主题）----
        private void StockSetRangeActive()
        {
            foreach (KeyValuePair<Button, int> kv in _stockRangeMap)
            {
                bool act = kv.Value == _stockRangeIdx;      // 比的是档位，不是根数
                kv.Key.BackColor = act ? C.Accent : _cPanel;
                kv.Key.ForeColor = act ? Color.White : _cSub;
                kv.Key.Invalidate();
            }
        }

        /// <summary>「范围」档位 → 当前周期下的根数（档位 2 = 全部 → 0 根，即不限）。</summary>
        private int StockRangeBars()
        {
            if (_stockRangeIdx == 2) return 0;
            bool half = _stockRangeIdx == 0;                // 近6月 : 近1年
            if (_stockPeriod == "week") return half ? 26 : 52;
            if (_stockPeriod == "month") return half ? 6 : 12;
            return half ? 120 : 250;
        }

        /// <summary>范围档位或周期变化后重算实际根数并应用到图表。</summary>
        private void StockApplyRange()
        {
            _stockRange = StockRangeBars();
            StockSetRangeActive();
            if (_stockKline != null)
            {
                _stockKline.SetRange(_stockRange);
                _stockKline.FocusForKeys();                 // 点完按钮焦点还给图表，方向键接着用
            }
        }

        private void StockSetAdjustActive()
        {
            foreach (KeyValuePair<Button, string> kv in _stockAdjustMap)
            {
                bool act = kv.Value == _stockAdjust;
                kv.Key.BackColor = act ? C.Accent : _cPanel;
                kv.Key.ForeColor = act ? Color.White : _cSub;
                kv.Key.Invalidate();
            }
        }

        private void StockSetPeriodActive()
        {
            foreach (KeyValuePair<Button, string> kv in _stockPeriodMap)
            {
                bool act = kv.Value == _stockPeriod;
                kv.Key.BackColor = act ? C.Accent : _cPanel;
                kv.Key.ForeColor = act ? Color.White : _cSub;
                kv.Key.Invalidate();
            }
        }

        /// <summary>切换 K 线周期（日 / 周 / 月）：K线、指标、筹码三处都要用同一周期重算，
        /// 否则周线蜡烛会配上日线 MA（MA5 变 5 日）或日线筹码。</summary>
        private void StockSetPeriod(string period)
        {
            if (_stockPeriod == period) return;
            _stockPeriod = period;
            StockSetPeriodActive();
            if (_stockCurrent != null)
            {
                StockLoadHistory(_stockCurrent, false);   // 会顺带重算指标（见 StockRenderHistory）
                StockLoadChip(_stockCurrent);
            }
            if (_stockKline != null) _stockKline.FocusForKeys();
        }

        // ---- 历史查看记录（左侧可折叠导航，持久化到 stockanaly-data）----
        private TableLayoutPanel BuildStockHistoryPanel()
        {
            var hist = new TableLayoutPanel();
            hist.Dock = DockStyle.Fill;
            hist.Margin = new Padding(0, 8, 8, 12);
            hist.ColumnCount = 1;
            hist.RowCount = 2;
            hist.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100f));
            hist.RowStyles.Add(new RowStyle(SizeType.AutoSize));      // 头部
            hist.RowStyles.Add(new RowStyle(SizeType.Percent, 100f)); // 列表

            var head = new FlowLayoutPanel();
            head.Dock = DockStyle.Top;
            head.Height = 30;
            head.WrapContents = false;
            head.Margin = new Padding(0);
            head.Padding = new Padding(0);
            _stockHistoryHeader = Lbl("历史查看");
            _stockHistoryHeader.Font = new Font("Microsoft YaHei UI", 10f, FontStyle.Bold);
            _stockHistoryHeader.AutoSize = true;
            _stockHistoryHeader.TextAlign = ContentAlignment.MiddleLeft;
            head.Controls.Add(_stockHistoryHeader);

            _stockHistoryToggle = new Button();
            _stockHistoryToggle.Text = "‹";        // ‹ 收起 / › 展开
            _stockHistoryToggle.FlatStyle = FlatStyle.Flat;
            _stockHistoryToggle.FlatAppearance.BorderSize = 0;
            _stockHistoryToggle.Font = new Font("Microsoft YaHei UI", 10f);
            _stockHistoryToggle.TabStop = false;
            _stockHistoryToggle.Width = 26;
            _stockHistoryToggle.Height = 26;
            _stockHistoryToggle.Margin = new Padding(0, 0, 0, 0);
            _stockHistoryToggle.Click += delegate { StockToggleHistory(); };
            head.Controls.Add(_stockHistoryToggle);
            hist.Controls.Add(head, 0, 0);

            _stockHistoryList = new TableLayoutPanel();
            _stockHistoryList.Dock = DockStyle.Fill;
            _stockHistoryList.AutoScroll = true;
            _stockHistoryList.ColumnCount = 1;
            _stockHistoryList.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100f));
            _stockHistoryList.Margin = new Padding(0);
            _stockHistoryList.Padding = new Padding(0, 2, 0, 0);
            hist.Controls.Add(_stockHistoryList, 0, 1);
            return hist;
        }

        private void StockToggleHistory()
        {
            _stockHistoryExpanded = !_stockHistoryExpanded;
            if (_stockHistoryRoot != null)
            {
                _stockHistoryRoot.ColumnStyles[0] = new ColumnStyle(SizeType.Absolute, _stockHistoryExpanded ? 170f : 32f);
                _stockHistoryRoot.PerformLayout();
            }
            if (_stockHistoryHeader != null) _stockHistoryHeader.Visible = _stockHistoryExpanded;
            if (_stockHistoryList != null) _stockHistoryList.Visible = _stockHistoryExpanded;
            if (_stockHistoryToggle != null) _stockHistoryToggle.Text = _stockHistoryExpanded ? "‹" : "›";
        }

        private void StockRenderHistoryNav()
        {
            if (_stockHistoryList == null) return;
            _stockHistoryList.Controls.Clear();
            _stockHistoryList.RowStyles.Clear();
            if (_stockHistory == null || _stockHistory.Count == 0)
            {
                var empty = Mute(Lbl("暂无查看记录"));
                AddRow(_stockHistoryList, empty);
                return;
            }
            foreach (StockHistoryItem h in _stockHistory)
            {
                var btn = new Button();
                btn.Dock = DockStyle.Top;
                btn.Height = 38;
                btn.FlatStyle = FlatStyle.Flat;
                btn.FlatAppearance.BorderSize = 0;
                btn.Font = new Font("Microsoft YaHei UI", 9f);
                btn.TextAlign = ContentAlignment.MiddleLeft;
                btn.TabStop = false;
                btn.Tag = h.Code;
                bool cur = (_stockCurrent == h.Code);
                btn.BackColor = cur ? C.Accent : _cPanel;
                btn.ForeColor = cur ? Color.White : _cText;
                string name = string.IsNullOrEmpty(h.Name) ? "" : ("  " + h.Name);
                btn.Text = h.Code + name;
                btn.Click += delegate { _stockCode.Text = h.Code; StockOpen(); StockSubSelect(0); };
                btn.MouseUp += delegate(object s, MouseEventArgs e)
                {
                    if (e.Button == MouseButtons.Right) StockRemoveHistory(h.Code);
                };
                AddRow(_stockHistoryList, btn);
            }
            var clear = MiniBtn("清空历史", delegate { StockClearHistory(); }, 0);
            clear.Dock = DockStyle.Top;
            clear.Margin = new Padding(0, 0, 0, 8);
            AddRow(_stockHistoryList, clear);
        }

        private void StockLoadHistoryFile()
        {
            _stockHistory = new List<StockHistoryItem>();
            try
            {
                _stockHistoryPath = Path.Combine(LauncherStateDir(), "history_stock_view.json");
                // 老版本把它写在数据目录里：新位置还没有时先读老位置，
                // 免得升级后「最近查看」凭空清空（下一次保存就会落到新位置）。
                string from = _stockHistoryPath;
                if (!File.Exists(from))
                {
                    // 老位置有两个：根目录（最早）与 config/（第 21 项分区后的位置），都试一遍
                    string root = ResolveDataDirSetting();
                    string[] legacyPaths = {
                        Path.Combine(root, "history_stock_view.json"),
                        Path.Combine(root, "config", "history_stock_view.json"),
                    };
                    foreach (string legacy in legacyPaths)
                    {
                        if (File.Exists(legacy)) { from = legacy; break; }
                    }
                }
                if (File.Exists(from))
                {
                    string txt = File.ReadAllText(from, Encoding.UTF8);
                    var arr = new JavaScriptSerializer().Deserialize<System.Collections.ArrayList>(txt);
                    if (arr != null)
                    {
                        foreach (Dictionary<string, object> d in arr)
                        {
                            var it = new StockHistoryItem();
                            it.Code = J.Str(J.Get(d, "code"));
                            it.Name = J.Str(J.Get(d, "name"));
                            object ts;
                            if (d.TryGetValue("ts", out ts) && ts != null)
                            {
                                try { it.Ts = Convert.ToInt64(ts); } catch (Exception) { it.Ts = 0; }
                            }
                            if (!string.IsNullOrEmpty(it.Code)) _stockHistory.Add(it);
                        }
                    }
                }
            }
            catch (Exception) { }
        }

        private void StockSaveHistoryFile()
        {
            try
            {
                if (string.IsNullOrEmpty(_stockHistoryPath)) return;
                string dir = Path.GetDirectoryName(_stockHistoryPath);
                if (!Directory.Exists(dir)) Directory.CreateDirectory(dir);
                var arr = new System.Collections.ArrayList();
                foreach (StockHistoryItem h in _stockHistory)
                {
                    var d = new Dictionary<string, object>();
                    d["code"] = h.Code;
                    d["name"] = h.Name ?? "";
                    d["ts"] = h.Ts;
                    arr.Add(d);
                }
                File.WriteAllText(_stockHistoryPath, new JavaScriptSerializer().Serialize(arr), Encoding.UTF8);
            }
            catch (Exception) { }
        }

        private void StockAddHistory(string code, string name)
        {
            if (string.IsNullOrEmpty(code) || !Regex.IsMatch(code, "^[0-9]{6}$")) return;
            StockHistoryItem ex = _stockHistory.Find(h => h.Code == code);
            if (ex != null) _stockHistory.Remove(ex);
            _stockHistory.Insert(0, new StockHistoryItem { Code = code, Name = name ?? "", Ts = DateTimeOffset.UtcNow.ToUnixTimeSeconds() });
            while (_stockHistory.Count > 10) _stockHistory.RemoveAt(_stockHistory.Count - 1);  // 滚动覆盖：超过 10 条丢弃最旧
            StockSaveHistoryFile();
            StockRenderHistoryNav();
        }

        private void StockRemoveHistory(string code)
        {
            StockHistoryItem ex = _stockHistory.Find(h => h.Code == code);
            if (ex != null) _stockHistory.Remove(ex);
            StockSaveHistoryFile();
            StockRenderHistoryNav();
        }

        private void StockClearHistory()
        {
            _stockHistory.Clear();
            StockSaveHistoryFile();
            StockRenderHistoryNav();
        }

        private void StockOnEnter()
        {
            // 个股页需要代码才能加载，进入时不自动拉取；仅确保图表按当前主题重绘。
            if (_stockKline != null) _stockKline.Invalidate();
            // 从别的顶级标签切进来时，重新挂载当前二级页并刷新，避免首屏不刷新
            if (_stockSubBody != null)
            {
                StockSubSelect(_stockSubIndex);
                _stockSubBody.PerformLayout();
                _stockSubBody.Invalidate(true);
            }
            StockRenderHistoryNav();   // 刷新左侧历史导航的当前项高亮
        }

        #endregion

        // ================= 自绘日 K 蜡烛图 =================

        private sealed class KBar
        {
            public string Date;
            public double O, C, L, H, V;
        }

        private sealed class KMark
        {
            public string Date;
            public string Kind;   // in / out / event
            public Color Color;
            public string Text;
        }

        // 指标副图（lower 面板）数据结构：一个面板含若干序列（线 / 柱）
        private sealed class ChartSeries
        {
            public string Name;
            public string Kind;          // "line" | "bar"
            public List<double> Data;    // 与 K 线等长（按日期对齐），NaN 表示缺失
            public Color Color;
            public float Width = 1.4f;   // 线宽（通达信 LINETHICK2 → 2f）
            // 「阈值以上转红」高亮：对齐通达信 RPS 副图里的
            // IF(RPS>=M, RPS, DRAWNULL), COLORRED；只在相邻两点都 ≥ 阈值时整段画红。
            public bool HasHi;
            public double HiThreshold;
            public Color HiColor;
        }

        private sealed class LowerPanel
        {
            public string Id;
            public string Title;
            public int Weight = 1;
            public List<ChartSeries> Series = new List<ChartSeries>();
        }

        /// <summary>筹码分布的一根价格柱（后端 bins[] 的一项）。</summary>
        private sealed class ChipBin
        {
            public double Lo;
            public double Hi;
            public double Price;
            public double Pct;          // 占比（%，全部合计 100）
        }

        /// <summary>筹码公式下拉项：显示名 + 后端 id。</summary>
        private sealed class ChipFormulaItem
        {
            public string Id;
            public string Name;
            public override string ToString() { return Name ?? Id; }
        }

        /// <summary>筹码分布统计（后端 stats）。字段可空：后端算不出时为 null。</summary>
        private sealed class ChipStats
        {
            public double? AvgCost;
            public double? PeakPrice;
            public double? ProfitRatio;
            public double? Scr90;
            public double Close;            // 该帧当日的收盘价（获利/套牢的分界）
        }

        /// <summary>
        /// 筹码分布窗口：位于 K 线**右侧**，横轴是筹码占比、纵轴是价格（与 K 线价格轴对齐）。
        /// 与指标体系（按日期的序列）不同，这里是「某个时点上各价位堆了多少筹码」的直方图，
        /// 故单独成一个窗口渲染，不走 KLineChart 的副图面板。
        /// 数据来自 GET /api/chip/dist（换手率衰减 + 三角形分布，见 chip_dist_service.py）。
        /// </summary>
        private sealed class ChipPanel : ChartControl
        {
            private const int LeftPad = 6;
            private const int RightPad = 8;
            private const int GridCount = 5;        // 与 K 线一致的横向网格数，便于对齐

            public KLineChart Owner;                // 取价格轴几何（与 K 线对齐用）
            private List<ChipBin> _bins = new List<ChipBin>();          // 价格轴（各分箱的价格区间）
            private List<string> _dates = new List<string>();           // 逐日快照的日期
            private List<double[]> _frames = new List<double[]>();      // 逐日快照的占比（与 _bins 同序）
            private List<ChipStats> _frameStats = new List<ChipStats>();
            private Dictionary<string, int> _dateIdx = new Dictionary<string, int>();
            private int _frame = -1;                // 当前显示的帧（默认最新）
            private string _status = "待加载";
            private string _note = "";              // 口径提示（如锁仓未修正）
            private bool _failed = false;

            public ChipPanel()
            {
                // SetStyle 已上移到基类 ChartControl（第 07 项）
                DoubleBuffered = true;
                Width = 172;
            }

            /// <summary>装入计算结果。dates / frames / frameStats 三者一一对应（逐日快照）。</summary>
            public void SetData(List<ChipBin> bins, List<string> dates,
                                List<double[]> frames, List<ChipStats> frameStats, string note)
            {
                _bins = bins ?? new List<ChipBin>();
                _dates = dates ?? new List<string>();
                _frames = frames ?? new List<double[]>();
                _frameStats = frameStats ?? new List<ChipStats>();
                _dateIdx = new Dictionary<string, int>();
                for (int i = 0; i < _dates.Count; i++)
                {
                    if (!_dateIdx.ContainsKey(_dates[i])) _dateIdx[_dates[i]] = i;
                }
                _note = note ?? "";
                _status = "";
                _failed = false;
                _frame = _frames.Count - 1;          // 默认显示最新一天
                Invalidate();
            }

            /// <summary>
            /// 光标跟随：切到光标所在交易日那一帧。日期不在快照区间内时保持原帧不动
            /// （例如滚轮缩小后光标落到更早的日期——等防抖重算完成后即可覆盖）。
            /// </summary>
            public void SetCursorDate(string date)
            {
                if (string.IsNullOrEmpty(date) || _dateIdx.Count == 0) return;
                int idx;
                if (!_dateIdx.TryGetValue(date, out idx)) return;
                if (idx == _frame) return;
                _frame = idx;
                Invalidate();
            }

            /// <summary>加载中 / 失败提示。</summary>
            public void SetStatus(string status, bool failed)
            {
                _bins = new List<ChipBin>();
                _dates = new List<string>();
                _frames = new List<double[]>();
                _frameStats = new List<ChipStats>();
                _dateIdx = new Dictionary<string, int>();
                _frame = -1;
                _status = status ?? "";
                _failed = failed;
                Invalidate();
            }

            protected override void OnPaint(PaintEventArgs e)
            {
                base.OnPaint(e);
                var g = e.Graphics;
                BeginPaint(g);                 // 抗锯齿统一由基类设置（第 07 项）
                bool dark = (BackColor.R + BackColor.G + BackColor.B) < 200;
                Color grid = dark ? Color.FromArgb(54, 58, 68) : Color.FromArgb(222, 224, 228);
                Color text = ForeColor;
                using (var bg = new SolidBrush(BackColor))
                    g.FillRectangle(bg, 0, 0, Width, Height);
                using (var tb = new SolidBrush(text))
                using (var titleFont = new Font(Font, FontStyle.Bold))
                {
                    g.DrawString("筹码分布", titleFont, tb, 2, 2);
                    // 标题右侧标出当前帧的日期：光标回溯时才知道看的是哪一天
                    if (_frame >= 0 && _frame < _dates.Count)
                    {
                        g.DrawString(_dates[_frame], Font, tb, Width - RightPad, 3, FmtRight);
                    }
                    if (_bins.Count == 0 || _frames.Count == 0)
                    {
                        using (var mb = new SolidBrush(_failed ? C.UpErr : text))
                            g.DrawString(_status, Font, mb,
                                new RectangleF(2, 22, Width - 4, Height - 24), FmtLeft);                        return;
                    }

                    // 价格轴：优先与 K 线完全一致（同一 lo/hi 映射到同一纵向区间）
                    double lo = 0, hi = 1;
                    int top = 20, bottom = Math.Max(30, Height - 18);
                    bool aligned = (Owner != null)
                        && Owner.TryGetPriceAxis(out lo, out hi, out top, out bottom);
                    if (!aligned)
                    {   // K 线还没画过（或已清空）：退回按筹码自身的价格范围铺满
                        lo = double.MaxValue; hi = double.MinValue;
                        foreach (ChipBin b in _bins) { if (b.Lo < lo) lo = b.Lo; if (b.Hi > hi) hi = b.Hi; }
                        if (hi <= lo) hi = lo + 1;
                        top = 20; bottom = Math.Max(30, Height - 18);
                    }
                    int plotH = Math.Max(10, bottom - top);
                    int plotW = Math.Max(10, Width - LeftPad - RightPad);
                    double span = hi - lo;
                    if (span <= 0) span = 1;
                    Func<double, double> yOf = p => bottom - (p - lo) / span * plotH;

                    using (var pen = new Pen(grid))
                    {
                        for (int i = 0; i <= GridCount; i++)
                        {
                            double p = lo + span * i / GridCount;
                            g.DrawLine(pen, LeftPad - 3, (int)yOf(p), Width - RightPad, (int)yOf(p));
                        }
                    }

                    // 当前帧：光标回溯时取光标所在交易日那一帧
                    if (_frame < 0 || _frame >= _frames.Count) _frame = _frames.Count - 1;
                    double[] pct = _frames[_frame];
                    ChipStats st = (_frame >= 0 && _frame < _frameStats.Count) ? _frameStats[_frame] : null;
                    double refClose = (st != null) ? st.Close : 0;   // 获利/套牢以**该日**收盘价为界

                    // 筹码分布：成本低于当日收盘 = 获利盘（暖色），高于 = 套牢盘（冷色）
                    // 画面形状画成**三角形分布**：每个分箱取其占比换算的宽度与中心价，
                    // 相邻分箱的顶点**直线相连**成峰形轮廓（不再是一格格的方块阶梯）。
                    // 占比为 0 的分箱也参与连线（宽度 0），这样峰与峰之间的谷能真正归零。
                    double maxPct = 0;
                    for (int i = 0; i < pct.Length; i++) if (pct[i] > maxPct) maxPct = pct[i];
                    if (maxPct <= 0) maxPct = 1;
                    Color profit = dark ? C.ProfitDark : C.ProfitLight;
                    Color locked = dark ? C.LockedDark : C.LockedLight;

                    // 先收集价格轴内各分箱的「顶点」：(左侧轴 + 占比宽度, 该箱中心价)
                    var idxs = new List<int>();
                    var ws = new List<int>();
                    var ys = new List<int>();
                    var profs = new List<bool>();
                    for (int i = 0; i < pct.Length && i < _bins.Count; i++)
                    {
                        ChipBin b = _bins[i];
                        if (b.Hi < lo || b.Lo > hi) continue;       // 价格轴之外的分箱不画（会盖住统计区）
                        int w = (int)Math.Round(pct[i] / maxPct * plotW);
                        if (w < 0) w = 0;
                        if (w > plotW) w = plotW;
                        int yc = (int)yOf(b.Price);
                        if (yc < top) yc = top;
                        if (yc > bottom) yc = bottom;
                        idxs.Add(i); ws.Add(w); ys.Add(yc); profs.Add(b.Price <= refClose);
                    }
                    // 逐段成面：连续且同色的一段连成一个多边形，避免逐个填充产生接缝。
                    // 配色按价格相对收盘价单调划分，故实际最多两段（获利段 / 套牢段）。
                    int k2 = 0;
                    while (k2 < idxs.Count)
                    {
                        bool isProfit = profs[k2];
                        int j = k2;
                        while (j + 1 < idxs.Count && profs[j + 1] == isProfit
                               && idxs[j + 1] == idxs[j] + 1) j++;
                        var poly = new List<Point>();
                        poly.Add(new Point(LeftPad, ys[k2]));            // 左轴上端点
                        for (int t = k2; t <= j; t++)                    // 峰形外轮廓
                            poly.Add(new Point(LeftPad + ws[t], ys[t]));
                        poly.Add(new Point(LeftPad, ys[j]));             // 左轴下端点，闭合
                        using (var br = new SolidBrush(isProfit ? profit : locked))
                            g.FillPolygon(br, poly.ToArray());
                        k2 = j + 1;
                    }

                    // 当日收盘 / 平均成本参考线
                    using (var penClose = new Pen(C.LineGold))
                    {
                        penClose.DashStyle = DashStyle.Dash;
                        int yc = (int)yOf(refClose);
                        if (yc >= top && yc <= bottom) g.DrawLine(penClose, LeftPad - 3, yc, Width - RightPad, yc);
                    }
                    if (st != null && st.AvgCost != null)
                    {
                        using (var penAvg = new Pen(C.FlatSoft))
                        {
                            penAvg.DashStyle = DashStyle.Dot;
                            int ya = (int)yOf(st.AvgCost.Value);
                            if (ya >= top && ya <= bottom) g.DrawLine(penAvg, LeftPad - 3, ya, Width - RightPad, ya);
                        }
                    }

                    // 横坐标刻度：横轴满宽 = 最高峰占比，四等分画短刻度。
                    // 只标最高峰那一处的百分比（= 满刻度值），其余不标以免小面板被数字挤满。
                    if (plotH > 30)
                    {
                        using (var penTick = new Pen(grid))
                        {
                            for (int t = 0; t <= 4; t++)
                            {
                                int xt = LeftPad + (int)Math.Round(plotW * t / 4.0);
                                g.DrawLine(penTick, xt, bottom, xt, bottom + 3);
                            }
                        }
                        // 标签贴着横轴、右对齐（图形只到 Width-RightPad，不会压住最右侧一格）
                        using (var bTick = new SolidBrush(C.Flat))
                            g.DrawString(maxPct.ToString("F2") + "%", Font, bTick,
                                         Width - RightPad, bottom - 14, FmtRight);
                    }

                    // 统计区（价格区之下）：平均成本 / 获利比例 / 峰位 / 集中度
                    var lines = new List<string>();
                    if (st != null)
                    {
                        if (st.AvgCost != null) lines.Add("平均成本 " + st.AvgCost.Value.ToString("F2"));
                        if (st.ProfitRatio != null) lines.Add("获利比例 " + st.ProfitRatio.Value.ToString("F1") + "%");
                        if (st.PeakPrice != null) lines.Add("峰位价 " + st.PeakPrice.Value.ToString("F2"));
                        if (st.Scr90 != null) lines.Add("集中度90 " + (st.Scr90.Value * 100).ToString("F1"));
                    }
                    lines.Add("收盘 " + refClose.ToString("F2"));
                    if (!string.IsNullOrEmpty(_note)) lines.Add(_note);
                    float y = bottom + 6;
                    foreach (string s in lines)
                    {
                        using (var b2 = new SolidBrush(s == _note && !string.IsNullOrEmpty(_note)
                            ? C.Flat : text))
                        {
                            g.DrawString(s, Font, b2, 2, y);
                        }
                        y += 15;
                        if (y > Height - 2) break;
                    }
                }
            }
        }

        private sealed class KLineChart : ChartControl
        {
            private const int LeftPad = 54;
            private const int RightPad = 10;
            private const int TopPad = 20;
            private const int BottomPad = 18;
            private const int VolRatio = 22;   // 成交量区占纵向比例（%）

            private List<KBar> _bars = new List<KBar>();      // 当前展示口径（指向 _qfq / _raw）
            private readonly List<KBar> _qfq = new List<KBar>();
            private readonly List<KBar> _raw = new List<KBar>();
            private string _adjust = "qfq";                    // qfq 前复权 / raw 不复权
            private readonly List<KMark> _marks = new List<KMark>();
            private List<List<double>> _ma = new List<List<double>>();
            private List<LowerPanel> _indicators = new List<LowerPanel>();   // 指标副图（lower）
            private List<ChartSeries> _mainIndicators = new List<ChartSeries>();   // 主图叠加指标（main 面板）
            private int _range = 250;                          // 可见 K 线根数，0 = 全部
            private int _offset = 0;                           // 平移：从最新端向左偏移的根数
            private bool _dragging = false;
            private int _dragStartX = 0;
            private int _dragStartOffset = 0;
            private readonly ToolTip _tip = new ToolTip();
            private int _hoverIndex = -1;
            public string Period = "day";                      // K 线周期（图例显示 日K / 周K / 月K）
            public Action<int> OnRangeChanged;                 // 缩放改变范围时通知外部刷新高亮
            /// <summary>光标所在 K 线的日期（移出时保留最后一次）——右侧筹码窗口据此切换到那一天。</summary>
            public Action<string> OnHoverDate;
            private string _hoverDate = null;

            /// <summary>最近一次光标所在的交易日；移出图表不清空，便于重算后回到原处。</summary>
            public string CursorDate { get { return _hoverDate; } }

            // 价格轴几何（每次 OnPaint 记录）：右侧「筹码分布」窗口按同一价格轴对齐，
            // 使筹码峰的高度位置与 K 线的价格坐标严格对应。
            private double _axisLo, _axisHi;
            private int _axisTop, _axisBottom;
            private bool _axisReady;
            public event Action PriceAxisChanged;

            /// <summary>取当前价格轴：[lo, hi] 映射到纵向 [top, bottom]；无数据时返回 false。</summary>
            public bool TryGetPriceAxis(out double lo, out double hi, out int top, out int bottom)
            {
                lo = _axisLo; hi = _axisHi; top = _axisTop; bottom = _axisBottom;
                return _axisReady;
            }

            private void PublishAxis(double lo, double hi, int top, int bottom)
            {
                bool changed = !_axisReady || Math.Abs(_axisLo - lo) > 1e-9
                    || Math.Abs(_axisHi - hi) > 1e-9 || _axisTop != top || _axisBottom != bottom;
                _axisLo = lo; _axisHi = hi; _axisTop = top; _axisBottom = bottom;
                _axisReady = true;
                if (changed && PriceAxisChanged != null) PriceAxisChanged();
            }

            private void ClearAxis()
            {
                bool changed = _axisReady;
                _axisReady = false;
                if (changed && PriceAxisChanged != null) PriceAxisChanged();
            }

            private static readonly int[] MaPeriods = new int[] { 5, 10, 20, 60 };
            private static readonly Color[] MaColors = new Color[] {
                C.LineDif, C.LineDea,
                C.LinePink, C.LineViolet };

            public KLineChart()
            {
                // SetStyle 已上移到基类 ChartControl（第 07 项）
                Height = 470;
                _tip.AutoPopDelay = 5000;
                _tip.InitialDelay = 120;
                _tip.ReshowDelay = 120;
                DoubleBuffered = true;
            }

            public void SetData(List<KBar> qfq, List<KBar> raw, List<KMark> marks)
            {
                _qfq.Clear();
                if (qfq != null) _qfq.AddRange(qfq);
                _raw.Clear();
                if (raw != null) _raw.AddRange(raw);
                _marks.Clear();
                if (marks != null) _marks.AddRange(marks);
                _offset = 0;
                ApplyAdjust();
            }

            public void SetAdjust(string a)
            {
                if (_adjust == a) return;
                _adjust = a;
                _offset = 0;
                ApplyAdjust();
            }

            private void ApplyAdjust()
            {
                _bars = (_adjust == "raw" && _raw.Count > 0) ? _raw : _qfq;
                _ma = ComputeMA(_bars);
                Invalidate();
            }

            public void SetRange(int r)
            {
                _range = r;
                _offset = 0;
                Invalidate();
            }

            /// <summary>设置下方指标副图（lower 面板）。传空列表即清除。</summary>
            public void SetIndicators(List<LowerPanel> panels)
            {
                _indicators = panels ?? new List<LowerPanel>();
                Invalidate();
            }

            /// <summary>设置主图叠加指标（main 面板）。传空列表即清除。</summary>
            public void SetMainIndicators(List<ChartSeries> series)
            {
                _mainIndicators = series ?? new List<ChartSeries>();
                Invalidate();
            }

            /// <summary>当前显示口径下的交易日序列（副图数据按此对齐）。</summary>
            public List<string> GetDates()
            {
                var d = new List<string>();
                foreach (var b in _bars) d.Add(b.Date);
                return d;
            }

            // GetPreferredSize 已上移到基类 ChartControl（第 07 项）

            private static List<List<double>> ComputeMA(List<KBar> bars)
            {
                var res = new List<List<double>>();
                foreach (int p in MaPeriods)
                {
                    var s = new List<double>();
                    for (int i = 0; i < bars.Count; i++)
                    {
                        if (i < p - 1) { s.Add(double.NaN); continue; }
                        double sum = 0;
                        for (int j = 0; j < p; j++) sum += bars[i - j].C;
                        s.Add(sum / p);
                    }
                    res.Add(s);
                }
                return res;
            }

            protected override void OnMouseDown(MouseEventArgs e)
            {
                base.OnMouseDown(e);
                if (e.Button == MouseButtons.Left && _bars.Count > 0)
                {
                    _dragging = true;
                    _dragStartX = e.X;
                    _dragStartOffset = _offset;
                    this.Capture = true;
                }
            }

            protected override void OnMouseUp(MouseEventArgs e)
            {
                base.OnMouseUp(e);
                if (_dragging) { _dragging = false; this.Capture = false; }
            }

            protected override void OnMouseEnter(EventArgs e)
            {
                base.OnMouseEnter(e);
                FocusForKeys();
            }

            /// <summary>聚焦图表以便接收**滚轮缩放与方向键**；但外层 AutoScroll 容器会顺手把图表
            /// 滚入视口，导致上方查询区被顶出屏幕——聚焦后把滚动位置还原回去。</summary>
            public void FocusForKeys()
            {
                if (!this.TabStop || this.Focused) return;
                ScrollableControl host = FindScrollHost();
                Point saved = host != null ? host.AutoScrollPosition : Point.Empty;
                this.Focus();
                if (host != null)
                    host.AutoScrollPosition = new Point(-saved.X, -saved.Y);
            }

            private ScrollableControl FindScrollHost()
            {
                Control c = this.Parent;
                while (c != null)
                {
                    ScrollableControl s = c as ScrollableControl;
                    if (s != null && s.AutoScroll) return s;
                    c = c.Parent;
                }
                return null;
            }

            protected override void OnMouseMove(MouseEventArgs e)
            {
                base.OnMouseMove(e);
                int n = VisibleCount();
                if (n <= 0) { _hoverIndex = -1; _tip.Hide(this); return; }
                int plotW = Math.Max(20, Width - LeftPad - RightPad);

                if (_dragging)
                {
                    int dx = e.X - _dragStartX;
                    int total = _bars.Count;
                    int deltaBars = (int)Math.Round(dx * (double)n / plotW);   // 右拖露出更早期数据
                    _offset = Math.Max(0, Math.Min(_dragStartOffset + deltaBars, total - n));
                    _hoverIndex = -1;
                    _tip.Hide(this);
                    Invalidate();
                    return;
                }

                double slot = (double)plotW / n;
                int idx = (int)((e.X - LeftPad) / slot);
                if (idx < 0) idx = 0;
                if (idx >= n) idx = n - 1;
                _hoverIndex = idx;
                Invalidate();
                PublishHoverDate();
                ShowHoverTip(e.X, e.Y);
            }

            // ---- 键盘：↑↓ 缩放、←→ 移动光标（与鼠标滚轮 / 悬停同一套实现）----

            protected override void OnPreviewKeyDown(PreviewKeyDownEventArgs e)
            {
                base.OnPreviewKeyDown(e);
                // 方向键默认被容器当成「移动焦点」吞掉，这里声明由图表自己处理
                if (e.KeyCode == Keys.Up || e.KeyCode == Keys.Down
                    || e.KeyCode == Keys.Left || e.KeyCode == Keys.Right)
                    e.IsInputKey = true;
            }

            protected override void OnKeyDown(KeyEventArgs e)
            {
                base.OnKeyDown(e);
                if (_bars.Count == 0) return;
                if (e.KeyCode == Keys.Up) { Zoom(-20); e.Handled = true; e.SuppressKeyPress = true; }
                else if (e.KeyCode == Keys.Down) { Zoom(20); e.Handled = true; e.SuppressKeyPress = true; }
                else if (e.KeyCode == Keys.Left) { MoveCursor(-1); e.Handled = true; e.SuppressKeyPress = true; }
                else if (e.KeyCode == Keys.Right) { MoveCursor(1); e.Handled = true; e.SuppressKeyPress = true; }
            }

            /// <summary>缩放：step&lt;0 放大（更少根），step&gt;0 缩小（更多根）。滚轮与 ↑↓ 共用。</summary>
            private void Zoom(int step)
            {
                int total = _bars.Count;
                if (total == 0) return;
                int cur = _range > 0 ? _range : total;
                int next = Math.Max(20, Math.Min(total, cur + step));
                _range = (next >= total) ? 0 : next;
                _offset = Math.Max(0, Math.Min(_offset, total - VisibleCount()));
                int n = VisibleCount();
                if (_hoverIndex >= n) _hoverIndex = n - 1;      // 缩小后光标可能越界
                Invalidate();
                if (OnRangeChanged != null) OnRangeChanged(_range);
            }

            /// <summary>光标左右移动一根；走到可视区边缘时**自动平移窗口**（与行情软件一致）。</summary>
            private void MoveCursor(int delta)
            {
                int n = VisibleCount();
                if (n <= 0) return;
                int total = _bars.Count;
                if (_hoverIndex < 0) _hoverIndex = n - 1;        // 首次按键从最新一根起步
                int next = _hoverIndex + delta;
                if (next < 0)
                {   // 已在最左：把窗口往早期挪，露出更老的 K 线
                    if (_offset < total - n) _offset = Math.Min(_offset + 1, total - n);
                    next = 0;
                }
                else if (next >= n)
                {   // 已在最右：把窗口往最新挪
                    if (_offset > 0) _offset -= 1;
                    next = n - 1;
                }
                _hoverIndex = next;
                Invalidate();
                PublishHoverDate();
                ShowHoverTip();
            }

            /// <summary>把光标所在交易日广播出去（右侧筹码窗口据此切换）。</summary>
            private void PublishHoverDate()
            {
                var vis = VisibleBars();
                int idx = _hoverIndex;
                if (idx < 0 || idx >= vis.Count) return;
                _hoverDate = vis[idx].Date;
                if (OnHoverDate != null) OnHoverDate(_hoverDate);
            }

            /// <summary>在光标处显示 OHLC 浮窗。键盘调用时没有鼠标坐标，改用光标对应的 x。</summary>
            private void ShowHoverTip(int mouseX = -1, int mouseY = -1)
            {
                var vis = VisibleBars();
                int idx = _hoverIndex;
                if (idx < 0 || idx >= vis.Count) { _tip.Hide(this); return; }
                KBar b = vis[idx];
                var sb = new StringBuilder();
                sb.Append(b.Date);
                sb.Append("  开 ").Append(b.O.ToString("F2"));
                sb.Append("  收 ").Append(b.C.ToString("F2"));
                sb.Append("  高 ").Append(b.H.ToString("F2"));
                sb.Append("  低 ").Append(b.L.ToString("F2"));
                sb.Append("  量 ").Append(VolumeText(b.V));
                foreach (KMark mk in _marks)
                {
                    if (mk.Date == b.Date) sb.Append("  [").Append(mk.Text).Append("]");
                }
                int n = VisibleCount();
                int plotW = Math.Max(20, Width - LeftPad - RightPad);
                int x = mouseX >= 0 ? mouseX
                    : (int)(LeftPad + (double)plotW / Math.Max(1, n) * (idx + 0.5));
                int y = mouseY >= 0 ? mouseY + 16 : 18;
                _tip.Show(sb.ToString(), this, x, y);
            }

            protected override void OnMouseWheel(MouseEventArgs e)
            {
                base.OnMouseWheel(e);
                Zoom(e.Delta > 0 ? -20 : 20);   // 上滚放大（更少根），下滚缩小
            }

            protected override void OnMouseLeave(EventArgs e)
            {
                base.OnMouseLeave(e);
                _hoverIndex = -1;
                _tip.Hide(this);
                Invalidate();
            }

            private int VisibleCount()
            {
                return _range > 0 ? Math.Min(_range, _bars.Count) : _bars.Count;
            }

            private void VisibleWindow(out int start, out int count)
            {
                int total = _bars.Count;
                int n = VisibleCount();
                if (n <= 0) { start = 0; count = 0; return; }
                int off = Math.Max(0, Math.Min(_offset, total - n));
                start = Math.Max(0, total - n - off);
                count = Math.Min(n, total - start);
            }

            private List<KBar> VisibleBars()
            {
                int s, c;
                VisibleWindow(out s, out c);
                if (c <= 0) return new List<KBar>();
                return _bars.GetRange(s, c);
            }

            private static string VolumeText(double v)
            {
                if (v >= 10000) return (v / 10000).ToString("F2") + "万";
                return v.ToString("F0");
            }

            protected override void OnPaint(PaintEventArgs e)
            {
                base.OnPaint(e);
                var g = e.Graphics;
                BeginPaint(g);                 // 抗锯齿统一由基类设置（第 07 项）

                bool dark = (BackColor.R + BackColor.G + BackColor.B) < 200;
                Color grid = dark ? Color.FromArgb(54, 58, 68) : Color.FromArgb(222, 224, 228);
                Color text = ForeColor;

                using (var bg = new SolidBrush(BackColor))
                    g.FillRectangle(bg, 0, 0, Width, Height);

                var vis = VisibleBars();
                if (vis.Count == 0)
                {
                    ClearAxis();
                    DrawLegend(g, text, -1);
                    using (var b = new SolidBrush(text))
                    {
                        var area = new RectangleF(0, 24, Width, Height - 24);
                        g.DrawString("请输入股票代码开始分析", Font, b, area, FmtCenter);
                    }
                    return;
                }

                int n = vis.Count;
                int plotW = Math.Max(20, Width - LeftPad - RightPad);
                int totalH = Height - TopPad - BottomPad;
                // 面板栈：主图（价格 + MA）→ 下方区（成交量 + 指标副图 lower）
                double mainShare = 0.58;
                int plotTop = TopPad;
                int plotBottom = plotTop + (int)(totalH * mainShare);
                int lowerTop = plotBottom + 8;
                int lowerBottom = Height - BottomPad;
                int lowerH = Math.Max(10, lowerBottom - lowerTop);
                int indCount = _indicators.Count;
                int volH = (int)(lowerH * 0.42);
                int volTop = lowerTop;
                int volBottom = volTop + volH;
                int indTop = volBottom + 6;
                int indBottom = lowerBottom;
                // 指标副图矩形（每个均分指标区）
                var indRects = new List<Rectangle>();
                if (indCount > 0)
                {
                    int gap = 6;
                    int eachH = (indBottom - indTop - gap * (indCount - 1)) / indCount;
                    int y = indTop;
                    for (int p = 0; p < indCount; p++)
                    {
                        indRects.Add(new Rectangle(LeftPad, y, plotW, Math.Max(12, eachH)));
                        y += eachH + gap;
                    }
                }
                int bottomMost = (indCount > 0) ? indBottom : volBottom;

                // 价格区间（含 MA）
                double pmin = double.MaxValue, pmax = double.MinValue;
                foreach (KBar b in vis) { if (b.L < pmin) pmin = b.L; if (b.H > pmax) pmax = b.H; }
                int wstart, wcount;
                VisibleWindow(out wstart, out wcount);
                var maTail = new List<List<double>>();
                foreach (List<double> s in _ma)
                {
                    List<double> tail = (s.Count >= wstart + wcount) ? s.GetRange(wstart, wcount) : new List<double>();
                    maTail.Add(tail);
                    foreach (double v in tail) if (!double.IsNaN(v)) { if (v < pmin) pmin = v; if (v > pmax) pmax = v; }
                }
                if (pmax <= pmin) pmax = pmin + 1;
                double pad = (pmax - pmin) * 0.04;
                pmin -= pad; pmax += pad;
                PublishAxis(pmin, pmax, plotTop, plotBottom);   // 供右侧筹码窗口对齐

                double maxVol = 0;
                foreach (KBar b in vis) if (b.V > maxVol) maxVol = b.V;
                if (maxVol <= 0) maxVol = 1;

                double slot = (double)plotW / n;
                double bodyW = Math.Max(1, slot * 0.66);
                double span = pmax - pmin;
                Func<double, double> yOf = p => plotBottom - (p - pmin) / span * (plotBottom - plotTop);
                Func<int, double> xOf = i => LeftPad + slot * (i + 0.5);

                // 网格 + 价格刻度
                using (var pen = new Pen(grid))
                using (var b2 = new SolidBrush(text))
                {
                    int gridN = 5;
                    for (int i = 0; i <= gridN; i++)
                    {
                        double p = pmin + span * i / gridN;
                        int y = (int)yOf(p);
                        g.DrawLine(pen, LeftPad, y, LeftPad + plotW, y);
                        g.DrawString(p.ToString("F2"), Font, b2, 2, y - 7);
                    }
                    g.DrawLine(pen, LeftPad, volBottom, LeftPad + plotW, volBottom);
                }

                // MA 线
                for (int k = 0; k < maTail.Count; k++)
                {
                    List<double> series = maTail[k];
                    using (var pen = new Pen(MaColors[k % MaColors.Length], 1.4f))
                    {
                        bool penUp = false;
                        for (int i = 0; i < series.Count; i++)
                        {
                            if (double.IsNaN(series[i])) { penUp = false; continue; }
                            int x = (int)xOf(i);
                            int y = (int)yOf(series[i]);
                            if (penUp) g.DrawLine(pen, (int)xOf(i - 1), (int)yOf(series[i - 1]), x, y);
                            penUp = true;
                        }
                    }
                }

                // 蜡烛
                for (int i = 0; i < vis.Count; i++)
                {
                    KBar b = vis[i];
                    int x = (int)xOf(i);
                    bool up = b.C >= b.O;
                    Color c = up ? C.Up : C.Down;
                    using (var pen = new Pen(c))
                    using (var br = new SolidBrush(c))
                    {
                        int yH = (int)yOf(b.H), yL = (int)yOf(b.L);
                        int yO = (int)yOf(b.O), yC = (int)yOf(b.C);
                        g.DrawLine(pen, x, yH, x, yL);
                        int top = Math.Min(yO, yC);
                        int h = Math.Max(1, Math.Abs(yC - yO));
                        g.FillRectangle(br, (int)(x - bodyW / 2), top, (int)bodyW, h);
                    }
                }

                // 主图叠加指标（main 面板）：在价格轴范围内绘制线 / 柱
                foreach (ChartSeries s in _mainIndicators)
                {
                    if (s.Data == null) continue;
                    if (s.Kind == "bar")
                    {
                        using (var br = new SolidBrush(s.Color))
                        {
                            for (int i = 0; i < s.Data.Count && i < n; i++)
                            {
                                double v = s.Data[i];
                                if (double.IsNaN(v)) continue;
                                int x = (int)xOf(i);
                                int y = (int)yOf(v);
                                int y0 = plotBottom;
                                int top = Math.Min(y, y0), hh = Math.Max(1, Math.Abs(y - y0));
                                g.FillRectangle(br, (int)(x - bodyW / 2), top, (int)bodyW, hh);
                            }
                        }
                    }
                    else
                    {
                        using (var pen = new Pen(s.Color, 1.4f))
                        {
                            bool penUp = false;
                            for (int i = 0; i < s.Data.Count && i < n; i++)
                            {
                                double v = s.Data[i];
                                if (double.IsNaN(v)) { penUp = false; continue; }
                                int x = (int)xOf(i);
                                int y = (int)yOf(v);
                                if (penUp) g.DrawLine(pen, (int)xOf(i - 1), (int)yOf(s.Data[i - 1]), x, y);
                                penUp = true;
                            }
                        }
                    }
                }

                // 成交量
                for (int i = 0; i < vis.Count; i++)
                {
                    KBar b = vis[i];
                    int x = (int)xOf(i);
                    bool up = b.C >= b.O;
                    Color c = up ? C.Up : C.Down;
                    using (var br = new SolidBrush(c))
                    {
                        int h = (int)(b.V / maxVol * (volBottom - volTop));
                        if (h < 1) h = 1;
                        g.FillRectangle(br, (int)(x - bodyW / 2), volBottom - h, (int)bodyW, h);
                    }
                }

                // 指标副图（lower 面板）：与主图共享 x 轴 / 缩放 / 平移 / 悬停
                for (int p = 0; p < indCount; p++)
                {
                    Rectangle rect = indRects[p];
                    LowerPanel panel = _indicators[p];
                    double ipmin = double.MaxValue, ipmax = double.MinValue;
                    foreach (ChartSeries s in panel.Series)
                        for (int i = 0; i < n; i++)
                        {
                            int gi = wstart + i;
                            if (gi >= s.Data.Count) continue;
                            double v = s.Data[gi];
                            if (double.IsNaN(v)) continue;
                            if (v < ipmin) ipmin = v;
                            if (v > ipmax) ipmax = v;
                        }
                    if (ipmax <= ipmin) ipmax = ipmin + 1;
                    double ppad = (ipmax - ipmin) * 0.12; ipmin -= ppad; ipmax += ppad;
                    double ispan = ipmax - ipmin;
                    Func<double, double> yInd = val => rect.Bottom - (val - ipmin) / ispan * rect.Height;
                    using (var pen = new Pen(grid))
                    {
                        int gN = 2;
                        for (int i = 0; i <= gN; i++)
                        {
                            double v = ipmin + ispan * i / gN;
                            int y = (int)yInd(v);
                            g.DrawLine(pen, LeftPad, y, LeftPad + plotW, y);
                            using (var b2 = new SolidBrush(text)) g.DrawString(v.ToString("F2"), Font, b2, 2, y - 7);
                        }
                        g.DrawLine(pen, LeftPad, rect.Bottom, LeftPad + plotW, rect.Bottom);
                    }
                    foreach (ChartSeries s in panel.Series)
                    {
                        if (s.Kind == "bar")
                        {
                            for (int i = 0; i < n; i++)
                            {
                                int gi = wstart + i;
                                if (gi >= s.Data.Count) continue;
                                double v = s.Data[gi];
                                if (double.IsNaN(v)) continue;
                                int x = (int)xOf(i);
                                Color c = v >= 0 ? C.Up : C.Down;
                                using (var br = new SolidBrush(c))
                                {
                                    int zero = (int)yInd(0);
                                    int top = v >= 0 ? (int)yInd(v) : zero;
                                    int h = Math.Abs((int)yInd(v) - zero);
                                    if (h < 1) h = 1;
                                    g.FillRectangle(br, (int)(x - bodyW / 2), top, (int)bodyW, h);
                                }
                            }
                        }
                        else
                        {
                            using (var pen = new Pen(s.Color, s.Width))
                            {
                                bool penUp = false;
                                for (int i = 0; i < n; i++)
                                {
                                    int gi = wstart + i;
                                    if (gi >= s.Data.Count) { penUp = false; continue; }
                                    double v = s.Data[gi];
                                    if (double.IsNaN(v)) { penUp = false; continue; }
                                    int x = (int)xOf(i);
                                    int y = (int)yInd(v);
                                    if (penUp)
                                    {
                                        double pv = s.Data[wstart + i - 1];
                                        // 对齐 IF(RPS>=M,RPS,DRAWNULL),COLORRED：相邻两点都在阈值之上才整段转红
                                        pen.Color = (s.HasHi && pv >= s.HiThreshold && v >= s.HiThreshold)
                                            ? s.HiColor : s.Color;
                                        g.DrawLine(pen, (int)xOf(i - 1), (int)yInd(pv), x, y);
                                    }
                                    penUp = true;
                                }
                            }
                        }
                    }
                    int lx = LeftPad + 4; int ly = rect.Top + 2;
                    using (var b = new SolidBrush(text))
                    {
                        g.DrawString(panel.Title, Font, b, lx, ly); lx += 44;
                        foreach (ChartSeries s in panel.Series)
                        {
                            using (var pen = new Pen(s.Color, Math.Max(2f, s.Width))) g.DrawLine(pen, lx, ly + 7, lx + 14, ly + 7);
                            g.DrawString(s.Name, Font, b, lx + 18, ly); lx += 58;
                        }
                    }
                }

                // 标记（入池/出池/事件）
                var idxMap = new Dictionary<string, int>();
                for (int i = 0; i < vis.Count; i++) idxMap[vis[i].Date] = i;
                foreach (KMark m in _marks)
                {
                    if (!idxMap.ContainsKey(m.Date)) continue;
                    int x = (int)xOf(idxMap[m.Date]);
                    using (var br = new SolidBrush(m.Color))
                    {
                        int s = 6;
                        if (m.Kind == "event")
                        {
                            Point[] pts = new Point[] { new Point(x, plotTop - 2 - s), new Point(x + s, plotTop - 2), new Point(x, plotTop - 2 + s), new Point(x - s, plotTop - 2) };
                            g.FillPolygon(br, pts);
                        }
                        else
                        {
                            Point[] pts = new Point[] { new Point(x, plotTop - 2 - s), new Point(x + s, plotTop - 2), new Point(x - s, plotTop - 2) };
                            g.FillPolygon(br, pts);
                        }
                    }
                }

                // 悬停竖线
                if (_hoverIndex >= 0 && _hoverIndex < vis.Count)
                {
                    int x = (int)xOf(_hoverIndex);
                    using (var pen = new Pen(C.Flat))
                        g.DrawLine(pen, x, plotTop, x, bottomMost);
                }

                // 日期刻度
                int labelN = Math.Min(8, vis.Count);
                if (labelN > 0)
                {
                    using (var b3 = new SolidBrush(text))
                    {
                        int step = labelN > 1 ? (vis.Count - 1) / (labelN - 1) : 0;
                        for (int i = 0; i < labelN; i++)
                        {
                            int idx = i < labelN - 1 ? i * step : vis.Count - 1;
                            string d = vis[idx].Date;
                            if (d.Length >= 5) d = d.Substring(5);   // MM-DD
                            int x = (int)xOf(idx);
                            int labelW = step > 0 ? step : 1;   // 标签间距（根），避免矩形过窄被裁
                            g.DrawString(d, Font, b3, new RectangleF((float)(x - labelW * slot / 2), (float)(bottomMost + 2), (float)(labelW * slot), (float)BottomPad), FmtCenter);
                        }
                    }
                }

                DrawLegend(g, text, 0);
                // 右下角的「滚轮缩放 · 拖动平移 · 前复权」提示已按需求移除；
                // 周期 / 复权状态看工具栏按钮的高亮即可（图例里也标了 日K / 周K / 月K）。
            }

            private void DrawLegend(Graphics g, Color text, int dummy)
            {
                int x = LeftPad + 4;
                int y = 4;
                using (var b = new SolidBrush(text))
                {
                    // 周期写进图例：周线下这里就是「周K」，MA5 也是 5 周均线，别让人误读
                    string label = (Period == "week") ? "周K" : (Period == "month") ? "月K" : "日K";
                    g.DrawString(label, Font, b, x, y); x += 34;
                    string[] names = new string[] { "MA5", "MA10", "MA20", "MA60" };
                    for (int i = 0; i < names.Length; i++)
                    {
                        using (var pen = new Pen(MaColors[i], 2f))
                            g.DrawLine(pen, x, y + 7, x + 14, y + 7);
                        g.DrawString(names[i], Font, b, x + 18, y);
                        x += 56;
                    }
                }
            }
        }
    }
}
