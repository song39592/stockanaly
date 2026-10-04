using System;
using System.Collections;
using System.Collections.Generic;
using System.Drawing;
using System.Threading.Tasks;
using System.Windows.Forms;
using System.Web.Script.Serialization;

namespace StockPool
{
    /// <summary>
    /// 筹码体系 · SCR90 周级三档（原生内嵌标签页）：MainForm 的拆分文件。
    ///
    /// 数据来自本机后端（见 backend_fastapi/chip_rank_service.py）：
    ///   GET  /api/chip/rank?limit=100   本周分析结果（含三档）
    ///   GET  /api/chip/rank/status      计算进度
    ///   POST /api/chip/rank/refresh     触发本周计算（后台线程）
    ///
    /// 看的是**周级变化**，不是静态榜单（这里**完全用本地日线**自己算筹码分布）：
    ///   · 连续 5 周都还在「SCR90 最小的前 N」里的有多少、是哪几只（磨主峰）
    ///   · 本周离开的是哪些
    ///   · 离开时是**上涨离开**（启动型，涨幅达阈值）还是**下跌离开**（破位）
    ///
    /// 口径：SCR90 = (P95 - P5) / (P95 + P5)，值越**小**筹码越集中。
    /// 批量不联网（避免几千只打外部接口），故**未做锁仓修正**。
    /// </summary>
    internal sealed partial class MainForm
    {
        private sealed class ScrViewItem
        {
            public string Key;
            public string Text;
            public override string ToString() { return Text; }
        }

        private int _scrTabIndex = -1;
        private DataGridView _scrGrid;
        private ComboBox _scrView;
        private Label _scrStatus;
        private Label _scrInfo;
        private Timer _scrTimer;
        private bool _scrBusy;
        private Dictionary<string, object> _scrPayload;

        private Panel BuildScr90Page()
        {
            var p = NewPage("SCR90周榜");
            _scrTabIndex = _tabPages.Count - 1;
            p.AutoScroll = true;

            var root = new FlowLayoutPanel();
            root.Dock = DockStyle.Top;
            root.AutoSize = true;
            root.AutoSizeMode = AutoSizeMode.GrowAndShrink;
            root.FlowDirection = FlowDirection.TopDown;
            root.WrapContents = false;
            root.Margin = new Padding(0, 0, 0, 10);
            root.Padding = new Padding(0);
            p.Controls.Add(root);

            // 第一行：标题 + 两个触发按钮 + 状态
            var head = new FlowLayoutPanel();
            head.AutoSize = true;
            head.WrapContents = false;
            head.Margin = new Padding(0, 0, 0, 6);
            head.Controls.Add(Lbl("SCR90 周级三档"));
            head.Controls.Add(MiniBtn("刷新本周", delegate { ScrRefresh(false); }, 92));
            head.Controls.Add(MiniBtn("强制重算", delegate { ScrRefresh(true); }, 92));
            _scrStatus = Mute(Lbl("尚未加载"));
            head.Controls.Add(_scrStatus);
            root.Controls.Add(head);

            // 第二行：视图切换（三档 + 最新一期榜单）
            var viewRow = new FlowLayoutPanel();
            viewRow.AutoSize = true;
            viewRow.WrapContents = false;
            viewRow.Margin = new Padding(0, 0, 0, 4);
            viewRow.Controls.Add(Lbl("查看"));
            _scrView = new ComboBox();
            _scrView.DropDownStyle = ComboBoxStyle.DropDownList;
            _scrView.Width = 210;
            _scrView.Items.Add(new ScrViewItem { Key = "stay", Text = "连续在榜 · 磨主峰" });
            _scrView.Items.Add(new ScrViewItem { Key = "up", Text = "上涨离榜 · 启动型" });
            _scrView.Items.Add(new ScrViewItem { Key = "down", Text = "下跌离榜 · 破位" });
            _scrView.Items.Add(new ScrViewItem { Key = "current", Text = "最新一期榜单" });
            _scrView.SelectedIndex = 0;
            _scrView.SelectedIndexChanged += delegate { ScrRender(); };
            viewRow.Controls.Add(_scrView);
            root.Controls.Add(viewRow);

            _scrInfo = Mute(Lbl(""));
            _scrInfo.MaximumSize = new Size(820, 0);
            root.Controls.Add(_scrInfo);

            _scrGrid = ScrGrid(new[] { "#", "代码", "名称", "SCR90", "收盘", "涨幅%",
                                       "在榜周数", "最近在榜" });
            _scrGrid.Width = 900;
            _scrGrid.Height = 560;
            root.Controls.Add(_scrGrid);

            _scrTimer = new Timer();
            _scrTimer.Interval = 2000;
            _scrTimer.Tick += delegate { ScrPoll(); };
            return p;
        }

        private void ScrOnEnter()
        {
            if (_scrGrid == null) return;
            if (_scrGrid.Rows.Count > 0) return;
            ScrLoadResult();
        }

        // ---- 数据加载 ----

        private void ScrRefresh(bool force)
        {
            if (_scrBusy) return;
            _scrBusy = true;
            ScrSetStatus(force ? "正在强制重算本周…" : "正在计算（全市场约 15~25 分钟，后台跑）…", true);
            var url = "http://127.0.0.1:8000/api/chip/rank/refresh"
                      + (force ? "?force=true" : "");
            Task.Run(delegate
            {
                string body;
                bool ok = PostJson(url, "{}", 15000, out body);
                Invoke((Action)delegate
                {
                    _scrBusy = false;
                    if (!ok)
                    {
                        ScrSetStatus("触发失败：" + ScrErr(body), true);
                        return;
                    }
                    ScrSetStatus("已在后台计算，自动刷新中…", true);
                    _scrTimer.Start();
                });
            });
        }

        private void ScrPoll()
        {
            if (_scrBusy) return;
            _scrBusy = true;
            Task.Run(delegate
            {
                string body;
                var j = ScrJson("http://127.0.0.1:8000/api/chip/rank/status", 10000, out body);
                Invoke((Action)delegate
                {
                    _scrBusy = false;
                    if (j == null)
                    {
                        _scrTimer.Stop();
                        ScrSetStatus("读取进度失败：" + ScrErr(body), true);
                        return;
                    }
                    string st = ScrStr(j, "state");
                    if (st == "running")
                    {
                        ScrSetStatus(string.Format("计算中 {0}/{1}（{2}%）…",
                                                   ScrNum(j, "done"), ScrNum(j, "total"),
                                                   ScrNum(j, "percent").ToString("F1")), true);
                        return;
                    }
                    _scrTimer.Stop();
                    if (st == "error")
                    {
                        ScrSetStatus("计算失败：" + ScrStr(j, "error"), true);
                        return;
                    }
                    ScrLoadResult();
                });
            });
        }

        private void ScrLoadResult()
        {
            if (_scrBusy) return;
            _scrBusy = true;
            Task.Run(delegate
            {
                string body;
                var j = ScrJson("http://127.0.0.1:8000/api/chip/rank?limit=100", 30000, out body);
                Invoke((Action)delegate
                {
                    _scrBusy = false;
                    if (j == null)
                    {
                        ScrSetStatus("读取结果失败：" + ScrErr(body), true);
                        return;
                    }
                    bool ok = j.ContainsKey("ok") && j["ok"] is bool && (bool)j["ok"];
                    if (!ok)
                    {
                        ScrSetStatus(ScrStr(j, "error"), true);
                        return;
                    }
                    _scrPayload = j;
                    ScrFillSummary();
                    ScrRender();
                });
            });
        }

        private void ScrFillSummary()
        {
            var j = _scrPayload;
            if (j == null) return;
            double stay = ScrTierCount(j, "stay");
            double up = ScrTierCount(j, "up");
            double down = ScrTierCount(j, "down");
            double weeks = ScrNum(ScrParams(j), "weeks");
            ScrSetStatus(string.Format("本周 {0} | 全市场 {1} 只，成功 {2} | "
                + "连续{3}周在榜 {4} 只；上涨离榜 {5} 只；下跌离榜 {6} 只 | 更新 {7}",
                ScrStr(j, "week"), ScrNum(j, "total"), ScrNum(j, "computed"),
                weeks, stay, up, down, ScrStr(j, "computed_at")), false);

            double launch = ScrNum(ScrParams(j), "launch_threshold");
            double chg = ScrNum(ScrParams(j), "chg_days");
            _scrInfo.Text = string.Format(
                "口径：SCR90=(P95-P5)/(P95+P5)，越小越集中；每期取最小的 {0} 只。"
                + "离开榜单按近 {1} 个交易日涨幅判定 —— ≥{2}% 为上涨离开（启动型），"
                + "未达为下跌离开（破位）。批量未做锁仓修正。",
                ScrNum(ScrParams(j), "top_n"), chg, launch);
        }

        private void ScrRender()
        {
            if (_scrGrid == null || _scrPayload == null) return;
            var it = _scrView.SelectedItem as ScrViewItem;
            if (it == null) return;

            ArrayList items;
            if (it.Key == "current")
                items = VArr(VSafe(_scrPayload, "current"));
            else
                items = ScrTierItems(_scrPayload, it.Key);

            var rows = new List<DataGridViewRow>();
            int no = 0;
            if (items != null)
            {
                foreach (Dictionary<string, object> d in items)
                {
                    no++;
                    string name = VStr(VSafe(d, "name"));
                    if (name.Length == 0) name = "—";
                    object chgObj = VSafe(d, "chg");
                    string chg = (chgObj == null) ? "—" : (ScrNum(d, "chg").ToString("F2") + "%");
                    var row = new DataGridViewRow();
                    row.CreateCells(_scrGrid,
                        no.ToString(),
                        VStr(VSafe(d, "code")),
                        name,
                        ScrNum(d, "scr90").ToString("F4"),
                        VSafe(d, "close") == null ? "—" : ScrNum(d, "close").ToString("F2"),
                        chg,
                        ScrNum(d, "weeks_on").ToString("F0"),
                        VStr(VSafe(d, "last_week")));
                    rows.Add(row);
                }
            }
            _scrGrid.Rows.Clear();
            foreach (DataGridViewRow r in rows) _scrGrid.Rows.Add(r);
            if (_scrGrid.Columns.Count > 0) _scrGrid.AutoResizeColumns(
                DataGridViewAutoSizeColumnsMode.DisplayedCells);
        }

        // ---- 小工具 ----

        private static DataGridView ScrGrid(string[] cols)
        {
            var g = new DataGridView();
            g.AllowUserToAddRows = false;
            g.ReadOnly = true;
            g.SelectionMode = DataGridViewSelectionMode.FullRowSelect;
            g.RowTemplate.Height = 24;
            g.AutoSizeColumnsMode = DataGridViewAutoSizeColumnsMode.DisplayedCells;
            g.ColumnHeadersHeightSizeMode = DataGridViewColumnHeadersHeightSizeMode.DisableResizing;
            for (int i = 0; i < cols.Length; i++) g.Columns.Add("c" + i, cols[i]);
            foreach (DataGridViewColumn c in g.Columns)
                c.SortMode = DataGridViewColumnSortMode.NotSortable;
            g.SelectionChanged += delegate { g.ClearSelection(); };
            return g;
        }

        private void ScrSetStatus(string text, bool failed)
        {
            if (_scrStatus == null) return;
            _scrStatus.Text = text;
            _scrStatus.Tag = "muted";
            _scrStatus.ForeColor = failed ? Color.FromArgb(201, 133, 0)
                                          : Color.FromArgb(150, 158, 172);
        }

        private static Dictionary<string, object> ScrParams(Dictionary<string, object> j)
        {
            if (j == null) return null;
            object v;
            if (j.TryGetValue("params", out v)) return v as Dictionary<string, object>;
            return null;
        }

        private static Dictionary<string, object> ScrTier(Dictionary<string, object> j, string key)
        {
            object tiers;
            if (j == null || !j.TryGetValue("tiers", out tiers)) return null;
            var t = tiers as Dictionary<string, object>;
            if (t == null) return null;
            object v;
            if (t.TryGetValue(key, out v)) return v as Dictionary<string, object>;
            return null;
        }

        private static double ScrTierCount(Dictionary<string, object> j, string key)
        {
            var t = ScrTier(j, key);
            return t == null ? 0 : ScrNum(t, "count");
        }

        private static ArrayList ScrTierItems(Dictionary<string, object> j, string key)
        {
            var t = ScrTier(j, key);
            return t == null ? null : VArr(VSafe(t, "items"));
        }

        private static Dictionary<string, object> ScrJson(string url, int timeoutMs, out string body)
        {
            body = "";
            if (!GetText(url, timeoutMs, out body) || string.IsNullOrEmpty(body)) return null;
            try
            {
                return new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(body);
            }
            catch { return null; }
        }

        private static string ScrStr(Dictionary<string, object> j, string key)
        {
            if (j == null) return "";
            object v;
            return j.TryGetValue(key, out v) ? (v == null ? "" : Convert.ToString(v)) : "";
        }

        private static double ScrNum(Dictionary<string, object> j, string key)
        {
            object v;
            if (j == null || !j.TryGetValue(key, out v) || v == null) return 0;
            try { return Convert.ToDouble(v); } catch { return 0; }
        }

        private static string ScrErr(string body)
        {
            if (string.IsNullOrEmpty(body)) return "后端未响应";
            return body.Length > 120 ? body.Substring(0, 120) : body;
        }
    }
}
