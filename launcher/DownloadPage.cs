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
        private ComboBox _dlYear;         // 起始年份（只精确到年，统一按该年 1 月 1 日）
        private Label _dlHint;
        private CheckBox _dlReference;
        private ProgressBar _dlBar;
        private Label _dlStatus;
        private Label _dlCount;
        private Button _dlBtnStart, _dlBtnPause, _dlBtnResume, _dlBtnCancel, _dlBtnRetry;
        private CheckBox _dlAuto, _dlIdle;
        private Label _dlAutoNote;
        private bool _dlSettingsLoading;          // 程序回填控件值时抑制保存事件
        private GroupBox _dlTdxGroup;             // 本地通达信区块（配置有效才显示）
        private Label _dlTdxNote;
        private Button _dlTdxBtn;
        private System.Windows.Forms.Timer _dlTimer;
        private string _dlTaskId = "";
        private string _dlLastStatus = "";
        private int _dlRound;                 // 本轮请求编号：上一轮的慢回调不再覆盖本轮

        /// <summary>历史数据下载：① 范围与参数 → ② 控制与进度。</summary>
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

            // ---- ① 下载范围与参数（范围 / 模式 / 并发 / 限速都固定，只留起始年份）----
            TableLayoutPanel b1;
            var g1 = Group("下载范围与参数", out b1);

            int thisYear = DateTime.Today.Year;
            _dlYear = new ComboBox();
            _dlYear.DropDownStyle = ComboBoxStyle.DropDownList;
            _dlYear.Width = 74;
            for (int year = thisYear; year >= 1990; year--) _dlYear.Items.Add(year.ToString());
            _dlYear.SelectedItem = (thisYear - 5).ToString();     // 默认最近 5 年
            _dlYear.SelectedIndexChanged += delegate { DlUpdateHint(); };
            _dlHint = Mute(Lbl("预计：—"));
            AddRow(b1, Row(Lbl("起始年份"), _dlYear, _dlHint));

            _dlReference = Check("同时下载复权因子 / 除权明细（用于复权与审计）", true);
            AddRow(b1, Row(_dlReference));
            AddRow(b1, Row(Mute(Lbl("范围固定为全市场 A 股，模式为完整下载（先近后远）；"
                + "并发与限速由后端统一控制。库里已覆盖该窗口的股票会自动跳过，不重复下载。"))));
            AddRow(b1, Row(Mute(Lbl("默认最近 5 年，够 MA60 / RPS 等现有分析用；"
                + "要更长历史就选更早的年份，数据源会自动截断到上市首日。"))));
            AddRow(stack, g1);

            // ---- ② 控制与进度 ----
            TableLayoutPanel b2;
            var g2 = Group("任务控制与进度", out b2);

            _dlBtnStart = MiniBtn("开始下载", delegate { DlStart("online"); }, 100);
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

            // ---- 后台自动化：每日自动更新 / 闲时补历史 ----
            _dlAuto = Check("每天自动更新数据", false);
            _dlAuto.Enabled = false;              // 等检测出「只缺最近 10 天」才放开
            _dlAuto.CheckedChanged += delegate { DlSaveSettings(); };
            _dlIdle = Check("后台闲时下载（补完整历史）", false);
            _dlIdle.CheckedChanged += delegate { DlSaveSettings(); };
            AddRow(b2, Row(_dlAuto, _dlIdle));

            _dlAutoNote = Mute(Lbl("正在检测数据完整性…"));
            _dlAutoNote.AutoSize = false;
            _dlAutoNote.Width = 700;
            _dlAutoNote.Height = 34;
            AddRow(b2, Row(_dlAutoNote));
            AddRow(b2, Row(Mute(Lbl("自动更新：每到一个固定时刻给已下载的股票补最新数据；"
                + "闲时下载：低并发接着上次没下完的股票继续补，够新了再转由自动更新维持。"
                + "两者同时只有一个在跑。"))));
            AddRow(stack, g2);

            // ---- ③ 本地通达信（配置了该目录才会显示）----
            TableLayoutPanel b4;
            var g4 = Group("同步通达信历史数据（本地）", out b4);
            _dlTdxNote = Mute(Lbl("未检测到通达信目录：到「设置」页配置后出现本功能"));
            _dlTdxNote.AutoSize = false;
            _dlTdxNote.Width = 700;
            _dlTdxNote.Height = 32;
            AddRow(b4, Row(_dlTdxNote));
            _dlTdxBtn = MiniBtn("同步通达信历史数据", delegate { DlStart("tdx"); }, 176);
            AddRow(b4, Row(_dlTdxBtn));
            AddRow(b4, Row(Mute(Lbl("只读本机 .day 文件（秒级、完全不联网），本机有几年的日线就同步几年。"
                + "本地数据没有换手率（缺流通股本），会保留库里已有的值。"))));
            g4.Visible = false;                  // 等检测到通达信目录再显示
            _dlTdxGroup = g4;
            AddRow(stack, g4);

            p.Controls.Add(stack);
            DlUpdateHint();
            DlRenderButtons("", 0);
            return p;
        }

        /// <summary>界面只选到「年」，统一按该年 1 月 1 日作为起始日。</summary>
        private string DlStartDate()
        {
            int year;
            if (_dlYear == null || !int.TryParse(Convert.ToString(_dlYear.SelectedItem), out year))
                year = DateTime.Today.Year - 5;
            return year.ToString("0000") + "-01-01";
        }

        /// <summary>拉一次 /universe，把「多少只 · 多大 · 磁盘可用多少」显示出来。</summary>
        private void DlUpdateHint()
        {
            if (_dlHint == null) return;
            // 范围与模式固定，容量预估只随起始年份变化
            string query = "?scope=all&mode=full&source=online&start_date="
                           + Uri.EscapeDataString(DlStartDate());
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

        private void DlStart(string source)
        {
            if (_dlLastStatus == "running" || _dlLastStatus == "queued")
            {
                Msg("已有任务正在下载，请先暂停或取消");
                return;
            }
            // 范围 / 模式 / 并发 / 限速都不再暴露给界面：范围固定全市场，
            // 模式固定完整下载，并发与限速用后端默认值（4 / 3）。
            var body = new Dictionary<string, object>();
            body["scope"] = "all";
            body["source"] = source;
            body["mode"] = "full";
            body["start_date"] = DlStartDate();
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
            DlLoadAutoState(false);
            DlRefreshTdx();
            if (string.IsNullOrEmpty(_dlTaskId)) return;
            DlRefresh();
        }

        // ------------------------------------------------- 本地通达信

        /// <summary>查询本机通达信目录是否可用；可用才显示「同步通达信历史数据」。</summary>
        private void DlRefreshTdx()
        {
            if (_dlTdxGroup == null) return;
            var th = new Thread(delegate()
            {
                string resp;
                try
                {
                    if (!Probe(DlApi + "/tdx/status", 10000, out resp)) return;
                    var root = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(resp);
                    Ui(delegate { DlRenderTdx(root); });
                }
                catch { }
            });
            th.IsBackground = true;
            th.Start();
        }

        private void DlRenderTdx(Dictionary<string, object> root)
        {
            if (_dlTdxGroup == null || _dlTdxNote == null || _dlTdxBtn == null) return;
            bool valid = false;
            try { valid = Convert.ToBoolean(DlVal(root, "valid")); }
            catch { }
            if (!valid)
            {
                _dlTdxGroup.Visible = false;
                return;
            }
            int codes = DlInt(DlVal(root, "codes"));
            var sample = Convert.ToString(DlVal(root, "sample") ?? "");
            _dlTdxNote.Text = string.Format("本机通达信：{0} 只 A 股，数据覆盖 {1}。"
                + "同步只读取本地文件（秒级、不联网），不补更早的历史。", codes, sample);
            _dlTdxGroup.Visible = true;
        }

        // ------------------------------------------------- 后台自动化（开关状态）

        /// <summary>拉一次 /auto-state：既有开关的当前值，也有是否允许勾选。</summary>
        private void DlLoadAutoState(bool refresh)
        {
            if (_dlAuto == null) return;
            string query = "/auto-state" + (refresh ? "?refresh=true" : "");
            var th = new Thread(delegate()
            {
                string resp;
                try
                {
                    if (!Probe(DlApi + query, 30000, out resp)) return;
                    var root = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(resp);
                    Ui(delegate { DlRenderAutoState(root); });
                }
                catch { }
            });
            th.IsBackground = true;
            th.Start();
        }

        private void DlRenderAutoState(Dictionary<string, object> root)
        {
            int downloaded = DlInt(DlVal(root, "downloaded"));
            bool eligible = false;
            try { eligible = Convert.ToBoolean(DlVal(root, "eligible")); }
            catch { }

            _dlSettingsLoading = true;            // 回填控件不算用户操作，别触发保存
            try
            {
                try { _dlAuto.Checked = Convert.ToBoolean(DlVal(root, "auto_update")); }
                catch { }
                try { _dlIdle.Checked = Convert.ToBoolean(DlVal(root, "idle_download")); }
                catch { }
                _dlAuto.Enabled = eligible;
            }
            finally { _dlSettingsLoading = false; }

            // 只报「数据更新到哪天」：还差多少只由「自动更新」开关能否点开体现，不再堆提示文字
            string latest = Convert.ToString(DlVal(root, "latest_date") ?? "");
            if (downloaded == 0) _dlAutoNote.Text = "库里还没有日K数据：先「开始下载」一次。";
            else if (!string.IsNullOrEmpty(latest)) _dlAutoNote.Text = "数据已更新到 " + latest;
            else _dlAutoNote.Text = string.Format("已下载 {0} 只。", downloaded);
        }

        /// <summary>保存后台开关；服务端会即时生效（不等下一个轮询周期）。</summary>
        private void DlSaveSettings()
        {
            if (_dlSettingsLoading || _dlAuto == null || _dlIdle == null) return;

            var body = new Dictionary<string, object>();
            body["auto_update"] = _dlAuto.Checked;
            body["idle_download"] = _dlIdle.Checked;
            body["idle_concurrency"] = 2;         // 闲时并发固定 2，不在界面暴露
            string json = new JavaScriptSerializer().Serialize(body);
            _dlAutoNote.Text = "正在保存…";

            var th = new Thread(delegate()
            {
                string resp;
                if (!PostJson(DlApi + "/settings", json, 20000, out resp))
                {
                    var err = JsonValue(resp, "detail");
                    Ui(delegate
                    {
                        Msg("设置未生效：" + (string.IsNullOrEmpty(err) ? resp : err));
                        DlLoadAutoState(true);           // 回读真实状态
                    });
                    return;
                }
                Ui(delegate
                {
                    DlLoadAutoState(true);
                    if (_dlAuto.Checked || _dlIdle.Checked) DlPickLatestTask();
                });
            });
            th.IsBackground = true;
            th.Start();
        }

        /// <summary>把界面切到刚由后台自动发起的那个任务，方便看进度。</summary>
        private void DlPickLatestTask()
        {
            var th = new Thread(delegate()
            {
                string resp;
                try
                {
                    if (!Probe(DlApi + "/list", 10000, out resp)) return;
                    var root = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(resp);
                    var tasks = DlVal(root, "tasks") as ArrayList;
                    if (tasks == null || tasks.Count == 0) return;
                    var first = tasks[0] as Dictionary<string, object>;
                    if (first == null) return;
                    var id = Convert.ToString(DlVal(first, "id") ?? "");
                    if (string.IsNullOrEmpty(id)) return;
                    Ui(delegate { _dlTaskId = id; DlRefresh(); DlStartPoll(); });
                }
                catch { }
            });
            th.IsBackground = true;
            th.Start();
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
            // 来源决定用词：本地通达信是「同步」（读本机文件、不联网），在线才是「下载」
            var source = Convert.ToString(DlVal(DlVal(task, "options"), "source") ?? "");

            int value = (int)Math.Round(percent * 10);
            _dlBar.Value = Math.Max(0, Math.Min(_dlBar.Maximum, value));
            _dlStatus.Text = "状态：" + DlStatusText(status, source)
                + (string.IsNullOrEmpty(_dlTaskId) ? "" : "　任务 " + _dlTaskId);
            int recent = DlInt(DlVal(task, "phase1_done"));
            int skipped = DlInt(DlVal(task, "skipped"));
            var extra = "";
            if (recent > 0) extra += string.Format("　近期阶段已覆盖 {0} 只", recent);
            if (skipped > 0) extra += string.Format("　跳过 {0} 只（库里已足量）", skipped);
            _dlCount.Text = string.Format("已完成 {0} / 共 {1}　失败 {2}　剩余 {3}　进度 {4:0.0}%{5}",
                                          completed, total, failed, remaining, percent, extra);

            DlRenderButtons(status, failed);
            if (status == "completed" || status == "cancelled")
            {
                DlStopPoll();
                // 跑完一轮后重算「是否只缺最近 10 天」——可能刚好攒够资格开启自动更新
                DlLoadAutoState(true);
            }
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
            if (_dlTdxBtn != null) _dlTdxBtn.Enabled = !active && status != "paused";
            if (string.IsNullOrEmpty(status) && failed == 0) _dlBtnStart.Enabled = true;
        }

        /// <summary>状态文案按数据来源区分：本地通达信是「同步」，在线才是「下载」。</summary>
        private static string DlStatusText(string status, string source)
        {
            bool tdx = source == "tdx";
            switch (status)
            {
                case "queued": return tdx ? "排队中（本地同步）" : "排队中";
                case "running": return tdx ? "本地同步中（读本机文件，不联网）" : "下载中（联网）";
                case "paused": return "已暂停";
                case "completed": return tdx ? "同步完成" : "下载完成";
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
