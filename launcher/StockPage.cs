using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Diagnostics;
using System.Text;
using System.Text.RegularExpressions;
using System.Windows.Forms;
using System.Web.Script.Serialization;

namespace StockPool
{
    /// <summary>
    /// 个股分析（原生内嵌标签页）：MainForm 的拆分文件，替代 frontend/stock-analysis.html。
    /// 数据来自本机后端：GET /api/history/stock/{code}（K线 / 入池出池轨迹 / 消息面），
    /// GET /api/stock/quote（名称与现价），POST /api/stock/valuation（估值）。
    /// 呈现贴近原网页：在榜统计 KPI + 自绘日 K 蜡烛图（MA5/10/20/60 + 成交量 + 入池/出池/事件标记）
    /// + 入池出池表 + 消息面时间轴 + 内联估值（完整过程见「估值计算」标签页）。
    /// </summary>
    internal sealed partial class MainForm
    {
        // ---- 个股分析页 ----
        private int _stockTabIndex = -1;
        private TextBox _stockCode;
        private Button _stockOpen, _stockRefresh;
        private Label _stockName, _stockHint, _stockStatus, _stockSyncStatus, _stockValStatus;
        private FlowLayoutPanel _stockKpi;                 // 在榜统计 KPI
        private KLineChart _stockKline;
        private int _stockRange = 250;                     // 0 = 全部
        private DataGridView _stockTimeline;                // 入池 / 出池记录
        private TableLayoutPanel _stockEvents;              // 消息面时间轴
        private Button _stockEventsToggle;                  // 消息面折叠按钮
        private System.Collections.ArrayList _stockEventData = new System.Collections.ArrayList();
        private bool _stockEventsExpanded = false;          // 默认折叠，仅显示前 3 条
        private FlowLayoutPanel _stockValKpi;               // 估值 KPI
        private DataGridView _stockValGrid;                 // 估值情景表
        private string _stockCurrent = null;
        private int _stockRound = 0;
        private readonly Dictionary<Button, int> _stockRangeMap = new Dictionary<Button, int>();

        #region 个股分析页（原生内嵌标签页）

        private Panel BuildStockPage()
        {
            var p = NewPage("个股分析");
            _stockTabIndex = _tabPages.Count - 1;

            var stack = Stack();

            var title = Lbl("📊 个股分析");
            title.Font = new Font("Microsoft YaHei UI", 14f, FontStyle.Bold);
            title.Margin = new Padding(0, 0, 0, 2);
            AddRow(stack, Row(title));

            var sub = Mute(Lbl("前复权日K（MA5/10/20/60）+ 入池出池轨迹 + 消息面时间轴｜数据来源：GET /api/history/stock/{code}"));
            sub.AutoSize = false;
            sub.Width = 760;
            sub.Height = 20;
            AddRow(stack, Row(sub));

            // ---- 查询区 ----
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
            var rangeRow = Row(Mute(Lbl("范围")));
            foreach (Button b in _stockRangeMap.Keys) rangeRow.Controls.Add(b);
            rangeRow.Controls.Add(_stockRefresh);
            rangeRow.Controls.Add(_stockStatus);
            AddRow(b0, rangeRow);
            AddRow(stack, g0);

            // ---- 在榜统计 ----
            TableLayoutPanel b1;
            var g1 = Group("个股概况（入池出池轨迹）", out b1);
            _stockKpi = VKpiRow();
            AddRow(b1, _stockKpi);
            AddRow(stack, g1);

            // ---- K 线 ----
            TableLayoutPanel b2;
            var g2 = Group("前复权日K线", out b2);
            _stockSyncStatus = Mute(Lbl("待加载"));
            AddRow(b2, Row(_stockSyncStatus));
            _stockKline = new KLineChart();
            _stockKline.Dock = DockStyle.Top;
            _stockKline.Height = 470;
            _stockKline.Tag = "kline";
            _stockKline.BackColorChanged += delegate { _stockKline.Invalidate(); };
            AddRow(b2, _stockKline);
            AddRow(stack, g2);

            // ---- 入池 / 出池 + 消息面（上下堆叠）----
            TableLayoutPanel b3;
            var g3 = Group("入池 / 出池记录", out b3);
            _stockTimeline = MktGrid(180, true, new string[] { "入池日期", "出池日期", "在榜天数", "状态" });
            AddRow(b3, _stockTimeline);
            AddRow(stack, g3);

            TableLayoutPanel b4;
            var g4 = Group("消息面时间轴", out b4);
            _stockEventsToggle = MiniBtn("展开全部", delegate
            {
                _stockEventsExpanded = !_stockEventsExpanded;
                StockRenderEvents(_stockEventData);
            }, 100);
            _stockEventsToggle.Visible = false;
            AddRow(b4, Row(_stockEventsToggle));
            _stockEvents = Stack();
            AddRow(b4, _stockEvents);
            AddRow(stack, g4);

            // ---- 估值（内联，完整过程见估值计算标签页）----
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
            AddRow(stack, g5);

            p.Controls.Add(stack);

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
            return p;
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
            StockLoadName(code);
            StockLoadHistory(code, false);
            StockLoadValuation(code);
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
                            _stockName.Text = VStr(VSafe(j, "name")) + (pr != null ? "  " + pr.Value.ToString("F2") + " 元" : "");
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
            _stockSyncStatus.Text = "加载中…";
            _stockSyncStatus.Tag = "muted";
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
            _stockStatus.Text = "加载失败";
            _stockStatus.Tag = "bad";
            _stockStatus.ForeColor = Color.FromArgb(208, 57, 59);
            _stockSyncStatus.Text = msg;
            _stockSyncStatus.ForeColor = Color.FromArgb(208, 57, 59);
            _stockKline.SetData(new List<KBar>(), new List<KMark>());
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

            // K 线
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

            _stockKline.SetData(bars, marks);
            StockRenderStats(spanList);
            StockRenderTimeline(spanList);
            StockRenderEvents(events);

            // 状态
            string last = bars.Count > 0 ? bars[bars.Count - 1].Date : "无数据";
            var sync = VMap(VSafe(j, "sync"));
            var syncErrs = VArr(sync != null ? VSafe(sync, "errors") : null);
            string errs = (syncErrs != null && syncErrs.Count > 0) ? "；部分失败：" + JoinErrs(syncErrs) : "";
            _stockSyncStatus.Text = "更新至 " + last + errs;
            _stockSyncStatus.Tag = "muted";
            _stockSyncStatus.ForeColor = Color.FromArgb(150, 158, 172);

            _stockStatus.Text = "前复权日K · " + bars.Count + " 个交易日 · 入池记录 " + spanList.Count + " 段" + errs;
            _stockStatus.Tag = "muted";
            _stockStatus.ForeColor = Color.FromArgb(150, 158, 172);
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
                _stockEventsToggle.Visible = false;
                _stockEvents.Controls.Add(Mute(Lbl("暂未采集到公告或新闻")));
                return;
            }
            int show = _stockEventsExpanded ? total : Math.Min(3, total);
            _stockEventsToggle.Visible = total > 3;
            _stockEventsToggle.Text = _stockEventsExpanded ? "收起" : ("展开全部 (" + total + ")");
            for (int i = 0; i < show; i++)
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

        private void StockOnEnter()
        {
            // 个股页需要代码才能加载，进入时不自动拉取；仅确保图表按当前主题重绘。
            if (_stockKline != null) _stockKline.Invalidate();
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

            private readonly List<KBar> _bars = new List<KBar>();
            private readonly List<KMark> _marks = new List<KMark>();
            private List<List<double>> _ma = new List<List<double>>();
            private int _range = 250;
            private readonly ToolTip _tip = new ToolTip();
            private int _hoverIndex = -1;

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

            public void SetData(List<KBar> bars, List<KMark> marks)
            {
                _bars.Clear();
                if (bars != null) _bars.AddRange(bars);
                _marks.Clear();
                if (marks != null) _marks.AddRange(marks);
                _ma = ComputeMA(_bars);
                Invalidate();
            }

            public void SetRange(int r)
            {
                _range = r;
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

            protected override void OnMouseMove(MouseEventArgs e)
            {
                base.OnMouseMove(e);
                int n = VisibleCount();
                if (n <= 0) { _hoverIndex = -1; _tip.Hide(this); return; }
                int plotW = Math.Max(20, Width - LeftPad - RightPad);
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

            private List<KBar> VisibleBars()
            {
                int n = VisibleCount();
                if (n <= 0) return new List<KBar>();
                return _bars.GetRange(_bars.Count - n, n);
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
                var maTail = new List<List<double>>();
                foreach (List<double> s in _ma)
                {
                    List<double> tail = s.Count >= n ? s.GetRange(s.Count - n, n) : s;
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
