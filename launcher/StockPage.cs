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
        private Label _stockName, _stockHint, _stockStatus, _stockValStatus;
        private FlowLayoutPanel _stockKpi;                 // 在榜统计 KPI
        private KLineChart _stockKline;
        private int _stockRange = 250;                     // 0 = 全部
        private string _stockAdjust = "qfq";               // qfq 前复权 / raw 不复权
        private readonly Dictionary<Button, string> _stockAdjustMap = new Dictionary<Button, string>();
        private DataGridView _stockTimeline;                // 入池 / 出池记录
        private TableLayoutPanel _stockEvents;              // 消息面时间轴
        private System.Collections.ArrayList _stockEventData = new System.Collections.ArrayList();
        private FlowLayoutPanel _stockValKpi;               // 估值 KPI
        private DataGridView _stockValGrid;                 // 估值情景表
        private Label _stockSaoleiStatus;                   // 扫雷状态行
        private TableLayoutPanel _stockSaoleiList;          // 扫雷 · 风险清单 + 个股亮点
        private string _stockCurrent = null;
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

        // 历史查看记录（最多 10 条，先进先出滚动覆盖；持久化到 stockanaly-data/history_stock_view.json）
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
            var head = Stack();
            var title = Lbl("📊 个股分析");
            title.Font = new Font("Microsoft YaHei UI", 14f, FontStyle.Bold);
            title.Margin = new Padding(0, 0, 0, 2);
            AddRow(head, Row(title));

            var sub = Mute(Lbl("前复权/不复权日K（MA5/10/20/60）+ 成交量红绿 + 入池出池轨迹 + 消息面时间轴｜数据来源：GET /api/history/stock/{code}｜K线页：滚轮缩放 · 拖动平移"));
            sub.AutoSize = false;
            sub.Width = 760;
            sub.Height = 20;
            AddRow(head, Row(sub));
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

            _stockHint = Mute(Lbl("输入 6 位代码后点「打开」，或从「筹码体系 / SCR 选股」进入；数据来自本机后端 /api/history/stock/{code}"));
            _stockHint.AutoSize = true;
            _stockHint.MaximumSize = new Size(740, 0);
            AddRow(b0, Row(_stockHint));

            // 范围按钮 + 刷新 + 状态
            var rangeLabels = new string[] { "近6月", "近1年", "全部" };
            var rangeVals = new int[] { 120, 250, 0 };
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
                    _stockRange = captured;
                    StockSetRangeActive();
                    if (_stockKline != null) _stockKline.SetRange(_stockRange);
                };
                _stockRangeMap[btn] = r;
            }
            _stockRefresh = MiniBtn("↻ 更新行情/消息", delegate { if (_stockCurrent != null) StockLoadHistory(_stockCurrent, true); }, 140);
            _stockStatus = Mute(Lbl("待加载"));

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
                    if (_stockKline != null) _stockKline.SetAdjust(_stockAdjust);
                };
                _stockAdjustMap[btn] = a;
            }

            // 范围 + 复权 + 刷新同处一行（复权按钮位于范围之后）
            var rangeRow = Row(Mute(Lbl("范围")));
            foreach (Button b in _stockRangeMap.Keys) rangeRow.Controls.Add(b);
            rangeRow.Controls.Add(Mute(Lbl("复权")));
            foreach (Button b in _stockAdjustMap.Keys) rangeRow.Controls.Add(b);
            rangeRow.Controls.Add(_stockRefresh);
            rangeRow.Controls.Add(_stockStatus);
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
            kLayout.ColumnCount = 1;
            kLayout.RowCount = 2;
            kLayout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100f));
            kLayout.RowStyles.Add(new RowStyle(SizeType.AutoSize));       // 概况 + 同步状态
            kLayout.RowStyles.Add(new RowStyle(SizeType.Percent, 100f));  // 图表撑满
            klinePage.Controls.Add(kLayout);
            var kTop = Stack();
            TableLayoutPanel b1;
            var g1 = Group("个股概况（入池出池轨迹）", out b1);
            _stockKpi = VKpiRow();
            AddRow(b1, _stockKpi);
            AddRow(kTop, g1);

            // 「更新至 …」栏已按需求移除；kTop 只保留概况，下方 K 线区域自动加高。

            _stockKline = new KLineChart();
            _stockKline.Dock = DockStyle.Fill;
            _stockKline.Tag = "kline";
            _stockKline.TabStop = true;
            _stockKline.OnRangeChanged = delegate (int r) { _stockRange = r; StockSetRangeActive(); };
            _stockKline.BackColorChanged += delegate { _stockKline.Invalidate(); };
            kLayout.Controls.Add(kTop, 0, 0);              // 概况 + 同步状态
            kLayout.Controls.Add(_stockKline, 0, 1);       // 图表占满剩余高度
            StockAddSubTab("K线", klinePage);

            // ---- 二级页 ② 记录 · 消息 ----
            var recPage = new Panel();
            recPage.AutoScroll = true;
            var recStack = Stack();
            TableLayoutPanel b3;
            var g3 = Group("入池 / 出池记录", out b3);
            _stockTimeline = MktGrid(180, true, new string[] { "入池日期", "出池日期", "在榜天数", "状态" });
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
            _stockValGrid = MktGrid(150, false, new string[] { "情景", "增长率", "每股价值", "股价/价值", "判断", "预测价", "收益率" });
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

            StockSetRangeActive();
            StockSetAdjustActive();

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
                _stockHint.ForeColor = Color.FromArgb(208, 57, 59);
                _stockCode.Focus();
                return;
            }
            if ((_stockHint.Tag as string) != "bad")
            {
                _stockHint.Tag = "muted";
                _stockHint.ForeColor = Color.FromArgb(150, 158, 172);
                _stockHint.Text = "数据加载中…";
            }
            _stockCurrent = code;
            StockAddHistory(code, null);   // 记录查看历史（名称稍后补全）
            StockLoadName(code);
            StockLoadHistory(code, false);
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
                        object okv;
                        bool ok = (j != null && j.TryGetValue("ok", out okv) && okv is bool && (bool)okv);
                        if (ok && j.ContainsKey("name"))
                        {
                            double? pr = VNum(VSafe(j, "price"));
                            string nm = VStr(VSafe(j, "name"));
                            _stockName.Text = nm + (pr != null ? "  " + pr.Value.ToString("F2") + " 元" : "");
                            StockAddHistory(code, nm);   // 补全历史记录中的名称
                        }
                        else
                        {
                            _stockName.Text = VStr(VSafe(j, "error"));
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
                    string url = "http://127.0.0.1:8000/api/history/stock/" + code + "?refresh=" + (refresh ? "true" : "false");
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
            _stockStatus.ForeColor = Color.FromArgb(208, 57, 59);
            _stockKline.SetData(new List<KBar>(), new List<KBar>(), new List<KMark>());
        }

        private void StockRenderHistory(Dictionary<string, object> j)
        {
            object okv;
            bool ok = (j != null && j.TryGetValue("ok", out okv) && okv is bool && (bool)okv);
            if (!ok)
            {
                string emsg = VStr(VSafe(j, "detail"));
                if (emsg == "") emsg = VStr(VSafe(j, "error"));
                if (emsg == "") emsg = "加载失败";
                StockFail(emsg);
                return;
            }

            // K 线（前复权）
            var bars = new List<KBar>();
            var arr = VArr(VSafe(j, "bars"));
            if (arr != null)
            {
                foreach (Dictionary<string, object> d in arr)
                {
                    var b = new KBar();
                    b.Date = VStr(VSafe(d, "trade_date"));
                    b.O = VNum(VSafe(d, "open")) ?? 0;
                    b.C = VNum(VSafe(d, "close")) ?? 0;
                    b.L = VNum(VSafe(d, "low")) ?? 0;
                    b.H = VNum(VSafe(d, "high")) ?? 0;
                    b.V = VNum(VSafe(d, "volume")) ?? 0;
                    bars.Add(b);
                }
            }

            // K 线（不复权原始价）
            var barsRaw = new List<KBar>();
            var arrRaw = VArr(VSafe(j, "bars_raw"));
            if (arrRaw != null)
            {
                foreach (Dictionary<string, object> d in arrRaw)
                {
                    var b = new KBar();
                    b.Date = VStr(VSafe(d, "trade_date"));
                    b.O = VNum(VSafe(d, "open")) ?? 0;
                    b.C = VNum(VSafe(d, "close")) ?? 0;
                    b.L = VNum(VSafe(d, "low")) ?? 0;
                    b.H = VNum(VSafe(d, "high")) ?? 0;
                    b.V = VNum(VSafe(d, "volume")) ?? 0;
                    barsRaw.Add(b);
                }
            }

            // 入池/出池轨迹
            var pool = VMap(VSafe(j, "pool"));
            var spans = VArr(pool != null ? VSafe(pool, "spans") : null);
            var spanList = new List<Dictionary<string, object>>();
            if (spans != null) foreach (Dictionary<string, object> s in spans) spanList.Add(s);

            // 消息面
            var events = VArr(VSafe(j, "events"));

            // 标记（入池/出池/事件）只取落在 K 线上的日期
            var dateSet = new HashSet<string>();
            foreach (KBar b in bars) dateSet.Add(b.Date);
            var marks = new List<KMark>();
            foreach (Dictionary<string, object> s in spanList)
            {
                string st = VStr(VSafe(s, "start"));
                if (dateSet.Contains(st))
                    marks.Add(new KMark { Date = st, Kind = "in", Color = Color.FromArgb(239, 83, 80), Text = "入" });
                object openv = VSafe(s, "open");
                bool open = (openv is bool) && (bool)openv;
                if (!open)
                {
                    string en = VStr(VSafe(s, "end"));
                    if (dateSet.Contains(en))
                        marks.Add(new KMark { Date = en, Kind = "out", Color = Color.FromArgb(125, 124, 120), Text = "出" });
                }
            }
            if (events != null)
            {
                foreach (Dictionary<string, object> ev in events)
                {
                    string dt = VStr(VSafe(ev, "published_at"));
                    if (!dateSet.Contains(dt)) continue;
                    string kind = VStr(VSafe(ev, "kind"));
                    Color c = kind == "announcement" ? Color.FromArgb(201, 133, 0) : Color.FromArgb(57, 135, 229);
                    marks.Add(new KMark { Date = dt, Kind = "event", Color = c, Text = kind == "announcement" ? "告" : "闻" });
                }
            }

            _stockKline.SetData(bars, barsRaw, marks);
            StockRenderStats(spanList);
            StockRenderTimeline(spanList);
            StockRenderEvents(events);

            // 状态：「更新至 …」栏与「前复权日K · …」统计文字已按需求移除，
            // 仅当有同步失败时在状态位提示，否则清空。
            var sync = VMap(VSafe(j, "sync"));
            var syncErrs = VArr(sync != null ? VSafe(sync, "errors") : null);
            string errs = (syncErrs != null && syncErrs.Count > 0) ? "；部分失败：" + JoinErrs(syncErrs) : "";
            if (errs.Length > 0)
            {
                _stockStatus.Text = "部分数据同步失败" + errs;
                _stockStatus.Tag = "muted";
                _stockStatus.ForeColor = Color.FromArgb(150, 158, 172);
            }
            else
            {
                _stockStatus.Text = "";
            }
        }

        private static string JoinErrs(System.Collections.ArrayList list)
        {
            var parts = new List<string>();
            foreach (object o in list) parts.Add(VStr(o));
            return string.Join("；", parts.ToArray());
        }

        private void StockRenderStats(List<Dictionary<string, object>> spans)
        {
            _stockKpi.Controls.Clear();
            int totalDays = 0;
            int curStreak = 0;
            string first = "—";
            if (spans.Count > 0)
            {
                foreach (Dictionary<string, object> s in spans) totalDays += MktInt(VSafe(s, "days"));
                Dictionary<string, object> last = spans[spans.Count - 1];
                object openv = VSafe(last, "open");
                bool open = (openv is bool) && (bool)openv;
                curStreak = open ? MktInt(VSafe(last, "days")) : 0;
                first = VStr(VSafe(spans[0], "start"));
            }
            AddKpi(_stockKpi, "当前连续在榜", curStreak > 0 ? curStreak + " 天" : "已出池", curStreak > 0 ? "仍在榜" : "已离榜");
            AddKpi(_stockKpi, "累计上榜", totalDays + " 天", "数据日累计");
            AddKpi(_stockKpi, "入池次数", spans.Count.ToString(), "历史合计");
            AddKpi(_stockKpi, "首次入池日期", first, spans.Count > 0 ? "最早记录" : "暂无记录");
        }

        private void StockRenderTimeline(List<Dictionary<string, object>> spans)
        {
            _stockTimeline.Rows.Clear();
            if (spans.Count == 0)
            {
                _stockTimeline.Rows.Add("—", "—", "—", "暂无记录");
                MktFit(_stockTimeline);
                return;
            }
            // 倒序：最近的在前面
            for (int i = spans.Count - 1; i >= 0; i--)
            {
                Dictionary<string, object> s = spans[i];
                object openv = VSafe(s, "open");
                bool open = (openv is bool) && (bool)openv;
                string end = open ? "至今" : VStr(VSafe(s, "end"));
                int days = MktInt(VSafe(s, "days"));
                int row = _stockTimeline.Rows.Add(
                    VStr(VSafe(s, "start")), end, days + " 天", open ? "在榜" : "已出池");
                _stockTimeline.Rows[row].Cells[2].Style.ForeColor = days >= 7 ? Color.FromArgb(239, 83, 80)
                    : (days >= 3 ? Color.FromArgb(233, 154, 53) : Color.FromArgb(150, 158, 172));
                _stockTimeline.Rows[row].Cells[3].Style.ForeColor = open ? Color.FromArgb(239, 83, 80) : Color.FromArgb(150, 158, 172);
            }
            MktFit(_stockTimeline);
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

            string kind = VStr(VSafe(ev, "kind"));
            string meta = VStr(VSafe(ev, "published_at")) + " · "
                + (kind == "announcement" ? "公告" : "新闻") + " · " + VStr(VSafe(ev, "source"));
            var m = Mute(Lbl(meta));
            m.Dock = DockStyle.Top;
            m.Margin = new Padding(0, 0, 0, 2);
            panel.Controls.Add(m);

            string url = VStr(VSafe(ev, "source_url"));
            string title = VStr(VSafe(ev, "title"));
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

            string summary = VStr(VSafe(ev, "summary"));
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
            _stockSaoleiStatus.ForeColor = Color.FromArgb(150, 158, 172);
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

            int total = VIntOf(VSafe(j, "total"));
            int risk = VIntOf(VSafe(j, "risk"));
            int safe = VIntOf(VSafe(j, "safe"));
            string date = VStr(VSafe(j, "date"));
            var cats = VArr(VSafe(j, "categories"));

            _stockSaoleiStatus.Text = "总检查 " + total + " 项 · 风险项 " + risk + " 项 · 安全项 " + safe + " 项"
                + (date != "" ? "（数据日期 " + date + "）" : "");
            _stockSaoleiStatus.Tag = "muted";
            _stockSaoleiStatus.ForeColor = risk > 0
                ? Color.FromArgb(208, 57, 59)
                : Color.FromArgb(150, 158, 172);

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
                    var cat = VMap(cats[c]);
                    if (cat == null) continue;
                    var blk = SaoleiCategory(VStr(VSafe(cat, "name")), VArr(VSafe(cat, "items")));
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
            var arr = VArr(VSafe(j, "highlights"));
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
                var ev = VMap(arr[i]);
                if (ev == null) continue;
                string name = VStr(VSafe(ev, "name"));
                string desc = VStr(VSafe(ev, "desc"));

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
                    var m = VMap(o);
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
                    bool trig = VStr(VSafe(ok[i], "trig")) == "1";
                    var nm = Lbl(VStr(VSafe(ok[i], "name")));
                    nm.Margin = new Padding(0, 0, 12, 3);
                    var st = Lbl(trig ? "有" : "无");
                    st.Margin = new Padding(0, 0, 0, 3);
                    if (trig)
                    {
                        st.ForeColor = Color.FromArgb(208, 57, 59);
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
            return int.TryParse(VStr(o), out v) ? v : 0;
        }

        private void StockSaoleiFail(string msg)
        {
            _stockSaoleiList.Controls.Clear();
            _stockSaoleiStatus.Text = msg;
            _stockSaoleiStatus.Tag = "bad";
            _stockSaoleiStatus.ForeColor = Color.FromArgb(208, 57, 59);
        }

        // ---- 估值（内联）----
        private void StockLoadValuation(string code)
        {
            _stockValStatus.Text = "计算中…";
            _stockValStatus.Tag = "muted";
            _stockValStatus.ForeColor = Color.FromArgb(150, 158, 172);
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
                            _stockValStatus.Text = "计算失败";
                            _stockValStatus.ForeColor = Color.FromArgb(208, 57, 59);
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

            object okv;
            bool ok = (j != null && j.TryGetValue("ok", out okv) && okv is bool && (bool)okv);
            if (!ok)
            {
                string emsg = VStr(VSafe(j, "error"));
                if (emsg == "") emsg = VStr(VSafe(j, "detail"));
                if (emsg == "") emsg = "计算失败";
                _stockValStatus.Text = emsg;
                _stockValStatus.ForeColor = Color.FromArgb(208, 57, 59);
                return;
            }

            var m = VMap(VSafe(j, "market"));
            var f = VMap(VSafe(j, "finance"));
            var g = VMap(VSafe(j, "growth"));
            var a = VMap(VSafe(j, "assumptions"));

            AddKpi(_stockValKpi, "股票", VStr(VSafe(j, "name")), VStr(VSafe(j, "code")));
            AddKpi(_stockValKpi, "当前股价", VFmt(VNum(VSafe(m, "price"))) + " 元", VStr(VSafe(m, "price_source")));
            AddKpi(_stockValKpi, "期初净利润", VFmt(VNum(VSafe(f, "net_profit_base"))) + " 亿", VStr(VSafe(f, "indicator")));
            double? cagr = VNum(VSafe(g, "cagr"));
            object cons;
            bool fromC = (g != null && g.TryGetValue("from_consensus", out cons) && cons is bool && (bool)cons);
            AddKpi(_stockValKpi, "复合增长率", cagr != null ? VFmtPct(cagr) : "走兜底",
                fromC ? "机构预测" : "按模板兜底");
            AddKpi(_stockValKpi, "贴现率", VRate(VNum(VSafe(a, "discount_rate"))), "两段法 DCF");

            var scenarios = VArr(VSafe(j, "scenarios"));
            if (scenarios != null)
            {
                foreach (Dictionary<string, object> s in scenarios)
                {
                    string verdict = VStr(VSafe(s, "verdict"));
                    string cls = (verdict == "低估" || verdict == "高估") ? verdict : "合理";
                    double? rr = VNum(VSafe(s, "return_rate"));
                    int row = _stockValGrid.Rows.Add(
                        VStr(VSafe(s, "label")),
                        VFmtPct(VNum(VSafe(s, "growth"))),
                        VFmt(VNum(VSafe(s, "value_per_share"))) + " 元",
                        VFmt(VNum(VSafe(s, "undervalued_ratio")), 4),
                        cls,
                        VFmt(VNum(VSafe(s, "target_price"))) + " 元",
                        VFmtPct(rr));
                    _stockValGrid.Rows[row].Cells[4].Style.ForeColor = verdict == "低估" ? Color.FromArgb(63, 185, 80)
                        : (verdict == "高估" ? Color.FromArgb(239, 83, 80) : Color.FromArgb(150, 158, 172));
                    if (rr != null)
                        _stockValGrid.Rows[row].Cells[6].Style.ForeColor = rr.Value > 0 ? Color.FromArgb(239, 83, 80) : (rr.Value < 0 ? Color.FromArgb(63, 185, 80) : Color.FromArgb(150, 158, 172));
                }
            }
            MktFit(_stockValGrid);

            _stockValStatus.Text = VStr(VSafe(j, "name")) + "（" + VStr(VSafe(j, "code")) + "）已计算";
            _stockValStatus.Tag = "muted";
            _stockValStatus.ForeColor = Color.FromArgb(30, 126, 52);
        }

        // ---- AI 分析（调 /api/stock/research，返回 AI 生成的 markdown）----
        private void StockLoadAi(string code, bool force = false)
        {
            if (_stockAiStatus != null)
            {
                _stockAiStatus.Text = force ? "AI 强制重新分析中…" : "AI 分析中…";
                _stockAiStatus.Tag = "muted";
                _stockAiStatus.ForeColor = Color.FromArgb(150, 158, 172);
            }
            _stockAiMarkdown = "";
            if (_stockAiBox != null) FillAiDoc(_stockAiBox);
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
                                _stockAiStatus.Text = "分析失败（网络/超时，确认后端已启动且配置了 LLM）";
                                _stockAiStatus.ForeColor = Color.FromArgb(208, 57, 59);
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
            object okv;
            bool ok = (j != null && j.TryGetValue("ok", out okv) && okv is bool && (bool)okv);
            if (!ok)
            {
                string emsg = VStr(VSafe(j, "error"));
                if (emsg == "") emsg = VStr(VSafe(j, "detail"));
                if (emsg == "") emsg = "分析失败";
                _stockAiStatus.Text = emsg;
                _stockAiStatus.ForeColor = Color.FromArgb(208, 57, 59);
                _stockAiMarkdown = "";
                FillAiDoc(_stockAiBox);
                return;
            }
            string md = VStr(VSafe(j, "markdown"));
            _stockAiMarkdown = md;
            object cached;
            bool isCached = (j.TryGetValue("cached", out cached) && cached is bool && (bool)cached);
            string asOf = VStr(VSafe(j, "data_as_of"));
            string d = VStr(VSafe(j, "depth"));
            string h = VStr(VSafe(j, "horizon"));
            var fobj = VSafe(j, "focus") as System.Collections.ArrayList;
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
            _stockAiStatus.ForeColor = Color.FromArgb(30, 126, 52);
            FillAiDoc(_stockAiBox);
        }

        /// <summary>把 AI 调研的 markdown 渲染进只读框；换肤时由 Skin 再调一次，用新配色重排。</summary>
        private void FillAiDoc(RichTextBox rt)
        {
            if (_docFont == null) _docFont = new Font("Microsoft YaHei UI", 9.5f);
            if (_docBold == null) _docBold = new Font(_docFont, FontStyle.Bold);
            var normal = _docFont;

            rt.Clear();
            rt.BackColor = _cPanel;
            rt.ForeColor = _cText;
            rt.SelectionFont = normal;
            rt.SelectionColor = _cText;

            string md = _stockAiMarkdown ?? "";
            if (md.Trim() == "")
            {
                rt.SelectionColor = _cSub;
                rt.AppendText("（暂无 AI 分析。打开个股后将自动调用 /api/stock/research 生成；需后端已配置 LLM_API_KEY。）");
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
                bool act = kv.Value == _stockRange;
                kv.Key.BackColor = act ? Color.FromArgb(64, 120, 192) : _cPanel;
                kv.Key.ForeColor = act ? Color.White : _cSub;
                kv.Key.Invalidate();
            }
        }

        private void StockSetAdjustActive()
        {
            foreach (KeyValuePair<Button, string> kv in _stockAdjustMap)
            {
                bool act = kv.Value == _stockAdjust;
                kv.Key.BackColor = act ? Color.FromArgb(64, 120, 192) : _cPanel;
                kv.Key.ForeColor = act ? Color.White : _cSub;
                kv.Key.Invalidate();
            }
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
                btn.BackColor = cur ? Color.FromArgb(64, 120, 192) : _cPanel;
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

        private string ResolveDataDir()
        {
            string env = Environment.GetEnvironmentVariable("STOCK_DATA_DIR");
            if (!string.IsNullOrEmpty(env)) return env.Trim();
            env = Environment.GetEnvironmentVariable("DATA_DIR");
            if (!string.IsNullOrEmpty(env)) return env.Trim();
            // 默认：程序所在目录的「上一级」下的 stockanaly-data（与后端 config.py 的 DEFAULT_DATA_DIR 一致）
            string exeDir = Path.GetDirectoryName(Application.ExecutablePath);
            if (string.IsNullOrEmpty(exeDir)) exeDir = ".";
            DirectoryInfo di = Directory.GetParent(exeDir);
            string parent = (di != null && !string.IsNullOrEmpty(di.FullName)) ? di.FullName : exeDir;
            return Path.Combine(parent, "stockanaly-data");
        }

        private void StockLoadHistoryFile()
        {
            _stockHistory = new List<StockHistoryItem>();
            try
            {
                string dir = ResolveDataDir();
                _stockHistoryPath = Path.Combine(dir, "history_stock_view.json");
                if (File.Exists(_stockHistoryPath))
                {
                    string txt = File.ReadAllText(_stockHistoryPath, Encoding.UTF8);
                    var arr = new JavaScriptSerializer().Deserialize<System.Collections.ArrayList>(txt);
                    if (arr != null)
                    {
                        foreach (Dictionary<string, object> d in arr)
                        {
                            var it = new StockHistoryItem();
                            it.Code = VStr(VSafe(d, "code"));
                            it.Name = VStr(VSafe(d, "name"));
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

        private sealed class KLineChart : Control
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
            private int _range = 250;                          // 可见 K 线根数，0 = 全部
            private int _offset = 0;                           // 平移：从最新端向左偏移的根数
            private bool _dragging = false;
            private int _dragStartX = 0;
            private int _dragStartOffset = 0;
            private readonly ToolTip _tip = new ToolTip();
            private int _hoverIndex = -1;
            public Action<int> OnRangeChanged;                 // 缩放改变范围时通知外部刷新高亮

            private static readonly int[] MaPeriods = new int[] { 5, 10, 20, 60 };
            private static readonly Color[] MaColors = new Color[] {
                Color.FromArgb(240, 160, 60), Color.FromArgb(57, 135, 229),
                Color.FromArgb(213, 81, 129), Color.FromArgb(144, 133, 233) };

            public KLineChart()
            {
                SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint
                    | ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true);
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

            public override Size GetPreferredSize(Size proposedSize)
            {
                return new Size(proposedSize.Width, Height);
            }

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
                if (!this.TabStop || this.Focused) return;
                // 聚焦图表以便接收滚轮（缩放）；但外层 AutoScroll 容器会顺手把图表
                // 滚入视口，导致上方查询区被顶出屏幕——聚焦后把滚动位置还原回去。
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
                var vis = VisibleBars();
                if (idx < vis.Count)
                {
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
                    _tip.Show(sb.ToString(), this, e.X, e.Y + 16);
                }
            }

            protected override void OnMouseWheel(MouseEventArgs e)
            {
                base.OnMouseWheel(e);
                int total = _bars.Count;
                if (total == 0) return;
                int cur = _range > 0 ? _range : total;
                int step = e.Delta > 0 ? -20 : 20;   // 上滚放大（更少根），下滚缩小
                int next = Math.Max(20, Math.Min(total, cur + step));
                int newRange = (next >= total) ? 0 : next;
                _range = newRange;
                _offset = Math.Max(0, Math.Min(_offset, total - VisibleCount()));
                Invalidate();
                if (OnRangeChanged != null) OnRangeChanged(_range);
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
                g.SmoothingMode = SmoothingMode.AntiAlias;

                bool dark = (BackColor.R + BackColor.G + BackColor.B) < 200;
                Color grid = dark ? Color.FromArgb(54, 58, 68) : Color.FromArgb(222, 224, 228);
                Color text = ForeColor;

                using (var bg = new SolidBrush(BackColor))
                    g.FillRectangle(bg, 0, 0, Width, Height);

                var vis = VisibleBars();
                if (vis.Count == 0)
                {
                    DrawLegend(g, text, -1);
                    using (var b = new SolidBrush(text))
                    using (var fmt = new StringFormat { Alignment = StringAlignment.Center, LineAlignment = StringAlignment.Center })
                    {
                        var area = new RectangleF(0, 24, Width, Height - 24);
                        g.DrawString("请输入股票代码开始分析", Font, b, area, fmt);
                    }
                    return;
                }

                int n = vis.Count;
                int plotW = Math.Max(20, Width - LeftPad - RightPad);
                int totalH = Height - TopPad - BottomPad;
                int mainH = (int)(totalH * (100 - VolRatio) / 100.0);
                int volH = (int)(totalH * VolRatio / 100.0);
                int plotTop = TopPad;
                int plotBottom = plotTop + mainH;
                int volTop = plotBottom + 8;
                int volBottom = volTop + volH;

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
                    Color c = up ? Color.FromArgb(239, 83, 80) : Color.FromArgb(63, 185, 80);
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

                // 成交量
                for (int i = 0; i < vis.Count; i++)
                {
                    KBar b = vis[i];
                    int x = (int)xOf(i);
                    bool up = b.C >= b.O;
                    Color c = up ? Color.FromArgb(239, 83, 80) : Color.FromArgb(63, 185, 80);
                    using (var br = new SolidBrush(c))
                    {
                        int h = (int)(b.V / maxVol * (volBottom - volTop));
                        if (h < 1) h = 1;
                        g.FillRectangle(br, (int)(x - bodyW / 2), volBottom - h, (int)bodyW, h);
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
                    using (var pen = new Pen(Color.FromArgb(150, 158, 172)))
                        g.DrawLine(pen, x, plotTop, x, volBottom);
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
                            var fmt = new StringFormat { Alignment = StringAlignment.Center };
                            g.DrawString(d, Font, b3, new RectangleF((float)(x - slot / 2), (float)(volBottom + 2), (float)slot, (float)BottomPad), fmt);
                        }
                    }
                }

                DrawLegend(g, text, 0);

                // 缩放 / 平移提示（右下角，淡色）
                Color hint = dark ? Color.FromArgb(120, 128, 140) : Color.FromArgb(150, 156, 168);
                using (var hb = new SolidBrush(hint))
                using (var fmt = new StringFormat { Alignment = StringAlignment.Far })
                {
                    g.DrawString("滚轮缩放 · 拖动平移 · " + (_adjust == "raw" ? "不复权" : "前复权"),
                        new Font("Microsoft YaHei UI", 8.5f), hb,
                        new RectangleF(0, volBottom + 1, Width - 4, BottomPad), fmt);
                }
            }

            private void DrawLegend(Graphics g, Color text, int dummy)
            {
                int x = LeftPad + 4;
                int y = 4;
                using (var b = new SolidBrush(text))
                {
                    g.DrawString("日K", Font, b, x, y); x += 34;
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
