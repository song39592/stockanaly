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
    internal sealed partial class MainForm
    {
        private void StockLoadHistory(string code, bool refresh)
        {
            int round = ++_stockRound;
            _stockStatus.Text = refresh ? "正在更新真实行情与消息…" : "正在读取K线与消息缓存…";
            _stockStatus.Tag = "muted";
            // 第 18 项：改用统一异步骨架。守卫语义与原来一字不差 ——
            // round 在发起请求前捕获，返回时若已不是当前轮就丢弃结果。
            RunUi<Dictionary<string, object>>(
                delegate
                {
                    string url = "http://127.0.0.1:8000/api/history/stock/" + code
                           + "?refresh=" + (refresh ? "true" : "false")
                           + "&period=" + (string.IsNullOrEmpty(_stockPeriod) ? "day" : _stockPeriod);
                    string resp = VRequest(url, null);
                    return new JavaScriptSerializer()
                        .Deserialize<Dictionary<string, object>>(resp);
                },
                delegate(Dictionary<string, object> j) { StockRenderHistory(j); },
                delegate(Exception ex) { StockFail("请求失败：" + ex.Message); },
                delegate { return round == _stockRound; });
        }


        private void StockRenderHistory(Dictionary<string, object> j)
        {
bool ok = J.IsOk(j);
            if (!ok)
            {
                string emsg = J.ErrMsg(j);
                if (string.IsNullOrEmpty(emsg)) emsg = "加载失败";
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
                    // 跳过坏柱（bug-04）：O/H/L 缺失被补成 0 的柱会把价格刻度
                    // 撑到负值（红烛从图例顶贯到绘图区底）。入库已在后端拒收，这里再兜一层。
                    if (b.O <= 0 || b.H <= 0 || b.L <= 0 || b.C <= 0 || b.H < b.L) continue;
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
                    if (b.O <= 0 || b.H <= 0 || b.L <= 0 || b.C <= 0 || b.H < b.L) continue;   // 坏柱（bug-04）
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

            // ⭐ 自愈补染（bug：K 线图偶发整块变白，用户重启才能恢复）：
            // 截图证据显示白块的图表控件处于「从未被 Skin 过」的默认配色（黑字白底图例），
            // 而其左侧轴刻度仍是深色旧帧 —— 但静态排查未能确证是哪条路径漏染
            // （Select(0) 会 Skin(page)、ApplyTheme 会递归已挂载控件，理论上都覆盖得到）。
            // 与其继续猜根因，这里在**每次数据渲染前**重新断言一次主题：
            // Skin 幂等（同色重设无视觉扰动），此后图表最多「白一帧」，不可能白到重启。
            Skin(_stockKline);
            Skin(_stockChip);
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
    }
}
