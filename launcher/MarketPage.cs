using System;
using System.Collections.Generic;
using System.Drawing;
using System.Windows.Forms;
using System.Web.Script.Serialization;

namespace StockPool
{
    /// <summary>
    /// 盘面及板块分析（原生内嵌标签页）：MainForm 的拆分文件，替代 frontend/market-sector.html。
    /// 数据来自本机后端 /api/market/{global,capital,sectors,limit-up,big-loss}。
    /// 呈现上尽量贴近原网页：分组卡片 + KPI 卡片 + 涨跌分段条 + 自绘条形图，
    /// 只有需要列对齐的明细（涨停 / 炸板 / 跌停）才用表格。
    /// </summary>
    internal sealed partial class MainForm
    {
        // ---- 盘面及板块分析页 ----
        private int _mktTabIndex = -1;
        private DateTimePicker _mktDate;
        private Label _mktStatus, _mktGlobalStatus, _mktCapStatus, _mktSectorStatus, _mktLuStatus, _mktBlStatus;
        private int _mktRound;                                       // 本轮加载编号：上一轮的慢回调不再干扰本轮状态
        private int _mktPending;                                     // 本轮尚未返回的 job 数
        private int _mktFailed;                                      // 本轮失败的 job 数

        private FlowLayoutPanel _mktGlobalCards;                     // ① 外围：分组卡片
        private FlowLayoutPanel _mktCapKpi;                          // ② KPI 卡片
        private MktRatioBar _mktCapRatio;                            // ② 涨跌家数分段条
        private TableLayoutPanel _mktCapRows;                        // ② 涨跌 / 涨跌停列表行
        private MktBars _mktIndBars, _mktConBars, _mktSwTopBars, _mktSwBotBars;   // ③ 板块β 条形图
        private FlowLayoutPanel _mktLuKpi;                           // ④ KPI 卡片
        private TableLayoutPanel _mktLuRows;                         // ④ 连板结构列表行
        private TableLayoutPanel _mktLuPromoRows;                    // ④ 晋级率列表行
        private StockGrid _mktLuStocks;                              // ④ 涨停明细（表格）
        private List<Dictionary<string, object>> _mktLuAll;          // 涨停明细原始数据（供板块筛选复用）
        private Label _mktLuCaption;                                 // 涨停明细标题（含计数）
        private Button _mktLuFold;                                   // 折叠 / 展开
        private ComboBox _mktLuFilter;                               // 板块筛选
        private ComboBox _mktSectorWindow;                            // ③ 板块β 时间窗口：今日 / 5日 / 10日
        private List<string> _mktLuInds;                             // 下拉里的板块名（与 Items 一一对应，index 0 之后）
        private List<Dictionary<string, object>> _mktBlAll, _mktDtAll;  // 炸板 / 跌停原始数据
        private List<string> _mktBlInds, _mktDtInds;
        private ComboBox _mktBlFilter, _mktDtFilter;
        private Label _mktBlCaption, _mktDtCaption;
        private Button _mktBlFold, _mktDtFold;
        private StockGrid _mktBlasted, _mktLimitDown;                // ⑤ 炸板 / 跌停（表格）

        // ⑥ AI 分析（调 /api/market/ai-analysis，复用同一个 LLM_API_KEY）
        private Label _mktAiStatus;
        private RichTextBox _mktAiBox;
        private ComboBox _mktAiDepth;
        private string _mktAiMarkdown = "";
        private int _mktAiSubIndex = -1;      // 「AI 分析」二级页下标
        private bool _mktAiFired = false;     // 是否已自动生成过一次

        // 二级导航：① / ② / ③ 各自一页，④ 连板 + ⑤ 大面股 合为一页
        private FlowLayoutPanel _mktSubBar;
        private Panel _mktSubBody;
        private readonly List<Button> _mktSubBtns = new List<Button>();
        private readonly List<Panel> _mktSubPages = new List<Panel>();
        private int _mktSubIndex;

        /// <summary>
        /// 盘面及板块：二级导航把五块分成四页 —— ① 外围环境 / ② 大盘资金 / ③ 板块β 各自独立，
        /// ④ 连板梯队 与 ⑤ 大面股 合在一页。顶部选交易日 + 刷新，手动触发加载。
        /// </summary>
        private Panel BuildMarketPage()
        {
            var p = NewPage("盘面及板块");
            _mktTabIndex = _tabPages.Count - 1;
            p.AutoScroll = false;   // 内容在二级页里滚动，整页不滚

            var root = new TableLayoutPanel();
            root.Dock = DockStyle.Fill;
            root.Margin = new Padding(0);
            root.Padding = new Padding(16, 8, 16, 12);
            root.ColumnCount = 1;
            root.RowCount = 3;
            root.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100f));
            root.RowStyles.Add(new RowStyle(SizeType.AutoSize));      // 标题 + 操作区
            root.RowStyles.Add(new RowStyle(SizeType.AutoSize));      // 二级标签栏
            root.RowStyles.Add(new RowStyle(SizeType.Percent, 100f)); // 二级页内容（自己滚动）
            p.Controls.Add(root);

            // ---- 顶部：只有操作区（标题与导航标签重复，已去掉）----
            var head = Stack();

            TableLayoutPanel b0;
            var g0 = Group("操作", out b0);
            _mktDate = new DateTimePicker();
            _mktDate.Width = 130;
            _mktDate.Format = DateTimePickerFormat.Custom;
            _mktDate.CustomFormat = "yyyy-MM-dd";
            _mktDate.ShowCheckBox = true;
            _mktDate.Checked = false;          // 不勾选 = 实时；勾选 = 按所选交易日取数
            _mktStatus = Mute(Lbl("进入本页自动加载最新数据"));
            AddRow(b0, Row(Lbl("交易日"), _mktDate,
                MiniBtn("刷新", delegate { MktLoadAll(); }, 80),
                MiniBtn("实时", delegate { _mktDate.Checked = false; MktLoadAll(); }, 80),
                _mktStatus));
            AddRow(head, g0);
            g0.Margin = new Padding(0, 0, 0, 4);   // 操作区与二级标签栏之间收紧
            root.Controls.Add(head, 0, 0);

            // ---- 二级标签栏 ----
            _mktSubBar = new FlowLayoutPanel();
            _mktSubBar.Dock = DockStyle.Top;   // Fill 在 AutoSize 行里高度算不准，会留下大片空白
            _mktSubBar.Height = 30;
            _mktSubBar.FlowDirection = FlowDirection.LeftToRight;
            _mktSubBar.WrapContents = false;
            _mktSubBar.Margin = new Padding(0, 0, 0, 2);
            _mktSubBar.Padding = new Padding(0);
            root.Controls.Add(_mktSubBar, 0, 1);

            _mktSubBody = new Panel();
            _mktSubBody.Dock = DockStyle.Fill;
            _mktSubBody.Margin = new Padding(0);
            root.Controls.Add(_mktSubBody, 0, 2);

            // ---- 四个二级页 ----
            MktAddSubPage("外围环境", MktBuildGlobal);
            MktAddSubPage("大盘资金", MktBuildCapital);
            MktAddSubPage("板块β", MktBuildSectors);
            MktAddSubPage("连板 · 大面股", MktBuildLadder);
            _mktAiSubIndex = _mktSubPages.Count;
            MktAddSubPage("AI 分析", MktBuildMktAi);

            MktSubSelect(0);
            return p;
        }

        /// <summary>建一个二级页：按钮进标签栏，内容面板进内容区（内容用 Stack 纵向堆叠）。</summary>
        private void MktAddSubPage(string title, Action<TableLayoutPanel> build)
        {
            int idx = _mktSubPages.Count;

            var b = new Button();
            b.Text = title;
            b.Tag = "mkt-subtab";
            b.AutoSize = false;
            b.Height = 28;
            b.Width = Math.Max(96, TextRenderer.MeasureText(title, Font).Width + 24);
            b.FlatStyle = FlatStyle.Flat;
            b.FlatAppearance.BorderSize = 0;
            b.Font = new Font("Microsoft YaHei UI", 9f);
            b.TabStop = false;
            b.Margin = new Padding(0, 0, 2, 0);
            b.Click += delegate { MktSubSelect(idx); };
            _mktSubBtns.Add(b);
            _mktSubBar.Controls.Add(b);

            var page = new Panel();
            page.Dock = DockStyle.Fill;
            page.Visible = false;
            page.AutoScroll = true;
            page.Margin = new Padding(0);
            page.Tag = "tabpage";
            var stack = Stack();
            build(stack);
            page.Controls.Add(stack);
            // 不在这里挂到 _mktSubBody：由 MktSubSelect 只挂载当前页，
            // 避免多个 Dock=Fill 面板叠放（叠放时切 Visible 会布局错乱）。
            _mktSubPages.Add(page);
        }

        private void MktSubSelect(int index)
        {
            if (index < 0 || index >= _mktSubPages.Count) return;
            _mktSubIndex = index;

            // 容器里只挂当前这一页：先摘掉旧的，再挂上新的。
            // 之前是四页都 Dock=Fill 叠在容器里靠 Visible 切换，会出现
            // 「切过去没变、再切一次才对」的布局错乱。
            _mktSubBody.Controls.Clear();
            var page = _mktSubPages[index];
            page.Visible = true;
            page.Dock = DockStyle.Fill;
            _mktSubBody.Controls.Add(page);
            // 二级页只在被选中时才挂到控件树上，而全局换肤 Skin(this) 只递归挂载中的控件，
            // 所以这些页在首次显示前一直是系统默认配色（表格白底、列头浅灰），
            // 必须切一次主题才会被补上。这里在挂载的同时按当前主题上色。
            Skin(page);
            page.Invalidate(true);

            SkinMktSubTabs();

            // 首次切到「AI 分析」页时才生成：此时应用早已启动、后端已就绪，
            // 避免在 BuildMarketPage（应用启动瞬间）抢跑导致「连不上后端」。
            if (index == _mktAiSubIndex && !_mktAiFired)
            {
                _mktAiFired = true;
                MktLoadMktAi(false);
            }
        }

        /// <summary>二级标签配色（跟随主题）：选中用卡片色，未选中用窗口底色。</summary>
        private void SkinMktSubTabs()
        {
            if (_mktSubBtns == null) return;
            for (int i = 0; i < _mktSubBtns.Count; i++)
            {
                bool sel = (i == _mktSubIndex);
                _mktSubBtns[i].BackColor = sel ? _cPanel : _cBg;
                _mktSubBtns[i].ForeColor = sel ? _cText : _cSub;
                _mktSubBtns[i].Invalidate();
            }
        }

        /// <summary>进入本页时自动拉最新数据：清掉日期勾选（走实时 / 最近交易日口径）再加载。</summary>
        private void MktOnEnter()
        {
            _mktDate.Checked = false;
            MktLoadAll();

            // 从别的顶级标签切进来时，重新挂载当前二级页并刷新，避免首屏不刷新
            if (_mktSubBody != null)
            {
                MktSubSelect(_mktSubIndex);
                _mktSubBody.PerformLayout();
                _mktSubBody.Invalidate(true);
            }
        }

        // ---------------- 二级页布局 ----------------

        /// <summary>① 外围环境：一屏分组卡片（每组一张），组内为「名称 · 现值 · 涨跌幅」行。</summary>
        private void MktBuildGlobal(TableLayoutPanel stack)
        {
            TableLayoutPanel b1;
            var g1 = Group("", out b1);   // 标题与二级标签重复，留空
            _mktGlobalStatus = Mute(Lbl("—"));
            AddRow(b1, Row(_mktGlobalStatus));
            _mktGlobalCards = new FlowLayoutPanel();
            _mktGlobalCards.FlowDirection = FlowDirection.LeftToRight;
            _mktGlobalCards.WrapContents = true;
            _mktGlobalCards.AutoSize = true;
            _mktGlobalCards.AutoSizeMode = AutoSizeMode.GrowAndShrink;
            _mktGlobalCards.MaximumSize = new Size(760, 0);
            _mktGlobalCards.Margin = new Padding(0);
            AddRow(b1, _mktGlobalCards);
            AddRow(stack, g1);
        }

        /// <summary>② 大盘资金：KPI 卡片 + 涨跌家数分段条 + 涨跌 / 涨跌停列表行。</summary>
        private void MktBuildCapital(TableLayoutPanel stack)
        {
            TableLayoutPanel b2;
            var g2 = Group("", out b2);   // 标题与二级标签重复，留空
            _mktCapStatus = Mute(Lbl("—"));
            AddRow(b2, Row(_mktCapStatus));
            _mktCapKpi = VKpiRow();
            AddRow(b2, _mktCapKpi);

            AddRow(b2, Row(Mute(Lbl("涨跌家数分布"))));
            _mktCapRatio = new MktRatioBar();
            _mktCapRatio.Dock = DockStyle.Top;
            _mktCapRatio.Height = 28;
            AddRow(b2, _mktCapRatio);

            _mktCapRows = new TableLayoutPanel();
            _mktCapRows.ColumnCount = 1;
            _mktCapRows.AutoSize = true;
            _mktCapRows.AutoSizeMode = AutoSizeMode.GrowAndShrink;
            _mktCapRows.Dock = DockStyle.Top;
            _mktCapRows.Margin = new Padding(0);
            AddRow(b2, _mktCapRows);
            AddRow(stack, g2);
        }

        /// <summary>③ 板块β：行业 / 概念资金流、申万领涨 / 领跌 都用条形图。</summary>
        private void MktBuildSectors(TableLayoutPanel stack)
        {
            TableLayoutPanel b3;
            var g3 = Group("", out b3);   // 标题与二级标签重复，留空
            _mktSectorStatus = Mute(Lbl("—"));
            _mktSectorWindow = new ComboBox();
            _mktSectorWindow.DropDownStyle = ComboBoxStyle.DropDownList;
            _mktSectorWindow.Items.AddRange(new string[] { "今日", "5日", "10日" });
            _mktSectorWindow.SelectedIndex = 0;
            _mktSectorWindow.Width = 70;
            AddRow(b3, Row(_mktSectorStatus, _mktSectorWindow));

            AddRow(b3, Row(Mute(Lbl("行业板块资金净额 Top10（单位：亿，红=净流入 / 绿=净流出）"))));
            _mktIndBars = new MktBars { Dock = DockStyle.Top };
            AddRow(b3, _mktIndBars);

            AddRow(b3, Row(Mute(Lbl("概念板块资金净额 Top10（单位：亿）"))));
            _mktConBars = new MktBars { Dock = DockStyle.Top };
            AddRow(b3, _mktConBars);

            AddRow(b3, Row(Mute(Lbl("申万一级行业 · 领涨 Top10"))));
            _mktSwTopBars = new MktBars { Dock = DockStyle.Top };
            AddRow(b3, _mktSwTopBars);

            AddRow(b3, Row(Mute(Lbl("申万一级行业 · 领跌 Top10"))));
            _mktSwBotBars = new MktBars { Dock = DockStyle.Top };
            AddRow(b3, _mktSwBotBars);
            AddRow(stack, g3);
        }

        /// <summary>④ 连板梯队 + ⑤ 大面股：KPI + 连板结构条形图 + 晋级率行 + 涨停明细表；最后是炸板 / 跌停表。</summary>
        private void MktBuildLadder(TableLayoutPanel stack)
        {
            TableLayoutPanel b4;
            var g4 = Group("", out b4);   // 标题与二级标签重复，留空
            _mktLuStatus = Mute(Lbl("—"));
            AddRow(b4, Row(_mktLuStatus));
            _mktLuKpi = VKpiRow();
            AddRow(b4, _mktLuKpi);

            AddRow(b4, Row(Mute(Lbl("连板结构（各连板高度家数）"))));
            _mktLuRows = new TableLayoutPanel();
            _mktLuRows.ColumnCount = 1;
            _mktLuRows.AutoSize = true;
            _mktLuRows.AutoSizeMode = AutoSizeMode.GrowAndShrink;
            _mktLuRows.Dock = DockStyle.Top;
            _mktLuRows.Margin = new Padding(0);
            AddRow(b4, _mktLuRows);

            AddRow(b4, Row(Mute(Lbl("晋级率（昨日 N 板 → 今日 N+1 板）"))));
            _mktLuPromoRows = new TableLayoutPanel();
            _mktLuPromoRows.ColumnCount = 1;
            _mktLuPromoRows.AutoSize = true;
            _mktLuPromoRows.AutoSizeMode = AutoSizeMode.GrowAndShrink;
            _mktLuPromoRows.Dock = DockStyle.Top;
            _mktLuPromoRows.Margin = new Padding(0);
            AddRow(b4, _mktLuPromoRows);

            _mktLuCaption = Mute(Lbl("涨停明细"));
            _mktLuFold = MiniBtn("收起", delegate { MktToggleFold(_mktLuStocks, _mktLuFold); }, 70);
            _mktLuInds = new List<string>();
            _mktLuFilter = new ComboBox();
            _mktLuFilter.Width = 170;
            _mktLuFilter.DropDownStyle = ComboBoxStyle.DropDownList;
            _mktLuFilter.SelectedIndexChanged += delegate { MktApplyFilter(_mktLuFilter, _mktLuInds, _mktLuStocks, _mktLuAll, _mktLuCaption, "涨停明细", MktLimitUpRow); };
            _mktLuStocks = NewGrid(new List<GridColumn> {
                new GridColumn("代码") { IsCode = true, Jump = true },
                NameColumn(),
                new GridColumn("连板", "boards", true),
                new GridColumn("行业"),
                new GridColumn("涨幅", "pct", true),
                new GridColumn("封板资金", "fund", true),
                new GridColumn("换手", "turn", true),
                new GridColumn("首封", "first_time"),
                new GridColumn("涨停统计", "stat"),
            });
            _mktLuStocks.SetSortable(true);
            AddRow(b4, Row(_mktLuCaption, _mktLuFold, Lbl("板块"), _mktLuFilter, MktCopyBtn(_mktLuStocks)));
            AddRow(b4, _mktLuStocks);
            AddRow(stack, g4);

            TableLayoutPanel b5;
            var g5 = Group("", out b5);   // 标题与二级标签重复，留空
            _mktBlStatus = Mute(Lbl("—"));
            AddRow(b5, Row(_mktBlStatus));
            _mktBlCaption = Mute(Lbl("炸板股"));
            _mktBlFold = MiniBtn("收起", delegate { MktToggleFold(_mktBlasted, _mktBlFold); }, 70);
            _mktBlInds = new List<string>();
            _mktBlFilter = new ComboBox();
            _mktBlFilter.Width = 170;
            _mktBlFilter.DropDownStyle = ComboBoxStyle.DropDownList;
            _mktBlFilter.SelectedIndexChanged += delegate { MktApplyFilter(_mktBlFilter, _mktBlInds, _mktBlasted, _mktBlAll, _mktBlCaption, "炸板股", MktBlastRow); };
            _mktBlasted = NewGrid(new List<GridColumn> {
                new GridColumn("代码") { IsCode = true, Jump = true },
                NameColumn(),
                new GridColumn("涨跌幅", "pct", true),
                new GridColumn("回撤", "drawdown", true),
                new GridColumn("振幅", "amplitude", true),
                new GridColumn("炸板次数", "times", true),
                new GridColumn("行业"),
            });
            _mktBlasted.SetSortable(true);
            AddRow(b5, Row(_mktBlCaption, _mktBlFold, Lbl("板块"), _mktBlFilter, MktCopyBtn(_mktBlasted)));
            AddRow(b5, _mktBlasted);

            _mktDtCaption = Mute(Lbl("跌停股"));
            _mktDtFold = MiniBtn("收起", delegate { MktToggleFold(_mktLimitDown, _mktDtFold); }, 70);
            _mktDtInds = new List<string>();
            _mktDtFilter = new ComboBox();
            _mktDtFilter.Width = 170;
            _mktDtFilter.DropDownStyle = ComboBoxStyle.DropDownList;
            _mktDtFilter.SelectedIndexChanged += delegate { MktApplyFilter(_mktDtFilter, _mktDtInds, _mktLimitDown, _mktDtAll, _mktDtCaption, "跌停股", MktDownRow); };
            _mktLimitDown = NewGrid(new List<GridColumn> {
                new GridColumn("代码") { IsCode = true, Jump = true },
                NameColumn(),
                new GridColumn("涨跌幅", "pct", true),
                new GridColumn("连续跌停", "days", true),
                new GridColumn("开板次数", "times", true),
                new GridColumn("行业"),
            });
            _mktLimitDown.SetSortable(true);
            AddRow(b5, Row(_mktDtCaption, _mktDtFold, Lbl("板块"), _mktDtFilter, MktCopyBtn(_mktLimitDown)));
            AddRow(b5, _mktLimitDown);
            AddRow(stack, g5);
        }

        // ---------------- 通用控件 ----------------

        /// <summary>生成「复制表格」按钮：点击将表格（含表头）以 TSV 写入剪贴板，并短暂反馈结果。</summary>
        private static Button MktCopyBtn(DataGridView g)
        {
            var btn = MiniBtn("复制表格", (EventHandler)null, 100);
            btn.Click += delegate
            {
                string msg;
                if (g == null || g.Rows.Count == 0) msg = "无数据";
                else if (MktCopyGrid(g)) msg = "已复制 ✓";
                else msg = "复制失败";
                btn.Text = msg;
                var t = new System.Windows.Forms.Timer();
                t.Interval = 1500;
                t.Tick += delegate { btn.Text = "复制表格"; t.Stop(); t.Dispose(); };
                t.Start();
            };
            return btn;
        }

        /// <summary>将表格（含表头）以 TSV 写入剪贴板。剪贴板可能被其它进程临时占用，这里重试几次。返回是否成功。</summary>
        private static bool MktCopyGrid(DataGridView g)
        {
            if (g == null || g.Rows.Count == 0) return false;
            var sb = new System.Text.StringBuilder();
            for (int c = 0; c < g.Columns.Count; c++)
            {
                if (c > 0) sb.Append("\t");
                sb.Append(g.Columns[c].HeaderText ?? "");
            }
            sb.Append("\r\n");
            foreach (DataGridViewRow r in g.Rows)
            {
                if (r.IsNewRow) continue;
                for (int c = 0; c < g.Columns.Count; c++)
                {
                    if (c > 0) sb.Append("\t");
                    object v = r.Cells[c].Value;
                    sb.Append(v == null ? "" : v.ToString());
                }
                sb.Append("\r\n");
            }
            string text = sb.ToString();
            for (int attempt = 0; attempt < 8; attempt++)
            {
                try
                {
                    // 第二个参数 true：退出程序后剪贴板内容仍保留；重试应对「剪贴板被占用」
                    System.Windows.Forms.Clipboard.SetDataObject(text, true);
                    return true;
                }
                catch (System.Runtime.InteropServices.ExternalException)
                {
                    System.Threading.Thread.Sleep(40);
                }
                catch (Exception)
                {
                    return false;
                }
            }
            return false;
        }

        // 高度自适应由 `StockGrid.Fit`（GridKit.cs）统一提供，不再各页各写一份。

        /// <summary>一行「名称 …… 值」（列表行，比表格轻）。</summary>
        private static Panel MktRow(string name, string value)
        {
            var p = new Panel { Height = 22, Dock = DockStyle.Top, Margin = new Padding(0) };
            var v = new Label { Text = value, Dock = DockStyle.Right, Width = 190, AutoSize = false, TextAlign = ContentAlignment.MiddleRight };
            var n = new Label { Text = name, Dock = DockStyle.Fill, AutoSize = false, TextAlign = ContentAlignment.MiddleLeft, Tag = "muted" };
            p.Controls.Add(v);
            p.Controls.Add(n);
            return p;
        }

        /// <summary>A 股配色：涨红、跌绿、平灰（tag="mkt-dir"，换肤时跳过，保留涨跌色）。</summary>
        private static void MktDir(Label lb, double? v)
        {
            lb.Tag = "mkt-dir";
            if (v == null) { lb.ForeColor = C.Flat; return; }
            lb.ForeColor = v.Value > 0 ? C.Up
                : (v.Value < 0 ? C.Down : C.Flat);
        }

        private static void MktColor(DataGridViewCell cell, double? v)
        {
            if (v == null) return;
            if (v.Value > 0) cell.Style.ForeColor = C.Up;
            else if (v.Value < 0) cell.Style.ForeColor = C.Down;
            else cell.Style.ForeColor = C.Flat;
        }

        private static MktBars.Item MktBarItem(string name, double value, string text)
        {
            return new MktBars.Item { Name = name, Value = value, Text = text };
        }

        // ---------------- 加载 ----------------
        private void MktLoadAll()
        {
            string date = _mktDate.Checked ? _mktDate.Value.ToString("yyyyMMdd") : "";
            _mktStatus.Text = "加载中…";
            _mktStatus.Tag = "muted";

            string[][] jobs = new string[][]
            {
                new string[] { "global",  "/api/market/global" },
                new string[] { "capital", "/api/market/capital" },
                new string[] { "sectors", "/api/market/sectors" },
                new string[] { "limitup", "/api/market/limit-up" },
                new string[] { "bigloss", "/api/market/big-loss" },
            };

            int round = ++_mktRound;
            _mktPending = jobs.Length;
            _mktFailed = 0;

            foreach (string[] job in jobs)
            {
                string name = job[0];
                string path = job[1];
                string sep = date == "" ? "" : "?date=" + date;
                string url = "http://127.0.0.1:8000" + path + sep;
                if (name == "sectors")
                {
                    string w = (_mktSectorWindow != null && _mktSectorWindow.SelectedItem != null)
                        ? _mktSectorWindow.SelectedItem.ToString() : "今日";
                    url += (sep == "" ? "?" : "&") + "window=" + Uri.EscapeDataString(w);
                }
                System.Threading.Tasks.Task.Run(delegate
                {
                    try
                    {
                        string resp = VRequest(url, null);
                        var j = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(resp);
                        Invoke((Action)delegate { MktRender(name, j); MktJobDone(round); });
                    }
                    catch (Exception ex)
                    {
                        string msg = "失败：" + ex.Message;
                        try { Invoke((Action)delegate { MktFail(name, msg); MktJobDone(round); }); }
                        catch (Exception) { }
                    }
                });
            }
        }

        /// <summary>单个 job 收尾：全部返回后才复位顶部状态。
        /// 之前成功路径从不复位，界面上会一直停在「加载中…」（数据其实早就到了）。</summary>
        private void MktJobDone(int round)
        {
            if (round != _mktRound || _mktPending <= 0) return;      // 过期轮次 / 重复回调
            if (--_mktPending > 0) return;
            _mktStatus.Text = _mktFailed > 0
                ? "部分数据加载失败"
                : "更新于 " + DateTime.Now.ToString("HH:mm:ss");
            _mktStatus.Tag = "muted";
        }

        private void MktFail(string name, string msg)
        {
            Label target = MktStatusOf(name);
            if (target != null)
            {
                target.Text = msg;
                target.ForeColor = C.UpErr;
            }
            _mktFailed++;
        }

        private Label MktStatusOf(string name)
        {
            if (name == "global") return _mktGlobalStatus;
            if (name == "capital") return _mktCapStatus;
            if (name == "sectors") return _mktSectorStatus;
            if (name == "limitup") return _mktLuStatus;
            if (name == "bigloss") return _mktBlStatus;
            return null;
        }

        private void MktRender(string name, Dictionary<string, object> j)
        {
            if (name == "global") MktRenderGlobal(j);
            else if (name == "capital") MktRenderCapital(j);
            else if (name == "sectors") MktRenderSectors(j);
            else if (name == "limitup") MktRenderLimitUp(j);
            else if (name == "bigloss") MktRenderBigLoss(j);
        }

        // ---------------- ① 外围环境 ----------------
        private void MktRenderGlobal(Dictionary<string, object> j)
        {
            _mktGlobalCards.Controls.Clear();
            var groups = J.Arr(J.Get(j, "groups"));
            int n = 0;
            if (groups != null)
            {
                foreach (Dictionary<string, object> g in groups)
                {
                    string gname = J.Str(J.Get(g, "name"));
                    var items = J.Arr(J.Get(g, "items"));
                    if (items == null) continue;

                    var card = new Panel();
                    card.Tag = "kpi";
                    card.Width = 250;
                    card.Margin = new Padding(0, 0, 10, 10);
                    card.Padding = new Padding(0);
                    var head = new Label { Text = gname, Dock = DockStyle.Top, Height = 22, AutoSize = false, TextAlign = ContentAlignment.MiddleLeft, Tag = "muted" };
                    card.Controls.Add(head);

                    foreach (Dictionary<string, object> it in items)
                    {
                        string session = J.Str(J.Get(it, "session"));
                        string mark = session == "open" ? " 交易中" : (session == "closed" ? " 休市" : "");
                        double? pct = J.NumOrNull(J.Get(it, "pct"));
                        var row = new Panel { Height = 22, Dock = DockStyle.Top };
                        var pv = new Label { Text = J.PctRaw(pct), Dock = DockStyle.Right, Width = 76, AutoSize = false, TextAlign = ContentAlignment.MiddleRight };
                        MktDir(pv, pct);
                        var vv = new Label { Text = J.Fmt(J.NumOrNull(J.Get(it, "value"))), Dock = DockStyle.Right, Width = 82, AutoSize = false, TextAlign = ContentAlignment.MiddleRight };
                        var nv = new Label { Text = J.Str(J.Get(it, "name")) + mark, Dock = DockStyle.Fill, AutoSize = false, TextAlign = ContentAlignment.MiddleLeft };
                        row.Controls.Add(pv);
                        row.Controls.Add(vv);
                        row.Controls.Add(nv);
                        card.Controls.Add(row);
                        n++;
                    }
                    card.Height = 24 + (items.Count * 22) + 8;
                    _mktGlobalCards.Controls.Add(card);
                }
            }

            bool hist = (J.Get(j, "historical") is bool) && (bool)J.Get(j, "historical");
            _mktGlobalStatus.Text = (hist ? J.Str(J.Get(j, "trade_date")) + " 收盘" : J.Str(J.Get(j, "as_of"))) + "（" + n + " 项）";
        }

        // ---------------- ② 大盘资金 ----------------
        private void MktRenderCapital(Dictionary<string, object> j)
        {
            _mktCapKpi.Controls.Clear();
            var mf = J.Map(J.Get(j, "main_flow"));
            var t = J.Map(J.Get(j, "turnover"));
            var ad = J.Map(J.Get(j, "adv_dec"));

            string src = mf != null ? J.Str(J.Get(mf, "source")) : "";
            if (src == "") src = "东方财富";
            double? bigNet = null;
            if (mf != null)
            {
                bigNet = J.NumOrNull(J.Get(mf, "super_net"));
                if (bigNet == null) bigNet = J.NumOrNull(J.Get(mf, "big_net"));
            }
            double? mainNet = mf != null ? J.NumOrNull(J.Get(mf, "main_net")) : null;

            AddKpi(_mktCapKpi, "沪深主力净流入", J.YiSigned(mainNet),
                mf != null ? (J.NumOrNull(J.Get(mf, "main_pct")) != null ? "净占比 " + J.PctRaw(J.NumOrNull(J.Get(mf, "main_pct"))) : "个股资金流汇总（近似口径）") : "资金流数据源暂不可用");
            AddKpi(_mktCapKpi, "特大单（超大单）", J.YiSigned(bigNet),
                mf != null ? "方向：" + J.Str(J.Get(mf, "super_direction")) : "—");
            AddKpi(_mktCapKpi, "数据口径", src, mf != null ? "数据日 " + J.Str(J.Get(mf, "date")) : "—");
            AddKpi(_mktCapKpi, "两市成交额", (t != null && J.NumOrNull(J.Get(t, "total")) != null) ? J.Fmt(J.NumOrNull(J.Get(t, "total"))) + " 亿" : "—",
                MktTurnoverDetail(t));

            _mktCapRows.Controls.Clear();
            if (ad != null)
            {
                int up = MktInt(J.Get(ad, "up"));
                int down = MktInt(J.Get(ad, "down"));
                int flat = MktInt(J.Get(ad, "flat"));
                int total = up + down + flat;
                if (total <= 0) total = 1;

                var segs = new List<MktRatioBar.Seg>();
                segs.Add(new MktRatioBar.Seg { Label = "涨 " + up, Value = up, Color = C.Up });
                segs.Add(new MktRatioBar.Seg { Label = "平 " + flat, Value = flat, Color = C.FlatDeep });
                segs.Add(new MktRatioBar.Seg { Label = "跌 " + down, Value = down, Color = C.Down });
                _mktCapRatio.SetSegments(segs);

                AddRow(_mktCapRows, MktRow("上涨占比", (up * 100.0 / total).ToString("F1") + "%"));
                AddRow(_mktCapRows, MktRow("涨停 / 跌停", MktText(J.Get(ad, "limit_up")) + " / " + MktText(J.Get(ad, "limit_down"))));
                if (J.Get(ad, "suspend") != null) AddRow(_mktCapRows, MktRow("停牌家数", MktText(J.Get(ad, "suspend"))));
            }
            else
            {
                _mktCapRatio.SetSegments(null);
            }

            bool hist = (J.Get(j, "historical") is bool) && (bool)J.Get(j, "historical");
            _mktCapStatus.Text = hist ? J.Str(J.Get(j, "trade_date")) + " 数据" : J.Str(J.Get(j, "as_of"));
        }

        private static string MktTurnoverDetail(Dictionary<string, object> t)
        {
            if (t == null) return "—";
            var items = J.Arr(J.Get(t, "items"));
            if (items == null || items.Count == 0) return "—";
            var parts = new List<string>();
            foreach (Dictionary<string, object> it in items)
                parts.Add(J.Str(J.Get(it, "name")) + " " + J.Fmt(J.NumOrNull(J.Get(it, "amount")), 0) + "亿");
            return string.Join(" · ", parts.ToArray());
        }

        // ---------------- ③ 板块β ----------------
        private void MktRenderSectors(Dictionary<string, object> j)
        {
            var ind = J.Map(J.Get(j, "industry"));
            var con = J.Map(J.Get(j, "concept"));
            var sw = J.Map(J.Get(j, "sw_first"));

            _mktIndBars.SetItems(MktFlowItems(ind));
            _mktConBars.SetItems(MktFlowItems(con));
            _mktSwTopBars.SetItems(MktPctItems(sw != null ? J.Arr(J.Get(sw, "top")) : null));
            _mktSwBotBars.SetItems(MktPctItems(sw != null ? J.Arr(J.Get(sw, "bottom")) : null));

            bool hist = (J.Get(j, "historical") is bool) && (bool)J.Get(j, "historical");
            string st = hist ? J.Str(J.Get(j, "trade_date")) + " 收盘" : J.Str(J.Get(j, "as_of"));
            var errs = J.Arr(J.Get(j, "errors"));
            if (errs != null && errs.Count > 0)
                st += "（" + J.Str(errs[0]) + "）";     // 说明原因，别让用户只看到「无数据」
            _mktSectorStatus.Text = st;
        }

        /// <summary>行业 / 概念资金流：净流入 + 净流出合并后按净额升序（流出在前、流入在后，与网页图表一致）。</summary>
        private static List<MktBars.Item> MktFlowItems(Dictionary<string, object> block)
        {
            var rows = new List<Dictionary<string, object>>();
            if (block != null)
            {
                var inArr = J.Arr(J.Get(block, "top_in"));
                var outArr = J.Arr(J.Get(block, "top_out"));
                if (inArr != null) foreach (Dictionary<string, object> x in inArr) rows.Add(x);
                if (outArr != null) foreach (Dictionary<string, object> x in outArr) rows.Add(x);
            }
            rows.Sort(delegate(Dictionary<string, object> a, Dictionary<string, object> b)
            {
                double va = J.NumOrNull(J.Get(a, "net")) ?? 0;
                double vb = J.NumOrNull(J.Get(b, "net")) ?? 0;
                return va.CompareTo(vb);
            });
            var items = new List<MktBars.Item>();
            foreach (Dictionary<string, object> x in rows)
            {
                double? net = J.NumOrNull(J.Get(x, "net"));
                items.Add(MktBarItem(J.Str(J.Get(x, "name")), net ?? 0, J.Fmt(net)));
            }
            return items;
        }

        private static List<MktBars.Item> MktPctItems(System.Collections.ArrayList list)
        {
            var items = new List<MktBars.Item>();
            if (list == null) return items;
            foreach (Dictionary<string, object> x in list)
            {
                double? pct = J.NumOrNull(J.Get(x, "pct"));
                items.Add(MktBarItem(J.Str(J.Get(x, "name")), pct ?? 0, J.PctRaw(pct)));
            }
            return items;
        }

        // ---------------- ⑥ AI 分析 ----------------
        private void MktBuildMktAi(TableLayoutPanel stack)
        {
            TableLayoutPanel b;
            var g = Group("盘面 AI 分析", out b);
            _mktAiStatus = Mute(Lbl("准备分析…"));
            _mktAiDepth = new ComboBox();
            _mktAiDepth.DropDownStyle = ComboBoxStyle.DropDownList;
            _mktAiDepth.Items.AddRange(new string[] { "精简", "适中", "详细" });
            _mktAiDepth.SelectedIndex = 1;
            _mktAiDepth.Width = 70;
            AddRow(b, Row(_mktAiStatus, _mktAiDepth,
                MiniBtn("生成分析", delegate { MktLoadMktAi(false); }, 96),
                MiniBtn("↻ 重新生成", delegate { MktLoadMktAi(true); }, 110)));
            _mktAiBox = new RichTextBox();
            _mktAiBox.Tag = "doc-ai";
            _mktAiBox.ReadOnly = true;
            _mktAiBox.BorderStyle = BorderStyle.None;
            _mktAiBox.Height = 460;
            _mktAiBox.Anchor = AnchorStyles.Left | AnchorStyles.Right | AnchorStyles.Top;
            _mktAiBox.ScrollBars = RichTextBoxScrollBars.Vertical;
            _mktAiBox.Font = new Font("Microsoft YaHei UI", 9.5f);
            _mktAiBox.BackColor = _cPanel;
            _mktAiBox.ForeColor = _cText;
            AddRow(b, _mktAiBox);
            AddRow(stack, g);
        }

        private void MktLoadMktAi(bool force)
        {
            if (_mktAiStatus != null)
            {
                _mktAiStatus.Text = force ? "AI 强制重新分析中…" : "AI 分析中…";
                _mktAiStatus.Tag = "muted";
                _mktAiStatus.ForeColor = C.Flat;
            }
            _mktAiMarkdown = "";
            if (_mktAiBox != null) _mktAiBox.Clear();
            MktAiLog("· 正在准备盘面数据…");
            System.Threading.Tasks.Task.Run(delegate
            {
                string depth = (_mktAiDepth != null && _mktAiDepth.SelectedIndex >= 0)
                    ? new[] { "concise", "normal", "detailed" }[_mktAiDepth.SelectedIndex] : "normal";
                string body = new JavaScriptSerializer().Serialize(new Dictionary<string, object> {
                    { "depth", depth }, { "force", force }
                });
                const string url = "http://127.0.0.1:8000/api/market/ai-analysis/stream";
                int got = 0;
                // 后端可能仍在启动（akshare 首次导入较慢），连不上时等待并重试若干次。
                for (int attempt = 1; attempt <= 6; attempt++)
                {
                    bool done = false;
                    try
                    {
                        VRequestStream(url, body, delegate(string line)
                        {
                            Dictionary<string, object> j;
                            try { j = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(line); }
                            catch (Exception) { return; }
                            if (j == null) return;

                            object donev;
                            if (j.TryGetValue("done", out donev) && donev is bool && (bool)donev)
                            {
                                done = true;
                                Invoke((Action)delegate { MktRenderMktAi(j); });
                                return;
                            }
                            string stage = J.Str(J.Get(j, "stage"));
                            if (stage == "") return;
                            if (stage == "AI 生成")
                            {
                                Invoke((Action)delegate
                                {
                                    MktAiLog("· 数据就绪，正在调用大模型生成分析…");
                                    if (_mktAiStatus != null)
                                    {
                                        _mktAiStatus.Text = "AI 生成中…";
                                        _mktAiStatus.ForeColor = C.Flat;
                                    }
                                });
                                return;
                            }
                            got++;
                            int n = got;
                            object cv; bool cached = (j.TryGetValue("cached", out cv) && cv is bool && (bool)cv);
                            var errs = J.Arr(J.Get(j, "errors"));
                            string msg = "✓ " + stage + (cached ? "（复用缓存）" : "（已拉取）");
                            if (errs != null && errs.Count > 0) msg += "  ⚠ " + errs.Count + " 项数据源告警";
                            Invoke((Action)delegate
                            {
                                MktAiLog(msg);
                                if (_mktAiStatus != null) _mktAiStatus.Text = "数据准备中（" + n + "/6）…";
                            });
                        });
                        if (done) return;
                    }
                    catch (System.Net.WebException)
                    {
                        // 连不上 / 超时：后端可能仍在启动，稍后重试
                    }
                    catch (Exception ex)
                    {
                        Invoke((Action)delegate { MktAiFail("分析失败：" + ex.Message); });
                        return;
                    }
                    if (attempt == 6)
                    {
                        Invoke((Action)delegate { MktAiFail("分析失败：无法连接后端（请确认后端已启动）"); });
                        return;
                    }
                    try
                    {
                        Invoke((Action)delegate
                        {
                            if (_mktAiStatus != null)
                            {
                                _mktAiStatus.Text = "等待后端就绪…（第 " + attempt + " 次重试）";
                                _mktAiStatus.ForeColor = C.Flat;
                            }
                        });
                    }
                    catch (Exception) { }
                    System.Threading.Thread.Sleep(3000);
                }
            });
        }

        /// <summary>把一行状态追加到 AI 输出框（供流式进度直接打印在页面上）。</summary>
        private void MktAiLog(string msg)
        {
            if (_mktAiBox == null) return;
            _mktAiBox.SelectionStart = _mktAiBox.TextLength;
            _mktAiBox.SelectionLength = 0;
            _mktAiBox.SelectionColor = C.Flat;
            _mktAiBox.AppendText(msg + Environment.NewLine);
            _mktAiBox.SelectionStart = _mktAiBox.TextLength;
            _mktAiBox.ScrollToCaret();
        }

        /// <summary>流式 POST：逐行回调 SSE 的 data 负载（用于 /api/market/ai-analysis/stream）。</summary>
        private static void VRequestStream(string url, string body, Action<string> onDataLine)
        {
            var req = (System.Net.HttpWebRequest)System.Net.WebRequest.Create(url);
            req.Proxy = null;                  // 本机直连，绕开系统代理 / 自动发现
            req.KeepAlive = false;
            req.Timeout = 300000;              // 数据拉取 + LLM 生成可能较慢
            req.ReadWriteTimeout = 300000;
            req.Method = "POST";
            req.ServicePoint.Expect100Continue = false;
            req.ContentType = "application/json; charset=utf-8";
            byte[] data = System.Text.Encoding.UTF8.GetBytes(body);
            req.ContentLength = data.Length;
            using (var s = req.GetRequestStream()) s.Write(data, 0, data.Length);
            using (var resp = (System.Net.HttpWebResponse)req.GetResponse())
            using (var sr = new System.IO.StreamReader(resp.GetResponseStream(), System.Text.Encoding.UTF8))
            {
                string line;
                while ((line = sr.ReadLine()) != null)
                {
                    if (line.StartsWith("data:")) onDataLine(line.Substring(5).Trim());
                }
            }
        }

        private void MktAiFail(string msg)
        {
            if (_mktAiStatus != null)
            {
                _mktAiStatus.Text = msg;
                _mktAiStatus.ForeColor = C.UpErr;
            }
            _mktAiMarkdown = "";
            if (_mktAiBox != null) FillAiDoc(_mktAiBox, "");
        }

        private void MktRenderMktAi(Dictionary<string, object> j)
        {
            if (_mktAiStatus == null || _mktAiBox == null) return;
            object okv;
            bool ok = (j != null && j.TryGetValue("ok", out okv) && okv is bool && (bool)okv);
            if (!ok)
            {
                string emsg = J.Str(J.Get(j, "error"));
                if (emsg == "") emsg = J.Str(J.Get(j, "detail"));
                if (emsg == "") emsg = "分析失败";
                _mktAiStatus.Text = emsg;
                _mktAiStatus.ForeColor = C.UpErr;
                _mktAiMarkdown = "";
                FillAiDoc(_mktAiBox, "");
                return;
            }
            _mktAiMarkdown = J.Str(J.Get(j, "markdown"));
            object cached;
            bool isCached = (j.TryGetValue("cached", out cached) && cached is bool && (bool)cached);
            string asOf = J.Str(J.Get(j, "data_as_of"));
            _mktAiStatus.Text = (isCached ? "（缓存）" : "已生成") + (asOf != "" ? " 数据截至 " + asOf : "");
            _mktAiStatus.Tag = "muted";
            _mktAiStatus.ForeColor = C.DownDeep;
            FillAiDoc(_mktAiBox, _mktAiMarkdown);
        }

        // ---------------- ④ 连板梯队 ----------------
        private void MktRenderLimitUp(Dictionary<string, object> j)
        {
            _mktLuKpi.Controls.Clear();
            _mktLuPromoRows.Controls.Clear();
            _mktLuRows.Controls.Clear();
            _mktLuStocks.Rows.Clear();

            object okv;
            bool ok = (j != null && j.TryGetValue("ok", out okv) && okv is bool && (bool)okv);
            if (!ok)
            {
                _mktLuStatus.Text = J.Str(J.Get(j, "trade_date")) + " 未获取到涨停池数据";
                _mktLuStatus.ForeColor = C.UpErr;
                return;
            }

            var structure = J.Arr(J.Get(j, "structure"));
            var promo = J.Arr(J.Get(j, "promotion"));
            var stocks = J.Arr(J.Get(j, "stocks"));

            int ge2 = 0, first = 0;
            if (structure != null)
            {
                foreach (Dictionary<string, object> s in structure)
                {
                    int level = MktInt(J.Get(s, "level"));
                    int count = MktInt(J.Get(s, "count"));
                    AddRow(_mktLuRows, MktRow(level + " 板", count + " 家"));
                    if (level >= 2) ge2 += count;
                    if (level == 1) first = count;
                }
            }

            AddKpi(_mktLuKpi, "今日涨停家数", MktText(J.Get(j, "total")), "数据日 " + J.Str(J.Get(j, "trade_date")));
            AddKpi(_mktLuKpi, "最高连板", MktText(J.Get(j, "max_level")) + " 板", "结构：" + MktStructureText(structure));
            AddKpi(_mktLuKpi, "连板梯队（≥2 板）", ge2.ToString(), "首板 " + first + " 家");

            if (promo != null)
            {
                foreach (Dictionary<string, object> p in promo)
                {
                    int from = MktInt(J.Get(p, "from"));
                    double? rate = J.NumOrNull(J.Get(p, "rate"));
                    AddRow(_mktLuPromoRows, MktRow(from + " 板 → " + (from + 1) + " 板",
                        MktText(J.Get(p, "total")) + " → " + MktText(J.Get(p, "promoted"))
                        + "（" + (rate != null ? rate.Value.ToString("F1") + "%" : "—") + "）"));
                }
            }

            // 原始数据留存，板块筛选时不用重新请求
            _mktLuAll = new List<Dictionary<string, object>>();
            if (stocks != null)
            {
                foreach (Dictionary<string, object> s in stocks) _mktLuAll.Add(s);
            }
            MktFillFilterBox(_mktLuFilter, _mktLuInds, _mktLuAll);
            MktApplyFilter(_mktLuFilter, _mktLuInds, _mktLuStocks, _mktLuAll, _mktLuCaption, "涨停明细", MktLimitUpRow);

            _mktLuStatus.Text = J.Str(J.Get(j, "trade_date")) + "（涨停 " + MktText(J.Get(j, "total")) + " 家）";
        }

        /// <summary>
        /// 板块下拉只列「确实有涨停股」的板块：跳过空值与占位（— / - / --），
        /// 每项带该板块的涨停家数，按家数从多到少排。
        /// </summary>
        private delegate void MktFillRow(DataGridView g, Dictionary<string, object> s);

        /// <summary>
        /// 板块下拉只列「确实有票」的板块：跳过空值与占位（— / - / --），
        /// 每项带该板块的只数，按只数从多到少排。
        /// </summary>
        private static void MktFillFilterBox(ComboBox cb, List<string> inds, List<Dictionary<string, object>> all)
        {
            var counts = new Dictionary<string, int>();
            if (all != null)
            {
                foreach (Dictionary<string, object> s in all)
                {
                    string ind = J.Str(J.Get(s, "industry"));
                    if (ind == "" || ind == "—" || ind == "-" || ind == "--") continue;
                    if (counts.ContainsKey(ind)) counts[ind] = counts[ind] + 1;
                    else counts[ind] = 1;
                }
            }
            inds.Clear();
            foreach (KeyValuePair<string, int> kv in counts) inds.Add(kv.Key);
            inds.Sort(delegate(string a, string b)
            {
                int c = counts[b].CompareTo(counts[a]);      // 只数多的在前
                return c != 0 ? c : a.CompareTo(b);
            });
            cb.Items.Clear();
            cb.Items.Add("全部（" + (all != null ? all.Count : 0) + "）");
            foreach (string x in inds) cb.Items.Add(x + "（" + counts[x] + "）");
            cb.SelectedIndex = 0;   // 会触发对应的 SelectedIndexChanged
        }

        /// <summary>按板块筛选渲染一张表，并更新标题计数。</summary>
        private static void MktApplyFilter(ComboBox cb, List<string> inds, StockGrid g,
            List<Dictionary<string, object>> all, Label caption, string title, MktFillRow fill)
        {
            if (all == null) return;
            int idx = cb.SelectedIndex;
            string sel = (idx > 0 && inds != null && idx - 1 < inds.Count) ? inds[idx - 1] : null;
            bool isAll = string.IsNullOrEmpty(sel);

            g.Rows.Clear();
            int n = 0;
            foreach (Dictionary<string, object> s in all)
            {
                string ind = J.Str(J.Get(s, "industry"));
                if (!isAll && ind != sel) continue;
                fill(g, s);
                n++;
            }
            g.Fit(160, 620);

            int total = all.Count;
            caption.Text = isAll ? (title + "（共 " + total + " 只）")
                : (title + " · " + sel + "（" + n + " / " + total + " 只）");
        }

        /// <summary>折叠 / 展开一张表。</summary>
        private static void MktToggleFold(DataGridView g, Button b)
        {
            bool vis = !g.Visible;
            g.Visible = vis;
            b.Text = vis ? "收起" : "展开";
        }

        private static void MktLimitUpRow(DataGridView g, Dictionary<string, object> s)
        {
            double? pct = J.NumOrNull(J.Get(s, "pct"));
            int row = g.Rows.Add(
                J.Str(J.Get(s, "code")),
                J.Str(J.Get(s, "name")),
                MktText(J.Get(s, "level")) + " 板",
                J.Str(J.Get(s, "industry")),
                J.PctRaw(pct),
                J.Fmt(J.NumOrNull(J.Get(s, "seal_fund"))) + " 亿",
                J.Fmt(J.NumOrNull(J.Get(s, "turnover"))) + "%",
                J.Str(J.Get(s, "first_seal")),
                J.Str(J.Get(s, "stat")));
            MktColor(g.Rows[row].Cells[4], pct);
        }

        private static void MktBlastRow(DataGridView g, Dictionary<string, object> s)
        {
            double? pct = J.NumOrNull(J.Get(s, "pct"));
            double? dd = J.NumOrNull(J.Get(s, "drawdown"));
            int row = g.Rows.Add(
                J.Str(J.Get(s, "code")),
                J.Str(J.Get(s, "name")),
                J.PctRaw(pct),
                dd != null ? dd.Value.ToString("F2") + "%" : "—",
                J.Fmt(J.NumOrNull(J.Get(s, "amplitude"))) + "%",
                MktText(J.Get(s, "blasted_times")),
                J.Str(J.Get(s, "industry")));
            MktColor(g.Rows[row].Cells[2], pct);
        }

        private static void MktDownRow(DataGridView g, Dictionary<string, object> s)
        {
            double? pct = J.NumOrNull(J.Get(s, "pct"));
            int row = g.Rows.Add(
                J.Str(J.Get(s, "code")),
                J.Str(J.Get(s, "name")),
                J.PctRaw(pct),
                MktText(J.Get(s, "continuous")),
                MktText(J.Get(s, "open_times")),
                J.Str(J.Get(s, "industry")));
            MktColor(g.Rows[row].Cells[2], pct);
        }

        private static string MktStructureText(System.Collections.ArrayList structure)
        {
            if (structure == null || structure.Count == 0) return "—";
            var parts = new List<string>();
            foreach (Dictionary<string, object> s in structure)
                parts.Add(MktInt(J.Get(s, "level")) + "板×" + MktInt(J.Get(s, "count")));
            return string.Join(" · ", parts.ToArray());
        }

        // ---------------- ⑤ 大面股 ----------------
        private void MktRenderBigLoss(Dictionary<string, object> j)
        {
            _mktBlasted.Rows.Clear();
            _mktLimitDown.Rows.Clear();

            object okv;
            bool ok = (j != null && j.TryGetValue("ok", out okv) && okv is bool && (bool)okv);
            if (!ok)
            {
                _mktBlStatus.Text = J.Str(J.Get(j, "trade_date")) + " 未获取到大面股数据";
                _mktBlStatus.ForeColor = C.UpErr;
                return;
            }

            var blasted = J.Arr(J.Get(j, "blasted"));
            var limitDown = J.Arr(J.Get(j, "limit_down"));

            _mktBlAll = new List<Dictionary<string, object>>();
            if (blasted != null)
            {
                foreach (Dictionary<string, object> s in blasted) _mktBlAll.Add(s);
            }
            _mktDtAll = new List<Dictionary<string, object>>();
            if (limitDown != null)
            {
                foreach (Dictionary<string, object> s in limitDown) _mktDtAll.Add(s);
            }

            MktFillFilterBox(_mktBlFilter, _mktBlInds, _mktBlAll);
            MktApplyFilter(_mktBlFilter, _mktBlInds, _mktBlasted, _mktBlAll, _mktBlCaption, "炸板股", MktBlastRow);
            MktFillFilterBox(_mktDtFilter, _mktDtInds, _mktDtAll);
            MktApplyFilter(_mktDtFilter, _mktDtInds, _mktLimitDown, _mktDtAll, _mktDtCaption, "跌停股", MktDownRow);

            _mktBlStatus.Text = J.Str(J.Get(j, "trade_date")) + "（炸板 " + (blasted != null ? blasted.Count : 0)
                + " 只 · 跌停 " + (limitDown != null ? limitDown.Count : 0) + " 只）";
        }

        // ---------------- 小工具 ----------------
        private static int MktInt(object o)
        {
            double? v = J.NumOrNull(o);
            if (v == null) return 0;
            return (int)v.Value;
        }

        private static string MktText(object o)
        {
            if (o == null) return "—";
            double? v = J.NumOrNull(o);
            if (v != null) return ((int)v.Value).ToString();
            string s = J.Str(o);
            return s == "" ? "—" : s;
        }

        // ================= 自绘控件 =================

        /// <summary>横向条形图（每行：名称 + 条形 + 数值；正值红、负值绿，0 轴按正负极值居中，贴近网页 echarts 的观感）。</summary>
        private sealed class MktBars : ChartControl
        {
            public class Item
            {
                public string Name;
                public double Value;
                public string Text;
            }

            private const int RowH = 20;
            private const int NameW = 98;
            private const int ValueW = 72;
            private const int BarGapX = 6;          // 名称列与条形之间的间隔
            /// <summary>条形区最多占可用宽度的比例：右侧留出余量，免得最长的条一路顶到数值列。</summary>
            private const double BarMaxRatio = 0.72;
            private readonly List<Item> _items = new List<Item>();

            public MktBars()
            {
                // SetStyle 已上移到基类 ChartControl（第 07 项）
                Height = 40;
            }

            public void SetItems(List<Item> items)
            {
                _items.Clear();
                if (items != null) _items.AddRange(items);
                Height = Math.Max(34, _items.Count * RowH + 16);
                Invalidate();
            }

            // GetPreferredSize 已上移到基类 ChartControl（第 07 项）

            protected override void OnPaint(PaintEventArgs e)
            {
                base.OnPaint(e);
                var g = e.Graphics;
                BeginPaint(g);                 // 抗锯齿统一由基类设置（第 07 项）
                if (_items.Count == 0)
                {
                    using (var br = new SolidBrush(ForeColor))
                        g.DrawString("无数据", Font, br, new RectangleF(2, 2, Width - 4, RowH), FmtLeft);
                    return;
                }

                double maxPos = 0, maxNeg = 0;
                foreach (Item it in _items)
                {
                    if (it.Value >= 0) maxPos = Math.Max(maxPos, it.Value);
                    else maxNeg = Math.Max(maxNeg, -it.Value);
                }
                int barX = NameW + BarGapX;
                // 可用宽度再乘 BarMaxRatio：最长的条不铺满整行，右侧留白，条与数值不挤在一起
                int availW = Math.Max(120, Width - barX - ValueW - 6);
                int barW = Math.Max(40, (int)(availW * BarMaxRatio));
                double span = maxPos + maxNeg;
                // 零轴位置：左侧留给负值（maxNeg）、右侧留给正值（maxPos）。
                // 之前写反成按 maxPos 分，导致最大负值的条越过 NameW、把左侧名称盖住。
                int zeroX = span > 0 ? barX + (int)(barW * maxNeg / span) : barX;
                // 正负混合时画一条零轴参考线，左右两侧的条长更好对比
                if (maxPos > 0 && maxNeg > 0)
                {
                    using (var pen = new Pen(Color.FromArgb(80, 127, 127, 127)))
                        g.DrawLine(pen, zeroX, 4, zeroX, Height - 6);
                }

                int y = 8;
                foreach (Item it in _items)
                {
                    using (var nb = new SolidBrush(ForeColor))
                        g.DrawString(it.Name, Font, nb, new RectangleF(0, y, NameW - 6, RowH - 2), FmtLeft);

                    int len = span > 0 ? (int)(Math.Abs(it.Value) / span * barW) : 0;
                    int x = it.Value >= 0 ? zeroX : zeroX - len;
                    if (x < barX) { len -= (barX - x); x = barX; }
                    if (x + len > barX + barW) len = barX + barW - x;
                    if (len < 1) len = 1;

                    Region old = g.Clip;
                    g.SetClip(new Rectangle(barX, 0, barW, Height), System.Drawing.Drawing2D.CombineMode.Replace);
                    using (var br = new SolidBrush(it.Value >= 0 ? C.Up : C.Down))
                        g.FillRectangle(br, x, y + 3, len, RowH - 8);
                    g.Clip = old;

                    using (var br = new SolidBrush(ForeColor))
                        g.DrawString(it.Text, Font, br, new RectangleF(Width - ValueW, y, ValueW - 4, RowH - 2), FmtRight);
                    y += RowH;
                }
            }

            // FmtLeft / FmtRight 已上移到基类 ChartControl（第 07 项）
        }

        /// <summary>涨跌家数分段条：按占比横向分段着色（涨红 / 平灰 / 跌绿）。</summary>
        private sealed class MktRatioBar : ChartControl
        {
            public class Seg
            {
                public string Label;
                public double Value;
                public Color Color;
            }

            private readonly List<Seg> _segs = new List<Seg>();

            public MktRatioBar()
            {
                // SetStyle 已上移到基类 ChartControl（第 07 项）
                Height = 28;
            }

            public void SetSegments(List<Seg> segs)
            {
                _segs.Clear();
                if (segs != null) _segs.AddRange(segs);
                Invalidate();
            }

            // GetPreferredSize 已上移到基类 ChartControl（第 07 项）

            protected override void OnPaint(PaintEventArgs e)
            {
                base.OnPaint(e);
                var g = e.Graphics;
                BeginPaint(g);                 // 抗锯齿统一由基类设置（第 07 项）
                double total = 0;
                foreach (Seg s in _segs) total += s.Value;
                if (total <= 0)
                {
                    using (var br = new SolidBrush(ForeColor))
                        g.DrawString("无数据", Font, br, new RectangleF(2, 2, Width - 4, Height - 4), FmtCenter);
                    return;
                }
                float x = 0;
                foreach (Seg s in _segs)
                {
                    float w = (float)(s.Value / total * Width);
                    if (w <= 0) continue;
                    using (var br = new SolidBrush(s.Color))
                        g.FillRectangle(br, x, 0, w - 1, Height);
                    if (w > 52)
                    {
                        using (var br = new SolidBrush(Color.White))
                            g.DrawString(s.Label, Font, br, new RectangleF(x, 0, w - 1, Height), FmtCenter);
                    }
                    x += w;
                }
            }

            // FmtCenter 已上移到基类 ChartControl（第 07 项）
        }
    }
}
