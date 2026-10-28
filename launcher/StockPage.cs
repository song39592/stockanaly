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
        // ⚠️ 「代码 / 指标」输入入口已统一收敛到右下角隐藏输入框（见 QuickSearch.cs），
        // 个股页不再保留顶部输入框；以下只保留展示用字段。
        private Button _stockRefresh;
        private Label _stockName, _stockHint, _stockStatus, _stockValStatus, _stockIndStatus;
        private KLineChart _stockKline;
        private ChipPanel _stockChip;                      // K 线右侧的筹码分布窗口
        // 筹码模型不再用下拉框：与主图 / 副图指标一致，由右下角输入框键入**索引名**切换
        // （候选来自 /api/chip/dist/formulas，见 StockLoadChipFormulas）。
        private string _stockChipFormulaId = null;         // 当前筹码公式 id（null = 用后端默认 tri_decay）
        private readonly List<IndOpt> _stockChipOpts = new List<IndOpt>();   // 筹码公式候选（Id=纯字母索引名，Raw=公式 id）
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
        // 主图指标：与副图一致，**固定只显示 1 个**，由右下角输入框键入指标名 / id 切换（再输一次同一指标 = 关闭）。
        // 主图指标（含 "ma" 均线）统一走后端指标通道，与副图同一套规则；null = 不显示任何主图指标。
        private string _stockMainIndicator = "ma";
        private readonly List<IndOpt> _stockMainOpts = new List<IndOpt>();           // 主图候选（名称 ↔ id，含 ma）
        // 副图指标：**固定只显示 1 个**。复用右下角输入框键入指标名（带输入联想）即可替换，
        // 不再用勾选框堆叠多个副图面板（多面板会把 K 线主图挤得太矮）。
        private string _stockSubIndicator = null;                                    // 当前副图指标 id（null = 无）
        private readonly List<IndOpt> _stockSubOpts = new List<IndOpt>();            // 副图候选（名称 ↔ id）
        // Raw：筹码公式的后端 id（仅筹码候选使用，其余为 null）
        private sealed class IndOpt { public string Id; public string Name; public string Raw; }
        // 副图指标的联想下拉逻辑见 QuickSearch.cs（与右下角输入框共用）。
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
        private SubTabStrip _stockStrip;

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
            // 「代码 / 指标」输入入口已统一收敛到右下角隐藏输入框（见 QuickSearch.cs）：
            // 敲数字 → 打开个股；敲字母 → 输指标英文 id（macd/rsi/rps…）切换副图。本区只展示状态。
            TableLayoutPanel b0;
            var g0 = Group("查询", out b0);
            _stockName = Mute(Lbl(""));
            AddRow(b0, Row(Lbl("当前个股"), _stockName));

            _stockHint = Mute(Lbl("敲任意数字 / 字母 → 右下角输入框：6 位代码打开个股；输指标英文代码（ma / cyc / macd / rsi / rps）回车切换主图 / 副图指标"));

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
                    StockSessionSave();            // 记住范围档位
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
                    StockSessionSave();            // 记住复权口径
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

            // ---- 二级标签栏（K 线页不滚动，图表撑满）----
            _stockStrip = new SubTabStrip(this, mainCol, 2, 3, "stock-subtab");
            _stockStrip.OnSelected = delegate(int index)
            {
                // 切到 K线 页就把焦点交给图表，↑↓ 缩放 / ←→ 移光标立刻可用
                if (index == 0 && _stockKline != null) _stockKline.FocusForKeys();
                // 切到指标索引页：首次自动拉取目录自检
                if (index == _stockIndSubIndex) StockIndOnEnter();
            };

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

            // 主图指标勾选行已移除：主图指标与副图一样由右下角输入框键入指标名 / id 切换。
            StockRefreshIndicatorList();   // 异步拉取指标清单：主图 / 副图候选灌进右下角输入框的联想下拉
            StockLoadChipFormulas();       // 同步筹码公式清单：索引名 → 切换右侧筹码窗口（与指标同一入口）

            // 「更新至 …」与「个股概况（在榜统计 KPI）」已按需求移除
            // （副图改用顶部代码框输入）；省下的高度全部归 K 线——row1 是 Percent 100，会自动吃满。

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
            // 窗口平移（键盘光标到边缘 auto-pan / 拖动）→ 筹码按新的可见窗口重算，
            // 否则平移到更早日期后，光标日期会落到筹码快照之外（对不上 / 显示边界帧）。
            _stockKline.OnViewChanged = delegate { ScheduleChipReload(); };
            _stockKline.PriceAxisChanged += delegate { if (_stockChip != null) _stockChip.Invalidate(); };
            _stockChip.BackColorChanged += delegate { _stockChip.Invalidate(); };

            // （原「筹码公式下拉框」已移除：筹码模型改由右下角输入框键入索引名切换，
            //   与主图 / 副图指标同一入口，见 QuickSearch.cs 与 StockOpen。）
            kLayout.Controls.Add(kTop, 0, 0);                  // 指标勾选
            kLayout.Controls.Add(_stockKline, 0, 1);       // 图表占满剩余高度
            kLayout.Controls.Add(_stockChip, 1, 1);        // 右侧筹码分布窗口
            _stockStrip.Add("K线", klinePage);

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
                new GridColumn("名次", "rank", true) { Ordinal = true },
                new GridColumn("股东名称"),
                new GridColumn("股份类型"),
                new GridColumn("持股数", "shares", true),
                new GridColumn("占总股本", "pct", true),
                new GridColumn("增减", "change", true),
                new GridColumn("变动", "change_ratio", true),
            });
            _stockBasicHolders.SetSortable(true);   // 第 20 项：按持股数/增减等排序
            AddRow(bh, _stockBasicHolders);
            AddRow(basicStack, gh);

            basicPage.Controls.Add(basicStack);
            _stockStrip.Add("基本信息", basicPage);

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
            _stockStrip.Add("记录 · 消息", recPage);

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
            _stockValGrid.SetSortable(true);        // 第 20 项：情景表按增长率/收益率排序
            AddRow(b5, _stockValGrid);
            AddRow(b5, Row(MiniBtn("在估值页打开完整计算", delegate
            {
                if (_stockCurrent != null) StockJumpValuation(_stockCurrent);
            }, 180)));
            AddRow(valStack, g5);
            valPage.Controls.Add(valStack);
            _stockStrip.Add("估值", valPage);

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
            _stockStrip.Add("扫雷", slPage);

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
            _stockStrip.Add("AI 分析", aiPage);

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
            _stockStrip.Add("偏好设置", prefPage);

            // ---- 二级页 ⑥ 指标索引（目录自检：索引是否重复 + 输入输出数据是否正确）----
            _stockStrip.Add("指标索引", BuildStockIndicatorIndexPage());
            _stockIndSubIndex = _stockStrip.Count - 1;

            _stockStrip.Select(0);


            StockApplyRange();
            StockSetAdjustActive();
            StockSetPeriodActive();

            // 历史查看记录：从 stockanaly-data 加载并渲染左侧导航
            StockLoadHistoryFile();
            StockRenderHistoryNav();
            return p;
        }

        // ---- 打开（输入来自右下角隐藏输入框；见 QuickSearch.cs）----
        private void StockOpen()
        {
            string code = _quickSearch.Text.Trim();
            _quickActiveCode = code;                    // 记入激活字段，供异步守卫使用（与输入框文本解耦）
            if (!Regex.IsMatch(code, "^[0-9]{6}$"))
            {
                // 非 6 位代码：当作「指标名称 / id」尝试切换主图 / 副图指标
                bool isMain;
                string id = StockResolveAnyId(code, out isMain);
                if (id != null)
                {
                    string nm = StockIndicatorNameOf(id) ?? id;
                    _stockHint.Tag = "muted";
                    _stockHint.ForeColor = C.Flat;
                    if (isMain)
                    {
                        if (_stockMainIndicator == id)
                        {   // 再输一次同一主图指标 → 关闭
                            _stockMainIndicator = null;
                            _stockHint.Text = "主图指标已关闭（曾用：" + nm + "）";
                        }
                        else
                        {
                            _stockMainIndicator = id;
                            _stockHint.Text = "主图指标已切换为：" + nm + "（再输一次可关闭；输入 6 位代码则打开个股）";
                        }
                    }
                    else
                    {
                        _stockSubIndicator = id;
                        _stockHint.Text = "副图指标已切换为：" + nm + "（输入 6 位代码则打开个股）";
                    }
                    StockSessionSave();   // 记住主图 / 副图指标选择
                    if (_stockCurrent != null) StockLoadIndicators(_stockCurrent);
                    return;
                }
                // 筹码模型（独立通道）：索引名 → 切换 K 线右侧筹码窗口的公式
                string chipId = StockResolveChipId(code);
                if (chipId != null)
                {
                    _stockChipFormulaId = chipId;
                    _stockHint.Tag = "muted";
                    _stockHint.ForeColor = C.Flat;
                    _stockHint.Text = "筹码模型已切换为：" + (StockChipNameOf(chipId) ?? chipId)
                                    + "（输入 6 位代码则打开个股）";
                    StockSessionSave();   // 记住筹码公式选择
                    if (_stockCurrent != null) StockLoadChip(_stockCurrent);
                    return;
                }
                _stockHint.Text = "股票代码须是 6 位数字（如 600519）；或输入已注册指标名以切换主图 / 副图指标";
                _stockHint.Tag = "bad";
                _stockHint.ForeColor = C.UpErr;
                _quickSearch.Focus();
                return;
            }
            if ((_stockHint.Tag as string) != "bad")
            {
                _stockHint.Tag = "muted";
                _stockHint.ForeColor = C.Flat;
                _stockHint.Text = "数据加载中…";
            }
            _stockCurrent = code;
            StockSessionSave();            // 记住上次打开的个股
            StockRefreshIndicatorList();   // 每次打开同步后端指标清单（后端可能刚启动）
            StockAddHistory(code, null);   // 记录查看历史（名称稍后补全）
            StockLoadName(code);
            // 第 24 项：**先确认这只票的数据是新的再拉 K 线**。库里陈旧时同步补齐
            // （实测约 2 秒，页面等得起），补完再走下面那一串加载 —— 否则用户看到的
            // K 线、筹码、估值可能整体停在几天前，而界面上毫无提示。
            StockEnsureFresh(code, delegate { StockLoadRest(code); });
        }

        // ---- 上次界面状态（settings.json · last_session）----
        // 只记界面偏好（个股 / 周期 / 主副图指标 / 筹码公式 / 范围 / 复权），绝不记行情或筹码矩阵。
        // 写入点全是低频用户操作（切股 / 切周期 / 换指标 / 换筹码 / 点范围 / 点复权），
        // 不在滚轮缩放等高频事件里落盘。

        /// <summary>把当前个股页状态写进 settings.json 的 last_session。</summary>
        private void StockSessionSave()
        {
            try
            {
                UserSettings.Current.LastSession = new LastSessionData
                {
                    code = _stockCurrent ?? "",
                    period = string.IsNullOrEmpty(_stockPeriod) ? "day" : _stockPeriod,
                    main_indicator = _stockMainIndicator ?? "",
                    sub_indicator = _stockSubIndicator ?? "",
                    chip_formula = _stockChipFormulaId ?? "",
                    range_idx = _stockRangeIdx,
                    range_bars = _stockRange,
                    adjust = string.IsNullOrEmpty(_stockAdjust) ? "qfq" : _stockAdjust,
                };
                UserSettings.Save();
            }
            catch { }
        }

        private bool _stockSessionRestored;

        /// <summary>后端就绪后恢复上次界面状态（只做一次；无记录或指标已删则静默跳过）。</summary>
        private void StockRestoreSession()
        {
            if (_stockSessionRestored) return;
            var ls = UserSettings.Current.LastSession;
            if (ls == null) return;
            _stockSessionRestored = true;
            try
            {
                if (ls.period == "day" || ls.period == "week" || ls.period == "month")
                    _stockPeriod = ls.period;
                _stockMainIndicator = string.IsNullOrEmpty(ls.main_indicator) ? null : ls.main_indicator;
                _stockSubIndicator = string.IsNullOrEmpty(ls.sub_indicator) ? null : ls.sub_indicator;
                _stockChipFormulaId = string.IsNullOrEmpty(ls.chip_formula) ? null : ls.chip_formula;
                if (ls.adjust == "qfq" || ls.adjust == "raw") _stockAdjust = ls.adjust;
                if (ls.range_idx.HasValue) _stockRangeIdx = ls.range_idx.Value;
                if (_stockRangeIdx == -1 && ls.range_bars.HasValue && ls.range_bars.Value > 0)
                    _stockRange = ls.range_bars.Value;
                else if (ls.range_idx.HasValue)
                    _stockRange = StockRangeBars();
                // 按钮高亮 / 图表口径与恢复值对齐
                StockSetPeriodActive();
                StockSetRangeActive();
                StockSetAdjustActive();
                if (_stockKline != null) _stockKline.SetAdjust(_stockAdjust);
                // 启动早期后端未就绪，指标 / 筹码清单首次拉取会失败，这里补一次
                StockRefreshIndicatorList();
                StockLoadChipFormulas();
                // 打开上次的个股：仅在还没打开任何票时，避免覆盖用户刚做的操作
                if (_stockCurrent == null && !string.IsNullOrEmpty(ls.code) && Regex.IsMatch(ls.code, "^[0-9]{6}$"))
                {
                    _quickSearch.Text = ls.code;
                    StockOpen();
                }
            }
            catch { }
        }

        /// <summary>
        /// 第 24 项：确保该票的行情数据新到应有交易日，陈旧则**同步补齐**后回调。
        /// 后端 `sync-code` 实测约 2 秒；失败不阻塞后续加载（宁可显示旧数据，
        /// 也不要整页打不开），只在状态行提示。
        /// </summary>
        private void StockEnsureFresh(string code, Action then)
        {
            System.Threading.Tasks.Task.Run(delegate
            {
                bool need = false;
                try
                {
                    string url = "http://127.0.0.1:8000/api/history/download/freshness?codes=" + code;
                    var fj = new JavaScriptSerializer()
                        .Deserialize<Dictionary<string, object>>(VRequest(url, null, 20000));
                    need = fj.ContainsKey("need_refresh") && fj["need_refresh"] is bool
                           && (bool)fj["need_refresh"];
                }
                catch (Exception) { need = false; }   // 检查失败就当不需要，不挡路
                if (!need) { Ui(then); return; }

                Invoke((Action)(() =>
                {
                    _stockHint.Text = "数据已过期，正在补齐 " + code + " 的最新行情（约 2~5 秒）…";
                    _stockHint.Tag = "muted";
                    _stockHint.ForeColor = _cSub;
                }));
                try
                {
                    VRequest("http://127.0.0.1:8000/api/history/download/sync-code",
                             "{\"code\":\"" + code + "\"}", 120000);
                }
                catch (Exception ex)
                {
                    string m = ex.Message;
                    Invoke((Action)(() =>
                    {
                        _stockHint.Text = "补齐数据失败（仍显示已有数据）：" + m;
                        _stockHint.Tag = "bad";
                        _stockHint.ForeColor = C.UpErr;
                    }));
                }
                Ui(then);
            });
        }

        /// <summary>第 24 项：个股页「确认数据新鲜之后」要加载的那一串。</summary>
        private void StockLoadRest(string code)
        {
            if (_stockCurrent != code) return;      // 期间又切了别的票就作废
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
            FetchStockName(code,
                delegate(string name, string display)
                {
                    if (_quickActiveCode != code) return;          // 竞态守卫：已换别的代码 / 输入框已清空
                    _stockName.Text = display;
                    StockAddHistory(code, name);                  // 补全历史记录中的名称
                },
                delegate(string err)
                {
                    if (_quickActiveCode != code) return;
                    _stockName.Text = err;
                });
        }

        // ---- 历史（K线 / 轨迹 / 消息）----

        private void StockFail(string msg)
        {
            _stockStatus.Text = "加载失败：" + msg;
            _stockStatus.Tag = "bad";
            _stockStatus.ForeColor = C.UpErr;
            _stockKline.SetData(new List<KBar>(), new List<KBar>(), new List<KMark>());
            // 右侧筹码窗口同步清空，避免还留着上一只股票的分布
            if (_stockChip != null) _stockChip.SetStatus("筹码未加载", false);
        }

        // ---- 指标：主图 / 副图均固定单指标，由右下角输入框键入指标名 / id 替换 → 后端计算 → 按日期对齐 → 叠加到 K 线 ----
        // 解析与替换的实际逻辑在 StockOpen() 内（复用同一输入框，无需独立方法）。

        /// <summary>输入 → 指标 id（主图或副图）。先精确（名称、id），再前缀，最后包含；
        /// 同层副图优先（macd / rsi 等长 id 不被主图短 id 截胡）。isMain 区分主图 / 副图。</summary>
        private string StockResolveAnyId(string typed, out bool isMain)
        {
            isMain = false;
            if (_stockMainOpts.Count == 0 && _stockSubOpts.Count == 0) return null;   // 清单未就绪：不接受非代码输入
            foreach (var o in _stockSubOpts) if (string.Equals(o.Name, typed, StringComparison.OrdinalIgnoreCase) || string.Equals(o.Id, typed, StringComparison.OrdinalIgnoreCase)) return o.Id;
            foreach (var o in _stockMainOpts) if (string.Equals(o.Name, typed, StringComparison.OrdinalIgnoreCase) || string.Equals(o.Id, typed, StringComparison.OrdinalIgnoreCase)) { isMain = true; return o.Id; }
            foreach (var o in _stockSubOpts)
                if (o.Name.StartsWith(typed, StringComparison.OrdinalIgnoreCase)) return o.Id;
            foreach (var o in _stockSubOpts)
                if (o.Id.StartsWith(typed, StringComparison.OrdinalIgnoreCase)) return o.Id;
            foreach (var o in _stockMainOpts)
                if (o.Name.StartsWith(typed, StringComparison.OrdinalIgnoreCase) || o.Id.StartsWith(typed, StringComparison.OrdinalIgnoreCase))
                { isMain = true; return o.Id; }
            foreach (var o in _stockSubOpts)
                if (o.Name.IndexOf(typed, StringComparison.OrdinalIgnoreCase) >= 0) return o.Id;
            foreach (var o in _stockMainOpts)
                if (o.Name.IndexOf(typed, StringComparison.OrdinalIgnoreCase) >= 0)
                { isMain = true; return o.Id; }
            return null;
        }

        /// <summary>输入 → 筹码公式 id（独立于指标通道）。匹配顺序同指标：先精确（索引名 / 名称 / 公式 id），
        /// 再前缀，最后按名称包含；命中返回**后端公式 id**（拼 &amp;formula= 用），未命中返回 null。</summary>
        private string StockResolveChipId(string typed)
        {
            if (_stockChipOpts.Count == 0) return null;
            foreach (var o in _stockChipOpts)
                if (string.Equals(o.Id, typed, StringComparison.OrdinalIgnoreCase)
                 || string.Equals(o.Name, typed, StringComparison.OrdinalIgnoreCase)
                 || string.Equals(o.Raw, typed, StringComparison.OrdinalIgnoreCase))
                    return o.Raw ?? o.Id;
            foreach (var o in _stockChipOpts)
                if (o.Id.StartsWith(typed, StringComparison.OrdinalIgnoreCase)
                 || (o.Raw != null && o.Raw.StartsWith(typed, StringComparison.OrdinalIgnoreCase)))
                    return o.Raw ?? o.Id;
            foreach (var o in _stockChipOpts)
                if (o.Name.IndexOf(typed, StringComparison.OrdinalIgnoreCase) >= 0)
                    return o.Raw ?? o.Id;
            return null;
        }

        /// <summary>筹码公式 id → 显示名（找不到返回 null）。</summary>
        private string StockChipNameOf(string rawId)
        {
            if (string.IsNullOrEmpty(rawId)) return null;
            foreach (var o in _stockChipOpts)
                if (o.Raw == rawId || o.Id == rawId) return o.Name;
            return null;
        }

        /// <summary>指标 id → 规范名称（主图 + 副图一起查，找不到返回 null）。</summary>

        // 指标联想下拉逻辑见 QuickSearch.cs（与右下角输入框共用 StockSug* 方法）。

        // 从后端自动枚举指标清单：panel=main → 主图候选；panel≠main/none → 副图候选（灌进输入框联想下拉）。

        /// <summary>后端指标清单 → 主图 / 副图候选（供右下角输入框解析与联想下拉，不再生成勾选框）。</summary>

        // ---- 筹码分布（K 线右侧窗口）----
        // 与指标体系的「按日期对齐的序列」不同，筹码分布是「按价位分布的直方图」，
        // 因此不走指标面板，单独向 /api/chip/dist 取数、单独渲染。
        // ---- 所属板块（基本信息页，通达信 HYBLOCK/GNBLOCK/DYBLOCK 风格）----
        // 数据来自 /api/stock/boards：新浪板块体系预计算索引（行业 / 概念 / 地域），
        // 每类内按成分股数量升序——成分越少越「专属」，第一个即最相关板块（加 ★）。
        // 用 Label 而非 FlowLayoutPanel：后者的 AutoSize 首选高度在表格里测量不稳，
        // 会把 Group 撑出一大块空白；Label + MaximumSize 限宽换行的高度计算是可靠的。

        /// <summary>索引正在后台重建时，隔几秒重查一次直到建好（重建约 1 分钟）。</summary>
        private void ScheduleBoardsRetry()
        {
            Debounce(ref _boardsTimer, 6000, delegate
            {
                if (_stockCurrent != null) StockLoadBoards(_stockCurrent);
            });
        }

        private void StockRebuildBoards(object sender, EventArgs e)
        {
            if (_stockCurrent != null) StockLoadBoards(_stockCurrent, true);
        }

        /// <summary>拼一行「标题： ★最相关 其余…」。板块很多时截断，超出部分显示省略。</summary>

        // ---- 基本信息（/api/stock/profile）：行业 / 市值 / 前十大股东 / 涨停与连板 ----

        // ---- 筹码模型：后端 /api/chip/dist/formulas 枚举 ----
        // 公式是「一个文件一个公式、文件名即 id」，新增公式无需改前端，这里自动同步成
        // 右下角输入框的候选（键入索引名即切换，原下拉框已移除）。

        /// <summary>滚轮缩放会连续改变可见根数，防抖后再重算筹码，避免每滚一格就发一次请求。</summary>

        /// <summary>
        /// 解析逐日快照 frames：dates[i] / pct[i] / stats[i] 一一对应，
        /// 光标左右移动时前端只切索引，不再回服务端。
        /// 后端没给 frames（老版本 / 异常）时，用最后一帧兜底成单帧，不至于开天窗。
        /// </summary>

        // 把后端返回的指标序列按 K 线日期对齐为等长数组（缺失填 NaN）

        // RPS 副图高亮阈值 M：本项目约定 M = 90（RPS ≥ 90 = 全市场前 10% 强势）。
        private const double RpsHiThreshold = 90.0;
        private static readonly Color RpsHiRed = C.Up;

        /// <summary>
        /// 按通达信 RPS 副图公式给序列配色：120 绿 / 250 白 / 50 黄 / 20 灰 / 10 浅青，
        /// 并开启「数值 ≥ M(90) 转红」高亮；其余指标沿用 <see cref="LineColor"/>。
        /// 对齐公式：RPS120:...,COLORGREEN; IF(RPS120&gt;=M,RPS120,DRAWNULL),COLORRED;
        /// </summary>

        // ---- 扫雷（通达信个股亮点，内联）----

        /// <summary>扫雷清单里的一个分类列：标题 + 条目（名称 / 有·无）。</summary>

        /// <summary>把 JSON 里的整数（可能是 int / decimal / string）安全转成 int。</summary>

        // ---- 估值（内联）----

        // ---- AI 分析（调 /api/stock/research，返回 AI 生成的 markdown）----

        // ---- AI 分析偏好：本地持久化（JSON，存于 ApplicationData\StockPoolLauncher）----

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

        /// <summary>把 AI 调研的 markdown 渲染进只读框；换肤时由 Skin 再调一次，用新配色重排。</summary>

        /// <summary>按 **加粗** 标记分段写入 RichTextBox（行内加粗）。</summary>

        /// <summary>跳到「估值计算」标签页并把代码填进去重算（完整过程在那一页）。</summary>

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
            StockSessionSave();            // 记住 K 线周期
            if (_stockCurrent != null)
            {
                StockLoadHistory(_stockCurrent, false);   // 会顺带重算指标（见 StockRenderHistory）
                StockLoadChip(_stockCurrent);
            }
            if (_stockKline != null) _stockKline.FocusForKeys();
        }

        /// <summary>F8 循环切换 K 线周期：日 → 周 → 月 → 日（K线、指标、筹码随周期一起重算）。</summary>
        private void StockCyclePeriod()
        {
            string next = _stockPeriod == "day" ? "week" : (_stockPeriod == "week" ? "month" : "day");
            StockSetPeriod(next);
        }

        // ---- 历史查看记录（左侧可折叠导航，持久化到 stockanaly-data）----

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
                btn.Click += delegate { _quickSearch.Text = h.Code; StockOpen(); _stockStrip.Select(0); };
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
            if (_stockStrip != null)
            {
                _stockStrip.Select(_stockStrip.Index);
                _stockStrip.Body.PerformLayout();
                _stockStrip.Body.Invalidate(true);
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
            private List<double[]> _grayFrames = null;                  // EMA 锁仓：逐日锁仓占比（与 _frames 同序；null=非锁仓模型）
            private List<double[]> _redFrames = null;                   // 红叠层：逐日强换手占比（与 _frames 同序；null=非红叠模型）
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
                                List<double[]> frames, List<ChipStats> frameStats, string note,
                                List<double[]> grayFrames = null, List<double[]> redFrames = null)
            {
                _bins = bins ?? new List<ChipBin>();
                _dates = dates ?? new List<string>();
                _frames = frames ?? new List<double[]>();
                _grayFrames = (grayFrames != null && grayFrames.Count == _frames.Count) ? grayFrames : null;
                _redFrames = (redFrames != null && redFrames.Count == _frames.Count) ? redFrames : null;
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
                if (!_dateIdx.TryGetValue(date, out idx))
                {
                    // 日期不在快照内（光标落到快照区间之外，如滚轮放大后、或筹码与 K 线
                    // 可见范围暂不一致）：二分夹到**最近的边界帧**，而不是保持旧帧不动——
                    // 否则早于快照起点的日期会一直停在同一个帧，看起来「某日之前筹码峰不变」。
                    // _dates 为升序（YYYY-MM-DD，字典序即时间序）。
                    int lo = 0, hi = _dates.Count - 1, ans = 0;
                    while (lo <= hi)
                    {
                        int mid = (lo + hi) >> 1;
                        if (string.CompareOrdinal(_dates[mid], date) <= 0) { ans = mid; lo = mid + 1; }
                        else hi = mid - 1;
                    }
                    idx = ans;
                }
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
                _grayFrames = null;
                _redFrames = null;
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
                    // 纵向范围收紧到「有筹码」的区间（当前帧 pct > 0），
                    // 避免股价从未到过的低价区（如 12.2~14.4）在底部留出大片空白。
                    // 灰/红都是蓝的子集（见后端 ema_lock），不再相加 —— 相加会把轴撑开。
                    if (_frames != null && _frames.Count > 0)
                    {
                        int fi = (_frame >= 0 && _frame < _frames.Count) ? _frame : _frames.Count - 1;
                        double[] fp = _frames[fi];
                        double clo = double.MaxValue, chi = double.MinValue;
                        for (int i = 0; i < _bins.Count && i < fp.Length; i++)
                        {
                            double v = fp[i];
                            if (v <= 1e-9) continue;              // 无筹码箱跳过
                            if (_bins[i].Lo < clo) clo = _bins[i].Lo;
                            if (_bins[i].Hi > chi) chi = _bins[i].Hi;
                        }
                        if (chi > clo)
                        {
                            double cpad = (chi - clo) * 0.03;
                            lo = clo - cpad; hi = chi + cpad;
                        }
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
                    // 蓝/红/灰模型（chip_brg）：有 red 帧则以「蓝底 + 灰折旧段 + 红外沿叠层」渲染
                    bool brgMode = (_redFrames != null && _grayFrames != null
                                    && _frame >= 0 && _frame < _redFrames.Count);
                    // EMA 锁仓模型分支：有 gray 帧则画「红底 + 灰叠层」，否则走原盈亏两色逻辑
                    bool emaMode = (!brgMode && _grayFrames != null && _frame >= 0 && _frame < _grayFrames.Count);
                    double[] gray = (brgMode || emaMode) ? _grayFrames[_frame] : null;
                    double[] red = brgMode ? _redFrames[_frame] : null;

                    // 横轴满宽基准 = 三层（蓝 pct / 灰 / 红）里的**最大占比**。
                    // 后端已保证三层同一分母、单箱 灰 ≤ 蓝、红 ≤ 蓝，用同一 maxPct 缩放即可：
                    // 谁都不会溢出画布，也不会像「取灰峰」那样把蓝裁成满宽（那会让灰/红看着比蓝长）。
                    double maxPct = 0;
                    for (int i = 0; i < pct.Length; i++) if (pct[i] > maxPct) maxPct = pct[i];
                    if (gray != null) for (int i = 0; i < gray.Length; i++) if (gray[i] > maxPct) maxPct = gray[i];
                    if (red != null) for (int i = 0; i < red.Length; i++) if (red[i] > maxPct) maxPct = red[i];
                    if (maxPct <= 0) maxPct = 1;

                    if (brgMode)
                    {
                        // 红/灰都从左侧（底部）起画，不溢出面板；maxPct 仍用 pct 峰值即可
                        Color blueC = C.LockedDark;                     // 蓝：base 原始筹码
                        Color grayC = Color.FromArgb(150, 156, 168);    // 灰：沉淀折旧
                        Color redC = C.Up;                              // 红：强换手叠层（外沿）
                        var idxs = new List<int>();
                        var baseWs = new List<int>();
                        var grayWs = new List<int>();
                        var redWs = new List<int>();
                        var ys = new List<int>();
                        for (int i = 0; i < pct.Length && i < _bins.Count; i++)
                        {
                            ChipBin b = _bins[i];
                            if (b.Hi < lo || b.Lo > hi) continue;
                            int w = (int)Math.Round(pct[i] / maxPct * plotW);
                            if (w < 0) w = 0; if (w > plotW) w = plotW;
                            int yc = (int)yOf(b.Price);
                            if (yc < top) yc = top; if (yc > bottom) yc = bottom;
                            idxs.Add(i); baseWs.Add(w); ys.Add(yc);
                            int gw = (int)Math.Round((i < gray.Length ? gray[i] : 0) / maxPct * plotW);
                            if (gw < 0) gw = 0; if (gw > plotW) gw = plotW;   // 不限制 ≤ 蓝宽，灰按真实占比铺满
                            grayWs.Add(gw);
                            int rw = (int)Math.Round((i < red.Length ? red[i] : 0) / maxPct * plotW);
                            if (rw < 0) rw = 0; if (rw > plotW) rw = plotW;
                            redWs.Add(rw);
                        }
                        if (idxs.Count > 0)
                        {
                            // 蓝底（base 全量堆积）
                            var poly = new List<Point>();
                            poly.Add(new Point(LeftPad, ys[0]));
                            for (int t = 0; t < idxs.Count; t++) poly.Add(new Point(LeftPad + baseWs[t], ys[t]));
                            poly.Add(new Point(LeftPad, ys[idxs.Count - 1]));
                            using (var br = new SolidBrush(blueC)) g.FillPolygon(br, poly.ToArray());

                            // 灰折旧段：base 内左段 [0, grayW]，表示这部分蓝已沉淀
                            var gp = new List<Point>();
                            gp.Add(new Point(LeftPad, ys[0]));
                            for (int t = 0; t < idxs.Count; t++) gp.Add(new Point(LeftPad + grayWs[t], ys[t]));
                            gp.Add(new Point(LeftPad, ys[idxs.Count - 1]));
                            using (var br = new SolidBrush(grayC)) g.FillPolygon(br, gp.ToArray());

                            // 红叠层：从左侧（底部）起覆盖蓝底左段 [0, redW]，与灰段同侧起点；
                            // 强换手档位显示红、沉淀档位显示灰、其余为蓝
                            var rp = new List<Point>();
                            rp.Add(new Point(LeftPad, ys[0]));
                            for (int t = 0; t < idxs.Count; t++) rp.Add(new Point(LeftPad + redWs[t], ys[t]));
                            rp.Add(new Point(LeftPad, ys[idxs.Count - 1]));
                            using (var br = new SolidBrush(redC)) g.FillPolygon(br, rp.ToArray());

                            // 蓝色轮廓细线：灰铺满后蓝被覆盖，用蓝色描边保证「蓝至少一条细线」始终可见；
                            // 只在**有筹码**（baseWs>0）的档位描边，无筹码档跳过，避免空白区画出竖线。
                            using (var pen = new Pen(blueC, 1.5f))
                            {
                                var bp = new List<Point>();
                                for (int t = 0; t < idxs.Count; t++)
                                {
                                    // 以「真实占比 > 0」为据：极薄但有筹码的价位（之前因像素宽舍入=0 被误杀）
                                    // 仍连入轮廓，避免峰形中间断开；纯空白档（真无筹码）才跳过。
                                    if (pct[idxs[t]] <= 0) continue;
                                    bp.Add(new Point(LeftPad + baseWs[t], ys[t]));
                                }
                                if (bp.Count >= 2) g.DrawLines(pen, bp.ToArray());
                            }
                        }
                    }
                    else if (emaMode)
                    {
                        // 先画红底（= 全量分布：强换手+锁仓），再在红底上叠灰（锁仓）。
                        // gray 与 pct 同一归一尺度、单箱 ≤ pct，故灰宽永不超过红宽。
                        Color active = C.Up;                          // 红：活跃/强换手
                        Color lockedC = Color.FromArgb(150, 156, 168); // 灰：锁仓（中性灰）
                        var gIdx = new List<int>(); var gWs = new List<int>(); var gYs = new List<int>();
                        for (int i = 0; i < pct.Length && i < _bins.Count; i++)
                        {
                            ChipBin b = _bins[i];
                            if (b.Hi < lo || b.Lo > hi) continue;
                            int w = (int)Math.Round(pct[i] / maxPct * plotW);
                            if (w < 0) w = 0; if (w > plotW) w = plotW;
                            int yc = (int)yOf(b.Price);
                            if (yc < top) yc = top; if (yc > bottom) yc = bottom;
                            gIdx.Add(i); gWs.Add(w); gYs.Add(yc);
                        }
                        if (gIdx.Count > 0)
                        {
                            var poly = new List<Point>();
                            poly.Add(new Point(LeftPad, gYs[0]));
                            for (int t = 0; t < gIdx.Count; t++) poly.Add(new Point(LeftPad + gWs[t], gYs[t]));
                            poly.Add(new Point(LeftPad, gYs[gIdx.Count - 1]));
                            using (var br = new SolidBrush(active)) g.FillPolygon(br, poly.ToArray());
                            var gp = new List<Point>();
                            gp.Add(new Point(LeftPad, gYs[0]));
                            for (int t = 0; t < gIdx.Count; t++)
                            {
                                int w = (int)Math.Round(gray[gIdx[t]] / maxPct * plotW);
                                if (w < 0) w = 0; if (w > plotW) w = plotW;
                                gp.Add(new Point(LeftPad + w, gYs[t]));
                            }
                            gp.Add(new Point(LeftPad, gYs[gIdx.Count - 1]));
                            using (var br = new SolidBrush(lockedC)) g.FillPolygon(br, gp.ToArray());
                        }
                    }
                    else
                    {
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
                    // 峰值占比的具体百分比不再在此标注（改由 PEAK 指标统一呈现），避免与指标数字打架。
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
            private List<LowerPanel> _indicators = new List<LowerPanel>();   // 指标副图（lower）
            private List<ChartSeries> _mainIndicators = new List<ChartSeries>();   // 主图叠加指标（main 面板）
            private int _range = 250;                          // 可见 K 线根数，0 = 全部
            private int _offset = 0;                           // 平移：从最新端向左偏移的根数
            private bool _dragging = false;
            private int _dragStartX = 0;
            private int _dragStartOffset = 0;
            private readonly ToolTip _tip = new ToolTip();
            private int _hoverIndex = -1;
            private int _wstart;                          // 当前可见窗口的全局起始索引（图例取光标数值用）
            public string Period = "day";                      // K 线周期（图例显示 日K / 周K / 月K）
            public Action<int> OnRangeChanged;                 // 缩放改变范围时通知外部刷新高亮
            /// <summary>可见窗口**平移**（_offset 变、_range 不变，如键盘光标到边缘 auto-pan / 拖动）时通知外部，
            /// 让右侧筹码按新的可见窗口重算——否则平移到更早日期后，光标会落到筹码快照之外对不上。</summary>
            public Action OnViewChanged;
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
                // ⭐ 换股 / 刷新数据前关掉悬停浮窗：数据整批换掉后旧浮窗毫无意义，
                // 且 _tip.Show 没给时长参数，不主动 Hide 会一直挂着（深色下即白块）。
                _tip.Hide(this);
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

            /// <summary>设置主图叠加指标（main 面板）。传空列表即清除。
            /// MA 等主图指标都走这一条通道（与副图同一套规则），不再单独处理。</summary>
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
                    if (OnViewChanged != null) OnViewChanged();   // 拖动平移：让筹码按新窗口重算（前端防抖）
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
                // ⭐ 悬停浮窗随缩放关闭（bug 修复）：_tip.Show 没给时长参数，会一直挂着；
                // 缩放会改变每根 K 线的屏幕位置，不关的话浮窗就冻结在旧位置、显示旧数据，
                // 在深色主题下就是一块醒目的白色小方块（用户报的「白块」症状之一）。
                _tip.Hide(this);
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
                int oldOffset = _offset;
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
                if (_offset != oldOffset && OnViewChanged != null) OnViewChanged();   // 平移了：让筹码按新窗口重算
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

            /// <summary>当前可见窗口起点到最新一根之间的根数（= total - start，含被平移藏到右侧的最新段）。
            /// 右侧筹码窗口用它作 days，frames 才能覆盖 K 线当前可见窗口（含键盘 / 拖动平移到更早的部分），
            /// 避免光标日期早于筹码快照起点时对不上。</summary>
            public int ViewBarsToLatest()
            {
                int total = _bars.Count;
                if (total <= 0) return 0;
                int s, c;
                VisibleWindow(out s, out c);
                return total - s;
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

            // ---- 主图标记图标（marker）：前端预设常用图标，后端只传【图标序号】 ----
            // 序号约定（前后端共享，务必同步）：
            //   0 = 无   1 = 上箭头(红)   2 = 下箭头(绿)   3 = 钱袋(金)   4 = 菱形(黄)
            //   5 = 叉(警示红)            6 = 圆点(灰)
            private static readonly Color[] _iconColors = new Color[]
            {
                Color.Transparent,                      // 0 无
                Color.FromArgb(255, 152, 0),            // 1 上箭头（橙，区别于涨跌红绿）
                Color.FromArgb(38, 198, 218),           // 2 下箭头（青，区别于涨跌红绿）
                Color.FromArgb(212, 175, 55),           // 3 钱袋（金）
                C.RpsYellow,                            // 4 菱形（黄）
                C.UpErr,                                // 5 叉（警示红）
                C.FlatWarm,                             // 6 圆点（灰）
            };

            private static void DrawIcon(Graphics g, int iconId, int x, int y, int size)
            {
                if (iconId <= 0 || iconId >= _iconColors.Length) return;
                Color c = _iconColors[iconId];
                switch (iconId)
                {
                    case 1: // 上箭头
                        using (var br = new SolidBrush(c))
                            g.FillPolygon(br, new Point[] {
                                new Point(x, y - size * 2), new Point(x - size, y), new Point(x + size, y) });
                        break;
                    case 2: // 下箭头
                        using (var br = new SolidBrush(c))
                            g.FillPolygon(br, new Point[] {
                                new Point(x, y + size * 2), new Point(x - size, y), new Point(x + size, y) });
                        break;
                    case 3: // 钱袋
                        DrawMoneyBag(g, x, y, size, c);
                        break;
                    case 4: // 菱形
                        using (var br = new SolidBrush(c))
                            g.FillPolygon(br, new Point[] {
                                new Point(x, y - size * 2), new Point(x + size, y),
                                new Point(x, y + size * 2), new Point(x - size, y) });
                        break;
                    case 5: // 叉
                        using (var pen = new Pen(c, 2f))
                        {
                            g.DrawLine(pen, x - size, y - size, x + size, y + size);
                            g.DrawLine(pen, x + size, y - size, x - size, y + size);
                        }
                        break;
                    case 6: // 圆点
                        using (var br = new SolidBrush(c))
                            g.FillEllipse(br, x - size, y - size, size * 2, size * 2);
                        break;
                }
            }

            private static void DrawMoneyBag(Graphics g, int x, int y, int size, Color c)
            {
                using (var br = new SolidBrush(c))
                using (var pen = new Pen(c, 2f))
                {
                    int w = size * 2, h = size * 2;
                    g.FillEllipse(br, x - w / 2, y - h / 2 + 2, w, h - 2);          // 袋身
                    g.DrawLine(pen, x - size / 2, y - h / 2 + 2, x + size / 2, y - h / 2 + 2);  // 束口
                }
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

                // 价格区间（先按 K 线）
                double pmin = double.MaxValue, pmax = double.MinValue;
                foreach (KBar b in vis) { if (b.L < pmin) pmin = b.L; if (b.H > pmax) pmax = b.H; }
                int wstart, wcount;
                VisibleWindow(out wstart, out wcount);
                _wstart = wstart;
                // 主图叠加指标（MA / CYC 等）一并纳入价格区间，避免线画到图外（与副图同一规则）
                // marker 是信号值（+1/-1/0），不是价格，必须排除，否则价格轴会被撑到 -1
                foreach (ChartSeries s in _mainIndicators)
                {
                    if (s.Data == null || s.Kind == "marker") continue;
                    for (int i = 0; i < n; i++)
                    {
                        int gi = wstart + i;
                        if (gi >= s.Data.Count) continue;
                        double v = s.Data[gi];
                        if (double.IsNaN(v)) continue;
                        if (v < pmin) pmin = v; if (v > pmax) pmax = v;
                    }
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

                // 主图叠加指标（main 面板）：在价格轴范围内绘制线 / 柱 / 箭头
                foreach (ChartSeries s in _mainIndicators)
                {
                    if (s.Data == null) continue;
                    if (s.Kind == "marker")
                    {
                        // 后端传图标序号（0=无），前端按 _iconColors 预设渲染。
                        // 锚点约定：上箭头(1)的 y 是【底边】，下箭头(2)的 y 是【顶边】；
                        // 箭头整体落在 K 线外侧并留 4px 间隙，避免与影线重合。
                        int size = 4;
                        for (int i = 0; i < n; i++)
                        {
                            int gi = wstart + i;
                            if (gi >= s.Data.Count) continue;
                            double v = s.Data[gi];
                            if (double.IsNaN(v) || v == 0) continue;
                            int iconId = (int)Math.Round(v);
                            int x = (int)xOf(i);
                            KBar kb = vis[i];
                            int y;
                            if (iconId == 2)
                            {
                                // 下箭头：顶边在最高价上方 4 + 2*size，整体在高点上方
                                y = (int)yOf(kb.H) - 4 - size * 2;
                                if (y < plotTop) y = plotTop;
                            }
                            else
                            {
                                // 上箭头：底边在最低价下方 4 + 2*size，整体在低点下方
                                y = (int)yOf(kb.L) + 4 + size * 2;
                                if (y > plotBottom) y = plotBottom;
                            }
                            DrawIcon(g, iconId, x, y, size);
                        }
                        continue;
                    }
                    else if (s.Kind == "bar")
                    {
                        using (var br = new SolidBrush(s.Color))
                        {
                            for (int i = 0; i < n; i++)
                            {
                                int gi = wstart + i;
                                if (gi >= s.Data.Count) continue;
                                double v = s.Data[gi];
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
                            for (int i = 0; i < n; i++)
                            {
                                int gi = wstart + i;
                                if (gi >= s.Data.Count) { penUp = false; continue; }
                                double v = s.Data[gi];
                                if (double.IsNaN(v)) { penUp = false; continue; }
                                int x = (int)xOf(i);
                                int y = (int)yOf(v);
                                if (penUp)
                                {
                                    double pv = s.Data[wstart + i - 1];
                                    g.DrawLine(pen, (int)xOf(i - 1), (int)yOf(pv), x, y);
                                }
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
                    // 顶部预留一条图例带：标题 + 各序列「名称 当前值」画在这里，
                    // 曲线 / 柱子只在图例带下方绘制 → 字与线不再重叠。
                    const int LegendH = 14;
                    Rectangle plot = new Rectangle(rect.X, rect.Y + LegendH,
                        rect.Width, Math.Max(10, rect.Height - LegendH));
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
                    Func<double, double> yInd = val => plot.Bottom - (val - ipmin) / ispan * plot.Height;
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
                        g.DrawLine(pen, LeftPad, plot.Bottom, LeftPad + plotW, plot.Bottom);
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
                    // 悬停：在副图上标注光标处各序列数值（跟随光标，对应竖线交点画一个圆点）
                    if (_hoverIndex >= 0 && _hoverIndex < n)
                    {
                        int gi = wstart + _hoverIndex;
                        int hx = (int)xOf(_hoverIndex);
                        foreach (ChartSeries s in panel.Series)
                        {
                            if (s.Data == null || gi >= s.Data.Count) continue;
                            double v = s.Data[gi];
                            if (double.IsNaN(v)) continue;
                            int yv = (int)yInd(v);
                            using (var dot = new SolidBrush(s.Color))
                                g.FillEllipse(dot, hx - 3, yv - 3, 6, 6);
                        }
                    }
                    int lx = LeftPad + 4; int ly = rect.Top + 2;
                    g.SetClip(rect);                 // 图例过长时裁剪在本面板内，不溢出到右侧筹码窗口
                    using (var b = new SolidBrush(text))
                    {
                        g.DrawString(panel.Title, Font, b, lx, ly);
                        lx += (int)(g.MeasureString(panel.Title, Font).Width) + 16;
                        // 图例实时显示光标所在交易日的指标值（移出图表则只显示名称）
                        int gi = _hoverIndex >= 0 ? wstart + _hoverIndex : -1;
                        foreach (ChartSeries s in panel.Series)
                        {
                            using (var pen = new Pen(s.Color, Math.Max(2f, s.Width))) g.DrawLine(pen, lx, ly + 7, lx + 14, ly + 7);
                            string label = s.Name;
                            if (gi >= 0 && s.Data != null && gi < s.Data.Count && !double.IsNaN(s.Data[gi]))
                                label += " " + s.Data[gi].ToString("F2");
                            g.DrawString(label, Font, b, lx + 18, ly);
                            lx += (int)(g.MeasureString(label, Font).Width) + 18;
                        }
                    }
                    g.ResetClip();
                }
                // 主图标记（入池/出池/事件的菱形、三角标注）已按需求移除

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

                DrawLegend(g, text, _hoverIndex);
                // 右下角的「滚轮缩放 · 拖动平移 · 前复权」提示已按需求移除；
                // 周期 / 复权状态看工具栏按钮的高亮即可（图例里也标了 日K / 周K / 月K）。
            }

            /// <summary>顶部图例：周期 + 主图叠加指标（MA / CYC 等，统一走 _mainIndicators）。
            /// hoverIdx &gt;= 0 时，在名称后实时显示光标所在交易日的数值（跟随光标）。</summary>
            private void DrawLegend(Graphics g, Color text, int hoverIdx)
            {
                int x = LeftPad + 4;
                int y = 4;
                using (var b = new SolidBrush(text))
                {
                    // 周期写进图例：周线下这里就是「周K」，MA5 也是 5 周均线，别让人误读
                    string label = (Period == "week") ? "周K" : (Period == "month") ? "月K" : "日K";
                    g.DrawString(label, Font, b, x, y); x += 34;
                    // 主图叠加指标（MA / CYC 等）：名称 + 光标处数值 —— 全部走同一套规则
                    foreach (ChartSeries s in _mainIndicators)
                    {
                        if (s.Kind == "marker")
                        {
                            // 图例：画一个代表性图标（hover 时取当日图标序号，否则用 1=上箭头）
                            int iconId = 1;
                            if (hoverIdx >= 0 && s.Data != null && _wstart + hoverIdx < s.Data.Count)
                            {
                                double hv = s.Data[_wstart + hoverIdx];
                                if (!double.IsNaN(hv) && hv != 0) iconId = (int)Math.Round(hv);
                            }
                            DrawIcon(g, iconId, x + 7, y + 5, 5);
                            g.DrawString(s.Name, Font, b, x + 18, y);
                            x += (int)(g.MeasureString(s.Name, Font).Width) + 14;
                            continue;
                        }
                        double v = double.NaN;
                        if (hoverIdx >= 0 && s.Data != null && _wstart + hoverIdx < s.Data.Count)
                            v = s.Data[_wstart + hoverIdx];
                        using (var pen = new Pen(s.Color, 2f)) g.DrawLine(pen, x, y + 7, x + 14, y + 7);
                        string lbl = s.Name;
                        if (!double.IsNaN(v)) lbl += " " + v.ToString("F2");
                        g.DrawString(lbl, Font, b, x + 18, y);
                        x += (int)(g.MeasureString(lbl, Font).Width) + 14;
                    }
                }
            }
        }
    }
}

