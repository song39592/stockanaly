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
        private DataGridView _mktLuStocks;                           // ④ 涨停明细（表格）
        private List<Dictionary<string, object>> _mktLuAll;          // 涨停明细原始数据（供板块筛选复用）
        private Label _mktLuCaption;                                 // 涨停明细标题（含计数）
        private Button _mktLuFold;                                   // 折叠 / 展开
        private ComboBox _mktLuFilter;                               // 板块筛选
        private List<string> _mktLuInds;                             // 下拉里的板块名（与 Items 一一对应，index 0 之后）
        private List<Dictionary<string, object>> _mktBlAll, _mktDtAll;  // 炸板 / 跌停原始数据
        private List<string> _mktBlInds, _mktDtInds;
        private ComboBox _mktBlFilter, _mktDtFilter;
        private Label _mktBlCaption, _mktDtCaption;
        private Button _mktBlFold, _mktDtFold;
        private DataGridView _mktBlasted, _mktLimitDown;             // ⑤ 炸板 / 跌停（表格）

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
            MktAddSubPage("① 外围环境", MktBuildGlobal);
            MktAddSubPage("② 大盘资金", MktBuildCapital);
            MktAddSubPage("③ 板块β", MktBuildSectors);
            MktAddSubPage("④ 连板 · ⑤ 大面股", MktBuildLadder);

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
            AddRow(b3, Row(_mktSectorStatus));

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
            AddRow(b4, Row(_mktLuCaption, _mktLuFold, Lbl("板块"), _mktLuFilter));

            _mktLuStocks = MktGrid(240, true, new string[] { "代码", "名称", "连板", "行业", "涨幅", "封板资金", "换手", "首封", "涨停统计" });
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
            AddRow(b5, Row(_mktBlCaption, _mktBlFold, Lbl("板块"), _mktBlFilter));
            _mktBlasted = MktGrid(200, true, new string[] { "代码", "名称", "涨跌幅", "回撤", "振幅", "炸板次数", "行业" });
            AddRow(b5, _mktBlasted);

            _mktDtCaption = Mute(Lbl("跌停股"));
            _mktDtFold = MiniBtn("收起", delegate { MktToggleFold(_mktLimitDown, _mktDtFold); }, 70);
            _mktDtInds = new List<string>();
            _mktDtFilter = new ComboBox();
            _mktDtFilter.Width = 170;
            _mktDtFilter.DropDownStyle = ComboBoxStyle.DropDownList;
            _mktDtFilter.SelectedIndexChanged += delegate { MktApplyFilter(_mktDtFilter, _mktDtInds, _mktLimitDown, _mktDtAll, _mktDtCaption, "跌停股", MktDownRow); };
            AddRow(b5, Row(_mktDtCaption, _mktDtFold, Lbl("板块"), _mktDtFilter));
            _mktLimitDown = MktGrid(180, true, new string[] { "代码", "名称", "涨跌幅", "连续跌停", "开板次数", "行业" });
            AddRow(b5, _mktLimitDown);
            AddRow(stack, g5);
        }

        // ---------------- 通用控件 ----------------

        /// <summary>统一风格的只读数据表：宽度撑满、高度固定、可选点击表头排序（只用于需要列对齐的明细）。</summary>
        private static DataGridView MktGrid(int height, bool sortable, string[] cols)
        {
            var g = new DataGridView();
            g.Dock = DockStyle.Top;
            g.Height = height;
            g.ReadOnly = true;
            g.AllowUserToAddRows = false;
            g.AllowUserToDeleteRows = false;
            g.RowHeadersVisible = false;
            g.BorderStyle = BorderStyle.None;
            g.Tag = "grid";
            g.ScrollBars = ScrollBars.None;   // 不自带滚动条：高度按内容撑开，由页面统一滚动
            g.AutoSizeColumnsMode = DataGridViewAutoSizeColumnsMode.Fill;
            g.ColumnHeadersHeightSizeMode = DataGridViewColumnHeadersHeightSizeMode.AutoSize;
            foreach (string cn in cols)
            {
                var col = new DataGridViewTextBoxColumn();
                col.HeaderText = cn;
                col.SortMode = sortable ? DataGridViewColumnSortMode.Automatic : DataGridViewColumnSortMode.NotSortable;
                g.Columns.Add(col);
            }
            return g;
        }

        /// <summary>把表格高度撑到内容高度，避免出现表格内部滚动条（嵌套滚动）。</summary>
        private static void MktFit(DataGridView g)
        {
            int h = g.ColumnHeadersHeight + 4;
            foreach (DataGridViewRow r in g.Rows) h += r.Height;
            g.Height = h + 4;
            g.ScrollBars = ScrollBars.None;
        }

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
            if (v == null) { lb.ForeColor = Color.FromArgb(150, 158, 172); return; }
            lb.ForeColor = v.Value > 0 ? Color.FromArgb(239, 83, 80)
                : (v.Value < 0 ? Color.FromArgb(63, 185, 80) : Color.FromArgb(150, 158, 172));
        }

        private static void MktColor(DataGridViewCell cell, double? v)
        {
            if (v == null) return;
            if (v.Value > 0) cell.Style.ForeColor = Color.FromArgb(239, 83, 80);
            else if (v.Value < 0) cell.Style.ForeColor = Color.FromArgb(63, 185, 80);
            else cell.Style.ForeColor = Color.FromArgb(150, 158, 172);
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
                string url = "http://127.0.0.1:8000" + path + (date == "" ? "" : "?date=" + date);
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
                target.ForeColor = Color.FromArgb(208, 57, 59);
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
            var groups = VArr(VSafe(j, "groups"));
            int n = 0;
            if (groups != null)
            {
                foreach (Dictionary<string, object> g in groups)
                {
                    string gname = VStr(VSafe(g, "name"));
                    var items = VArr(VSafe(g, "items"));
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
                        string session = VStr(VSafe(it, "session"));
                        string mark = session == "open" ? " 交易中" : (session == "closed" ? " 休市" : "");
                        double? pct = VNum(VSafe(it, "pct"));
                        var row = new Panel { Height = 22, Dock = DockStyle.Top };
                        var pv = new Label { Text = MktPct(pct), Dock = DockStyle.Right, Width = 76, AutoSize = false, TextAlign = ContentAlignment.MiddleRight };
                        MktDir(pv, pct);
                        var vv = new Label { Text = VFmt(VNum(VSafe(it, "value"))), Dock = DockStyle.Right, Width = 82, AutoSize = false, TextAlign = ContentAlignment.MiddleRight };
                        var nv = new Label { Text = VStr(VSafe(it, "name")) + mark, Dock = DockStyle.Fill, AutoSize = false, TextAlign = ContentAlignment.MiddleLeft };
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

            bool hist = (VSafe(j, "historical") is bool) && (bool)VSafe(j, "historical");
            _mktGlobalStatus.Text = (hist ? VStr(VSafe(j, "trade_date")) + " 收盘" : VStr(VSafe(j, "as_of"))) + "（" + n + " 项）";
        }

        // ---------------- ② 大盘资金 ----------------
        private void MktRenderCapital(Dictionary<string, object> j)
        {
            _mktCapKpi.Controls.Clear();
            var mf = VMap(VSafe(j, "main_flow"));
            var t = VMap(VSafe(j, "turnover"));
            var ad = VMap(VSafe(j, "adv_dec"));

            string src = mf != null ? VStr(VSafe(mf, "source")) : "";
            if (src == "") src = "东方财富";
            double? bigNet = null;
            if (mf != null)
            {
                bigNet = VNum(VSafe(mf, "super_net"));
                if (bigNet == null) bigNet = VNum(VSafe(mf, "big_net"));
            }
            double? mainNet = mf != null ? VNum(VSafe(mf, "main_net")) : null;

            AddKpi(_mktCapKpi, "沪深主力净流入", MktYi(mainNet),
                mf != null ? (VNum(VSafe(mf, "main_pct")) != null ? "净占比 " + MktPct(VNum(VSafe(mf, "main_pct"))) : "个股资金流汇总（近似口径）") : "资金流数据源暂不可用");
            AddKpi(_mktCapKpi, "特大单（超大单）", MktYi(bigNet),
                mf != null ? "方向：" + VStr(VSafe(mf, "super_direction")) : "—");
            AddKpi(_mktCapKpi, "数据口径", src, mf != null ? "数据日 " + VStr(VSafe(mf, "date")) : "—");
            AddKpi(_mktCapKpi, "两市成交额", (t != null && VNum(VSafe(t, "total")) != null) ? VFmt(VNum(VSafe(t, "total"))) + " 亿" : "—",
                MktTurnoverDetail(t));

            _mktCapRows.Controls.Clear();
            if (ad != null)
            {
                int up = MktInt(VSafe(ad, "up"));
                int down = MktInt(VSafe(ad, "down"));
                int flat = MktInt(VSafe(ad, "flat"));
                int total = up + down + flat;
                if (total <= 0) total = 1;

                var segs = new List<MktRatioBar.Seg>();
                segs.Add(new MktRatioBar.Seg { Label = "涨 " + up, Value = up, Color = Color.FromArgb(239, 83, 80) });
                segs.Add(new MktRatioBar.Seg { Label = "平 " + flat, Value = flat, Color = Color.FromArgb(120, 124, 132) });
                segs.Add(new MktRatioBar.Seg { Label = "跌 " + down, Value = down, Color = Color.FromArgb(63, 185, 80) });
                _mktCapRatio.SetSegments(segs);

                AddRow(_mktCapRows, MktRow("上涨占比", (up * 100.0 / total).ToString("F1") + "%"));
                AddRow(_mktCapRows, MktRow("涨停 / 跌停", MktText(VSafe(ad, "limit_up")) + " / " + MktText(VSafe(ad, "limit_down"))));
                if (VSafe(ad, "suspend") != null) AddRow(_mktCapRows, MktRow("停牌家数", MktText(VSafe(ad, "suspend"))));
            }
            else
            {
                _mktCapRatio.SetSegments(null);
            }

            bool hist = (VSafe(j, "historical") is bool) && (bool)VSafe(j, "historical");
            _mktCapStatus.Text = hist ? VStr(VSafe(j, "trade_date")) + " 数据" : VStr(VSafe(j, "as_of"));
        }

        private static string MktTurnoverDetail(Dictionary<string, object> t)
        {
            if (t == null) return "—";
            var items = VArr(VSafe(t, "items"));
            if (items == null || items.Count == 0) return "—";
            var parts = new List<string>();
            foreach (Dictionary<string, object> it in items)
                parts.Add(VStr(VSafe(it, "name")) + " " + VFmt(VNum(VSafe(it, "amount")), 0) + "亿");
            return string.Join(" · ", parts.ToArray());
        }

        // ---------------- ③ 板块β ----------------
        private void MktRenderSectors(Dictionary<string, object> j)
        {
            var ind = VMap(VSafe(j, "industry"));
            var con = VMap(VSafe(j, "concept"));
            var sw = VMap(VSafe(j, "sw_first"));

            _mktIndBars.SetItems(MktFlowItems(ind));
            _mktConBars.SetItems(MktFlowItems(con));
            _mktSwTopBars.SetItems(MktPctItems(sw != null ? VArr(VSafe(sw, "top")) : null));
            _mktSwBotBars.SetItems(MktPctItems(sw != null ? VArr(VSafe(sw, "bottom")) : null));

            bool hist = (VSafe(j, "historical") is bool) && (bool)VSafe(j, "historical");
            string st = hist ? VStr(VSafe(j, "trade_date")) + " 收盘" : VStr(VSafe(j, "as_of"));
            var errs = VArr(VSafe(j, "errors"));
            if (errs != null && errs.Count > 0)
                st += "（" + VStr(errs[0]) + "）";     // 说明原因，别让用户只看到「无数据」
            _mktSectorStatus.Text = st;
        }

        /// <summary>行业 / 概念资金流：净流入 + 净流出合并后按净额升序（流出在前、流入在后，与网页图表一致）。</summary>
        private static List<MktBars.Item> MktFlowItems(Dictionary<string, object> block)
        {
            var rows = new List<Dictionary<string, object>>();
            if (block != null)
            {
                var inArr = VArr(VSafe(block, "top_in"));
                var outArr = VArr(VSafe(block, "top_out"));
                if (inArr != null) foreach (Dictionary<string, object> x in inArr) rows.Add(x);
                if (outArr != null) foreach (Dictionary<string, object> x in outArr) rows.Add(x);
            }
            rows.Sort(delegate(Dictionary<string, object> a, Dictionary<string, object> b)
            {
                double va = VNum(VSafe(a, "net")) ?? 0;
                double vb = VNum(VSafe(b, "net")) ?? 0;
                return va.CompareTo(vb);
            });
            var items = new List<MktBars.Item>();
            foreach (Dictionary<string, object> x in rows)
            {
                double? net = VNum(VSafe(x, "net"));
                items.Add(MktBarItem(VStr(VSafe(x, "name")), net ?? 0, VFmt(net)));
            }
            return items;
        }

        private static List<MktBars.Item> MktPctItems(System.Collections.ArrayList list)
        {
            var items = new List<MktBars.Item>();
            if (list == null) return items;
            foreach (Dictionary<string, object> x in list)
            {
                double? pct = VNum(VSafe(x, "pct"));
                items.Add(MktBarItem(VStr(VSafe(x, "name")), pct ?? 0, MktPct(pct)));
            }
            return items;
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
                _mktLuStatus.Text = VStr(VSafe(j, "trade_date")) + " 未获取到涨停池数据";
                _mktLuStatus.ForeColor = Color.FromArgb(208, 57, 59);
                return;
            }

            var structure = VArr(VSafe(j, "structure"));
            var promo = VArr(VSafe(j, "promotion"));
            var stocks = VArr(VSafe(j, "stocks"));

            int ge2 = 0, first = 0;
            if (structure != null)
            {
                foreach (Dictionary<string, object> s in structure)
                {
                    int level = MktInt(VSafe(s, "level"));
                    int count = MktInt(VSafe(s, "count"));
                    AddRow(_mktLuRows, MktRow(level + " 板", count + " 家"));
                    if (level >= 2) ge2 += count;
                    if (level == 1) first = count;
                }
            }

            AddKpi(_mktLuKpi, "今日涨停家数", MktText(VSafe(j, "total")), "数据日 " + VStr(VSafe(j, "trade_date")));
            AddKpi(_mktLuKpi, "最高连板", MktText(VSafe(j, "max_level")) + " 板", "结构：" + MktStructureText(structure));
            AddKpi(_mktLuKpi, "连板梯队（≥2 板）", ge2.ToString(), "首板 " + first + " 家");

            if (promo != null)
            {
                foreach (Dictionary<string, object> p in promo)
                {
                    int from = MktInt(VSafe(p, "from"));
                    double? rate = VNum(VSafe(p, "rate"));
                    AddRow(_mktLuPromoRows, MktRow(from + " 板 → " + (from + 1) + " 板",
                        MktText(VSafe(p, "total")) + " → " + MktText(VSafe(p, "promoted"))
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

            _mktLuStatus.Text = VStr(VSafe(j, "trade_date")) + "（涨停 " + MktText(VSafe(j, "total")) + " 家）";
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
                    string ind = VStr(VSafe(s, "industry"));
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
        private static void MktApplyFilter(ComboBox cb, List<string> inds, DataGridView g,
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
                string ind = VStr(VSafe(s, "industry"));
                if (!isAll && ind != sel) continue;
                fill(g, s);
                n++;
            }
            MktFit(g);

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
            double? pct = VNum(VSafe(s, "pct"));
            int row = g.Rows.Add(
                VStr(VSafe(s, "code")),
                VStr(VSafe(s, "name")),
                MktText(VSafe(s, "level")) + " 板",
                VStr(VSafe(s, "industry")),
                MktPct(pct),
                VFmt(VNum(VSafe(s, "seal_fund"))) + " 亿",
                VFmt(VNum(VSafe(s, "turnover"))) + "%",
                VStr(VSafe(s, "first_seal")),
                VStr(VSafe(s, "stat")));
            MktColor(g.Rows[row].Cells[4], pct);
        }

        private static void MktBlastRow(DataGridView g, Dictionary<string, object> s)
        {
            double? pct = VNum(VSafe(s, "pct"));
            double? dd = VNum(VSafe(s, "drawdown"));
            int row = g.Rows.Add(
                VStr(VSafe(s, "code")),
                VStr(VSafe(s, "name")),
                MktPct(pct),
                dd != null ? dd.Value.ToString("F2") + "%" : "—",
                VFmt(VNum(VSafe(s, "amplitude"))) + "%",
                MktText(VSafe(s, "blasted_times")),
                VStr(VSafe(s, "industry")));
            MktColor(g.Rows[row].Cells[2], pct);
        }

        private static void MktDownRow(DataGridView g, Dictionary<string, object> s)
        {
            double? pct = VNum(VSafe(s, "pct"));
            int row = g.Rows.Add(
                VStr(VSafe(s, "code")),
                VStr(VSafe(s, "name")),
                MktPct(pct),
                MktText(VSafe(s, "continuous")),
                MktText(VSafe(s, "open_times")),
                VStr(VSafe(s, "industry")));
            MktColor(g.Rows[row].Cells[2], pct);
        }

        private static string MktStructureText(System.Collections.ArrayList structure)
        {
            if (structure == null || structure.Count == 0) return "—";
            var parts = new List<string>();
            foreach (Dictionary<string, object> s in structure)
                parts.Add(MktInt(VSafe(s, "level")) + "板×" + MktInt(VSafe(s, "count")));
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
                _mktBlStatus.Text = VStr(VSafe(j, "trade_date")) + " 未获取到大面股数据";
                _mktBlStatus.ForeColor = Color.FromArgb(208, 57, 59);
                return;
            }

            var blasted = VArr(VSafe(j, "blasted"));
            var limitDown = VArr(VSafe(j, "limit_down"));

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

            _mktBlStatus.Text = VStr(VSafe(j, "trade_date")) + "（炸板 " + (blasted != null ? blasted.Count : 0)
                + " 只 · 跌停 " + (limitDown != null ? limitDown.Count : 0) + " 只）";
        }

        // ---------------- 小工具 ----------------
        private static int MktInt(object o)
        {
            double? v = VNum(o);
            if (v == null) return 0;
            return (int)v.Value;
        }

        /// <summary>盘面接口的涨跌幅本身就是百分数（如 10.02），不能像估值页那样再乘 100。</summary>
        private static string MktPct(double? v)
        {
            if (v == null) return "—";
            return (v.Value > 0 ? "+" : "") + v.Value.ToString("F2") + "%";
        }

        /// <summary>带符号的「亿」金额（对应原网页的 fmtYi）。</summary>
        private static string MktYi(double? v)
        {
            if (v == null) return "—";
            return (v.Value > 0 ? "+" : "") + v.Value.ToString("F2") + " 亿";
        }

        private static string MktText(object o)
        {
            if (o == null) return "—";
            double? v = VNum(o);
            if (v != null) return ((int)v.Value).ToString();
            string s = VStr(o);
            return s == "" ? "—" : s;
        }

        // ================= 自绘控件 =================

        /// <summary>横向条形图（每行：名称 + 条形 + 数值；正值红、负值绿，0 轴按正负极值居中，贴近网页 echarts 的观感）。</summary>
        private class MktBars : Control
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
                SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint
                    | ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true);
                Height = 40;
            }

            public void SetItems(List<Item> items)
            {
                _items.Clear();
                if (items != null) _items.AddRange(items);
                Height = Math.Max(34, _items.Count * RowH + 16);
                Invalidate();
            }

            /// <summary>在 AutoSize 容器（TableLayoutPanel）里，行高按 PreferredSize 计算，
            /// 不重写的话行高会偏小、控件顶部被裁（首行压没）。</summary>
            public override Size GetPreferredSize(Size proposedSize)
            {
                return new Size(proposedSize.Width, Height);
            }

            protected override void OnPaint(PaintEventArgs e)
            {
                base.OnPaint(e);
                var g = e.Graphics;
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
                    using (var br = new SolidBrush(it.Value >= 0 ? Color.FromArgb(239, 83, 80) : Color.FromArgb(63, 185, 80)))
                        g.FillRectangle(br, x, y + 3, len, RowH - 8);
                    g.Clip = old;

                    using (var br = new SolidBrush(ForeColor))
                        g.DrawString(it.Text, Font, br, new RectangleF(Width - ValueW, y, ValueW - 4, RowH - 2), FmtRight);
                    y += RowH;
                }
            }

            private static readonly StringFormat FmtLeft = new StringFormat { Alignment = StringAlignment.Near, LineAlignment = StringAlignment.Center };
            private static readonly StringFormat FmtRight = new StringFormat { Alignment = StringAlignment.Far, LineAlignment = StringAlignment.Center };
        }

        /// <summary>涨跌家数分段条：按占比横向分段着色（涨红 / 平灰 / 跌绿）。</summary>
        private class MktRatioBar : Control
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
                SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint
                    | ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true);
                Height = 28;
            }

            public void SetSegments(List<Seg> segs)
            {
                _segs.Clear();
                if (segs != null) _segs.AddRange(segs);
                Invalidate();
            }

            public override Size GetPreferredSize(Size proposedSize)
            {
                return new Size(proposedSize.Width, Height);
            }

            protected override void OnPaint(PaintEventArgs e)
            {
                base.OnPaint(e);
                var g = e.Graphics;
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

            private static readonly StringFormat FmtCenter = new StringFormat { Alignment = StringAlignment.Center, LineAlignment = StringAlignment.Center };
        }
    }
}
