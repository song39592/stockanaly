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
        private CheckBox _dlAuto, _dlIdle;
        private ComboBox _dlIdleConc;
        private Label _dlAutoNote;
        private bool _dlSettingsLoading;          // 程序回填控件值时抑制保存事件
        private GroupBox _dlTdxGroup;             // 本地通达信区块（配置有效才显示）
        private Label _dlTdxNote;
        private Button _dlTdxBtn;
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
                + "增量更新：按库里最新日期只补最近几天，用于日常更新。"
                + "库里已覆盖该窗口的股票会自动跳过，不重复下载。"))));

            _dlStart = new TextBox();
            _dlStart.Width = 100;
            _dlStart.Text = DateTime.Today.AddYears(-5).ToString("yyyy-MM-dd");   // 默认最近 5 年
            _dlStart.Leave += delegate { DlUpdateHint(); };                       // 改完重算容量提示
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
            AddRow(b1, Row(Mute(Lbl("默认最近 5 年，可直接改：改成 1990-01-01 即拿到上市至今（数据源会自动截断）"))));

            _dlReference = Check("同时下载复权因子 / 除权明细（用于复权与审计）", true);
            AddRow(b1, Row(_dlReference));
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
            _dlIdleConc = new ComboBox();
            _dlIdleConc.DropDownStyle = ComboBoxStyle.DropDownList;
            _dlIdleConc.Width = 50;
            _dlIdleConc.Items.AddRange(new object[] { "1", "2" });
            _dlIdleConc.SelectedItem = "2";
            _dlIdleConc.SelectedIndexChanged += delegate { DlSaveSettings(); };
            AddRow(b2, Row(_dlAuto, _dlIdle, Lbl("闲时并发"), _dlIdleConc));

            _dlAutoNote = Mute(Lbl("正在检测数据完整性…"));
            _dlAutoNote.AutoSize = false;
            _dlAutoNote.Width = 700;
            _dlAutoNote.Height = 34;
            AddRow(b2, Row(_dlAutoNote));
            AddRow(b2, Row(Mute(Lbl("自动更新：每到一个固定时刻给已下载的股票补最新数据；"
                + "闲时下载：低并发接着上次没下完的股票继续补，够新了再转由自动更新维持。"
                + "两者同时只有一个在跑。"))));
            AddRow(stack, g2);

            // ---- ③ 失败清单 ----
            TableLayoutPanel b3;
            var g3 = Group("失败清单（可点「重试失败项」只补这些）", out b3);
            _dlFailures = MktGrid(180, false, new string[] { "代码", "原因" });
            _dlFailures.ScrollBars = ScrollBars.Vertical;   // 失败可能很多，这里要能滚
            AddRow(b3, _dlFailures);
            AddRow(stack, g3);

            // ---- ④ 本地通达信（配置了该目录才会显示）----
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
            string scope = _dlScope.SelectedIndex == 1 ? "pool" : "all";
            string mode = _dlMode.SelectedIndex == 0 ? "full" : "incremental";
            string query = "?scope=" + scope + "&mode=" + mode;
            var since = _dlStart == null ? "" : _dlStart.Text.Trim();
            if (mode == "full" && since.Length == 10)
                query += "&start_date=" + Uri.EscapeDataString(since);   // 容量预估随窗口变化
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
            string scope = _dlScope.SelectedIndex == 1 ? "pool" : "all";
            string mode = _dlMode.SelectedIndex == 0 ? "full" : "incremental";

            double rate;
            if (!double.TryParse((_dlRate.Text ?? "").Trim(), NumberStyles.Any,
                                 CultureInfo.InvariantCulture, out rate)) rate = 3.0;
            int concurrency;
            if (!int.TryParse(Convert.ToString(_dlConcurrency.SelectedItem), out concurrency)) concurrency = 4;

            var body = new Dictionary<string, object>();
            body["scope"] = scope;
            body["source"] = source;
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
            int stale = DlInt(DlVal(root, "stale"));
            int days = DlInt(DlVal(root, "freshness_days"));
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
                _dlIdleConc.SelectedItem = DlInt(DlVal(root, "idle_concurrency")) == 1 ? "1" : "2";
                _dlAuto.Enabled = eligible;
            }
            finally { _dlSettingsLoading = false; }

            string note;
            if (downloaded == 0)
                note = "库里还没有日K数据：先「开始下载」一次，之后才能开启自动更新。";
            else if (eligible)
                note = string.Format("已下载 {0} 只，数据只缺最近 {1} 天内 → 可以开启自动更新。",
                                     downloaded, days);
            else
            {
                note = string.Format("已下载 {0} 只，其中 {1} 只落后超过 {2} 天 → 暂不可开启自动更新；"
                                     + "可勾选「后台闲时下载」先把历史补齐。", downloaded, stale, days);
                var examples = DlVal(root, "stale_examples") as ArrayList;
                if (examples != null && examples.Count > 0)
                {
                    var names = new List<string>();
                    foreach (var item in examples) names.Add(Convert.ToString(item));
                    note += "　例：" + string.Join("、", names.ToArray());
                }
            }
            _dlAutoNote.Text = note;
        }

        /// <summary>保存后台开关；服务端会即时生效（不等下一个轮询周期）。</summary>
        private void DlSaveSettings()
        {
            if (_dlSettingsLoading || _dlAuto == null || _dlIdle == null) return;
            int concurrency;
            if (!int.TryParse(Convert.ToString(_dlIdleConc.SelectedItem), out concurrency)) concurrency = 2;

            var body = new Dictionary<string, object>();
            body["auto_update"] = _dlAuto.Checked;
            body["idle_download"] = _dlIdle.Checked;
            body["idle_concurrency"] = concurrency;
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

            int value = (int)Math.Round(percent * 10);
            _dlBar.Value = Math.Max(0, Math.Min(_dlBar.Maximum, value));
            _dlStatus.Text = "状态：" + DlStatusText(status)
                + (string.IsNullOrEmpty(_dlTaskId) ? "" : "　任务 " + _dlTaskId);
            int recent = DlInt(DlVal(task, "phase1_done"));
            int skipped = DlInt(DlVal(task, "skipped"));
            var extra = "";
            if (recent > 0) extra += string.Format("　近期阶段已覆盖 {0} 只", recent);
            if (skipped > 0) extra += string.Format("　跳过 {0} 只（库里已足量）", skipped);
            _dlCount.Text = string.Format("已完成 {0} / 共 {1}　失败 {2}　剩余 {3}　进度 {4:0.0}%{5}",
                                          completed, total, failed, remaining, percent, extra);

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
