using System;
using System.Collections.Generic;
using System.Drawing;
using System.Windows.Forms;
using System.Web.Script.Serialization;

namespace StockPool
{
    /// <summary>
    /// 策略回测（原生内嵌标签页）：MainForm 的拆分文件。
    /// 数据来自本机后端 /api/strategies（清单 / 代码 / 回测）。
    /// 页面结构：顶层一个「策略回测」标签，内部三个二级页 ——
    ///   ① 策略选择与编辑（选策略 + 按参数规格动态生成编辑表单）
    ///   ② 回测范围（股票池 / 时间区间 / 资金费用 / 基准）
    ///   ③ 回测结果（抬头简述策略与范围 + 指标卡 + 净值曲线 + 个股明细）
    /// 策略本身在后端 backend_fastapi/strategies/ 以「文件名即 id」的文件夹形式扩展，
    /// 本页只消费其标准化清单与回测结果，不写死任何策略算法。
    /// </summary>
    internal sealed partial class MainForm
    {
        // ---- 策略回测页 ----
        private int _stTabIndex = -1;
        private bool _stLoaded = false;

        // 二级导航
        private FlowLayoutPanel _stSubBar;
        private Panel _stSubBody;
        private readonly List<Button> _stSubBtns = new List<Button>();
        private readonly List<Panel> _stSubPages = new List<Panel>();
        private int _stSubIndex;

        // ① 策略选择与编辑
        private List<Dictionary<string, object>> _stStrategies;
        private ListBox _stList;
        private Label _stDesc;
        private FlowLayoutPanel _stParamPanel;
        private readonly List<StParamEditor> _stParamEditors = new List<StParamEditor>();

        // ② 回测范围
        private RadioButton _stAll, _stCustom;
        private TextBox _stCodes, _stComm, _stBench;
        private Label _stCodeHint;
        private DateTimePicker _stStart, _stEnd;
        private NumericUpDown _stCapital;
        private CheckBox _stBenchChk;
        private int _stLocalCodeCount;

        // ③ 回测结果
        private Label _stResultHeader, _stResultStatus;
        private FlowLayoutPanel _stMetricCards;
        private PictureBox _stEquityBox;
        private DataGridView _stPerStockGrid;
        private List<Dictionary<string, object>> _stPerStockData;   // 原始 per_stock（展开渲染用）
        private readonly HashSet<string> _stExpanded = new HashSet<string>();   // 已展开的代码
        private readonly Dictionary<string, System.Collections.IList> _stDetailCache =
            new Dictionary<string, System.Collections.IList>();     // 已拉取的逐笔明细（code → trades）
        private string _stResultId;                                 // 后端回测上下文句柄（明细按需拉取）
        private List<string> _stEqDates;
        private List<double?> _stEq;
        private List<double?> _stEqBench;

        private sealed class StParamEditor
        {
            public string Name;
            public string Type;
            public Control Ctrl;
            public object Read()
            {
                NumericUpDown nud = Ctrl as NumericUpDown;
                if (nud != null)
                    return nud.DecimalPlaces == 0 ? (object)(int)nud.Value : (object)(double)nud.Value;
                ComboBox cb = Ctrl as ComboBox;
                if (cb != null) return cb.SelectedItem == null ? "" : cb.SelectedItem.ToString();
                TextBox tb = Ctrl as TextBox;
                if (tb != null) return tb.Text.Trim();
                return null;
            }
        }

        // ---------------- 顶层页 ----------------
        private Panel BuildStrategyPage()
        {
            var p = NewPage("策略回测");
            _stTabIndex = _tabPages.Count - 1;
            p.AutoScroll = false;

            var root = new TableLayoutPanel();
            root.Dock = DockStyle.Fill;
            root.Margin = new Padding(0);
            root.Padding = new Padding(16, 8, 16, 12);
            root.ColumnCount = 1;
            root.RowCount = 3;
            root.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100f));
            root.RowStyles.Add(new RowStyle(SizeType.AutoSize));      // 操作区
            root.RowStyles.Add(new RowStyle(SizeType.AutoSize));      // 二级标签栏
            root.RowStyles.Add(new RowStyle(SizeType.Percent, 100f)); // 二级页内容（自己滚动）
            p.Controls.Add(root);

            var head = Stack();
            TableLayoutPanel b0;
            var g0 = Group("操作", out b0);
            _stResultStatus = Mute(Lbl("进入本页自动加载策略清单"));
            AddRow(b0, Row(Lbl("策略回测"), MiniBtn("刷新清单", delegate { StLoadStrategies(); }, 90), _stResultStatus));
            AddRow(head, g0);
            g0.Margin = new Padding(0, 0, 0, 4);
            root.Controls.Add(head, 0, 0);

            _stSubBar = new FlowLayoutPanel();
            _stSubBar.Dock = DockStyle.Top;
            _stSubBar.Height = 30;
            _stSubBar.FlowDirection = FlowDirection.LeftToRight;
            _stSubBar.WrapContents = false;
            _stSubBar.Margin = new Padding(0, 0, 0, 2);
            _stSubBar.Padding = new Padding(0);
            root.Controls.Add(_stSubBar, 0, 1);

            _stSubBody = new Panel();
            _stSubBody.Dock = DockStyle.Fill;
            _stSubBody.Margin = new Padding(0);
            _stSubBody.AutoScroll = true;
            root.Controls.Add(_stSubBody, 0, 2);

            StAddSubPage("策略选择与编辑", StBuildSelect);
            StAddSubPage("回测范围", StBuildScope);
            StAddSubPage("回测结果", StBuildResult);

            StSubSelect(0);
            return p;
        }

        private void StAddSubPage(string title, Action<TableLayoutPanel> build)
        {
            int idx = _stSubPages.Count;

            var b = new Button();
            b.Text = title;
            b.Tag = "mkt-subtab";
            b.FlatStyle = FlatStyle.Flat;
            b.FlatAppearance.BorderSize = 0;
            b.Font = new Font("Microsoft YaHei UI", 9f);
            b.TabStop = false;
            b.Margin = new Padding(0, 0, 2, 0);
            b.Click += delegate { StSubSelect(idx); };
            _stSubBtns.Add(b);
            _stSubBar.Controls.Add(b);

            var page = new Panel();
            page.Dock = DockStyle.Fill;
            page.AutoScroll = true;
            page.Tag = "tabpage";
            var stack = Stack();
            build(stack);
            page.Controls.Add(stack);
            _stSubPages.Add(page);
        }

        private void StSubSelect(int index)
        {
            if (index < 0 || index >= _stSubPages.Count) return;
            _stSubIndex = index;
            _stSubBody.Controls.Clear();
            var page = _stSubPages[index];
            page.Visible = true;
            page.Dock = DockStyle.Fill;
            _stSubBody.Controls.Add(page);
            Skin(page);
            page.Invalidate(true);
            StSkinSubTabs();
        }

        private void StSkinSubTabs()
        {
            if (_stSubBtns == null) return;
            for (int i = 0; i < _stSubBtns.Count; i++)
            {
                bool sel = (i == _stSubIndex);
                _stSubBtns[i].BackColor = sel ? _cPanel : _cBg;
                _stSubBtns[i].ForeColor = sel ? _cText : _cSub;
                _stSubBtns[i].Invalidate();
            }
        }

        private void StOnEnter()
        {
            if (!_stLoaded) StLoadStrategies();
            if (_stSubBody != null)
            {
                StSubSelect(_stSubIndex);
                _stSubBody.PerformLayout();
                _stSubBody.Invalidate(true);
            }
        }

        // ---------------- ① 策略选择与编辑 ----------------
        private void StBuildSelect(TableLayoutPanel stack)
        {
            TableLayoutPanel b;
            var g1 = Group("策略列表", out b);
            _stList = new ListBox();
            _stList.Width = 320;
            _stList.Height = 160;
            _stList.Font = new Font("Microsoft YaHei UI", 10f);
            _stList.SelectedIndexChanged += delegate { StOnSelectStrategy(); };
            AddRow(b, Row(_stList,
                Mute(Lbl("选一个策略；右侧可编辑其参数。新增策略请在后端 strategies/ 加 <id>.py 文件。"))));
            AddRow(stack, g1);

            var g2 = Group("策略说明与参数", out b);
            _stDesc = Lbl("—");
            _stDesc.MaximumSize = new Size(720, 0);
            _stDesc.AutoSize = true;
            AddRow(b, _stDesc);
            _stParamPanel = new FlowLayoutPanel();
            _stParamPanel.FlowDirection = FlowDirection.LeftToRight;
            _stParamPanel.WrapContents = true;
            _stParamPanel.AutoSize = true;
            _stParamPanel.AutoSizeMode = AutoSizeMode.GrowAndShrink;
            _stParamPanel.MaximumSize = new Size(860, 0);
            _stParamPanel.Margin = new Padding(0);
            AddRow(b, _stParamPanel);
            AddRow(stack, g2);
        }

        private void StOnSelectStrategy()
        {
            if (_stList.SelectedIndex < 0 || _stStrategies == null) return;
            var meta = _stStrategies[_stList.SelectedIndex];
            _stDesc.Text = StStr(meta["description"], "（无说明）");
            _stParamPanel.Controls.Clear();
            _stParamEditors.Clear();
            object po;
            if (meta.TryGetValue("params", out po))
            {
                var plist = po as System.Collections.IList;   // JSON 数组反序列化为 object[]
                if (plist != null)
                {
                    foreach (var item in plist)
                    {
                        var p = item as Dictionary<string, object>;
                        if (p != null) StAddParamEditor(p);
                    }
                }
            }
            if (_stParamEditors.Count == 0)
                _stParamPanel.Controls.Add(Mute(Lbl("该策略无可调参数。")));
            Skin(_stParamPanel);
        }

        private void StAddParamEditor(Dictionary<string, object> p)
        {
            string name = StStr(p["name"], "");
            string type = StStr(p["type"], "float");
            string label = StStr(p["label"], name);
            var lbl = Lbl(label);
            lbl.AutoSize = true;
            lbl.Margin = new Padding(0, 4, 6, 0);
            _stParamPanel.Controls.Add(lbl);

            Control ctrl;
            var ch = p["choices"] as System.Collections.IList;
            if (type == "choice" && ch != null && ch.Count > 0)
            {
                var cb = new ComboBox();
                cb.DropDownStyle = ComboBoxStyle.DropDownList;
                cb.Width = 120;
                cb.FlatStyle = FlatStyle.Flat;
                foreach (var c in ch) cb.Items.Add(StStr(c, ""));
                object def = StGet(p, "default");
                int idx = cb.Items.IndexOf(def == null ? "" : def.ToString());
                cb.SelectedIndex = idx >= 0 ? idx : 0;
                ctrl = cb;
            }
            else if (type == "int")
            {
                var n = new NumericUpDown();
                n.DecimalPlaces = 0;
                n.Width = 100;
                double mn = StNum(p["min"], int.MinValue);
                double mx = StNum(p["max"], int.MaxValue);
                n.Minimum = (decimal)Math.Max(int.MinValue, mn);
                n.Maximum = (decimal)Math.Min(int.MaxValue, mx);
                n.Value = (decimal)Clamp(StNum(p["default"], 0), (double)n.Minimum, (double)n.Maximum);
                ctrl = n;
            }
            else if (type == "float")
            {
                var n = new NumericUpDown();
                n.DecimalPlaces = 4;
                n.Width = 100;
                double mn = StNum(p["min"], double.MinValue);
                double mx = StNum(p["max"], double.MaxValue);
                n.Minimum = (decimal)mn;
                n.Maximum = (decimal)mx;
                n.Value = (decimal)Clamp(StNum(p["default"], 0), mn, mx);
                ctrl = n;
            }
            else
            {
                var tb = new TextBox();
                tb.Width = 160;
                object def = StGet(p, "default");
                var defList = def as System.Collections.IList;
                if (def == null) tb.Text = "";
                else if (defList != null)
                {
                    var parts = new List<string>();
                    foreach (var x in defList) parts.Add(x == null ? "" : x.ToString());
                    tb.Text = string.Join(",", parts);
                }
                else tb.Text = def.ToString();
                ctrl = tb;
            }
            ctrl.Margin = new Padding(0, 0, 18, 0);
            _stParamPanel.Controls.Add(ctrl);
            _stParamEditors.Add(new StParamEditor { Name = name, Type = type, Ctrl = ctrl });
        }

        private Dictionary<string, object> StReadParams()
        {
            var d = new Dictionary<string, object>();
            foreach (var e in _stParamEditors)
            {
                object v = e.Read();
                var sv = v as string;
                if (e.Type == "choice" && sv != null && sv.IndexOf(',') >= 0)
                {
                    var parts = sv.Split(new[] { ',', ' ', '，' }, StringSplitOptions.RemoveEmptyEntries);
                    bool allInt = true;
                    var lst = new List<int>();
                    foreach (var part in parts)
                    {
                        int ii;
                        if (int.TryParse(part, out ii)) lst.Add(ii);
                        else { allInt = false; break; }
                    }
                    if (allInt && lst.Count > 0) v = lst;
                }
                d[e.Name] = v;
            }
            return d;
        }

        // ---------------- ② 回测范围 ----------------
        private void StBuildScope(TableLayoutPanel stack)
        {
            TableLayoutPanel b;

            var g1 = Group("股票池", out b);
            _stAll = new RadioButton();
            _stAll.Text = "全部本地已下载股票";
            _stAll.AutoSize = true;
            _stAll.Checked = true;
            _stCustom = new RadioButton();
            _stCustom.Text = "自定义代码";
            _stCustom.AutoSize = true;
            _stCodes = new TextBox();
            _stCodes.Width = 360;
            _stCodes.Enabled = false;
            _stCodeHint = Mute(Lbl("本地共 0 只，填入代码（逗号/空格分隔，最多 800 只）"));
            _stAll.CheckedChanged += delegate { _stCodes.Enabled = !_stAll.Checked; };
            _stCustom.CheckedChanged += delegate { _stCodes.Enabled = _stCustom.Checked; };
            AddRow(b, Row(_stAll));
            AddRow(b, Row(_stCustom));
            AddRow(b, Row(_stCodes, _stCodeHint));
            AddRow(stack, g1);

            var g2 = Group("时间范围", out b);
            _stStart = new DateTimePicker();
            _stStart.Width = 130;
            _stStart.Format = DateTimePickerFormat.Custom;
            _stStart.CustomFormat = "yyyy-MM-dd";
            _stStart.ShowCheckBox = true;
            _stStart.Checked = true;
            _stStart.Value = DateTime.Today.AddYears(-3);
            _stEnd = new DateTimePicker();
            _stEnd.Width = 130;
            _stEnd.Format = DateTimePickerFormat.Custom;
            _stEnd.CustomFormat = "yyyy-MM-dd";
            _stEnd.ShowCheckBox = true;
            _stEnd.Checked = true;
            _stEnd.Value = DateTime.Today;
            AddRow(b, Row(Lbl("开始"), _stStart, Lbl("结束"), _stEnd, Mute(Lbl("取消勾选=不限制该端"))));
            AddRow(stack, g2);

            var g3 = Group("资金与费用", out b);
            _stCapital = new NumericUpDown();
            _stCapital.Minimum = 1000;
            _stCapital.Maximum = 100000000;
            _stCapital.Increment = 10000;
            _stCapital.DecimalPlaces = 0;
            _stCapital.Width = 120;
            _stCapital.Value = 100000;
            _stComm = new TextBox();
            _stComm.Width = 100;
            _stComm.Text = "0.0003";
            AddRow(b, Row(Lbl("初始资金(元)"), _stCapital));
            AddRow(b, Row(Lbl("单边佣金比例"), _stComm, Mute(Lbl("如 0.0003 = 万三"))));
            AddRow(stack, g3);

            var g4 = Group("基准对比", out b);
            _stBenchChk = new CheckBox();
            _stBenchChk.Text = "对比基准（买入持有）";
            _stBenchChk.AutoSize = true;
            _stBench = new TextBox();
            _stBench.Width = 100;
            _stBench.Text = "000300";
            _stBench.Enabled = false;
            _stBenchChk.CheckedChanged += delegate { _stBench.Enabled = _stBenchChk.Checked; };
            AddRow(b, Row(_stBenchChk, _stBench, Mute(Lbl("留空则不对比；指数可能无本地数据）"))));
            AddRow(stack, g4);
        }

        // ---------------- ③ 回测结果 ----------------
        private void StBuildResult(TableLayoutPanel stack)
        {
            _stResultHeader = new Label();
            _stResultHeader.Text = "—";
            _stResultHeader.Font = new Font("Microsoft YaHei UI", 11f, FontStyle.Bold);
            _stResultHeader.AutoSize = true;
            _stResultHeader.MaximumSize = new Size(900, 0);
            _stResultHeader.Margin = new Padding(0, 0, 0, 8);
            AddRow(stack, _stResultHeader);

            AddRow(stack, Row(MiniBtn("运行回测", delegate { StRunBacktest(); }, 120), _stResultStatus = Mute(Lbl("尚未回测"))));

            _stMetricCards = new FlowLayoutPanel();
            _stMetricCards.FlowDirection = FlowDirection.LeftToRight;
            _stMetricCards.WrapContents = true;
            _stMetricCards.AutoSize = true;
            _stMetricCards.AutoSizeMode = AutoSizeMode.GrowAndShrink;
            _stMetricCards.MaximumSize = new Size(900, 0);
            _stMetricCards.Margin = new Padding(0, 0, 0, 8);
            AddRow(stack, _stMetricCards);

            var chartHost = new Panel();
            chartHost.Height = 240;
            chartHost.Anchor = AnchorStyles.Left | AnchorStyles.Right | AnchorStyles.Top;
            chartHost.Margin = new Padding(0, 0, 0, 8);
            _stEquityBox = new PictureBox();
            _stEquityBox.Dock = DockStyle.Fill;
            _stEquityBox.Paint += StEquityPaint;
            chartHost.Controls.Add(_stEquityBox);
            AddRow(stack, chartHost);

            TableLayoutPanel bg;
            var g = Group("个股明细（按区间收益降序 · 点击行展开逐笔交易）", out bg);
            _stPerStockGrid = new DataGridView();
            _stPerStockGrid.AllowUserToAddRows = false;
            _stPerStockGrid.ReadOnly = true;
            _stPerStockGrid.SelectionMode = DataGridViewSelectionMode.FullRowSelect;
            _stPerStockGrid.RowTemplate.Height = 24;
            // 列宽随内容自适应；「名称」列弹性吸收剩余宽度，表格自身仍随窗口拉满
            _stPerStockGrid.AutoSizeColumnsMode = DataGridViewAutoSizeColumnsMode.DisplayedCellsExceptHeader;
            _stPerStockGrid.ColumnHeadersHeightSizeMode = DataGridViewColumnHeadersHeightSizeMode.DisableResizing;
            _stPerStockGrid.Height = 260;
            _stPerStockGrid.Anchor = AnchorStyles.Left | AnchorStyles.Right | AnchorStyles.Top;
            _stPerStockGrid.Columns.Add("code", "代码");
            _stPerStockGrid.Columns.Add("name", "名称");
            _stPerStockGrid.Columns.Add("ret", "区间收益");
            _stPerStockGrid.Columns.Add("trades", "交易次数");
            _stPerStockGrid.Columns["name"].AutoSizeMode = DataGridViewAutoSizeColumnMode.Fill;
            // 手动插行做「展开明细」，排序会把主行/明细行拆散，直接禁掉
            foreach (DataGridViewColumn col in _stPerStockGrid.Columns)
                col.SortMode = DataGridViewColumnSortMode.NotSortable;
            // 只用点击做展开，不出现选中高亮（点谁都是干净的）
            _stPerStockGrid.SelectionChanged += delegate { _stPerStockGrid.ClearSelection(); };
            _stPerStockGrid.CellClick += StPerStockCellClick;
            AddRow(bg, _stPerStockGrid);
            AddRow(stack, g);
        }

        private void StRunBacktest()
        {
            if (_stStrategies == null || _stList.SelectedIndex < 0)
            {
                _stResultStatus.Text = "请先在「策略选择与编辑」里选一个策略";
                _stResultStatus.ForeColor = Color.FromArgb(208, 57, 59);
                return;
            }
            var meta = _stStrategies[_stList.SelectedIndex];
            string sid = StStr(meta["id"], "");
            var codes = _stAll.Checked ? new List<string>() : StParseCodes(_stCodes.Text);
            var req = new Dictionary<string, object>
            {
                { "strategy_id", sid },
                { "params", StReadParams() },
                { "use_all", _stAll.Checked },
                { "codes", codes },
                { "start", StDate(_stStart) },
                { "end", StDate(_stEnd) },
                { "initial_capital", (double)_stCapital.Value },
                { "commission", StNum(_stComm.Text, 0.0003) },
                { "benchmark", (_stBenchChk.Checked && _stBench.Text.Trim().Length > 0) ? _stBench.Text.Trim() : null },
            };

            string start = StDate(_stStart) ?? "起点";
            string end = StDate(_stEnd) ?? "至今";
            string scope = _stAll.Checked
                ? "全部本地(" + _stLocalCodeCount + "只)"
                : "自定义(" + codes.Count + "只)";
            double cap = (double)_stCapital.Value;
            _stResultHeader.Text = "策略：" + StStr(meta["name"], sid) + " · 范围：" + scope + " "
                + start + "~" + end + " · 初始 " + (cap / 10000).ToString("F1") + "万 · 佣金 "
                + StNum(_stComm.Text, 0.0003).ToString("P4").Replace(" ", "");

            _stResultStatus.Text = "回测中…";
            _stResultStatus.ForeColor = _cSub;
            string body = new JavaScriptSerializer().Serialize(req);

            System.Threading.Tasks.Task.Run(delegate
            {
                try
                {
                    // 全量（5000+ 只）冷缓存也要几十秒，超时放宽到 10 分钟
                    string resp = VRequest("http://127.0.0.1:8000/api/strategies/backtest", body, 600000);
                    var j = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(resp);
                    Invoke((Action)(() => StRenderResult(j)));
                }
                catch (Exception ex)
                {
                    Invoke((Action)(() =>
                    {
                        _stResultStatus.Text = "回测失败：" + ex.Message;
                        _stResultStatus.ForeColor = Color.FromArgb(208, 57, 59);
                    }));
                }
            });
        }

        private void StRenderResult(Dictionary<string, object> j)
        {
            object okv;
            if (j.TryGetValue("ok", out okv) && okv is bool && !(bool)okv)
            {
                string msg = "回测失败";
                object err;
                if (j.TryGetValue("error", out err))
                {
                    var ed = err as Dictionary<string, object>;
                    if (ed != null) msg = "回测失败：" + StStr(ed["message"], "");
                }
                _stResultStatus.Text = msg;
                _stResultStatus.ForeColor = Color.FromArgb(208, 57, 59);
                return;
            }

            var metrics = (Dictionary<string, object>)j["metrics"];
            _stMetricCards.Controls.Clear();
            _stMetricCards.Controls.Add(StKpiCard("总收益率", Pct(ToDbl(metrics["total_return"]))));
            _stMetricCards.Controls.Add(StKpiCard("年化收益", Pct(ToDbl(metrics["annual_return"]))));
            _stMetricCards.Controls.Add(StKpiCard("最大回撤", Pct(ToDbl(metrics["max_drawdown"]))));
            _stMetricCards.Controls.Add(StKpiCard("夏普比率", ToDbl(metrics["sharpe"]).ToString("F2")));
            _stMetricCards.Controls.Add(StKpiCard("胜率", Pct(ToDbl(metrics["win_rate"]))));
            _stMetricCards.Controls.Add(StKpiCard("交易次数", ToInt(metrics["num_trades"]).ToString()));
            object bret;
            if (metrics.TryGetValue("benchmark_return", out bret) && bret != null)
                _stMetricCards.Controls.Add(StKpiCard("基准收益", Pct(ToDbl(bret))));
            Skin(_stMetricCards);

            _stEqDates = ToStringList(j["dates"]);
            _stEq = ToNullableDoubleList(j["equity"]);
            object eb;
            bool hasEb = j.TryGetValue("equity_benchmark", out eb);
            _stEqBench = (hasEb && eb != null) ? ToNullableDoubleList(eb) : null;
            _stEquityBox.Invalidate();

            var ps = j["per_stock"] as System.Collections.IList;
            _stPerStockData = new List<Dictionary<string, object>>();
            if (ps != null)
            {
                foreach (var it in ps)
                {
                    var d = it as Dictionary<string, object>;
                    if (d != null) _stPerStockData.Add(d);
                }
            }
            _stResultId = StStr(j.ContainsKey("result_id") ? j["result_id"] : null, "");
            _stExpanded.Clear();          // 新一轮回测结果，展开状态作废
            _stDetailCache.Clear();       // 明细跟着旧 result_id，一并作废
            StFillPerStockRows();

            object nstk;
            int doneCount = (metrics.TryGetValue("num_stocks", out nstk)) ? ToInt(nstk) : 0;
            _stResultStatus.Text = "回测完成（参与统计 " + doneCount + " 只）";
            _stResultStatus.ForeColor = Color.FromArgb(60, 160, 90);
            object errs;
            if (j.TryGetValue("data_errors", out errs))
            {
                var el = errs as System.Collections.IList;
                if (el != null && el.Count > 0)
                    _stResultStatus.Text = "回测完成（参与统计 " + doneCount
                        + " 只，" + el.Count + " 只数据缺失/跳过）";
            }
        }

        // ---------------- 个股明细展开 ----------------
        // 主行：代码 | 名称 | 区间收益 | 交易次数；点击主行切换展开，逐笔明细
        // 按 result_id 向后端按需拉取（/backtest/trades），插入主行之下。
        // 明细不随回测结果下发——全量 5000+ 只的明细 JSON 会到 10MB 级。
        private void StFillPerStockRows()
        {
            _stPerStockGrid.Rows.Clear();
            if (_stPerStockData == null) return;
            _stPerStockGrid.SuspendLayout();
            foreach (var d in _stPerStockData)
            {
                string code = StStr(d["code"], "");
                string name = (d.ContainsKey("name") && d["name"] != null) ? StStr(d["name"], "") : "—";
                int ri = _stPerStockGrid.Rows.Add("▸ " + code, name,
                    Pct(ToDbl(d["total_return"])), ToInt(d["trades"]).ToString());
                var row = _stPerStockGrid.Rows[ri];
                row.Tag = "main:" + code;
                // 主行也不吃选中高亮（点击只做展开，界面保持干净）
                row.DefaultCellStyle.SelectionBackColor = _cPanel;
                row.DefaultCellStyle.SelectionForeColor = _cText;
            }
            _stPerStockGrid.ResumeLayout();
            StAdjustHeight();
        }

        private static string StMainCode(DataGridViewRow row)
        {
            string tag = row.Tag as string;
            return (tag != null && tag.StartsWith("main:")) ? tag.Substring(5) : null;
        }

        private void StToggleExpand(int mainIdx)
        {
            var grid = _stPerStockGrid;
            var row = grid.Rows[mainIdx];
            string code = StMainCode(row);
            if (code == null) return;
            if (_stExpanded.Contains(code))
            {
                _stExpanded.Remove(code);
                row.Cells[0].Value = "▸ " + code;
                // 删除主行之后连续的明细 / 占位行
                int i = mainIdx + 1;
                while (i < grid.Rows.Count)
                {
                    string t = grid.Rows[i].Tag as string;
                    if (t == "detail" || (t != null && t.StartsWith("loading:")))
                        grid.Rows.RemoveAt(i);
                    else break;
                }
            }
            else
            {
                _stExpanded.Add(code);
                row.Cells[0].Value = "▾ " + code;
                if (_stDetailCache.ContainsKey(code)) StInsertDetails(mainIdx + 1, code);
                else StLoadDetails(code);
            }
            StAdjustHeight();
        }

        // 异步拉取逐笔明细；主行下先插「加载中」占位行，回来后原位替换
        private void StLoadDetails(string code)
        {
            string rid = _stResultId;
            int mainIdx = StFindMainRow(code);
            if (mainIdx < 0) return;
            if (string.IsNullOrEmpty(rid))
            {
                StDetailFailed(code, "请先运行回测");
                return;
            }
            _stPerStockGrid.Rows.Insert(mainIdx + 1, new object[] { "└", "明细加载中…", "", "" });
            var prow = _stPerStockGrid.Rows[mainIdx + 1];
            prow.Tag = "loading:" + code;
            prow.DefaultCellStyle.ForeColor = _cSub;
            System.Threading.Tasks.Task.Run(delegate
            {
                try
                {
                    string resp = VRequest("http://127.0.0.1:8000/api/strategies/backtest/trades?rid="
                        + rid + "&code=" + code, null, 30000);
                    var j = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(resp);
                    var trades = (j != null && j.ContainsKey("trades"))
                        ? j["trades"] as System.Collections.IList : null;
                    Invoke((Action)delegate { StDetailArrived(code, trades); });
                }
                catch (Exception ex)
                {
                    string msg = ex.Message;
                    try { Invoke((Action)delegate { StDetailFailed(code, msg); }); }
                    catch (Exception) { }
                }
            });
        }

        private int StFindMainRow(string code)
        {
            for (int i = 0; i < _stPerStockGrid.Rows.Count; i++)
                if (StMainCode(_stPerStockGrid.Rows[i]) == code) return i;
            return -1;
        }

        private void StDetailArrived(string code, System.Collections.IList trades)
        {
            if (trades == null) trades = new System.Collections.ArrayList();
            _stDetailCache[code] = trades;
            // 替换占位行；期间已收起 / 表格已重建（找不到占位行）则丢弃
            for (int i = 0; i < _stPerStockGrid.Rows.Count; i++)
            {
                string t = _stPerStockGrid.Rows[i].Tag as string;
                if (t == "loading:" + code)
                {
                    _stPerStockGrid.Rows.RemoveAt(i);
                    if (_stExpanded.Contains(code)) StInsertDetails(i, code);
                    break;
                }
            }
            StAdjustHeight();
        }

        private void StDetailFailed(string code, string msg)
        {
            for (int i = 0; i < _stPerStockGrid.Rows.Count; i++)
            {
                string t = _stPerStockGrid.Rows[i].Tag as string;
                if (t == "loading:" + code)
                {
                    _stPerStockGrid.Rows[i].SetValues("└", "明细加载失败：" + msg, "", "");
                    break;
                }
            }
        }

        private void StInsertDetails(int insertAt, string code)
        {
            var grid = _stPerStockGrid;
            System.Collections.IList trades;
            if (!_stDetailCache.TryGetValue(code, out trades) || trades.Count == 0)
            {
                grid.Rows.Insert(insertAt, new object[] { "└", "（无逐笔明细）", "", "" });
                StStyleDetailRow(grid.Rows[insertAt]);
                return;
            }
            int n = 1;
            foreach (var t in trades)
            {
                var x = t as Dictionary<string, object>;
                if (x == null) continue;
                string buyDate = StStr(x["buy_date"], "");
                string buyPrice = x["buy_price"] != null ? ToDbl(x["buy_price"]).ToString("F2") : "—";
                bool closed = x["sell_date"] != null;
                string sellDate = closed ? StStr(x["sell_date"], "") : "未平仓";
                string sellPrice = x["sell_price"] != null ? ToDbl(x["sell_price"]).ToString("F2") : "—";
                object retObj;
                string retTxt = (x.TryGetValue("ret", out retObj) && retObj != null) ? Pct(ToDbl(retObj)) : "—";
                grid.Rows.Insert(insertAt, new object[] { "└ " + n,
                    "买 " + buyDate + " @ " + buyPrice,
                    (closed ? "卖 " : "") + sellDate + (closed ? " @ " + sellPrice : ""),
                    retTxt });
                StStyleDetailRow(grid.Rows[insertAt]);
                insertAt++;
                n++;
            }
        }

        private void StStyleDetailRow(DataGridViewRow row)
        {
            row.Tag = "detail";
            row.DefaultCellStyle.BackColor = _cPanel;
            row.DefaultCellStyle.ForeColor = _cSub;
            row.DefaultCellStyle.SelectionBackColor = _cPanel;
            row.DefaultCellStyle.SelectionForeColor = _cSub;
        }

        // 表格高度随内容自适应（封顶 620，超出滚动；行少时不少于 240）
        private void StAdjustHeight()
        {
            int rh = _stPerStockGrid.RowTemplate.Height > 0 ? _stPerStockGrid.RowTemplate.Height : 24;
            int h = _stPerStockGrid.ColumnHeadersHeight + 8 + _stPerStockGrid.Rows.Count * rh;
            _stPerStockGrid.Height = Math.Max(240, Math.Min(620, h));
        }

        private void StPerStockCellClick(object sender, DataGridViewCellEventArgs e)
        {
            if (e.RowIndex < 0) return;
            if (StMainCode(_stPerStockGrid.Rows[e.RowIndex]) == null) return;
            StToggleExpand(e.RowIndex);
        }

        // ---------------- 净值曲线绘制 ----------------
        private void StEquityPaint(object sender, PaintEventArgs e)
        {
            var box = (PictureBox)sender;
            var g = e.Graphics;
            var r = box.ClientRectangle;
            g.Clear(_cBg);
            if (_stEq == null || _stEq.Count < 2)
            {
                using (var b = new SolidBrush(_cSub))
                    g.DrawString("暂无净值曲线（先运行回测）", this.Font, b, 10f, 10f);
                return;
            }

            double min = double.MaxValue, max = double.MinValue;
            foreach (var v in _stEq) if (v != null) { min = Math.Min(min, v.Value); max = Math.Max(max, v.Value); }
            if (_stEqBench != null)
                foreach (var v in _stEqBench) if (v != null) { min = Math.Min(min, v.Value); max = Math.Max(max, v.Value); }
            if (min == max) { min -= 1; max += 1; }

            int padL = 8, padR = 8, padT = 12, padB = 18;
            int w = Math.Max(1, r.Width - padL - padR);
            int h = Math.Max(1, r.Height - padT - padB);
            using (var grid = new Pen(_light ? Color.FromArgb(222, 224, 228) : Color.FromArgb(58, 62, 72)))
            {
                for (int i = 0; i <= 4; i++)
                {
                    int y = padT + h * i / 4;
                    g.DrawLine(grid, padL, y, padL + w, y);
                }
            }

            int n = _stEq.Count - 1;
            Func<int, double, Point> pt = (i, val) => new Point(
                padL + (int)(w * i / n),
                padT + (int)(h * (1 - (val - min) / (max - min))));

            if (_stEqBench != null)
            {
                using (var pen = new Pen(Color.FromArgb(240, 160, 40), 1.5f))
                    for (int i = 1; i < _stEqBench.Count; i++)
                        if (_stEqBench[i] != null && _stEqBench[i - 1] != null)
                            g.DrawLine(pen, pt(i - 1, _stEqBench[i - 1].Value), pt(i, _stEqBench[i].Value));
            }
            using (var pen = new Pen(Color.FromArgb(0, 120, 215), 2f))
                for (int i = 1; i < _stEq.Count; i++)
                    if (_stEq[i] != null && _stEq[i - 1] != null)
                        g.DrawLine(pen, pt(i - 1, _stEq[i - 1].Value), pt(i, _stEq[i].Value));

            using (var bb = new SolidBrush(_cText))
            {
                g.DrawString("组合", new Font("Microsoft YaHei UI", 9f), new SolidBrush(Color.FromArgb(0, 120, 215)), (float)padL, 0f);
                if (_stEqBench != null)
                    g.DrawString("基准", new Font("Microsoft YaHei UI", 9f), new SolidBrush(Color.FromArgb(240, 160, 40)), (float)(padL + 44), 0f);
                g.DrawString(((double)(_stEq[_stEq.Count - 1] ?? min)).ToString("F0"), this.Font, bb, (float)padL, (float)(padT + h + 2));
            }
        }

        // ---------------- 清单加载 ----------------
        private void StLoadStrategies()
        {
            System.Threading.Tasks.Task.Run(delegate
            {
                try
                {
                    string rs = VRequest("http://127.0.0.1:8000/api/strategies", null);
                    string rc = VRequest("http://127.0.0.1:8000/api/strategies/codes", null);
                    var js = new JavaScriptSerializer().Deserialize<object[]>(rs);
                    var jc = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(rc);
                    var list = new List<Dictionary<string, object>>();
                    if (js != null)
                        foreach (var it in js)
                        {
                            var m = it as Dictionary<string, object>;
                            if (m != null) list.Add(m);
                        }
                    int cnt = 0;
                    object co;
                    if (jc.TryGetValue("codes", out co))
                    {
                        var cl = co as System.Collections.IList;
                        if (cl != null) cnt = cl.Count;
                    }
                    Invoke((Action)(() =>
                    {
                        _stStrategies = list;
                        _stLocalCodeCount = cnt;
                        _stList.Items.Clear();
                        foreach (var m in list) _stList.Items.Add(StStr(m["name"], StStr(m["id"], "?")));
                        if (_stCodeHint != null) _stCodeHint.Text = "本地共 " + cnt + " 只，填入代码（逗号/空格分隔，最多 800 只）";
                        _stResultStatus.Text = "清单已加载（" + list.Count + " 个策略 / " + cnt + " 只本地股票）";
                        _stResultStatus.ForeColor = _cSub;
                        if (_stList.Items.Count > 0) _stList.SelectedIndex = 0;
                        _stLoaded = true;
                    }));
                }
                catch (Exception ex)
                {
                    Invoke((Action)(() =>
                    {
                        _stResultStatus.Text = "加载失败（确认后端已启动）：" + ex.Message;
                        _stResultStatus.ForeColor = Color.FromArgb(208, 57, 59);
                    }));
                }
            });
        }

        // ---------------- 小工具 ----------------
        private static Panel StKpiCard(string title, string value)
        {
            var card = new Panel();
            card.Width = 150;
            card.Height = 66;
            card.Margin = new Padding(0, 0, 8, 0);
            card.BorderStyle = BorderStyle.FixedSingle;
            card.BackColor = Color.FromArgb(0); // 由 Skin 经 Tag 上色
            card.Tag = "kpi";
            card.Padding = new Padding(8, 6, 8, 6);
            var v = new Label();
            v.Text = value;
            v.Font = new Font("Microsoft YaHei UI", 13f, FontStyle.Bold);
            v.AutoSize = true;
            v.Location = new Point(8, 6);
            var t = new Label();
            t.Text = title;
            t.Font = new Font("Microsoft YaHei UI", 9f);
            t.AutoSize = true;
            t.Tag = "muted";
            t.Location = new Point(8, 34);
            card.Controls.Add(v);
            card.Controls.Add(t);
            return card;
        }

        private static string StStr(object o, string d) { return o == null ? d : o.ToString(); }
        private static object StGet(Dictionary<string, object> d, string k) { object v; return d.TryGetValue(k, out v) ? v : null; }
        private static double StNum(object o, double d)
        {
            if (o == null) return d;
            try { return Convert.ToDouble(o); } catch { return d; }
        }
        private static double Clamp(double v, double lo, double hi) { return Math.Max(lo, Math.Min(hi, v)); }
        private static int ToInt(object o) { try { return Convert.ToInt32(o); } catch { return 0; } }
        private static double ToDbl(object o) { try { return Convert.ToDouble(o); } catch { return 0; } }
        private static string Pct(double v) { return (v * 100).ToString("F2") + "%"; }
        private static string StDate(DateTimePicker p) { return p.Checked ? p.Value.ToString("yyyy-MM-dd") : null; }
        private static List<string> StParseCodes(string text)
        {
            var outp = new List<string>();
            if (string.IsNullOrWhiteSpace(text)) return outp;
            foreach (var part in text.Split(new[] { ',', ' ', ';', '，', '；', '\n', '\r' }, StringSplitOptions.RemoveEmptyEntries))
            {
                string c = part.Trim();
                if (c.Length > 0) outp.Add(c);
            }
            return outp;
        }
        private static List<string> ToStringList(object o)
        {
            var outp = new List<string>();
            var l = o as System.Collections.IList;
            if (l != null) foreach (var x in l) outp.Add(x == null ? "" : x.ToString());
            return outp;
        }
        private static List<double?> ToNullableDoubleList(object o)
        {
            var outp = new List<double?>();
            var l = o as System.Collections.IList;
            if (l != null)
                foreach (var x in l) outp.Add(x == null ? (double?)null : Convert.ToDouble(x));
            return outp;
        }
    }
}
