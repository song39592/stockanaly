using System;
using System.Collections;
using System.Collections.Generic;
using System.Drawing;
using System.Globalization;
using System.Threading;
using System.Windows.Forms;
using System.Web.Script.Serialization;

namespace StockPool
{
    /// <summary>
    /// 历史数据下载（原生内嵌标签页）：驱动后端 /api/history/download/*。
    /// 遍历编排（worker 池 / 限速 / 断点续传 / 失败重试）都在后端，
    /// 这里只负责下发参数、轮询进度、做暂停 / 继续 / 取消 / 重试失败项的控制，
    /// 因此关掉窗口甚至重启后端都不会让任务丢——重新打开点「继续」即可。
    /// </summary>
    internal sealed partial class MainForm
    {
        private const string DlApi = "http://127.0.0.1:8000/api/history/download";

        private int _dlTabIndex = -1;
        private ComboBox _dlScope;
        private ComboBox _dlMode;
        private Label _dlHint;
        private TextBox _dlStart;
        private ComboBox _dlConcurrency;
        private TextBox _dlRate;
        private CheckBox _dlReference;
        private ProgressBar _dlBar;
        private Label _dlStatus;
        private Label _dlCount;
        private DataGridView _dlFailures;
        private Button _dlBtnStart, _dlBtnPause, _dlBtnResume, _dlBtnCancel, _dlBtnRetry;
        private System.Windows.Forms.Timer _dlTimer;
        private string _dlTaskId = "";
        private string _dlLastStatus = "";
        private int _dlRound;                 // 本轮请求编号：上一轮的慢回调不再覆盖本轮

        /// <summary>历史数据下载：① 范围与参数 → ② 控制与进度 → ③ 失败清单。</summary>
        private Panel BuildDownloadPage()
        {
            var p = NewPage("历史数据下载");
            _dlTabIndex = _tabPages.Count - 1;

            var stack = Stack();

            var title = Lbl("⬇ 历史数据下载");
            title.Font = new Font("Microsoft YaHei UI", 14f, FontStyle.Bold);
            title.Margin = new Padding(0, 0, 0, 2);
            AddRow(stack, Row(title));

            var sub = Mute(Lbl("按代码逐只遍历下载完整历史日K（可带复权因子 / 除权明细）。"
                + "后端 worker 池并发 + 限速，支持暂停 / 继续 / 取消 / 重试失败项；任务存在库里，重启后端后可继续。"));
            sub.AutoSize = false;
            sub.Width = 760;
            sub.Height = 36;
            AddRow(stack, Row(sub));

            // ---- ① 范围与参数 ----
            TableLayoutPanel b1;
            var g1 = Group("下载范围与参数", out b1);

            _dlScope = new ComboBox();
            _dlScope.DropDownStyle = ComboBoxStyle.DropDownList;
            _dlScope.Width = 140;
            _dlScope.Items.AddRange(new object[] { "全市场 A 股", "当前股票池" });
            _dlScope.SelectedIndex = 0;
            _dlScope.SelectedIndexChanged += delegate { DlSyncInputs(); };
            AddRow(b1, Row(Lbl("范围"), _dlScope,
                Mute(Lbl("全市场约 5000 只，完整日K 合计约 2.5 GB，耗时以小时计"))));

            _dlMode = new ComboBox();
            _dlMode.DropDownStyle = ComboBoxStyle.DropDownList;
            _dlMode.Width = 160;
            _dlMode.Items.AddRange(new object[] { "完整历史（由近到远）", "增量更新（只补最新）" });
            _dlMode.SelectedIndex = 0;
            _dlMode.SelectedIndexChanged += delegate { DlSyncInputs(); };
            _dlHint = Mute(Lbl("预计：—"));
            AddRow(b1, Row(Lbl("模式"), _dlMode, _dlHint));
            AddRow(b1, Row(Mute(Lbl("完整历史：全市场先跑最近 3 年，再回头补更早历史；"
                + "增量更新：按库里最新日期只补最近几天，用于日常更新"))));

            _dlStart = new TextBox();
            _dlStart.Width = 100;
            _dlStart.Text = "1990-01-01";
            _dlConcurrency = new ComboBox();
            _dlConcurrency.DropDownStyle = ComboBoxStyle.DropDownList;
            _dlConcurrency.Width = 64;
            _dlConcurrency.Items.AddRange(new object[] { "1", "2", "4", "6", "8" });
            _dlConcurrency.SelectedItem = "4";
            _dlRate = new TextBox();
            _dlRate.Width = 56;
            _dlRate.Text = "3";
            AddRow(b1, Row(Lbl("起始日期"), _dlStart, Lbl("并发"), _dlConcurrency,
                Lbl("限速(次/秒)"), _dlRate));
            AddRow(b1, Row(Mute(Lbl("起始日期早于上市日即可拿到完整历史（数据源会自动截断）"))));

            _dlReference = Check("同时下载复权因子 / 除权明细（用于复权与审计）", true);
            AddRow(b1, Row(_dlReference));
            AddRow(stack, g1);

            // ---- ② 控制与进度 ----
            TableLayoutPanel b2;
            var g2 = Group("任务控制与进度", out b2);

            _dlBtnStart = MiniBtn("开始下载", delegate { DlStart(); }, 100);
            _dlBtnPause = MiniBtn("暂停", delegate { DlControl("pause"); }, 80);
            _dlBtnResume = MiniBtn("继续", delegate { DlControl("resume"); }, 80);
            _dlBtnCancel = MiniBtn("取消", delegate { DlControl("cancel"); }, 80);
            _dlBtnRetry = MiniBtn("重试失败项", delegate { DlControl("retry-failed"); }, 110);
            var refresh = MiniBtn("刷新", delegate { DlRefresh(); }, 80);
            AddRow(b2, Row(_dlBtnStart, _dlBtnPause, _dlBtnResume, _dlBtnCancel, _dlBtnRetry, refresh));

            _dlBar = new ProgressBar();
            _dlBar.Width = 430;
            _dlBar.Height = 20;
            _dlBar.Minimum = 0;
            _dlBar.Maximum = 1000;      // 进度保留一位小数：percent * 10
            _dlBar.Value = 0;
            AddRow(b2, Row(_dlBar));

            _dlStatus = Lbl("状态：未开始");
            AddRow(b2, Row(_dlStatus));
            _dlCount = Mute(Lbl(""));
            AddRow(b2, Row(_dlCount));
            AddRow(stack, g2);

            // ---- ③ 失败清单 ----
            TableLayoutPanel b3;
            var g3 = Group("失败清单（可点「重试失败项」只补这些）", out b3);
            _dlFailures = MktGrid(180, false, new string[] { "代码", "原因" });
            _dlFailures.ScrollBars = ScrollBars.Vertical;   // 失败可能很多，这里要能滚
            AddRow(b3, _dlFailures);
            AddRow(stack, g3);

            p.Controls.Add(stack);
            DlSyncInputs();
            DlRenderButtons("", 0);
            return p;
        }

        /// <summary>按「范围 / 模式」放开对应的输入框，并刷新容量提示。</summary>
        private void DlSyncInputs()
        {
            // 增量更新按库里最新日期补，不需要起始日
            bool full = _dlMode.SelectedIndex == 0;
            _dlStart.Enabled = full;
            _dlStart.BackColor = full ? SystemColors.Window : SystemColors.Control;

            DlUpdateHint();
        }

        /// <summary>拉一次 /universe，把「多少只 · 多大 · 磁盘可用多少」显示出来。</summary>
        private void DlUpdateHint()
        {
            if (_dlHint == null || _dlScope == null || _dlMode == null) return;
            string scope = "all";
            if (_dlScope.SelectedIndex == 1) scope = "pool";
            else if (_dlScope.SelectedIndex == 2) scope = "custom";
            string mode = _dlMode.SelectedIndex == 0 ? "full" : "incremental";
            string query = "?scope=" + scope + "&mode=" + mode;
            _dlHint.Text = "预计：计算中…";
            int round = ++_dlRound;
            var th = new Thread(delegate()
            {
                string resp;
                try
                {
                    if (!Probe(DlApi + "/universe" + query, 30000, out resp)) return;
                    var root = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(resp);
                    Ui(delegate { if (round == _dlRound) DlRenderHint(root); });
                }
                catch { }
            });
            th.IsBackground = true;
            th.Start();
        }

        private void DlRenderHint(Dictionary<string, object> root)
        {
            int count = DlInt(DlVal(root, "count"));
            double est = DlDbl(DlVal(root, "estimated_bytes"));
            double free = DlDbl(DlVal(root, "free_bytes"));
            bool ok = true;
            try { ok = Convert.ToBoolean(DlVal(root, "disk_ok")); }
            catch { }
            _dlHint.Text = string.Format("预计 {0} 只 · 约 {1} · 磁盘可用 {2}{3}",
                count, DlFmtBytes(est), free < 0 ? "未知" : DlFmtBytes(free),
                ok ? "" : "　⚠ 空间可能不足");
        }

        private static string DlFmtBytes(double size)
        {
            string[] units = { "B", "KB", "MB", "GB", "TB" };
            double value = size;
            foreach (var unit in units)
            {
                if (value < 1024 || unit == "TB") return string.Format("{0:0.0} {1}", value, unit);
                value /= 1024;
            }
            return string.Format("{0:0.0} TB", value);
        }

        // ---------------------------------------------------------------- 启动

        private void DlStart()
        {
            if (_dlLastStatus == "running" || _dlLastStatus == "queued")
            {
                Msg("已有任务正在下载，请先暂停或取消");
                return;
            }
            string scope = _dlScope.SelectedIndex == 1 ? "pool" : "all";
            string mode = _dlMode.SelectedIndex == 0 ? "full" : "incremental";

            double rate;
            if (!double.TryParse((_dlRate.Text ?? "").Trim(), NumberStyles.Any,
                                 CultureInfo.InvariantCulture, out rate)) rate = 3.0;
            int concurrency;
            if (!int.TryParse(Convert.ToString(_dlConcurrency.SelectedItem), out concurrency)) concurrency = 4;

            var body = new Dictionary<string, object>();
            body["scope"] = scope;
            body["mode"] = mode;
            body["start_date"] = (_dlStart.Text ?? "").Trim();
            body["concurrency"] = concurrency;
            body["rate_limit"] = rate;
            body["with_reference"] = _dlReference.Checked;
            string json = new JavaScriptSerializer().Serialize(body);

            _dlBtnStart.Enabled = false;
            _dlStatus.Text = "状态：正在启动…（全市场范围要先取一次 A 股代码清单，可能十几秒）";

            var th = new Thread(delegate()
            {
                string resp;
                // 全市场范围要先拉代码清单，超时给足 2 分钟
                if (!PostJson(DlApi + "/start", json, 120000, out resp))
                {
                    var err = JsonValue(resp, "detail");
                    if (string.IsNullOrEmpty(err)) err = resp;
                    Ui(delegate { Msg("启动失败：" + err); _dlBtnStart.Enabled = true; });
                    return;
                }
                var root = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(resp);
                Ui(delegate { DlRenderTask(root); });
            });
            th.IsBackground = true;
            th.Start();
        }

        // ---------------------------------------------------------------- 控制

        private void DlControl(string action)
        {
            if (string.IsNullOrEmpty(_dlTaskId)) { Msg("还没有下载任务，请先点「开始下载」"); return; }
            string id = _dlTaskId;
            var th = new Thread(delegate()
            {
                string resp;
                if (!PostJson(DlApi + "/" + id + "/" + action, "{}", 15000, out resp))
                {
                    var err = JsonValue(resp, "detail");
                    if (string.IsNullOrEmpty(err)) err = resp;
                    Ui(delegate { Msg("操作失败：" + err); });
                    return;
                }
                var root = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(resp);
                Ui(delegate { DlRenderTask(root); });
            });
            th.IsBackground = true;
            th.Start();
        }

        /// <summary>进入本页且有任务时，立刻拉一次进度并接着轮询。</summary>
        private void DlOnEnter()
        {
            DlUpdateHint();
            if (string.IsNullOrEmpty(_dlTaskId)) return;
            DlRefresh();
        }

        private void DlRefresh()
        {
            if (string.IsNullOrEmpty(_dlTaskId)) return;
            string id = _dlTaskId;
            int round = ++_dlRound;
            var th = new Thread(delegate()
            {
                string resp;
                try
                {
                    if (!Probe(DlApi + "/" + id, 8000, out resp)) return;
                    var root = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(resp);
                    Ui(delegate { if (round == _dlRound) DlRenderTask(root); });
                }
                catch { }
            });
            th.IsBackground = true;
            th.Start();
        }

        private void DlStartPoll()
        {
            if (_dlTimer == null)
            {
                _dlTimer = new System.Windows.Forms.Timer();
                _dlTimer.Interval = 1500;
                _dlTimer.Tick += delegate { DlRefresh(); };
            }
            _dlTimer.Enabled = true;
        }

        private void DlStopPoll()
        {
            if (_dlTimer != null) _dlTimer.Enabled = false;
        }

        // ---------------------------------------------------------------- 渲染

        private void DlRenderTask(Dictionary<string, object> root)
        {
            var task = DlDict(DlVal(root, "task"));
            if (task == null) return;

            var id = Convert.ToString(DlVal(task, "id") ?? "");
            if (!string.IsNullOrEmpty(id)) _dlTaskId = id;
            string status = Convert.ToString(DlVal(task, "status") ?? "");
            int total = DlInt(DlVal(task, "total"));
            int completed = DlInt(DlVal(task, "completed"));
            int failed = DlInt(DlVal(task, "failed"));
            int remaining = DlInt(DlVal(task, "remaining"));
            double percent = DlDbl(DlVal(task, "percent"));
            _dlLastStatus = status;

            int value = (int)Math.Round(percent * 10);
            _dlBar.Value = Math.Max(0, Math.Min(_dlBar.Maximum, value));
            _dlStatus.Text = "状态：" + DlStatusText(status)
                + (string.IsNullOrEmpty(_dlTaskId) ? "" : "　任务 " + _dlTaskId);
            int recent = DlInt(DlVal(task, "phase1_done"));
            _dlCount.Text = string.Format("已完成 {0} / 共 {1}　失败 {2}　剩余 {3}　进度 {4:0.0}%{5}",
                                          completed, total, failed, remaining, percent,
                                          recent > 0 ? string.Format("　近期阶段已覆盖 {0} 只", recent) : "");

            _dlFailures.Rows.Clear();
            var failures = DlVal(task, "failures") as ArrayList;
            if (failures != null)
            {
                foreach (var row in failures)
                {
                    var item = DlDict(row);
                    if (item == null) continue;
                    _dlFailures.Rows.Add(Convert.ToString(DlVal(item, "code") ?? ""),
                                         Convert.ToString(DlVal(item, "error") ?? ""));
                }
            }

            DlRenderButtons(status, failed);
            if (status == "completed" || status == "cancelled") DlStopPoll();
            else DlStartPoll();
        }

        /// <summary>按钮可用性跟着任务状态走，避免点到无效操作。</summary>
        private void DlRenderButtons(string status, int failed)
        {
            bool active = status == "running" || status == "queued";
            bool terminal = status == "completed" || status == "cancelled";
            _dlBtnStart.Enabled = !active && status != "paused";
            _dlBtnPause.Enabled = active;
            _dlBtnResume.Enabled = status == "paused";
            _dlBtnCancel.Enabled = active || status == "paused";
            _dlBtnRetry.Enabled = failed > 0 && !active;
            if (string.IsNullOrEmpty(status) && failed == 0) _dlBtnStart.Enabled = true;
        }

        private static string DlStatusText(string status)
        {
            switch (status)
            {
                case "queued": return "排队中";
                case "running": return "下载中";
                case "paused": return "已暂停";
                case "completed": return "已完成";
                case "cancelled": return "已取消";
                default: return string.IsNullOrEmpty(status) ? "未开始" : status;
            }
        }

        // ---- 取值小工具：JSON 反序列化出来都是 object，统一做安全转换 ----

        private static object DlVal(object container, string key)
        {
            var dict = container as Dictionary<string, object>;
            if (dict == null) return null;
            object value;
            return dict.TryGetValue(key, out value) ? value : null;
        }

        private static Dictionary<string, object> DlDict(object value)
        {
            return value as Dictionary<string, object>;
        }

        private static int DlInt(object value)
        {
            try { return Convert.ToInt32(value); }
            catch { return 0; }
        }

        private static double DlDbl(object value)
        {
            try { return Convert.ToDouble(value, CultureInfo.InvariantCulture); }
            catch { return 0; }
        }
    }
}
