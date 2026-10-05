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
        private StockGrid _scrGrid;
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
            head.Controls.Add(MiniBtn("重取名称", delegate { ScrFillNames(); }, 92));
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

            _scrGrid = NewGrid(new List<GridColumn> {
                new GridColumn("#", "rank", true),
                new GridColumn("代码", "code") { IsCode = true, Jump = true },
                NameColumn(),
                new GridColumn("SCR90", "scr90", true),
                new GridColumn("收盘", "close", true),
                new GridColumn("涨幅%", "chg", true),
                new GridColumn("在榜周数", "weeks_on", true),
                new GridColumn("最近在榜", "last_week"),
            });
            _scrGrid.Dock = DockStyle.None;         // 本页是流式布局，宽度显式给
            _scrGrid.Width = 900;
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

        /// <summary>名称与榜单计算解耦：算完后名称若没取到，单独补一次即可，不必重跑十几分钟。</summary>
        private void ScrFillNames()
        {
            if (_scrBusy) return;
            _scrBusy = true;
            ScrSetStatus("正在重取股票名称…", true);
            Task.Run(delegate
            {
                string body;
                bool ok = PostJson("http://127.0.0.1:8000/api/chip/rank/names", "{}", 120000, out body);
                Invoke((Action)delegate
                {
                    _scrBusy = false;
                    if (!ok)
                    {
                        ScrSetStatus("重取名称失败：" + ScrErr(body), true);
                        return;
                    }
                    ScrLoadResult();
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
                    string st = J.StrAt(j, "state");
                    if (st == "running")
                    {
                        ScrSetStatus(string.Format("计算中 {0}/{1}（{2}%）…",
                                                   J.NumAt(j, "done"), J.NumAt(j, "total"),
                                                   J.NumAt(j, "percent").ToString("F1")), true);
                        return;
                    }
                    _scrTimer.Stop();
                    if (st == "error")
                    {
                        ScrSetStatus("计算失败：" + J.StrAt(j, "error"), true);
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
                        ScrSetStatus(J.StrAt(j, "error"), true);
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
            double weeks = J.NumAt(ScrParams(j), "weeks");
            string nameTip = J.NumAt(j, "names_resolved") > 0 ? ""
                           : "　⚠ 名称未取到，点「重取名称」补上";
            ScrSetStatus(string.Format("本周 {0} | 全市场 {1} 只，成功 {2} | "
                + "连续{3}周在榜 {4} 只；上涨离榜 {5} 只；下跌离榜 {6} 只 | 更新 {7}{8}",
                J.StrAt(j, "week"), J.NumAt(j, "total"), J.NumAt(j, "computed"),
                weeks, stay, up, down, J.StrAt(j, "computed_at"), nameTip), false);

            double launch = J.NumAt(ScrParams(j), "launch_threshold");
            double chg = J.NumAt(ScrParams(j), "chg_days");
            _scrInfo.Text = string.Format(
                "口径：SCR90=(P95-P5)/(P95+P5)，越小越集中；每期取最小的 {0} 只。"
                + "离开榜单按近 {1} 个交易日涨幅判定 —— ≥{2}% 为上涨离开（启动型），"
                + "未达为下跌离开（破位）。批量未做锁仓修正。",
                J.NumAt(ScrParams(j), "top_n"), chg, launch);
        }

        private void ScrRender()
        {
            if (_scrGrid == null || _scrPayload == null) return;
            var it = _scrView.SelectedItem as ScrViewItem;
            if (it == null) return;

            ArrayList items;
            if (it.Key == "current")
                items = J.Arr(J.Get(_scrPayload, "current"));
            else
                items = ScrTierItems(_scrPayload, it.Key);

            var data = new List<string[]>();
            int no = 0;
            if (items != null)
            {
                foreach (Dictionary<string, object> d in items)
                {
                    no++;
                    string name = J.Str(J.Get(d, "name"));
                    if (name.Length == 0) name = "—";
                    object chgObj = J.Get(d, "chg");
                    string chg = (chgObj == null) ? "—" : (J.NumAt(d, "chg").ToString("F2") + "%");
                    string close = J.Get(d, "close") == null ? "—" : J.NumAt(d, "close").ToString("F2");
                    data.Add(new[] {
                        no.ToString(), J.Str(J.Get(d, "code")), name,
                        J.NumAt(d, "scr90").ToString("F4"), close, chg,
                        J.NumAt(d, "weeks_on").ToString("F0"), J.Str(J.Get(d, "last_week")) });
                }
            }
            _scrGrid.SetRows(data);
            _scrGrid.Fit(200, 560);
        }

        // ---- 小工具 ----

        private void ScrSetStatus(string text, bool failed)
        {
            if (_scrStatus == null) return;
            _scrStatus.Text = text;
            _scrStatus.Tag = "muted";
            _scrStatus.ForeColor = failed ? C.Warn
                                          : C.Flat;
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
            return t == null ? 0 : J.NumAt(t, "count");
        }

        private static ArrayList ScrTierItems(Dictionary<string, object> j, string key)
        {
            var t = ScrTier(j, key);
            return t == null ? null : J.Arr(J.Get(t, "items"));
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

        private static string ScrErr(string body)
        {
            if (string.IsNullOrEmpty(body)) return "后端未响应";
            return body.Length > 120 ? body.Substring(0, 120) : body;
        }
    }
}
