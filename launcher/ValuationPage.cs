using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Net;
using System.Text;
using System.Windows.Forms;
using System.Web.Script.Serialization;

namespace StockPool
{
    /// <summary>
    /// 股票估值计算（原生内嵌标签页，不弹新窗口）：MainForm 的拆分文件，
    /// 负责「估值计算」标签页的构建、后端调用与结果渲染。
    /// 依赖后端 http://127.0.0.1:8000 的 /api/stock/valuation 与 /api/stock/quote。
    /// </summary>
    internal sealed partial class MainForm
    {
        // ---- 估值计算页（原生内嵌，替代「分析工具 → 股票估值计算」的外部网页）----
        private int _valTabIndex = -1;
        private TextBox _vCode, _vForecast, _vBase, _vPrice, _vShares, _vForecastYears, _vRate, _vPredictYears;
        private Label _vName, _vHint, _vStatusResult, _vStatusSteps, _vNote;
        private Button _vCalc, _vToggle;
        private TableLayoutPanel _vParams;
        private FlowLayoutPanel _vKpiRow1, _vKpiRow2;
        private DataGridView _vGrid;
        private ComboBox _vScenario;
        private RichTextBox _vSteps;
        private Dictionary<string, object> _vLast;
        private string _vActive;
        private readonly HashSet<string> _vEdited = new HashSet<string>();
        private bool _vSuppress;

        #region 估值计算页（原生内嵌标签页，不弹新窗口；依赖后端 /api/stock/valuation 与 /api/stock/quote）

        /// <summary>估值计算：① 输入参数 → ② 计算结果（KPI + 三档情景表）→ ③ 计算过程（可切情景）。</summary>
        private Panel BuildValuationPage()
        {
            var p = NewPage("估值计算");
            _valTabIndex = _tabPages.Count - 1;

            var stack = Stack();

            var title = Lbl("📈 股票估值计算");
            title.Font = new Font("Microsoft YaHei UI", 14f, FontStyle.Bold);
            title.Margin = new Padding(0, 0, 0, 2);
            AddRow(stack, Row(title));

            var sub = Mute(Lbl("简化 DCF（前 5 年预测 + 永续增长）｜贴现率 10%｜乐观 = 机构预测增速 × 1.5、中性 = 机构预测增速、悲观固定 5%（无预测时兜底 25% / 10% / 5%）"));
            sub.AutoSize = false;
            sub.Width = 760;
            sub.Height = 20;
            AddRow(stack, Row(sub));

            // ① 输入参数
            TableLayoutPanel b1;
            var g1 = Group("① 输入参数", out b1);

            _vCode = new TextBox();
            _vCode.Width = 130;
            _vCode.MaxLength = 6;
            _vCode.Margin = new Padding(0);
            _vCalc = MiniBtn("开始计算", delegate { ValuationCalc(); }, 96);
            _vToggle = MiniBtn("展开参数", delegate { ValuationToggleParams(); }, 96);
            _vName = Mute(Lbl(""));
            AddRow(b1, Row(Lbl("代码"), _vCode, _vCalc, _vToggle));

            _vHint = Mute(Lbl("只填代码即可自动抓取股价 / 总股本 / 净利润（TTM 归母优先），机构预测为可选项"));
            _vHint.AutoSize = true;
            _vHint.MaximumSize = new Size(740, 0);
            AddRow(b1, Row(_vName));
            AddRow(b1, Row(_vHint));

            _vParams = new TableLayoutPanel();
            _vParams.ColumnCount = 4;
            _vParams.RowCount = 4;
            _vParams.Dock = DockStyle.Top;
            _vParams.AutoSize = true;
            _vParams.AutoSizeMode = AutoSizeMode.GrowAndShrink;
            _vParams.Margin = new Padding(0);
            _vParams.Padding = new Padding(0);
            _vParams.Visible = false;
            _vParams.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
            _vParams.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 50f));
            _vParams.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
            _vParams.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 50f));
            for (int i = 0; i < 4; i++) _vParams.RowStyles.Add(new RowStyle(SizeType.AutoSize));

            _vForecast = VParam(_vParams, 0, 0, "机构预测净利润（亿，可选）", "forecast");
            _vBase = VParam(_vParams, 0, 2, "期初扣非净利润（亿）", "base");
            _vPrice = VParam(_vParams, 1, 0, "当前股价（元）", "price");
            _vShares = VParam(_vParams, 1, 2, "总股本（亿股）", "shares");
            _vForecastYears = VParam(_vParams, 2, 0, "机构预测年数 N", "fyears");
            _vForecastYears.Text = "3";
            _vRate = VParam(_vParams, 2, 2, "贴现率（%）", "rate");
            _vRate.Text = "10";
            _vPredictYears = VParam(_vParams, 3, 0, "预测 N 年价 / 收益率", "pyears");
            _vPredictYears.Text = "1";
            AddRow(b1, _vParams);
            AddRow(stack, g1);

            // ② 计算结果
            TableLayoutPanel b2;
            var g2 = Group("② 计算结果", out b2);
            _vStatusResult = Mute(Lbl("待计算"));
            AddRow(b2, Row(_vStatusResult));

            _vKpiRow1 = VKpiRow();
            _vKpiRow2 = VKpiRow();
            AddRow(b2, _vKpiRow1);
            AddRow(b2, _vKpiRow2);

            InitVGrid();
            AddRow(b2, _vGrid);

            _vNote = Mute(Lbl(""));
            _vNote.AutoSize = false;
            _vNote.Width = 760;
            _vNote.Height = 0;
            _vNote.Visible = false;
            AddRow(b2, Row(_vNote));
            AddRow(stack, g2);

            // ③ 计算过程
            TableLayoutPanel b3;
            var g3 = Group("③ 计算过程", out b3);

            _vScenario = new ComboBox();
            _vScenario.Width = 240;
            _vScenario.DropDownStyle = ComboBoxStyle.DropDownList;
            _vScenario.SelectedIndexChanged += delegate { _vActive = _vScenario.SelectedItem as string; ValuationRenderSteps(); };
            _vStatusSteps = Mute(Lbl("待计算"));
            AddRow(b3, Row(_vScenario, _vStatusSteps));

            _vSteps = new RichTextBox();
            _vSteps.Dock = DockStyle.Top;
            _vSteps.Height = 150;
            _vSteps.ReadOnly = true;
            _vSteps.WordWrap = false;
            _vSteps.BorderStyle = BorderStyle.None;
            _vSteps.Font = new Font("Consolas", 9.5f);
            _vSteps.Text = "计算过程将在此逐步展示（增长率 → 各年贴现 → 永续 → 每股价值）";
            AddRow(b3, _vSteps);
            AddRow(stack, g3);

            p.Controls.Add(stack);

            _vCode.KeyDown += delegate(object s, KeyEventArgs e)
            {
                if (e.KeyCode == Keys.Enter) ValuationCalc();
            };
            _vCode.TextChanged += delegate { ValuationCodeChanged(); };
            _vCode.Leave += delegate { ValuationLookupName(); };

            TextBox[] manual = new TextBox[] { _vForecast, _vBase, _vPrice, _vShares };
            foreach (TextBox mt in manual)
            {
                TextBox box = mt;
                box.TextChanged += delegate
                {
                    if (_vSuppress) return;
                    _vEdited.Add(box.Name);
                };
            }
            return p;
        }

        /// <summary>参数网格里的一格：左侧标签 + 右侧输入框（输入框左右锚定，随页面宽度伸缩）。</summary>
        private static TextBox VParam(TableLayoutPanel t, int row, int col, string label, string name)
        {
            var lb = Lbl(label);
            lb.Anchor = AnchorStyles.Left;
            lb.Margin = new Padding(0, 9, 10, 0);
            t.Controls.Add(lb, col, row);

            var tb = new TextBox();
            tb.Name = name;
            tb.Anchor = AnchorStyles.Left | AnchorStyles.Right;
            tb.Height = 26;
            tb.Margin = new Padding(0, 3, 24, 3);
            t.Controls.Add(tb, col + 1, row);
            return tb;
        }

        private static FlowLayoutPanel VKpiRow()
        {
            var f = new FlowLayoutPanel();
            f.FlowDirection = FlowDirection.LeftToRight;
            f.WrapContents = false;
            f.AutoSize = true;
            f.AutoSizeMode = AutoSizeMode.GrowAndShrink;
            f.Margin = new Padding(0, 0, 0, 6);
            f.Padding = new Padding(0);
            return f;
        }

        private void InitVGrid()
        {
            _vGrid = new DataGridView();
            _vGrid.Dock = DockStyle.Top;
            _vGrid.Height = 170;
            _vGrid.ReadOnly = true;
            _vGrid.AllowUserToAddRows = false;
            _vGrid.AllowUserToDeleteRows = false;
            _vGrid.RowHeadersVisible = false;
            _vGrid.BorderStyle = BorderStyle.None;
            _vGrid.Tag = "grid";
            _vGrid.AutoSizeColumnsMode = DataGridViewAutoSizeColumnsMode.Fill;
            _vGrid.ColumnHeadersHeightSizeMode = DataGridViewColumnHeadersHeightSizeMode.AutoSize;

            string[] cols = new string[] { "情景", "增长率", "每股价值", "股价/价值", "判断", "预测价", "收益率" };
            foreach (string cn in cols)
            {
                var col = new DataGridViewTextBoxColumn();
                col.HeaderText = cn;
                col.SortMode = DataGridViewColumnSortMode.NotSortable;
                col.DefaultCellStyle.Alignment = (cn == "情景" || cn == "判断")
                    ? DataGridViewContentAlignment.MiddleLeft
                    : DataGridViewContentAlignment.MiddleRight;
                _vGrid.Columns.Add(col);
            }
        }

        /// <summary>一张 KPI 卡片（换肤时按 tag="kpi" 上卡片底色）。</summary>
        private static void AddKpi(FlowLayoutPanel row, string l, string v, string s)
        {
            var card = new Panel();
            card.Tag = "kpi";
            card.Width = 230;
            card.Height = 66;
            card.Margin = new Padding(0, 0, 8, 0);
            card.Padding = new Padding(0);

            var l1 = new Label();
            l1.Text = l;
            l1.AutoSize = true;
            l1.Location = new Point(10, 7);
            l1.Tag = "muted";

            var v1 = new Label();
            v1.Text = v;
            v1.Font = new Font("Consolas", 12.5f);
            v1.AutoSize = true;
            v1.Location = new Point(10, 23);
            v1.MaximumSize = new Size(212, 0);

            var s1 = new Label();
            s1.Text = s;
            s1.AutoSize = true;
            s1.Location = new Point(10, 46);
            s1.MaximumSize = new Size(212, 0);
            s1.Tag = "muted";

            card.Controls.Add(l1);
            card.Controls.Add(v1);
            card.Controls.Add(s1);
            row.Controls.Add(card);
        }

        private void ValuationToggleParams()
        {
            _vParams.Visible = !_vParams.Visible;
            _vToggle.Text = _vParams.Visible ? "收起参数" : "展开参数";
        }

        // ---- 输入 ----
        private void ValuationCodeChanged()
        {
            if (_vSuppress) return;
            string digits = System.Text.RegularExpressions.Regex.Replace(_vCode.Text, "[^0-9]", "");
            if (digits.Length > 6) digits = digits.Substring(0, 6);
            if (_vCode.Text != digits)
            {
                _vSuppress = true;
                int sel = _vCode.SelectionStart;
                _vCode.Text = digits;
                _vCode.SelectionStart = Math.Min(sel, digits.Length);
                _vSuppress = false;
            }
            if ((_vHint.Tag as string) != "bad")
            {
                _vHint.Text = "只填代码即可自动抓取股价 / 总股本 / 净利润（TTM 归母优先），机构预测为可选项";
                _vHint.Tag = "muted";
            }
            _vSuppress = true;
            TextBox[] auto = new TextBox[] { _vPrice, _vShares, _vBase };
            foreach (TextBox tb in auto)
            {
                if ((tb.Tag as string) == "auto")
                {
                    tb.Text = "";
                    tb.Tag = null;
                    _vEdited.Remove(tb.Name);
                }
            }
            _vSuppress = false;
        }

        private string ValuationBody(string code)
        {
            var o = new Dictionary<string, object>();
            o["code"] = code;
            o["net_profit_forecast"] = ValuationNum(_vForecast);
            o["net_profit_base"] = _vEdited.Contains("base") ? ValuationNum(_vBase) : (double?)null;
            o["price"] = _vEdited.Contains("price") ? ValuationNum(_vPrice) : (double?)null;
            o["shares"] = _vEdited.Contains("shares") ? ValuationNum(_vShares) : (double?)null;
            o["forecast_years"] = (int)(ValuationNum(_vForecastYears) ?? 3);
            o["discount_rate"] = (ValuationNum(_vRate) ?? 10) / 100.0;
            o["predict_years"] = (int)(ValuationNum(_vPredictYears) ?? 1);
            return new JavaScriptSerializer().Serialize(o);
        }

        private double? ValuationNum(TextBox tb)
        {
            if (tb == null || string.IsNullOrWhiteSpace(tb.Text)) return null;
            double v;
            if (double.TryParse(tb.Text, out v)) return (double?)v;
            return null;
        }

        // ---- 计算 ----
        private void ValuationCalc()
        {
            string code = _vCode.Text.Trim();
            if (!System.Text.RegularExpressions.Regex.IsMatch(code, "^[0-9]{6}$"))
            {
                _vHint.Text = "请输入 6 位数字股票代码（如 600519）";
                _vHint.Tag = "bad";
                _vHint.ForeColor = Color.FromArgb(208, 57, 59);
                _vCode.Focus();
                return;
            }
            _vHint.Tag = "muted";
            _vHint.Text = "计算中…";
            _vCalc.Enabled = false;
            _vStatusResult.Text = "计算中…";

            System.Threading.Tasks.Task.Run(delegate
            {
                try
                {
                    string resp = VRequest("http://127.0.0.1:8000/api/stock/valuation", ValuationBody(code));
                    var j = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(resp);
                    Invoke((Action)delegate { ValuationShowResult(j, resp); });
                }
                catch (Exception ex)
                {
                    string msg = "请求失败：" + ex.Message;
                    try { Invoke((Action)delegate { ValuationShowError(msg); }); }
                    catch (Exception) { }
                }
                finally
                {
                    try { Invoke((Action)delegate { _vCalc.Enabled = true; }); }
                    catch (Exception) { }
                }
            });
        }

        private void ValuationLookupName()
        {
            string code = _vCode.Text.Trim();
            if (!System.Text.RegularExpressions.Regex.IsMatch(code, "^[0-9]{6}$")) { _vName.Text = ""; return; }
            System.Threading.Tasks.Task.Run(delegate
            {
                try
                {
                    string resp = VRequest("http://127.0.0.1:8000/api/stock/quote?code=" + code, null);
                    var j = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(resp);
                    Invoke((Action)delegate
                    {
                        if (_vCode.Text.Trim() != code) return;
                        object okv;
                        bool isOk = (j != null && j.TryGetValue("ok", out okv) && okv is bool && (bool)okv);
                        if (isOk && j.ContainsKey("name"))
                        {
                            double? pr = VNum(VSafe(j, "price"));
                            _vName.Text = VStr(VSafe(j, "name")) + (pr != null ? "  " + pr.Value.ToString("F2") + " 元" : "");
                        }
                        else
                        {
                            string e2 = VStr(VSafe(j, "error"));
                            _vName.Text = e2 == "" ? "未找到该代码对应的股票" : e2;
                        }
                    });
                }
                catch (Exception ex)
                {
                    string m = "名称查询失败：" + ex.Message;
                    try { Invoke((Action)delegate { _vName.Text = m; }); }
                    catch (Exception) { }
                }
            });
        }

        // ---- 渲染 ----
        private void ValuationShowResult(Dictionary<string, object> j, string raw)
        {
            bool ok = false;
            object ov;
            if (j != null && j.TryGetValue("ok", out ov) && ov is bool) ok = (bool)ov;

            if (!ok)
            {
                _vKpiRow1.Controls.Clear();
                _vKpiRow2.Controls.Clear();
                _vGrid.Rows.Clear();
                _vStatusResult.Text = "待补充参数";
                _vStatusResult.ForeColor = Color.FromArgb(180, 83, 9);
                var sb = new StringBuilder();
                string emsg = VStr(VSafe(j, "error"));
                if (emsg == "") emsg = VStr(VSafe(j, "detail"));
                if (emsg == "" && !string.IsNullOrEmpty(raw))
                    emsg = raw.Length > 300 ? raw.Substring(0, 300) : raw;
                if (emsg == "") emsg = "计算失败";
                sb.AppendLine(emsg);
                var errs = VArr(VSafe(j, "errors"));
                if (errs != null)
                {
                    foreach (object e in errs) sb.AppendLine("· " + VStr(e));
                }
                _vNote.Text = sb.ToString();
                _vNote.Height = 48;
                _vNote.Visible = true;
                _vParams.Visible = true;
                _vToggle.Text = "收起参数";
                _vHint.Text = "请在下方补充参数后重算";
                _vHint.Tag = "muted";
                return;
            }

            _vLast = j;
            _vActive = null;
            var scenarios = VArr(VSafe(j, "scenarios"));
            if (scenarios != null)
            {
                foreach (Dictionary<string, object> s in scenarios)
                {
                    if (_vActive == null) _vActive = VStr(VSafe(s, "key"));
                }
            }
            ValuationFillAuto(j);
            ValuationRenderResult(j);
            ValuationRenderSteps();

            var ah = VMap(VSafe(j, "assumptions"));
            _vHint.Text = "已按 " + VRate(VNum(VSafe(ah, "discount_rate"))) + " 贴现率计算";
            _vHint.Tag = "muted";
        }

        private void ValuationFillAuto(Dictionary<string, object> j)
        {
            var m = VMap(VSafe(j, "market"));
            var f = VMap(VSafe(j, "finance"));
            if (m != null)
            {
                VFill(_vPrice, VSafe(m, "price"));
                VFill(_vShares, VSafe(m, "shares"));
            }
            if (f != null) VFill(_vBase, VSafe(f, "net_profit_base"));
        }

        private void VFill(TextBox tb, object val)
        {
            double? v = VNum(val);
            if (v == null) return;
            if ((tb.Tag as string) == "auto" || string.IsNullOrWhiteSpace(tb.Text))
            {
                _vSuppress = true;
                tb.Text = v.Value.ToString();
                tb.Tag = "auto";
                _vSuppress = false;
            }
        }

        private void ValuationRenderResult(Dictionary<string, object> j)
        {
            var m = VMap(VSafe(j, "market"));
            var f = VMap(VSafe(j, "finance"));
            var g = VMap(VSafe(j, "growth"));
            var a = VMap(VSafe(j, "assumptions"));

            _vKpiRow1.Controls.Clear();
            _vKpiRow2.Controls.Clear();

            AddKpi(_vKpiRow1, "股票", VStr(VSafe(j, "name")), VStr(VSafe(j, "code")));
            AddKpi(_vKpiRow1, "当前股价", VFmt(VNum(VSafe(m, "price"))) + " 元", VStr(VSafe(m, "price_source")));
            AddKpi(_vKpiRow1, "总股本", VFmt(VNum(VSafe(m, "shares")), 4) + " 亿股", VStr(VSafe(m, "shares_source")));

            string np = VStr(VSafe(f, "indicator"));
            string period = VStr(VSafe(f, "period"));
            if (period != "") np = (np == "" ? "" : np + " · ") + period;
            AddKpi(_vKpiRow2, "期初净利润", VFmt(VNum(VSafe(f, "net_profit_base"))) + " 亿", np);

            double? cagr = VNum(VSafe(g, "cagr"));
            object cons;
            bool fromC = (g != null && g.TryGetValue("from_consensus", out cons) && cons is bool && (bool)cons);
            AddKpi(_vKpiRow2, "复合增长率（" + VStr(VSafe(f, "forecast_years")) + " 年）",
                cagr != null ? VFmtPct(cagr) : "走兜底",
                fromC ? "机构预测 " + VFmt(VNum(VSafe(f, "net_profit_forecast"))) + " 亿" : "未提供机构预测，按模板兜底");

            AddKpi(_vKpiRow2, "贴现率 / 永续增长",
                VRate(VNum(VSafe(a, "discount_rate"))) + " / " + VRate(VNum(VSafe(a, "perpetual_growth"))),
                "前段 " + VStr(VSafe(a, "stage1_years")) + " 年 + 永续");

            _vGrid.Rows.Clear();
            _vScenario.Items.Clear();
            var scenarios = VArr(VSafe(j, "scenarios"));
            if (scenarios != null)
            {
                foreach (Dictionary<string, object> s in scenarios)
                {
                    _vScenario.Items.Add(VStr(VSafe(s, "key")));
                    string verdict = VStr(VSafe(s, "verdict"));
                    string cls = (verdict == "低估" || verdict == "高估") ? verdict : "合理";
                    _vGrid.Rows.Add(
                        VStr(VSafe(s, "label")),
                        VFmtPct(VNum(VSafe(s, "growth"))),
                        VFmt(VNum(VSafe(s, "value_per_share"))) + " 元",
                        VFmt(VNum(VSafe(s, "undervalued_ratio")), 4),
                        cls,
                        VFmt(VNum(VSafe(s, "target_price"))) + " 元",
                        VFmtPct(VNum(VSafe(s, "return_rate"))));
                }
                if (_vScenario.Items.Count > 0) _vScenario.SelectedIndex = 0;
            }

            _vNote.Text = "怎么读：「股价 / 价值」小于 1 表示低估、大于 1 表示高估（按 <0.9 低估、0.9~1.1 合理、>1.1 高估划分）；"
                + "三档增长率：乐观 = 机构预测复合增速 × 1.5、中性 = 机构预测复合增速、悲观固定 5%；未提供机构预测时兜底 25% / 10% / 5%。模型结果为估算，不构成投资建议。";
            _vNote.Height = 64;
            _vNote.Visible = true;

            if (scenarios != null && scenarios.Count > 0)
            {
                var first = scenarios[0] as Dictionary<string, object>;
                if (first != null)
                {
                    _vStatusResult.Text = "股价 / 价值 " + VFmt(VNum(VSafe(first, "undervalued_ratio")), 3);
                    _vStatusResult.ForeColor = Color.FromArgb(30, 126, 52);
                }
            }
        }

        private void ValuationRenderSteps()
        {
            if (_vLast == null)
            {
                _vSteps.Text = "计算过程将在此逐步展示（增长率 → 各年贴现 → 永续 → 每股价值）";
                _vStatusSteps.Text = "待计算";
                return;
            }

            var scenarios = VArr(VSafe(_vLast, "scenarios"));
            Dictionary<string, object> sel = null;
            if (scenarios != null)
            {
                foreach (Dictionary<string, object> s in scenarios)
                {
                    if (VStr(VSafe(s, "key")) == _vActive) sel = s;
                }
                if (sel == null && scenarios.Count > 0) sel = scenarios[0] as Dictionary<string, object>;
            }
            if (sel == null) return;

            var sb = new StringBuilder();
            var g = VMap(VSafe(_vLast, "growth"));
            var gsteps = VArr(VSafe(g, "steps"));
            if (gsteps != null && gsteps.Count > 0)
            {
                var first = gsteps[0] as Dictionary<string, object>;
                if (first != null)
                {
                    sb.AppendLine("1. " + VStr(VSafe(first, "label")));
                    sb.AppendLine("   " + VStr(VSafe(first, "formula")));
                    sb.AppendLine("   " + VStr(VSafe(first, "detail")) + "  =  " + VFmt(VNum(VSafe(first, "value")), 4));
                }
            }
            int i = 2;
            var st = VArr(VSafe(sel, "steps"));
            if (st != null)
            {
                foreach (Dictionary<string, object> x in st)
                {
                    sb.AppendLine(i + ". " + VStr(VSafe(x, "label")));
                    sb.AppendLine("   " + VStr(VSafe(x, "formula")));
                    sb.AppendLine("   " + VStr(VSafe(x, "detail")) + "  =  " + VFmt(VNum(VSafe(x, "value")), 4));
                    i++;
                }
            }
            var notes = VArr(VSafe(_vLast, "notes"));
            if (notes != null && notes.Count > 0)
            {
                var parts = new List<string>();
                foreach (object n in notes) parts.Add(VStr(n));
                sb.AppendLine("");
                sb.AppendLine("说明：" + string.Join("；", parts.ToArray()));
            }
            _vSteps.Text = sb.ToString();
            _vStatusSteps.Text = (i - 1) + " 步";
        }

        private void ValuationShowError(string msg)
        {
            _vKpiRow1.Controls.Clear();
            _vKpiRow2.Controls.Clear();
            _vGrid.Rows.Clear();
            _vStatusResult.Text = "失败";
            _vStatusResult.ForeColor = Color.FromArgb(208, 57, 59);
            _vNote.Text = msg;
            _vNote.Height = 32;
            _vNote.Visible = true;
        }

        /// <summary>向本机后端发请求：body=null 为 GET，否则 POST(JSON)。返回响应体；HTTP 4xx/5xx 时返回错误体便于显示。</summary>
        private static string VRequest(string url, string body)
        {
            var req = (HttpWebRequest)WebRequest.Create(url);
            req.Proxy = null;                 // 本机直连，绕开系统代理 / 自动发现
            req.KeepAlive = false;
            req.Timeout = 90000;
            req.ReadWriteTimeout = 90000;
            req.Method = (body == null) ? "GET" : "POST";
            if (body != null)
            {
                req.ServicePoint.Expect100Continue = false;
                req.ContentType = "application/json; charset=utf-8";
                byte[] data = Encoding.UTF8.GetBytes(body);
                req.ContentLength = data.Length;
                using (var s = req.GetRequestStream()) s.Write(data, 0, data.Length);
            }
            HttpWebResponse resp = null;
            try
            {
                resp = (HttpWebResponse)req.GetResponse();
            }
            catch (WebException we)
            {
                if (we.Response != null) resp = (HttpWebResponse)we.Response;
                else throw;
            }
            using (resp)
            {
                using (var sr = new StreamReader(resp.GetResponseStream(), Encoding.UTF8))
                {
                    return sr.ReadToEnd();
                }
            }
        }

        // ---- JSON 取值辅助（后端返回嵌套字典 + 数组）----
        private static Dictionary<string, object> VMap(object o)
        {
            return o as Dictionary<string, object>;
        }

        private static System.Collections.ArrayList VArr(object o)
        {
            if (o == null) return null;
            if (o is System.Collections.ArrayList) return (System.Collections.ArrayList)o;
            if (o is object[])
            {
                var arr = (object[])o;
                var al = new System.Collections.ArrayList();
                foreach (object x in arr) al.Add(x);
                return al;
            }
            return null;
        }

        private static object VSafe(Dictionary<string, object> d, string k)
        {
            if (d == null) return null;
            object v;
            if (d.TryGetValue(k, out v)) return v;
            return null;
        }

        private static double? VNum(object o)
        {
            if (o == null) return null;
            if (o is double) return (double)o;
            // JavaScriptSerializer 会把 JSON 小数反序列化成 decimal，必须显式处理
            if (o is decimal) return Convert.ToDouble(o);
            if (o is float) return Convert.ToDouble(o);
            if (o is int) return (int)o;
            if (o is long) return (long)o;
            if (o is string)
            {
                double dv;
                if (double.TryParse((string)o, out dv)) return dv;
            }
            return null;
        }

        private static string VStr(object o)
        {
            if (o == null) return "";
            return Convert.ToString(o);
        }

        private static string VFmt(double? v, int d = 2)
        {
            if (v == null) return "—";
            return v.Value.ToString("F" + d);
        }

        private static string VFmtPct(double? v)
        {
            if (v == null) return "—";
            return (v.Value > 0 ? "+" : "") + (v.Value * 100).ToString("F2") + "%";
        }

        /// <summary>不带正负号的百分比（用于贴现率 / 永续增长等口径值）。</summary>
        private static string VRate(double? v)
        {
            if (v == null) return "—";
            return (v.Value * 100).ToString("F2") + "%";
        }

        #endregion
    }
}
