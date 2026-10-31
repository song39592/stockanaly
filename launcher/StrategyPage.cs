using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Text;
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
        private SubTabStrip _stStrip;

        // ① 策略选择与编辑
        private List<Dictionary<string, object>> _stStrategies;
        private ListBox _stList;
        private Label _stDesc;
        private FlowLayoutPanel _stParamPanel;
        private readonly List<StParamEditor> _stParamEditors = new List<StParamEditor>();

        // ② 回测范围：基池=全部本地已下载股票，勾选即「排除」该特征
        private CheckBox _stExChiNext, _stExStar, _stExBse, _stExSt, _stExStarSt, _stExIpo, _stExSubNew, _stExMicro;
        // 股票池筛选记忆：8 位 mask（1=剔除），持久化到 settings.ini 的 scope_exclude
        private string _stScopeMask = "";
        private bool _stScopeLoading;
        private TextBox _stComm, _stBench;
        private Label _stScopeHint;
        private DateTimePicker _stStart, _stEnd;
        private NumericUpDown _stCapital;
        private CheckBox _stBenchChk;
        private int _stLocalCodeCount;

        // ③ 回测结果
        private Label _stResultHeader, _stResultStatus;
        // 回测进度（第 18 项）：全市场回测要几分钟，改后台任务 + 轮询，
        // 进度条显示「已测只数 / 总数」。_stPollRunId 用来丢弃**旧任务**的轮询响应
        // （用户可能在跑的过程中又点了一次「运行」，否则旧任务的进度会盖掉新的）。
        private ProgressBar _stProgress;
        private Label _stProgressText;
        private Timer _stPollTimer;
        private int _stPollRunId;
        private bool _stPollBusy;      // 上一次轮询没回来就不发下一次
        private FlowLayoutPanel _stMetricCards;
        private PictureBox _stEquityBox;
        private StockGrid _stPerStockGrid;
        private List<Dictionary<string, object>> _stPerStockData;   // 原始 per_stock（展开渲染用）
        private readonly HashSet<string> _stExpanded = new HashSet<string>();   // 已展开的代码
        private readonly Dictionary<string, System.Collections.IList> _stDetailCache =
            new Dictionary<string, System.Collections.IList>();     // 已拉取的逐笔明细（code → trades）
        private string _stResultId;                                 // 后端回测上下文句柄（明细按需拉取）

        // ④ 近期策略成果（最近 N 个交易日回测）
        private NumericUpDown _stRecentDays;
        private Label _stRecentStatus;
        private FlowLayoutPanel _stRecentCards;
        private StockGrid _stRecentGrid;

        // ⑤ 当前策略推荐（买入 / 卖出 / 持股）
        private Label _stRecoStatus;
        private FlowLayoutPanel _stRecoCards;
        private StockGrid _stBuyGrid, _stSellGrid, _stHoldGrid;

        // ⑥ 历史结果（已保存回测报告的独立页签）
        private ListView _stHistList;
        private Label _stHistStatus;
        private List<string> _stEqDates;
        private List<double?> _stEq;
        private List<double?> _stEqBench;
        // 全市场推荐要跑好几分钟（实测 5585 只需 418.7s），已改为后端异步任务，
        // 这里用轮询跟进进度。⚠️ _stRecoBusy 是**必需**的：原来完全没有守卫，
        // 用户等不及再点一次就会叠加多个全市场计算互相拖慢。
        private Timer _stRecoTimer;
        private bool _stRecoBusy;
        private bool _stRecoPollBusy;      // 有一次轮询请求在飞（防tick 重叠）
        private int _stRecoRunId;          // 每次启动 +1：陈旧响应据此丢弃（同回测 _stPollRunId）
        // 近期回测（最近 N 个交易日）也改走异步任务：全市场 3 年实测 761s，
        // 同步接口固定 600s 必然超时（旧实现就死在这），故与推荐页同一套轮询模式。
        private Timer _stRecentTimer;
        private bool _stRecentBusy;
        private bool _stRecentPollBusy;
        private int _stRecentRunId;
        private CheckBox _stRecentRuinChk;       // 近期回测：「破产截断」开关
        private NumericUpDown _stRecentRuinPct;  // 破产阈值（%，0=净值归零）
        // 当前选中的策略名（第 23 项）。放在**顶部操作区**而不是「策略选择与编辑」
        // 子页里 —— 因为所有「运行回测 / 生成推荐」都读列表选中项，跑错策略时用户
        // 在别的子页看不到自己选了什么。顶部常驻，任何子页都一眼可见。
        private Label _stCurStrategy;
        private Label _stRecoTitle;         // 第 23 项：「当前策略推荐」页顶部的策略名说明
        // 第 24 项：运行前数据新鲜度守卫。
        private bool _stFreshBusy;           // 正在检查/等待补齐，挡住重复点击
        private Timer _stFreshTimer;        // 等待补齐期间的进度轮询
        // 暂停 / 继续 / 停止。三个按钮互斥可见：运行中显「暂停」「停止」，
        // 暂停后换成「继续」「停止」，空闲时三个全隐（避免用户点一个必然失败的操作）。
        private Button _stBtnPause, _stBtnResume, _stBtnStop;

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
            // 第 23 项：第一行常驻显示「当前策略」。策略名要显眼（用户在「回测结果」
            // 或「当前策略推荐」页按下运行时，真正跑的是列表里选中那个 —— 不显示
            // 名字就很容易以为在跑另一个。空选中时显示提示而不是留白。
            _stCurStrategy = Lbl("—");
            _stCurStrategy.Font = new Font("Microsoft YaHei UI", 10f, FontStyle.Bold);
            AddRow(b0, Row(Lbl("当前策略"), _stCurStrategy));
            _stResultStatus = Mute(Lbl("进入本页自动加载策略清单"));
            AddRow(b0, Row(Lbl("策略回测"), MiniBtn("刷新清单", delegate { StLoadStrategies(); }, 90), _stResultStatus));
            AddRow(head, g0);
            g0.Margin = new Padding(0, 0, 0, 4);
            root.Controls.Add(head, 0, 0);

            // 本页与个股 / 盘面两处**刻意不同**，都是历史形态，不要顺手统一：
            //   FixedButtonSize=false —— 按钮不设固定高宽，走 Button 默认的紧凑形态；
            //   BodyAutoScroll=true  —— 内容容器自带滚动条（另两个页的容器不滚）。
            _stStrip = new SubTabStrip(this, root, 1, 2, "st-subtab")
            {
                FixedButtonSize = false,
                BodyAutoScroll = true
            };

            _stStrip.Add("策略选择与编辑", StBuildSelect);
            _stStrip.Add("回测范围", StBuildScope);
            _stStrip.Add("回测结果", StBuildResult);
            _stStrip.Add("近期策略成果", StBuildRecent);
            _stStrip.Add("当前策略推荐", StBuildReco);
            _stStrip.Add("历史结果", StBuildHistory);

            _stStrip.Select(0);
            return p;
        }

        private void StOnEnter()
        {
            if (!_stLoaded) StLoadStrategies();
            if (_stStrip != null)
            {
                _stStrip.Select(_stStrip.Index);
                _stStrip.Body.PerformLayout();
                _stStrip.Body.Invalidate(true);
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
                Mute(Lbl("单选：一次只运行列表里选中的那一个策略（不是全列表一起跑）。右侧可编辑其参数。新增策略请在后端 strategies/ 加 <id>.py 文件。"))));
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
            StRefreshCurStrategy();          // 第 23 项：顶部「当前策略」随选中即时更新
            // 第 23 项：回测结果页的标题也跟着换。**只在「尚未回测」时改**——
            // 一旦跑过，那行标题描述的是上一次回测的范围与参数，换策略后
            // 若无条件覆盖会把「上一次跑的是哪个策略」这段历史信息抹掉，
            // 那正是回测后核对结果最需要的。跑完再切回本页会由
            // StRunBacktest 自己填上新的完整标题。
            if (_stResultHeader != null && _stResultHeader.Text.Contains("尚未回测"))
                _stResultHeader.Text = "策略：" + StCurStrategyName() + " ·尚未回测";
            StRefreshRecoTitle();
            var meta = StSelectedMeta();
            if (meta == null) return;
            _stDesc.Text = J.StrOr(meta["description"], "（无说明）");
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

        /// <summary>
        /// 当前选中的策略元数据（列表选中项）；无选中 / 越界返回 null。
        ///
        /// ⚠️ 这是「当前策略」的**唯一**来源，而列表选中项会被清单加载改写
        /// （见 StLoadStrategies），所以**真正发起请求前必须先取一次快照**再把
        /// 这份 meta 一路传下去：否则「弹窗里写的策略」与「实际提交的策略」
        /// 可能取到不同时刻的选中项，用户看到的就是「选了 RPS，跑的是筹码」。
        /// 越界也在这里挡掉 —— `_stStrategies` 被新清单替换后，旧的 SelectedIndex
        /// 可能已经超出新列表长度。
        /// </summary>
        private Dictionary<string, object> StSelectedMeta()
        {
            if (_stStrategies == null || _stList == null) return null;
            int i = _stList.SelectedIndex;
            return (i >= 0 && i < _stStrategies.Count) ? _stStrategies[i] : null;
        }

        /// <summary>当前选中策略的显示名（顶部、回测结果、推荐三处共用一个口径）。</summary>
private string StCurStrategyName()
        {
            var meta = StSelectedMeta();
            if (meta == null) return "未选择";
            return J.StrOr(meta["name"], J.StrOr(meta["id"], "—"));
        }

        /// <summary>
        /// 第 23 项：刷新顶部「当前策略」显示。
        /// 由 StOnSelectStrategy 调用（切换策略即时生效），并且页面构建时
        /// 也会先用一次初始值。
        /// </summary>
private void StRefreshCurStrategy()
        {
            if (_stCurStrategy == null) return;
            if (_stStrategies == null || _stList == null || _stList.SelectedIndex < 0)
            {
                _stCurStrategy.Text = "未选择";
                _stCurStrategy.ForeColor = _cSub;
                return;
            }
            _stCurStrategy.Text = StCurStrategyName();
            _stCurStrategy.ForeColor = C.DownSoft;
        }

        /// <summary>第 23 项：刷新「当前策略推荐」页顶部的说明行。</summary>
private void StRefreshRecoTitle()
        {
            if (_stRecoTitle == null) return;
            _stRecoTitle.Text = "当前策略：" + StCurStrategyName()
                + "　—　按它在**最新交易日**的信号给出操作建议；范围 / 资金 / 佣金沿用「回测范围」页的设置。";
        }

        private void StAddParamEditor(Dictionary<string, object> p)
        {
            string name = J.StrOr(p["name"], "");
            string type = J.StrOr(p["type"], "float");
            string label = J.StrOr(p["label"], name);
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
                foreach (var c in ch) cb.Items.Add(J.StrOr(c, ""));
                object def = J.Get(p, "default");
                int idx = cb.Items.IndexOf(def == null ? "" : def.ToString());
                cb.SelectedIndex = idx >= 0 ? idx : 0;
                ctrl = cb;
            }
            else if (type == "int")
            {
                var n = new NumericUpDown();
                n.DecimalPlaces = 0;
                n.Width = 100;
                double mn = J.NumOr(p["min"], int.MinValue);
                double mx = J.NumOr(p["max"], int.MaxValue);
                n.Minimum = (decimal)Math.Max(int.MinValue, mn);
                n.Maximum = (decimal)Math.Min(int.MaxValue, mx);
                n.Value = (decimal)Clamp(J.NumOr(p["default"], 0), (double)n.Minimum, (double)n.Maximum);
                ctrl = n;
            }
            else if (type == "float")
            {
                var n = new NumericUpDown();
                n.DecimalPlaces = 4;
                n.Width = 100;
                double mn = J.NumOr(p["min"], double.MinValue);
                double mx = J.NumOr(p["max"], double.MaxValue);
                n.Minimum = (decimal)mn;
                n.Maximum = (decimal)mx;
                n.Value = (decimal)Clamp(J.NumOr(p["default"], 0), mn, mx);
                ctrl = n;
            }
            else
            {
                var tb = new TextBox();
                tb.Width = 160;
                object def = J.Get(p, "default");
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
            _stScopeHint = Mute(Lbl("共 0 只 · 剔除后 0 只"));
            AddRow(b, Row(Mute(Lbl("基池：全部本地已下载")), _stScopeHint));

            var exRow = new FlowLayoutPanel();
            exRow.AutoSize = true;
            exRow.WrapContents = true;
            exRow.Margin = new Padding(0, 4, 0, 0);
            _stExChiNext = ExChk("剔除创业板"); _stExStar = ExChk("剔除科创板"); _stExBse = ExChk("剔除北交所");
            _stExSt = ExChk("剔除ST"); _stExStarSt = ExChk("剔除*ST");
            _stExIpo = ExChk("剔除新股"); _stExSubNew = ExChk("剔除次新股"); _stExMicro = ExChk("剔除微盘股");
            foreach (var cb in new[] { _stExChiNext, _stExStar, _stExBse, _stExSt, _stExStarSt, _stExIpo, _stExSubNew, _stExMicro })
                cb.CheckedChanged += delegate
                {
                    if (_stScopeLoading) return;
                    _stScopeMask = StScopeMask();
                    StSaveScopePrefs();        // 记住本次筛选，下次启动默认沿用
                    StScopePreview();
                };
            exRow.Controls.AddRange(new Control[] { _stExChiNext, _stExStar, _stExBse, _stExSt, _stExStarSt, _stExIpo, _stExSubNew, _stExMicro });
            AddRow(b, Row(exRow));
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

            StLoadScopePrefs();      // 恢复上次的股票池筛选
        }

        /// <summary>当前 8 个排除勾选拼成 mask（如 "10100010"）。</summary>
        private string StScopeMask()
        {
            var cbs = new[] { _stExChiNext, _stExStar, _stExBse, _stExSt, _stExStarSt, _stExIpo, _stExSubNew, _stExMicro };
            var sb = new StringBuilder();
            foreach (var cb in cbs) sb.Append(cb != null && cb.Checked ? '1' : '0');
            return sb.ToString();
        }

        /// <summary>把 mask 应用到 8 个勾选；不足的位按「不剔除」处理。</summary>
        private void StApplyScopeMask(string mask)
        {
            string m = (mask ?? "").Trim();
            var cbs = new[] { _stExChiNext, _stExStar, _stExBse, _stExSt, _stExStarSt, _stExIpo, _stExSubNew, _stExMicro };
            _stScopeLoading = true;
            try
            {
                for (int i = 0; i < cbs.Length; i++)
                {
                    bool on = i < m.Length && m[i] == '1';
                    if (cbs[i] != null) cbs[i].Checked = on;
                }
            }
            finally { _stScopeLoading = false; }
        }

        /// <summary>从 settings.ini 读 scope_exclude 并应用；不存在则保持默认（全部不剔除）。</summary>
        private void StLoadScopePrefs()
        {
            string mask = null;
            try
            {
                if (File.Exists(_settingsPath))
                {
                    foreach (var line in File.ReadAllLines(_settingsPath, Encoding.UTF8))
                    {
                        var kv = line.Split(new char[] { '=' }, 2);
                        if (kv.Length == 2 && kv[0].Trim() == "scope_exclude" && kv[1].Trim().Length > 0)
                        {
                            mask = kv[1].Trim();
                            break;
                        }
                    }
                }
            }
            catch { }
            if (!string.IsNullOrEmpty(mask)) StApplyScopeMask(mask);
            _stScopeMask = StScopeMask();
        }

        /// <summary>把当前 mask 写回 settings.ini（只改 scope_exclude 一行，保留其它键）。</summary>
        private void StSaveScopePrefs()
        {
            try
            {
                var lines = File.Exists(_settingsPath)
                    ? new List<string>(File.ReadAllLines(_settingsPath, Encoding.UTF8))
                    : new List<string>();
                string want = "scope_exclude=" + (_stScopeMask ?? "");
                int idx = lines.FindIndex(delegate(string l)
                {
                    var kv = l.Split(new char[] { '=' }, 2);
                    return kv.Length == 2 && kv[0].Trim() == "scope_exclude";
                });
                if (idx >= 0) lines[idx] = want; else lines.Add(want);
                var dir = Path.GetDirectoryName(_settingsPath);
                if (!string.IsNullOrEmpty(dir) && !Directory.Exists(dir)) Directory.CreateDirectory(dir);
                File.WriteAllLines(_settingsPath, lines.ToArray(), Encoding.UTF8);
            }
            catch { }
        }

        // ---------------- ③ 回测结果 ----------------
        private void StBuildResult(TableLayoutPanel stack)
        {
            _stResultHeader = new Label();
            // 第 23 项：初始就带上策略名，而不是「—」等到跑完才显示 —— 这一页
            // 的操作按钮写的就是「运行回测（选中策略）」，用户在这里按下时最需要
            // 确认自己选对了没。跑完后会被 StRunBacktest 里的完整区间信息覆盖。
            _stResultHeader.Text = "策略：" + StCurStrategyName() + " · 尚未回测";
            _stResultHeader.Font = new Font("Microsoft YaHei UI", 11f, FontStyle.Bold);
            _stResultHeader.AutoSize = true;
            _stResultHeader.MaximumSize = new Size(900, 0);
            _stResultHeader.Margin = new Padding(0, 0, 0, 8);
            AddRow(stack, _stResultHeader);

            AddRow(stack, Row(
                MiniBtn("开始回测", delegate { StRunBacktest(); }, 100),
                // 暂停 / 继续 / 停止：全市场一次实测约 13 分钟（5578 只 × 3 年），
                // 而并发上限是 1 —— 没有这三个按钮，用户改完策略想重跑就只能干等。
                // 原来后端提示「请先点『停止』」而界面上根本没有这个按钮，属于死路。
                _stBtnPause = MiniBtn("暂停", delegate { StControlTask("pause"); }, 64),
                _stBtnResume = MiniBtn("继续", delegate { StControlTask("resume"); }, 64),
                _stBtnStop = MiniBtn("停止", delegate { StControlTask("cancel"); }, 64),
                _stResultStatus = Mute(Lbl("尚未回测"))));
            StSetControlButtons(false, false);

            // 进度条：默认隐藏，跑起来才显示（_stProgress.Visible 控制）
            _stProgress = new ProgressBar();
            _stProgress.Height = 14;
            _stProgress.Dock = DockStyle.Top;
            _stProgress.Visible = false;
            _stProgressText = Mute(Lbl(""));
            _stProgressText.Visible = false;
            AddRow(stack, _stProgress);
            AddRow(stack, _stProgressText);

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
            var g = Group("个股明细（按区间收益降序 · 点击代码/名称跳个股详情）", out bg);
            _stPerStockGrid = NewGrid(new List<GridColumn> {
                new GridColumn("代码", "code") { IsCode = true, Jump = true },
                NameColumn(),          // 名称列带 Jump：点名称同样可跳个股详情
                new GridColumn("区间收益", "total_return", true),
                new GridColumn("交易次数", "trades", true),
                new GridColumn("买入日期", "buy_date"),
                new GridColumn("卖出日期", "sell_date"),
                new GridColumn("单笔收益", "ret", true),
            });
            _stPerStockGrid.SetSortable(true);
            StPlaceGrid(_stPerStockGrid);
            // 兜底跳转：内置 OnCellClick 依赖列的 Jump 标记；若未接管（仍停在回测页），
            // 按代码列取值直接 OpenStock，已接管则不重复触发。
            _stPerStockGrid.CellClick += delegate(object s, DataGridViewCellEventArgs e)
            {
                if (e.RowIndex < 0 || _tabIndex == _stockTabIndex) return;
                string c = Convert.ToString(_stPerStockGrid.Rows[e.RowIndex].Cells[0].Value);
                if (!string.IsNullOrEmpty(c) && c.Trim().Length == 6) OpenStock(c.Trim());
            };
            AddRow(bg, _stPerStockGrid);
            AddRow(stack, g);
        }

        // ---------------- ④ 近期策略成果 ----------------
        // 最近 N 个交易日的短窗口回测：区间由后端按本地日历自动定位（window），
        // 与「回测结果」共用同一套信号与统计口径，只是窗口固定为最近 N 日。
        private void StBuildRecent(TableLayoutPanel stack)
        {
            AddRow(stack, Mute(Lbl("用当前策略在「最近 N 个交易日」上跑一次回测，快速看近期表现；范围 / 资金 / 佣金沿用「回测范围」页的设置。")));
            _stRecentDays = new NumericUpDown();
            _stRecentDays.Minimum = 2;
            _stRecentDays.Maximum = 120;
            _stRecentDays.Value = 5;
            _stRecentDays.Width = 70;
            _stRecentDays.TabStop = false;
            AddRow(stack, Row(MiniBtn("运行近期回测", delegate { StRunRecent(); }, 120),
                Lbl("交易日数"), _stRecentDays, _stRecentStatus = Mute(Lbl("尚未运行"))));
            _stRecentRuinChk = new CheckBox();
            _stRecentRuinChk.Text = "破产截断";
            _stRecentRuinChk.Checked = true;
            _stRecentRuinChk.TabStop = false;
            _stRecentRuinPct = new NumericUpDown();
            _stRecentRuinPct.Minimum = 0;
            _stRecentRuinPct.Maximum = 100;
            _stRecentRuinPct.Value = 0;
            _stRecentRuinPct.Width = 60;
            _stRecentRuinPct.TabStop = false;
            AddRow(stack, Row(_stRecentRuinChk, Lbl("阈值%"), _stRecentRuinPct,
                Mute(Lbl("(0=净值归零即断)"))));

            _stRecentCards = StCardRow();
            AddRow(stack, _stRecentCards);

            TableLayoutPanel bg;
            var g = Group("个股明细（按区间收益降序）", out bg);
            _stRecentGrid = NewGrid(new List<GridColumn> {
                new GridColumn("代码", "code") { IsCode = true, Jump = true },
                NameColumn(),
                new GridColumn("区间收益", "ret", true),
                new GridColumn("交易次数", "trades", true),
                new GridColumn("买入时间", "buy_date"),
                new GridColumn("卖出时间", "sell_date"),
            });
            StPlaceGrid(_stRecentGrid);
            _stRecentGrid.SetSortable(true);   // 第 20 项：可改按交易次数等排序
            AddRow(bg, _stRecentGrid);
            AddRow(stack, g);
        }

        private void StBuildHistory(TableLayoutPanel stack)
        {
            AddRow(stack, Mute(Lbl("已保存的回测报告（每次回测自动落盘）。选中一份后点「加载所选」，按回测结果的样式展示指标卡 / 净值曲线 / 个股明细。")));
            _stHistList = new ListView();
            _stHistList.View = View.Details;
            _stHistList.FullRowSelect = true;
            _stHistList.GridLines = true;
            _stHistList.HideSelection = false;
            _stHistList.MultiSelect = false;
            _stHistList.Font = new Font("Microsoft YaHei UI", 9f);
            _stHistList.Height = 320;
            _stHistList.Width = 880;              // 不设宽度会被表格布局压成第一列宽
            _stHistList.Columns.Add("生成时间", 160);
            _stHistList.Columns.Add("策略", 300);
            _stHistList.Columns.Add("区间", 220);
            _stHistList.Columns.Add("收益", 100);
            _stHistList.DoubleClick += delegate { StHistLoad(); };
            TableLayoutPanel bg;
            var g = Group("历史报告", out bg);
            AddRow(bg, _stHistList);
            AddRow(stack, g);
            _stHistStatus = Mute(Lbl("尚未加载"));
            AddRow(stack, Row(
                MiniBtn("刷新列表", delegate { StHistRefresh(); }, 90),
                MiniBtn("加载所选", delegate { StHistLoad(); }, 90),
                MiniBtn("删除所选", delegate { StHistDelete(); }, 90),
                _stHistStatus));
            StHistRefresh();
        }

        private void StRunRecent()
        {
            if (_stRecentBusy) return;      // 防叠加：全市场一次只能算一个
            var meta = StSelectedMeta();
            if (meta == null)
            {
                SetErr(_stRecentStatus, "请先在「策略选择与编辑」里选一个策略");
                return;
            }
            int days = (int)_stRecentDays.Value;
            double? ruin = (_stRecentRuinChk != null && _stRecentRuinChk.Checked)
                ? (double?)_stRecentRuinPct.Value / 100.0 : null;
            _stRecentBusy = true;
            int myRun = ++_stRecentRunId;
            var req = StRequest(meta, days, ruin);
            SetErr(_stRecentStatus, "提交中…");
            string body = new JavaScriptSerializer().Serialize(req);
            System.Threading.Tasks.Task.Run(delegate
            {
                try
                {
                    // 只等「启动」立即返回；旧实现是同步 /backtest + 600s 固定超时，
                    // 全市场 3 年实测 761s 必然超时，这里不再继承那个坑。
                    string resp = VRequest("http://127.0.0.1:8000/api/strategies/backtest/start", body, 30000);
                    var j = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(resp);
                    Invoke((Action)(() =>
                    {
                        if (myRun != _stRecentRunId) return;
                        if (J.IsFailed(j))
                        {
                            _stRecentBusy = false;
                            SetErr(_stRecentStatus, "无法启动：" + StErrMsg(j));
                            return;
                        }
                        var st = (Dictionary<string, object>)J.Get(j, "state");
                        int tot = st == null ? 0 : J.Int(st["total"]);
                        SetErr(_stRecentStatus, string.Format(
                            "回测中 0/{0}（0.0%）…", tot <= 0 ? "?" : tot.ToString()));
                        StartRecentPoll();
                    }));
                }
                catch (Exception ex)
                {
                    string msg = ex.Message;
                    // 启动请求超时/失败时，后端可能其实已经启动了任务（只是响应没赶上）。
                    // 先探一次进度：已有任务在跑就直接接管并轮询，别误报「无法启动」让用户重复点。
                    bool adopted = false;
                    try
                    {
                        string pj = VRequest("http://127.0.0.1:8000/api/strategies/backtest/progress", null, 5000);
                        var p = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(pj);
                        var st = (Dictionary<string, object>)J.Get(p, "state");
                        string state = st == null ? "" : J.StrAt(st, "state");
                        if (state == "running" || state == "ready")
                        {
                            adopted = true;
                            Invoke((Action)(() =>
                            {
                                if (myRun != _stRecentRunId) return;
                                SetErr(_stRecentStatus, "已接管后台任务，读取进度…");
                                StartRecentPoll();
                            }));
                        }
                    }
                    catch { /* 探测失败就走普通报错 */ }
                    if (adopted) return;
                    Invoke((Action)(() =>
                    {
                        if (myRun != _stRecentRunId) return;
                        _stRecentBusy = false;
                        SetErr(_stRecentStatus, "无法启动：" + msg);
                    }));
                }
            });
        }

        /// <summary>启动 2 秒一次的近期回测进度轮询（幂等，重复调用只是重新 Start）。</summary>
        private void StartRecentPoll()
        {
            if (_stRecentTimer == null)
            {
                _stRecentTimer = new Timer();
                _stRecentTimer.Interval = 2000;
                _stRecentTimer.Tick += delegate { StRecentPoll(); };
            }
            _stRecentTimer.Start();
        }

        /// <summary>轮询近期回测进度；ready 后停表取结果。</summary>
        private void StRecentPoll()
        {
            if (_stRecentPollBusy) return;
            _stRecentPollBusy = true;
            int myRun = _stRecentRunId;
            System.Threading.Tasks.Task.Run(delegate
            {
                try
                {
                    string resp = VRequest("http://127.0.0.1:8000/api/strategies/backtest/progress", null, 15000);
                    var j = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(resp);
                    Invoke((Action)(() =>
                    {
                        _stRecentPollBusy = false;
                        if (myRun != _stRecentRunId) return;
                        var st = (Dictionary<string, object>)J.Get(j, "state");
                        string state = st == null ? "" : J.StrAt(st, "state");
                        string phase = st == null ? "" : J.StrAt(st, "phase");
                        int done = st == null ? 0 : J.Int(st["done"]);
                        int tot = st == null ? 0 : J.Int(st["total"]);
                        double sec = st == null ? 0 : J.Num(st["elapsed"]);

                        if (state == "error")
                        {
                            if (_stRecentTimer != null) _stRecentTimer.Stop();
                            _stRecentBusy = false;
                            SetErr(_stRecentStatus, "回测失败：" + J.StrAt(st, "error"));
                            return;
                        }
                        if (state == "cancelled")
                        {
                            if (_stRecentTimer != null) _stRecentTimer.Stop();
                            _stRecentBusy = false;
                            SetErr(_stRecentStatus, "已被用户停止");
                            return;
                        }
                        if (state == "running")
                        {
                            if (phase == "running")
                                SetErr(_stRecentStatus, string.Format(
                                    "回测中（{0}/{1}，正在算策略信号，已用 {2:F0} 秒）…", done, tot, sec));
                            else
                                SetErr(_stRecentStatus, string.Format(
                                    "读取数据中 {0}/{1}（{2:F1}%），已用 {3:F0} 秒…",
                                    done, tot, tot > 0 ? done * 100.0 / tot : 0, sec));
                            return;
                        }

                        // ready：停表，取结果
                        if (_stRecentTimer != null) _stRecentTimer.Stop();
                        StRecentFetchResult(myRun);
                    }));
                }
                catch (Exception ex)
                {
                    string msg = ex.Message;
                    Invoke((Action)(() =>
                    {
                        _stRecentPollBusy = false;
                        if (myRun != _stRecentRunId) return;
                        if (_stRecentTimer != null) _stRecentTimer.Stop();
                        _stRecentBusy = false;
                        SetErr(_stRecentStatus, "读取进度失败：" + msg);
                    }));
                }
            });
        }

        private void StRecentFetchResult(int myRun)
        {
            System.Threading.Tasks.Task.Run(delegate
            {
                string resp = null, emsg = "";
                Dictionary<string, object> j = null;
                try
                {
                    resp = VRequest("http://127.0.0.1:8000/api/strategies/backtest/result", null, 60000);
                    j = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(resp);
                }
                catch (Exception ex) { emsg = ex.Message; }
                var jj = j;
                var ee = emsg;
                Invoke((Action)(() =>
                {
                    if (myRun != _stRecentRunId) return;
                    _stRecentBusy = false;
                    if (jj == null) { SetErr(_stRecentStatus, "读取结果失败：" + ee); return; }
                    StRenderRecent(jj);
                }));
            });
        }

        private void StRenderRecent(Dictionary<string, object> j)
        {
            if (J.IsFailed(j))
            {
                SetErr(_stRecentStatus, "回测失败：" + StErrMsg(j));
                return;
            }
            var metrics = (Dictionary<string, object>)j["metrics"];
            _stRecentCards.Controls.Clear();
            _stRecentCards.Controls.Add(StKpiCard("区间收益", J.Pct100Plain(J.Num(metrics["total_return"]))));
            _stRecentCards.Controls.Add(StKpiCard("最大回撤", J.Pct100Plain(J.Num(metrics["max_drawdown"]))));
            // ⚠️ 「胜率」原来只有一个字段，而它的值其实是**日胜率**，被当交易胜率读了很久。
            //   现在后端拆成两个，这里两个都显示，避免继续误读。
            _stRecentCards.Controls.Add(StKpiCard("日胜率", StMPct(metrics, "win_rate_daily")));
            _stRecentCards.Controls.Add(StKpiCard("交易胜率", StMPct(metrics, "win_rate_trade")));
            _stRecentCards.Controls.Add(StKpiCard("盈亏比", StMText(metrics, "profit_factor")));
            _stRecentCards.Controls.Add(StKpiCard("平均持仓", StMText(metrics, "avg_positions", "F1")));
            // 资金利用率应接近 position；远低于 1 说明配置有问题，是排查的第一眼
            _stRecentCards.Controls.Add(StKpiCard("资金利用率", StMPct(metrics, "capital_utilization")));
            Skin(_stRecentCards);

            _stRecentGrid.Rows.Clear();
            var ps = j["per_stock"] as System.Collections.IList;
            if (ps != null)
            {
                _stRecentGrid.SuspendLayout();
                foreach (var it in ps)
                {
                    var d = it as Dictionary<string, object>;
                    if (d == null) continue;
                    // 买卖时间：per_stock 已带首笔交易的 buy_date / sell_date（未平仓时 sell_date 为空）
                    string buyD = J.StrOr(J.Get(d, "buy_date"), "");
                    string sellD = J.StrOr(J.Get(d, "sell_date"), "");
                    _stRecentGrid.Rows.Add(J.StrOr(d["code"], ""), StName(d),
                        J.Pct100Plain(J.Num(d["total_return"])), J.Int(d["trades"]).ToString(),
                        buyD, sellD);
                }
                _stRecentGrid.ResumeLayout();
            }
            _stRecentGrid.Fit();
            _stRecentStatus.Text = "完成（最近 " + (int)_stRecentDays.Value + " 个交易日）";
            _stRecentStatus.ForeColor = C.DownSoft;
            // 破产截断：账户净值跌破阈值后强制空仓，结果里明确标出，避免被当成「正常回测」
            bool bankrupt = false;
            object bk;
            if (metrics.TryGetValue("bankrupt", out bk) && bk is bool) bankrupt = (bool)bk;
            if (bankrupt)
            {
                var bd = J.Get(metrics, "bankrupt_day");
                var br = J.Get(metrics, "bankrupt_ratio");
                _stRecentStatus.Text = "⚠ 账户于 " + J.StrOr(bd, "") + " 破产（净值 "
                    + J.Pct100Plain(J.Num(br)) + "），后续交易日已截断";
                _stRecentStatus.ForeColor = C.UpErr;
            }
        }

        // ---------------- ⑤ 当前策略推荐 ----------------
        // 按最新交易日的信号给出三档建议：买入（今日建仓）/ 卖出（今日清仓）/
        // 持股（继续持有，含建仓成本与浮盈亏）。
        private void StBuildReco(TableLayoutPanel stack)
        {
            // 第 23 项：标题行直接写出策略名。这一页的按钮是「生成当前推荐」，
            // 「当前」指的是列表选中项而不是上回测过的那个；不写出来用户很容易
            // 切了策略却以为在重跑上一个。下方状态行还会带出「策略：」前缀，双保险。
            _stRecoTitle = Mute(Lbl(""));
            AddRow(stack, _stRecoTitle);
            StRefreshRecoTitle();
            AddRow(stack, Row(MiniBtn("生成当前推荐", delegate { StRunReco(); }, 130),
                _stRecoStatus = Mute(Lbl("尚未生成"))));

            _stRecoCards = StCardRow();
            AddRow(stack, _stRecoCards);

            TableLayoutPanel b1;
            var g1 = Group("买入（今日建仓）", out b1);
            _stBuyGrid = NewGrid(new List<GridColumn> {
                new GridColumn("代码", "code") { IsCode = true, Jump = true },
                NameColumn(),
                new GridColumn("现价", "price", true),
                new GridColumn("建议仓位", "weight", true),
            });
            StPlaceGrid(_stBuyGrid);
            _stBuyGrid.SetSortable(true);        // 第 20 项
            AddRow(b1, _stBuyGrid);
            AddRow(stack, g1);

            TableLayoutPanel b2;
            var g2 = Group("卖出（今日清仓）", out b2);
            _stSellGrid = NewGrid(new List<GridColumn> {
                new GridColumn("代码", "code") { IsCode = true, Jump = true },
                NameColumn(),
                new GridColumn("现价", "price", true),
                new GridColumn("原仓位", "weight", true),
            });
            StPlaceGrid(_stSellGrid);
            _stSellGrid.SetSortable(true);       // 第 20 项
            AddRow(b2, _stSellGrid);
            AddRow(stack, g2);

            TableLayoutPanel b3;
            var g3 = Group("持股（继续持有）", out b3);
            _stHoldGrid = NewGrid(new List<GridColumn> {
                new GridColumn("代码", "code") { IsCode = true, Jump = true },
                NameColumn(),
                new GridColumn("状态", "action"),
                new GridColumn("成本价", "cost_price", true),
                new GridColumn("现价", "price", true),
                new GridColumn("建仓日", "buy_date"),
                new GridColumn("仓位", "weight", true),
                new GridColumn("浮盈亏", "pnl", true),
            });
            StPlaceGrid(_stHoldGrid);
            _stHoldGrid.SetSortable(true);       // 第 20 项：可按浮盈亏/仓位排序
            AddRow(b3, _stHoldGrid);
            AddRow(stack, g3);
        }

        private void StRunReco()
        {
            // ⚠️ 守卫必需：原来这里完全没有，用户等不及再点一次就会叠加多个
            // 全市场计算（后端并发上限 1，第二个会被拒，但两个都得等）。
            if (_stRecoBusy) return;
            var meta = StSelectedMeta();
            if (meta == null)
            {
                SetErr(_stRecoStatus, "请先在「策略选择与编辑」里选一个策略");
                return;
            }
            // 第 24 项：与回测共用同一个数据新鲜度守卫；策略用点击时的快照。
            StEnsureFresh(meta, delegate { StRunRecoInner(meta); });
        }

        private void StRunRecoInner(Dictionary<string, object> meta)
        {
            _stRecoBusy = true;
            int myRun = ++_stRecoRunId;
            var req = StRequest(meta, 0);
            SetErr(_stRecoStatus, "正在提交…（全市场约 7~10 分钟，可随时切走）");
            string body = new JavaScriptSerializer().Serialize(req);
            System.Threading.Tasks.Task.Run(delegate
            {
                try
                {
                    // 只等「启动」这一下（实测0.01s返回），不再等算完
                    string resp = VRequest("http://127.0.0.1:8000/api/strategies/recommend", body, 30000);
                    var j = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(resp);
                    Invoke((Action)(() =>
                    {
                        if (myRun != _stRecoRunId) return;      // 已被新一轮取代
                        if (J.IsFailed(j))
                        {
                            _stRecoBusy = false;
                            SetErr(_stRecoStatus, "无法启动：" + StErrMsg(j));
                            return;
                        }
                        var st = (Dictionary<string, object>)J.Get(j, "state");
                        int tot = st == null ? 0 : J.Int(st["total"]);
                        SetErr(_stRecoStatus, string.Format(
                            "计算中 0/{0}（0.0%）…", tot <= 0 ? "?" : tot.ToString()));
                        if (_stRecoTimer == null)
                        {
                            _stRecoTimer = new Timer();
                            _stRecoTimer.Interval = 2000;
                            _stRecoTimer.Tick += delegate { StRecoPoll(); };
                        }
                        _stRecoTimer.Start();
                    }));
                }
                catch (Exception ex)
                {
                    string msg = ex.Message;
                    Invoke((Action)(() =>
                    {
                        if (myRun != _stRecoRunId) return;
                        // ⚠️ 失败路径也必须复位，否则按钮点一次之后永久卡死
                        _stRecoBusy = false;
                        SetErr(_stRecoStatus, "无法启动：" + msg);
                    }));
                }
            });
        }

        /// <summary>轮询推荐进度；完成后停表并取结果。</summary>
        private void StRecoPoll()
        {
            // ⚠️ 这里**不能**拿 _stRecoBusy 做防重入：它是「整个流程在跑」的标志。
            // 若在此清掉，下一个 tick 会被 if(!_stRecoBusy) return 挡掉，轮询就自停了
            //（单次请求一旦超过 Interval 2 秒就必然发生）。
            // 正确分工：_stRecoPollBusy 管「本次请求在飞」，_stRecoBusy 全程保持 true
            //——它必须一直为 true，用户才点不动第二次。
            if (_stRecoPollBusy) return;
            _stRecoPollBusy = true;
            int myRun = _stRecoRunId;
            System.Threading.Tasks.Task.Run(delegate
            {
                try
                {
                    string resp = VRequest("http://127.0.0.1:8000/api/strategies/recommend/progress", null, 15000);
                    var j = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(resp);
                    Invoke((Action)(() =>
                    {
                        _stRecoPollBusy = false;
                        if (myRun != _stRecoRunId) return;      // 已被新一轮取代
                        var st = (Dictionary<string, object>)J.Get(j, "state");
                        string state = st == null ? "" : J.StrAt(st, "state");
                        string phase = st == null ? "" : J.StrAt(st, "phase");
                        int done = st == null ? 0 : J.Int(st["done"]);
                        int tot = st == null ? 0 : J.Int(st["total"]);
                        double sec = st == null ? 0 : J.Num(st["elapsed"]);

                        if (state == "error")
                        {
                            if (_stRecoTimer != null) _stRecoTimer.Stop();
                            _stRecoBusy = false;
                            SetErr(_stRecoStatus, "计算失败：" + J.StrAt(st, "error"));
                            return;
                        }
                        if (state == "running")
                        {
                            if (phase == "running")
                                // 策略计算阶段外部无法细分（done 已置满），别编百分比
                                SetErr(_stRecoStatus, string.Format(
                                    "计算中（{0}/{1}，正在算策略信号，已用 {2:F0} 秒）…",
                                    done, tot, sec));
                            else
                                SetErr(_stRecoStatus, string.Format(
                                    "读取数据中 {0}/{1}（{2:F1}%），已用 {3:F0} 秒…",
                                    done, tot, tot > 0 ? done * 100.0 / tot : 0, sec));
                            return;
                        }

                        // ready：停表，取结果（_stRecoBusy 保持 true 直到渲染完）
                        if (_stRecoTimer != null) _stRecoTimer.Stop();
                        StRecoFetchResult(myRun);
                    }));
                }
                catch (Exception ex)
                {
                    string msg = ex.Message;
                    Invoke((Action)(() =>
                    {
                        _stRecoPollBusy = false;
                        if (myRun != _stRecoRunId) return;
                        if (_stRecoTimer != null) _stRecoTimer.Stop();
                        _stRecoBusy = false;
                        SetErr(_stRecoStatus, "读取进度失败：" + msg);
                    }));
                }
            });
        }

        private void StRecoFetchResult(int myRun)
        {
            System.Threading.Tasks.Task.Run(delegate
            {
                string resp = null, emsg = "";
                Dictionary<string, object> j = null;
                try
                {
                    resp = VRequest("http://127.0.0.1:8000/api/strategies/recommend/result", null, 60000);
                    j = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(resp);
                }
                catch (Exception ex) { emsg = ex.Message; }
                var jj = j;
                var ee = emsg;
                Invoke((Action)(() =>
                {
                    if (myRun != _stRecoRunId) return;
                    _stRecoBusy = false;
                    if (jj == null) { SetErr(_stRecoStatus, "读取结果失败：" + ee); return; }
                    StRenderReco(jj);
                }));
            });
        }

        private void StRenderReco(Dictionary<string, object> j)
        {
            if (J.IsFailed(j))
            {
                SetErr(_stRecoStatus, "计算失败：" + StErrMsg(j));
                return;
            }
            var s = (Dictionary<string, object>)j["summary"];
            _stRecoCards.Controls.Clear();
            _stRecoCards.Controls.Add(StKpiCard("买入", J.Int(s["buy_count"]) + " 只"));
            _stRecoCards.Controls.Add(StKpiCard("买入金额", J.Money(J.Num(s["buy_amount"]))));
            _stRecoCards.Controls.Add(StKpiCard("卖出", J.Int(s["sell_count"]) + " 只"));
            _stRecoCards.Controls.Add(StKpiCard("卖出金额", J.Money(J.Num(s["sell_amount"]))));
            _stRecoCards.Controls.Add(StKpiCard("持股", J.Int(s["hold_count"]) + " 只"));
            _stRecoCards.Controls.Add(StKpiCard("持股市值", J.Money(J.Num(s["hold_amount"]))));
            _stRecoCards.Controls.Add(StKpiCard("合计浮盈亏", J.Money(J.Num(s["hold_pnl"]))));
            Skin(_stRecoCards);

            StFillRecoGrid(_stBuyGrid, j["buy"], false);
            StFillRecoGrid(_stSellGrid, j["sell"], false);
            StFillRecoGrid(_stHoldGrid, j["hold"], true);

            _stRecoStatus.Text = "策略：" + StCurStrategyName() + " · 截至 " + J.StrOr(j["date"], "")
                    + " · 目标资金 " + J.Money(StM(j as Dictionary<string, object>, "target_capital"));
            _stRecoStatus.ForeColor = C.DownSoft;
        }

        private void StFillRecoGrid(StockGrid grid, object list, bool hold)
        {
            grid.Rows.Clear();
            var arr = list as System.Collections.IList;
            if (arr != null)
            {
                grid.SuspendLayout();
                foreach (var it in arr)
                {
                    var d = it as Dictionary<string, object>;
                    if (d == null) continue;
                    int ri;
                    if (hold)
                    {
                        double pnl = J.Num(d["pnl"]);
                        ri = grid.Rows.Add(J.StrOr(d["code"], ""), StName(d), J.StrOr(d["action"], ""),
                            J.FmtNum(d["cost_price"]), J.FmtNum(d["price"]), J.StrOr(d["buy_date"], ""),
                            J.Pct100Plain(J.Num(d["weight"])), J.Money(pnl));
                        // A 股习惯：红涨绿跌
                        grid.Rows[ri].Cells[7].Style.ForeColor =
                            pnl >= 0 ? C.UpErr : C.DownSoft;
                        grid.Rows[ri].Cells[6].Style.ForeColor = _cSub;
                    }
                    else
                    {
                        ri = grid.Rows.Add(J.StrOr(d["code"], ""), StName(d), J.FmtNum(d["price"]),
                            J.Pct100Plain(J.Num(d["weight"])));
                    }
                }
                grid.ResumeLayout();
            }
            grid.Fit();
        }

        // ---------------- 回测范围：排除特征筛选 ----------------
        private CheckBox ExChk(string text)
        {
            var c = new CheckBox();
            c.Text = text;
            c.AutoSize = true;
            c.Margin = new Padding(0, 0, 14, 0);
            return c;
        }

        private List<string> StExcludeList()
        {
            var ex = new List<string>();
            if (_stExChiNext != null && _stExChiNext.Checked) ex.Add("chinext");
            if (_stExStar != null && _stExStar.Checked) ex.Add("star");
            if (_stExBse != null && _stExBse.Checked) ex.Add("bse");
            if (_stExSt != null && _stExSt.Checked) ex.Add("st");
            if (_stExStarSt != null && _stExStarSt.Checked) ex.Add("star_st");
            if (_stExIpo != null && _stExIpo.Checked) ex.Add("ipo");
            if (_stExSubNew != null && _stExSubNew.Checked) ex.Add("sub_new");
            if (_stExMicro != null && _stExMicro.Checked) ex.Add("micro");
            return ex;
        }

        // 勾选排除特征时实时刷新「本地共 N 只 · 剔除后 M 只」（依赖后端 /universe/summary）。
        private void StScopePreview()
        {
            if (_stScopeHint == null) return;
            var ex = StExcludeList();
            string q = ex.Count > 0 ? "?exclude=" + string.Join(",", ex) : "";
            RunUi<Dictionary<string, object>>(delegate
            {
                string body;
                if (!GetText("http://127.0.0.1:8000/api/strategies/universe/summary" + q, 20000, out body))
                    return null;
                try { return new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(body); }
                catch { return null; }
            }, delegate(Dictionary<string, object> j)
            {
                if (j == null || !J.IsOk(j))
                {
                    _stScopeHint.Text = "共 " + _stLocalCodeCount + " 只";
                    return;
                }
                int baseN = (int)J.Num(J.Get(j, "base_count"));
                int filtN = (int)J.Num(J.Get(j, "filtered_count"));
                _stScopeHint.Text = "共 " + baseN + " 只 · 剔除后 " + filtN + " 只";
            }, delegate(Exception ex2)
            {
                _stScopeHint.Text = "共 " + _stLocalCodeCount + " 只";
            }, null);
        }

        // ---------------- 通用小工具 ----------------
        // 回测 / 推荐共用请求体：策略、范围、资金、佣金都取「回测范围」页的设置。
        // window>0 时后端直接把区间定位到最近 N 个交易日（近期成果用）。
        // meta 由调用方传入（点击时取的快照），不在函数内部读列表选中项 —— 理由见 StSelectedMeta。
        private Dictionary<string, object> StRequest(Dictionary<string, object> meta, int window, double? ruinThreshold = null)
        {
            return new Dictionary<string, object>
            {
                { "strategy_id", J.StrOr(meta["id"], "") },
                { "params", StReadParams() },
                { "use_all", true },
                { "codes", new List<string>() },
                { "exclude", StExcludeList() },
                { "start", StDate(_stStart) },
                { "end", StDate(_stEnd) },
                { "window", window > 0 ? (object)window : null },
                { "initial_capital", (double)_stCapital.Value },
                { "commission", J.NumOr(_stComm.Text, 0.0003) },
                { "benchmark", (_stBenchChk.Checked && _stBench.Text.Trim().Length > 0) ? _stBench.Text.Trim() : null },
                { "ruin_threshold", ruinThreshold },
            };
        }

        private static FlowLayoutPanel StCardRow()
        {
            var f = new FlowLayoutPanel();
            f.FlowDirection = FlowDirection.LeftToRight;
            f.WrapContents = true;
            f.AutoSize = true;
            f.AutoSizeMode = AutoSizeMode.GrowAndShrink;
            f.MaximumSize = new Size(900, 0);
            f.Margin = new Padding(0, 0, 0, 8);
            return f;
        }

        // 只读表格：列宽随内容、禁排序、禁选中高亮。
        // 用 DisplayedCellsExceptHeader 而**不用 Fill**——Fill 会把某一列按比例
        // 拉满，名称这种短文本列会被撑得很宽（用户反馈「名称这列太长了」）；
        // 按内容自适应，剩余空白留在表格右侧，视觉更干净。
        /// <summary>
        /// 表格本体由 `NewGrid`（GridKit.cs）统一创建：外观 / 只读 / 禁选中 / 自适应都在控件里。
        /// 这里只补「放进本页流式布局」的摆放方式（本页用 Anchor，不让 Dock 抢布局）。
        /// </summary>
        private static void StPlaceGrid(StockGrid g)
        {
            g.Dock = DockStyle.None;
            g.Anchor = AnchorStyles.Left | AnchorStyles.Right | AnchorStyles.Top;
        }

        private static string StName(Dictionary<string, object> d)
        {
            return (d.ContainsKey("name") && d["name"] != null) ? J.StrOr(d["name"], "") : "—";
        }

        private static string StErrMsg(Dictionary<string, object> j)
        {
            object err;
            if (j.TryGetValue("error", out err))
            {
                var ed = err as Dictionary<string, object>;
                if (ed != null) return J.StrOr(ed["message"], "");
            }
            return "未知错误";
        }

        /// <summary>
/// 第 24 项：运行策略前的**数据新鲜度守卫**。
///
/// 策略固定跑全市场（筛选发生在策略内部），所以「补数据」必然是补全市场
/// 5585 只 —— 实测吞吐 1.2~1.4 只/秒，一轮 60~80 分钟。因此这里**不做同步补齐**
/// （那等于让用户对着界面等一小时），只做：
///   1. 检查新鲜度（基准是**交易日**，收盘前不期望今天）；
///   2. 陈旧则弹窗把话说清楚，让用户选「等它补完 / 用现有数据继续 / 取消」；
///   3. 选「等」就阻塞在此轮询进度，补齐完成后自动继续原本的操作。
///
/// 选「等」而不是直接开跑，是因为数据停在几天前时，选股结果本身就不可信；
/// 但把选择权交给用户，因为补齐可能要一小时。
/// </summary>
private void StEnsureFresh(Dictionary<string, object> meta, Action then)
        {
            if (_stFreshBusy)
            {
                // ⚠️ 不能静默 return：上一次点击的检查还在飞时用户又选了个策略再点，
                // 这一下被吞掉，用户会以为「已按新选的策略重跑」，其实后台跑的还是上一次那个。
                SetErr(_stResultStatus, "正在检查行情数据是否最新，请稍候再点「运行回测」…");
                return;
            }
            _stFreshBusy = true;
            // 策略名取**点击时**的快照（而不是稍后 proceed 时再读列表选中项），
            // 保证「弹窗里写的策略」==「实际提交的策略」。
            string sid = J.StrOr(meta["name"], J.StrOr(meta["id"], "—"));
            Action proceed = delegate
            {
                _stFreshBusy = false;
                if (_stFreshTimer != null) { _stFreshTimer.Stop(); _stFreshTimer = null; }
                then();
            };
            System.Threading.Tasks.Task.Run(delegate
            {
                Dictionary<string, object> f = null;
                try
                {
                    f = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(
                        VRequest("http://127.0.0.1:8000/api/history/download/freshness", null, 60000));
                }
                catch (Exception ex)
                {
                    string msg = "无法检查行情数据是否最新：" + ex.Message;
                    Ui(delegate
                    {
                        _stFreshBusy = false;
                        SetErr(_stResultStatus, msg + "（将按现有数据继续）");
                        then();
                    });
                    return;
                }
                bool need = f.ContainsKey("need_refresh") && f["need_refresh"] is bool
                            && (bool)f["need_refresh"];
                if (!need) { Ui(proceed); return; }

                string expected = J.StrOr(f["expected"], "?");
                int covered = J.Int(f["covered"]);
                int checkedCount = J.Int(f["checked"]);
                DialogResult dr = DialogResult.None;
                Ui(delegate
                {
                    dr = MessageBox.Show(this,
                        "「" + sid + "」要用全市场 " + checkedCount + " 只股票做筛选，\r\n"
                        // ⚠️ 新鲜度是**逐票**判定的：covered = 已更新到 expected 的只数，
                        // latest = 全市场里最新那一只的日期（通常就是 expected）。
                        // 原来写「本地行情只更新到 <latest>（应有 <expected>）」在
                        // latest==expected 时会自相矛盾（"只更新到 10-13，应有 10-13"），
                        // 完全看不出「其实 5573 只还停在更早」。这里改成按只数说。
                        + "但其中只有 " + covered + " 只的行情更新到 " + expected
                        + "（其余 " + Math.Max(0, checkedCount - covered) + " 只还停在更早的交易日）。\r\n\r\n"
                        + "· 现在补齐：预计 60~80 分钟（可随时关窗，断了也能从断点续上）\r\n"
                        + "· 用现有数据继续：结果基于各票现有数据（未补齐），可能不是最新行情",
                        AppTitle + " - 行情数据不是最新", MessageBoxButtons.YesNoCancel,
                        MessageBoxIcon.Warning, MessageBoxDefaultButton.Button2);
                });
                if (dr == DialogResult.Cancel) { Ui(delegate { _stFreshBusy = false; }); return; }
                if (dr != DialogResult.Yes) { Ui(proceed); return; }
                Ui(delegate { StWaitFresh(proceed, sid); });
            });
        }

        /// <summary>第 24 项：轮询等待补齐完成，完成后自动继续 `then`。</summary>
        private void StWaitFresh(Action then, string sid)
        {
            if (_stFreshTimer == null)
            {
                _stFreshTimer = new Timer();
                _stFreshTimer.Interval = 5000;
                _stFreshTimer.Tick += delegate { StPollFresh(then, sid); };
            }
            SetErr(_stResultStatus, "正在等待行情数据补齐…（" + sid + "）");
            _stFreshTimer.Start();
            StPollFresh(then, sid);
        }

        private void StPollFresh(Action then, string sid)
        {
            System.Threading.Tasks.Task.Run(delegate
            {
                Dictionary<string, object> f = null;
                try
                {
                    f = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(
                        VRequest("http://127.0.0.1:8000/api/history/download/freshness", null, 60000));
                }
                catch (Exception) { return; }        // 单次失败下个周期再试
                bool need = f.ContainsKey("need_refresh") && f["need_refresh"] is bool
                            && (bool)f["need_refresh"];
                int covered = J.Int(f["covered"]);
                int total = J.Int(f["checked"]);
                string latest = J.StrOr(f["latest"], "?");
                Ui(delegate
                {
                    if (!need)
                    {
                        SetErr(_stResultStatus, "数据已补齐（至" + latest + "），继续运行…");
                        _stFreshTimer.Stop();
                        then();
                        return;
                    }
                    SetErr(_stResultStatus, string.Format(
                        "等待补齐中 {0}/{1}（数据至 {2}）…可切到别的页签，补齐在后台继续",
                        covered, total, latest));
                });
            });
        }

        private void StRunBacktest()
        {
            // 点击时取一次快照：后面弹窗、等待补齐、真正提交统一用这一份。
            // 不能各步各读一次列表选中项 —— 中途清单被刷新 / 用户改了选中，
            // 就会出现「弹窗说筹码、实际跑 RPS」这类对不上的情况。
            var meta = StSelectedMeta();
            if (meta == null)
            {
                SetErr(_stResultStatus, "请先在「策略选择与编辑」里选一个策略");
                return;
            }
            // 第 24 项：数据新鲜度守卫在真正发起回测之前（数据陈旧时的弹窗与等待
            // 都在 `StEnsureFresh` 里）。用守卫包一层而不是就地判断，是为了让
            // 回测与「生成当前推荐」两条入口共用同一套逻辑与同一个弹窗。
            StEnsureFresh(meta, delegate { StRunBacktestInner(meta); });
        }

        private void StRunBacktestInner(Dictionary<string, object> meta)
        {
            string sid = J.StrOr(meta["id"], "");
            var req = new Dictionary<string, object>
            {
                { "strategy_id", sid },
                { "params", StReadParams() },
                { "use_all", true },
                { "codes", new List<string>() },
                { "exclude", StExcludeList() },
                { "start", StDate(_stStart) },
                { "end", StDate(_stEnd) },
                { "initial_capital", (double)_stCapital.Value },
                { "commission", J.NumOr(_stComm.Text, 0.0003) },
                { "benchmark", (_stBenchChk.Checked && _stBench.Text.Trim().Length > 0) ? _stBench.Text.Trim() : null },
            };

            string start = StDate(_stStart) ?? "起点";
            string end = StDate(_stEnd) ?? "至今";
            var exList = StExcludeList();
            string scope = exList.Count == 0
                ? "全部本地(" + _stLocalCodeCount + "只)"
                : "剔除 " + string.Join("/", exList) + "(基线 " + _stLocalCodeCount + " 只)";
            double cap = (double)_stCapital.Value;
            _stResultHeader.Text = "策略：" + J.StrOr(meta["name"], sid) + " · 范围：" + scope + " "
                + start + "~" + end + " · 初始 " + (cap / 10000).ToString("F1") + "万 · 佣金 "
                + J.NumOr(_stComm.Text, 0.0003).ToString("P4").Replace(" ", "");

            _stResultStatus.Text = "回测中：" + J.StrOr(meta["name"], sid) + "…";
            _stResultStatus.ForeColor = _cSub;
            string body = new JavaScriptSerializer().Serialize(req);
            int myRun = ++_stPollRunId;      // 本次运行的编号：用来丢弃旧轮询响应
            StShowProgress(0, 0, "正在启动…");

            // 第 18 项：改**后台任务 + 轮询**。原来是一次同步请求 + 10 分钟超时，
            // 全市场（5000+ 只）冷缓存实测要 7 分钟以上，结构上仍会超时，
            // 而且中途白屏、看不到任何进展。现在启动接口 2 秒内返回，
            // 进度由 /backtest/progress 逐票汇报。
            System.Threading.Tasks.Task.Run(delegate
            {
                try
                {
                    string resp = VRequest("http://127.0.0.1:8000/api/strategies/backtest/start",
                                           body, 60000);
                    var j = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(resp);
                    Invoke((Action)(() =>
                    {
                        if (myRun != _stPollRunId) return;      // 已被新一轮取代
                        if (J.IsFailed(j))                       // 例如「已有回测在运行」
                        {
                            StStopProgress();
                            SetErr(_stResultStatus, "回测失败：" + J.ErrMsg(j));
                            return;
                        }
                        StPollResult(myRun);
                    }));
                }
                catch (Exception ex)
                {
                    Invoke((Action)(() =>
                    {
                        if (myRun != _stPollRunId) return;
                        StStopProgress();
                        SetErr(_stResultStatus, "回测失败：" + ex.Message);
                    }));
                }
            });
        }

        /// <summary>进度条 / 进度文字的显示与更新。</summary>
        private void StShowProgress(int done, int total, string text)
        {
            if (_stProgress == null) return;
            _stProgress.Visible = true;
            _stProgressText.Visible = true;
            _stProgress.Maximum = Math.Max(1, total);
            _stProgress.Value = Math.Min(Math.Max(0, done), Math.Max(1, total));
            _stProgressText.Text = text;
        }

        private void StStopProgress()
        {
            if (_stPollTimer != null)
            {
                _stPollTimer.Stop();
                _stPollTimer.Dispose();
                _stPollTimer = null;
            }
            if (_stProgress != null) _stProgress.Visible = false;
            if (_stProgressText != null)
            {
                _stProgressText.Visible = false;
                _stProgressText.Text = "";
            }
        }

        /// <summary>轮询一次进度；state 变成ready/error 时取结果（或报错）并停表。</summary>
        private void StPollResult(int myRun)
        {
            StStopProgress();                        // 先清掉旧的（可能还在跑）
            _stPollTimer = new Timer();
            _stPollTimer.Interval = 900;
            _stPollTimer.Tick += delegate { StPollTick(myRun); };
            _stPollTimer.Start();
            StPollTick(myRun);                       // 立刻打一次，别等第一个周期
        }

        private void StPollTick(int myRun)
        {
            if (myRun != _stPollRunId) return;       // 旧任务，直接不响应
            if (_stPollBusy) return;                 // 上一次还没回来
            _stPollBusy = true;
            RunUi<Dictionary<string, object>>(
                delegate
                {
                    string resp = VRequest("http://127.0.0.1:8000/api/strategies/backtest/progress", null, 30000);
                    return new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(resp);
                },
                delegate(Dictionary<string, object> j)
                {
                    _stPollBusy = false;
                    if (myRun != _stPollRunId) return;
                    StApplyProgress(j, myRun);
                },
                delegate(Exception ex)
                {
                    _stPollBusy = false;
                    if (myRun != _stPollRunId) return;
                    // 单次轮询失败（后端忙）不致命：让表继续打，下一次再说
                    if (_stProgressText != null)
                        _stProgressText.Text = "进度查询失败，正在重试…" + ex.Message;
                },
                null);
        }

        /// <summary>把 /backtest/progress 的状态画到进度条上；完成则取结果。</summary>
        private void StApplyProgress(Dictionary<string, object> j, int myRun)
        {
            if (J.IsFailed(j)) { StStopProgress(); return; }
            var st = J.Get(j, "state") as Dictionary<string, object>;
            if (st == null) return;
            string state = J.Str(J.Get(st, "state"));
            string phase = J.Str(J.Get(st, "phase"));
            int done = (int)J.NumAt(st, "done");
            int total = (int)J.NumAt(st, "total");
            double elapsed = J.NumAt(st, "elapsed");
            string err = J.Str(J.Get(st, "error"));

            if (state == "running")
            {
                // 暂停标志从控制句柄读（后端 state 里带 paused 字段）。
                // 暂停时进度条**不动**，所以只靠 done 看不出来 ——
                // 用户看到进度卡住会以为死了，必须显式说明「已暂停」。
                // ⚠️ 本项目用 .NET Framework 的 csc（C# 5），**不支持** `is bool b` 模式匹配
                //   与内联 `out object v` 声明 —— 写了就是编译错误。故用最朴素写法。
                object pv = J.Get(st, "paused");
                bool paused = pv is bool && (bool)pv;
                StSetControlButtons(true, paused);
                string text;
                if (phase == "loading")
                    text = "加载数据 " + done + " / " + total + "只（已用 "
                           + ((int)elapsed) + " 秒）";
                else
                    text = "计算信号中…" + done + " / " + total + "只（已用 "
                           + ((int)elapsed) + " 秒）";
                if (paused) text = "⏸ 已暂停 · " + text + " · 可点「继续」或「停止」";
                StShowProgress(total > 0 ? done : 0, total, text);
                return;
            }

            // ready / error / cancelled：停表，取结果
            StStopProgress();
            StSetControlButtons(false, false);
            if (state == "cancelled")
            {
                // 用户主动停止：**中性提示**，不用错误态的红色。
                // 中途的结果不保留（后端已清空），所以不能接着去取结果。
                _stResultStatus.Text = "已停止（已完成的回测结果不保留，可改参数后重跑）";
                _stResultStatus.ForeColor = _cSub;
                return;
            }
            if (state == "error")
            {
                SetErr(_stResultStatus, "回测失败：" + (string.IsNullOrEmpty(err) ? "未知错误" : err));
                return;
            }
            StFetchResult(myRun);
        }

        private void StFetchResult(int myRun)
        {
            RunUi<Dictionary<string, object>>(
                delegate
                {
                    string resp = VRequest("http://127.0.0.1:8000/api/strategies/backtest/result", null, 120000);
                    return new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(resp);
                },
                delegate(Dictionary<string, object> j)
                {
                    if (myRun != _stPollRunId) return;
                    if (J.IsFailed(j)) { SetErr(_stResultStatus, "取结果失败：" + J.ErrMsg(j)); return; }
                    StRenderResult(j);
                },
                delegate(Exception ex)
                {
                    if (myRun != _stPollRunId) return;
                    SetErr(_stResultStatus, "取结果失败：" + ex.Message);
                },
                null);
        }

        private void StRenderResult(Dictionary<string, object> j)
        {
            if (J.IsFailed(j))
            {
                string msg = "回测失败";
                object err;
                if (j.TryGetValue("error", out err))
                {
                    var ed = err as Dictionary<string, object>;
                    if (ed != null) msg = "回测失败：" + J.StrOr(ed["message"], "");
                }
                SetErr(_stResultStatus, msg);
                return;
            }

            var metrics = (Dictionary<string, object>)j["metrics"];
            _stMetricCards.Controls.Clear();
            _stMetricCards.Controls.Add(StKpiCard("总收益率", J.Pct100Plain(J.Num(metrics["total_return"]))));
            _stMetricCards.Controls.Add(StKpiCard("年化收益", J.Pct100Plain(J.Num(metrics["annual_return"]))));
            _stMetricCards.Controls.Add(StKpiCard("最大回撤", J.Pct100Plain(J.Num(metrics["max_drawdown"]))));
            _stMetricCards.Controls.Add(StKpiCard("夏普比率", J.Num(metrics["sharpe"]).ToString("F2")));
            _stMetricCards.Controls.Add(StKpiCard("日胜率", StMPct(metrics, "win_rate_daily")));
            _stMetricCards.Controls.Add(StKpiCard("交易胜率", StMPct(metrics, "win_rate_trade")));
            // 笔数盈亏比与金额盈亏比会背离，**盈亏比才是必看的那一个**
            _stMetricCards.Controls.Add(StKpiCard("盈亏比", StMText(metrics, "profit_factor")));
            _stMetricCards.Controls.Add(StKpiCard("单笔期望", StMPct(metrics, "expectancy_pct")));
            _stMetricCards.Controls.Add(StKpiCard("交易次数", J.Int(StM(metrics, "num_trades_eff")).ToString()));
            _stMetricCards.Controls.Add(StKpiCard("平均持仓", StMText(metrics, "avg_positions", "F1")));
            _stMetricCards.Controls.Add(StKpiCard("年化换手", StMText(metrics, "turnover_annual", "F1")));
            // 横向比策略时该看的是「每单位动用资金收益」，不是总收益率（后者混着仓位高低）
            _stMetricCards.Controls.Add(StKpiCard("资金利用率", StMPct(metrics, "capital_utilization")));
            _stMetricCards.Controls.Add(StKpiCard("每单位动用资金", StMPct(metrics, "return_per_utilized_capital")));
            object bret;
            if (metrics.TryGetValue("benchmark_return", out bret) && bret != null)
                _stMetricCards.Controls.Add(StKpiCard("基准收益", J.Pct100Plain(J.Num(bret))));
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
            _stResultId = J.StrOr(j.ContainsKey("result_id") ? j["result_id"] : null, "");
            _stExpanded.Clear();          // 新一轮回测结果，展开状态作废
            _stDetailCache.Clear();       // 明细跟着旧 result_id，一并作废
            StFillPerStockRows();

            // 参与只数改从 scope.codes_count 取（metrics.num_stocks 已废止 —— 它其实是
            // 回测池大小，不是持仓数，显示出来会被当成「持仓了这么多只」）
            var scope = j["scope"] as Dictionary<string, object>;
            int doneCount = scope != null ? J.Int(scope["codes_count"]) : 0;
            _stResultStatus.Text = "回测完成（参与统计 " + doneCount + " 只）";
            _stResultStatus.ForeColor = C.DownSoft;
            object errs;
            if (j.TryGetValue("data_errors", out errs))
            {
                var el = errs as System.Collections.IList;
                if (el != null && el.Count > 0)
                    _stResultStatus.Text = "回测完成（参与统计 " + doneCount
                        + " 只，" + el.Count + " 只数据缺失/跳过）";
            }
        }

        // ---------------- 历史结果（加载已保存的回测报告） ----------------
        /// <summary>列出已保存的回测报告，选一份加载，按回测结果的样式重放渲染。</summary>
        private void StShowHistory()
        {
            RunUi<Dictionary<string, object>>(
                delegate
                {
                    string resp = VRequest("http://127.0.0.1:8000/api/strategies/backtest/reports?limit=100",
                                           null, 15000);
                    return new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(resp);
                },
                delegate(Dictionary<string, object> j)
                {
                    var list = (j != null && j.ContainsKey("reports"))
                        ? j["reports"] as System.Collections.IList : null;
                    if (list == null || list.Count == 0)
                    {
                        SetErr(_stResultStatus, "暂无历史回测报告（先运行一次回测）");
                        return;
                    }
                    string file = StPickReportDialog(list);
                    if (!string.IsNullOrEmpty(file)) StLoadHistory(file);
                },
                delegate(Exception ex) { SetErr(_stResultStatus, "读取历史列表失败：" + ex.Message); },
                null);
        }

        /// <summary>弹出历史报告选择框；返回选中的文件名（取消返回 null）。</summary>
        private string StPickReportDialog(System.Collections.IList list)
        {
            var files = new List<string>();
            using (var dlg = new Form())
            {
                dlg.Text = "历史回测结果";
                dlg.StartPosition = FormStartPosition.CenterParent;
                dlg.ShowInTaskbar = false;
                dlg.MinimizeBox = false;
                dlg.MaximizeBox = false;
                dlg.Width = 840;
                dlg.Height = 440;
                dlg.MinimumSize = new Size(560, 300);

                var lb = new ListBox();
                lb.Dock = DockStyle.Fill;
                lb.IntegralHeight = false;
                lb.Font = new Font("Microsoft YaHei UI", 9f);
                foreach (var it in list)
                {
                    var d = it as Dictionary<string, object>;
                    if (d == null) continue;
                    string f = J.StrOr(J.Get(d, "file"), "");
                    if (f.Length == 0) continue;
                    files.Add(f);
                    lb.Items.Add(string.Format("{0}  |  {1}  |  {2} ~ {3}  |  收益 {4}",
                        J.StrOr(J.Get(d, "generated_at"), ""),
                        J.StrOr(J.Get(d, "strategy_name"), ""),
                        J.StrOr(J.Get(d, "start"), ""),
                        J.StrOr(J.Get(d, "end"), ""),
                        J.Pct100Plain(J.Num(J.Get(d, "total_return")))));
                }

                var bottom = new Panel();
                bottom.Dock = DockStyle.Bottom;
                bottom.Height = 44;
                var ok = new Button();
                ok.Text = "加载";
                ok.DialogResult = DialogResult.OK;
                ok.Width = 88;
                ok.Height = 28;
                var cancel = new Button();
                cancel.Text = "取消";
                cancel.DialogResult = DialogResult.Cancel;
                cancel.Width = 88;
                cancel.Height = 28;
                bottom.Controls.Add(ok);
                bottom.Controls.Add(cancel);
                bottom.Resize += delegate
                {
                    int right = bottom.ClientSize.Width;
                    ok.Location = new Point(Math.Max(0, right - 196), 8);
                    cancel.Location = new Point(Math.Max(0, right - 100), 8);
                };

                dlg.Controls.Add(lb);
                dlg.Controls.Add(bottom);
                dlg.AcceptButton = ok;
                dlg.CancelButton = cancel;
                lb.DoubleClick += delegate { dlg.DialogResult = DialogResult.OK; dlg.Close(); };
                if (dlg.ShowDialog(this) != DialogResult.OK || lb.SelectedIndex < 0) return null;
                return files[lb.SelectedIndex];
            }
        }

        /// <summary>取回选中的历史报告全文，按回测结果的样式渲染（指标卡 / 净值曲线 / 个股明细）。</summary>
        private void StLoadHistory(string file)
        {
            SetErr(_stResultStatus, "正在加载历史报告…");
            RunUi<Dictionary<string, object>>(
                delegate
                {
                    string resp = VRequest(
                        "http://127.0.0.1:8000/api/strategies/backtest/reports/"
                        + System.Uri.EscapeDataString(file), null, 60000);
                    return new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(resp);
                },
                delegate(Dictionary<string, object> j)
                {
                    if (j == null || J.IsFailed(j))
                    {
                        SetErr(_stResultStatus, "加载历史报告失败：" + J.ErrMsg(j));
                        return;
                    }
                    // 老 schema 报告可能没有净值序列：补空，避免渲染时缺 key 抛异常
                    if (!j.ContainsKey("dates")) j["dates"] = new System.Collections.ArrayList();
                    if (!j.ContainsKey("equity")) j["equity"] = new System.Collections.ArrayList();
                    StRenderResult(j);           // 与刚跑完的回测结果共用同一套渲染
                    _stResultId = "";            // 历史报告的逐笔明细不在后端单槽缓存里，禁用展开
                    var strat = j.ContainsKey("strategy") ? j["strategy"] as Dictionary<string, object> : null;
                    var scope = j.ContainsKey("scope") ? j["scope"] as Dictionary<string, object> : null;
                    string nm = strat != null ? J.StrOr(J.Get(strat, "name"), "") : "";
                    string s = scope != null ? J.StrOr(J.Get(scope, "start"), "") : "";
                    string e = scope != null ? J.StrOr(J.Get(scope, "end"), "") : "";
                    // 覆盖成「历史」标识：否则用户会把它当成刚跑出来的结果
                    if (nm.Length > 0)
                        _stResultHeader.Text = "策略：" + nm + " · " + s + " ~ " + e + "（历史结果）";
                    _stResultStatus.Text = "历史结果（生成于 "
                        + J.StrOr(J.Get(j, "generated_at"), "") + "）";
                    _stResultStatus.ForeColor = C.DownSoft;
                },
                delegate(Exception ex) { SetErr(_stResultStatus, "加载历史报告失败：" + ex.Message); },
                null);
        }

        /// <summary>历史结果页：刷新已保存报告列表。</summary>
        private void StHistRefresh()
        {
            if (_stHistList == null) return;
            RunUi<Dictionary<string, object>>(
                delegate
                {
                    string resp = VRequest("http://127.0.0.1:8000/api/strategies/backtest/reports?limit=100",
                                           null, 15000);
                    return new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(resp);
                },
                delegate(Dictionary<string, object> j)
                {
                    if (_stHistList == null) return;
                    _stHistList.Items.Clear();
                    var list = (j != null && j.ContainsKey("reports"))
                        ? j["reports"] as System.Collections.IList : null;
                    if (list == null || list.Count == 0)
                    {
                        SetErr(_stHistStatus, "暂无历史报告（先运行一次回测）");
                        return;
                    }
                    foreach (var it in list)
                    {
                        var d = it as Dictionary<string, object>;
                        if (d == null) continue;
                        string f = J.StrOr(J.Get(d, "file"), "");
                        if (f.Length == 0) continue;
                        var lvi = new ListViewItem(J.StrOr(J.Get(d, "generated_at"), ""));
                        lvi.SubItems.Add(J.StrOr(J.Get(d, "strategy_name"), ""));
                        lvi.SubItems.Add(J.StrOr(J.Get(d, "start"), "") + " ~ " + J.StrOr(J.Get(d, "end"), ""));
                        lvi.SubItems.Add(J.Pct100Plain(J.Num(J.Get(d, "total_return"))));
                        lvi.Tag = f;                    // 文件名随行携带，加载/删除都靠它
                        _stHistList.Items.Add(lvi);
                    }
                    SetErr(_stHistStatus, "共 " + _stHistList.Items.Count + " 份");
                },
                delegate(Exception ex) { SetErr(_stHistStatus, "加载失败：" + ex.Message); },
                null);
        }

        /// <summary>历史结果页：加载选中的报告，切到「回测结果」页按同样样式渲染。</summary>
        private void StHistLoad()
        {
            if (_stHistList == null || _stHistList.SelectedItems.Count == 0)
            {
                SetErr(_stHistStatus, "先选一份报告");
                return;
            }
            string file = _stHistList.SelectedItems[0].Tag as string;
            if (string.IsNullOrEmpty(file)) { SetErr(_stHistStatus, "先选一份报告"); return; }
            StLoadHistory(file);          // 复用回测结果渲染逻辑
            _stStrip.Select(2);           // 切到「回测结果」页展示
        }

        /// <summary>历史结果页：删除选中的报告（后端删文件 + 索引行）。</summary>
        private void StHistDelete()
        {
            if (_stHistList == null || _stHistList.SelectedItems.Count == 0)
            {
                SetErr(_stHistStatus, "先选一份报告");
                return;
            }
            string file = _stHistList.SelectedItems[0].Tag as string;
            if (string.IsNullOrEmpty(file)) { SetErr(_stHistStatus, "先选一份报告"); return; }
            if (MessageBox.Show(this, "确定删除这份历史报告？\n" + file, "删除确认",
                    MessageBoxButtons.YesNo, MessageBoxIcon.Warning) != DialogResult.Yes) return;
            SetErr(_stHistStatus, "正在删除…");
            RunUi<Dictionary<string, object>>(
                delegate
                {
                    string resp = VRequest(
                        "http://127.0.0.1:8000/api/strategies/backtest/reports/"
                        + System.Uri.EscapeDataString(file), null, 15000, "DELETE");
                    return new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(resp);
                },
                delegate(Dictionary<string, object> j)
                {
                    if (j == null || J.IsFailed(j))
                    {
                        SetErr(_stHistStatus, "删除失败：" + J.ErrMsg(j));
                        return;
                    }
                    StHistRefresh();          // 重新拉列表，让删除即时可见
                },
                delegate(Exception ex) { SetErr(_stHistStatus, "删除失败：" + ex.Message); },
                null);
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
                string code = J.StrOr(d["code"], "");
                string name = (d.ContainsKey("name") && d["name"] != null) ? J.StrOr(d["name"], "") : "—";
                string buyDate = J.StrOr(d.ContainsKey("buy_date") ? d["buy_date"] : null, "");
                string sellDate = J.StrOr(d.ContainsKey("sell_date") ? d["sell_date"] : null, "");
                object retObj = d.ContainsKey("ret") ? d["ret"] : null;
                string retTxt = (retObj != null) ? J.Pct100Plain(J.Num(retObj)) : "—";
                int ri = _stPerStockGrid.Rows.Add(
                    code, name,
                    J.Pct100Plain(J.Num(d["total_return"])), J.Int(d["trades"]).ToString(),
                    buyDate, sellDate, retTxt);
                var row = _stPerStockGrid.Rows[ri];
                row.Tag = "main:" + code;
                row.DefaultCellStyle.SelectionBackColor = _cPanel;
                row.DefaultCellStyle.SelectionForeColor = _cText;
            }
            _stPerStockGrid.ResumeLayout();
            _stPerStockGrid.Fit(240, 620);
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
            _stPerStockGrid.Fit(240, 620);
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
            _stPerStockGrid.Fit(240, 620);
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
                string buyDate = J.StrOr(x["buy_date"], "");
                string buyPrice = x["buy_price"] != null ? J.Num(x["buy_price"]).ToString("F2") : "—";
                bool closed = x["sell_date"] != null;
                string sellDate = closed ? J.StrOr(x["sell_date"], "") : "未平仓";
                string sellPrice = x["sell_price"] != null ? J.Num(x["sell_price"]).ToString("F2") : "—";
                object retObj;
                string retTxt = (x.TryGetValue("ret", out retObj) && retObj != null) ? J.Pct100Plain(J.Num(retObj)) : "—";
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
            // 本函数是 PictureBox 的事件处理器、不是 Control 子类，进不了 ChartControl 基类，
            // 抗锯齿只能手工设一次（与其它 4 个自绘表面保持一致，第 07 项）
            g.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
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
                using (var pen = new Pen(C.LineGoldDeep, 1.5f))
                    for (int i = 1; i < _stEqBench.Count; i++)
                        if (_stEqBench[i] != null && _stEqBench[i - 1] != null)
                            g.DrawLine(pen, pt(i - 1, _stEqBench[i - 1].Value), pt(i, _stEqBench[i].Value));
            }
            using (var pen = new Pen(C.AccentDeep, 2f))
                for (int i = 1; i < _stEq.Count; i++)
                    if (_stEq[i] != null && _stEq[i - 1] != null)
                        g.DrawLine(pen, pt(i - 1, _stEq[i - 1].Value), pt(i, _stEq[i].Value));

            using (var bb = new SolidBrush(_cText))
            {
                g.DrawString("组合", new Font("Microsoft YaHei UI", 9f), new SolidBrush(C.AccentDeep), (float)padL, 0f);
                if (_stEqBench != null)
                    g.DrawString("基准", new Font("Microsoft YaHei UI", 9f), new SolidBrush(C.LineGoldDeep), (float)(padL + 44), 0f);
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
                        // 刷新清单时**保住用户已选的策略**：先记下旧选中项的 id，重新填充后按
                        // id 找回同一项。否则「刷新清单」会把选中悄悄换成列表第 1 项 —— 用户在
                        // 别的子页看不到列表（选中项看不见），只会在按下「运行回测」时发现跑的
                        // 不是自己选的策略。后端清单第 1 项恰好是筹码，于是表现为
                        // 「明明选了 RPS，跑的却是筹码集中度90」。
                        string keepId = "";
                        var oldMeta = StSelectedMeta();
                        if (oldMeta != null) keepId = J.StrOr(oldMeta["id"], "");
                        _stStrategies = list;
                        _stLocalCodeCount = cnt;
                        _stList.Items.Clear();
                        foreach (var m in list) _stList.Items.Add(J.StrOr(m["name"], J.StrOr(m["id"], "?")));
                        if (_stScopeHint != null) _stScopeHint.Text = "共 " + cnt + " 只 · 剔除后 " + cnt + " 只";
                        // 文案别写成「清单已加载（N 个策略 / M 只股票）」—— 那个「N 个策略」
// 紧挨着「运行回测」按钮，读起来像是「本次要跑 N 个策略」。实际是**单选**：
// 一次只跑列表里选中的那一个。策略页的 ListBox 是单选、后端 BacktestRequest
// 也只接一个 strategy_id。
                        _stResultStatus.Text = "已加载 " + list.Count
                            + " 个策略供选择 · 一次只运行选中的 1 个 · 本地股票 " + cnt + " 只";
                        _stResultStatus.ForeColor = _cSub;
                        // 按 id 找回原选中项；该策略已从后端删除（id 找不到）才退回第 1 项。
                        int keepIdx = 0;
                        if (keepId.Length > 0)
                            for (int i = 0; i < list.Count; i++)
                                if (J.StrOr(list[i]["id"], "") == keepId) { keepIdx = i; break; }
                        if (_stList.Items.Count > 0) _stList.SelectedIndex = keepIdx;
                        _stLoaded = true;
                    }));
                }
                catch (Exception ex)
                {
                    Invoke((Action)(() =>
                    {
                        SetErr(_stResultStatus, "加载失败（确认后端已启动）：" + ex.Message);
                    }));
                }
            });
        }

        /// <summary>暂停/继续/停止按钮的可见性。</summary>
        /// <param name="running">任务在跑（决定「暂停」还是「继续」可见）。</param>
        /// <param name="paused">当前处于暂停态。</param>
        private void StSetControlButtons(bool running, bool paused)
        {
            if (_stBtnPause != null) { _stBtnPause.Visible = running && !paused; _stBtnPause.Enabled = running && !paused; }
            if (_stBtnResume != null) { _stBtnResume.Visible = running && paused; _stBtnResume.Enabled = running && paused; }
            if (_stBtnStop != null) { _stBtnStop.Visible = running; _stBtnStop.Enabled = running; }
        }

        /// <summary>
        /// 调后端的暂停/继续/停止。三者都是**幂等**的：重复点不会出错，
        /// 也不需要在前端额外上锁 —— 真状态在后端的 RunControl 里。
        /// </summary>
        private void StControlTask(string action)
        {
            RunUi<Dictionary<string, object>>(
                delegate
                {
                    string resp = VRequest(
                        "http://127.0.0.1:8000/api/strategies/backtest/" + action, "{}", 15000);
                    return new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(resp);
                },
                delegate(Dictionary<string, object> j)
                {
                    if (J.IsFailed(j)) { SetErr(_stResultStatus, "操作失败：" + J.ErrMsg(j)); return; }
                    object pv;
                    bool paused = j.TryGetValue("paused", out pv) && pv is bool && (bool)pv;
                    // 非错误态：用普通子色写，别用 SetErr（那是红色错误态）。
                    // 暂停/继续是**正常操作**，标红会让人以为出错了。
                    _stResultStatus.Text = J.StrOr(J.Get(j, "message"), "已处理");
                    _stResultStatus.ForeColor = _cSub;
                    StSetControlButtons(true, paused);
                },
                delegate(Exception ex)
                {
                    // 后端没起来是最常见的原因，给一句能照做的提示
                    SetErr(_stResultStatus, "操作失败（确认后端已启动）：" + ex.Message);
                    StSetControlButtons(true, false);
                },
                null);
        }

        // ---------------- 小工具 ----------------

        /// <summary>
        /// 安全读取 metrics 里的数值键：缺失或 null 返回 def。
        ///
        /// ⚠️ **不能**直接写 metrics["win_rate"]：组合权重口径下该字段已拆成
        /// win_rate_daily / win_rate_trade，Dictionary 缺键会抛 KeyNotFoundException，
        /// 把整个结果面板搞崩（旧代码正是直接索引，所以这次必须全部换掉）。
        /// </summary>
        private static double StM(Dictionary<string, object> d, string key, double def = 0)
        {
            object v;
            if (d == null || !d.TryGetValue(key, out v) || v == null) return def;
            return J.Num(v);
        }

        /// <summary>
        /// 指标缺失 / 不可计算时显示「—」而不是 0。
        ///
        /// 后端约定：样本为空、或全胜导致 profit_factor 分母为 0 时一律返回 null
        /// （**绝不返回 0 或 inf** —— json.dumps(inf) 产出的 Infinity 不是合法 JSON）。
        /// 这里若把 null 显示成 0.00，读者会以为「盈亏比真的是 0」。
        /// </summary>
        private static string StMText(Dictionary<string, object> d, string key, string fmt = "F2")
        {
            object v;
            if (d == null || !d.TryGetValue(key, out v) || v == null) return "—";
            return J.Num(v).ToString(fmt);
        }

        /// <summary>可空百分比：null → 「—」。</summary>
        private static string StMPct(Dictionary<string, object> d, string key)
        {
            object v;
            if (d == null || !d.TryGetValue(key, out v) || v == null) return "—";
            return J.Pct100Plain(J.Num(v));
        }

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

        private static double Clamp(double v, double lo, double hi) { return Math.Max(lo, Math.Min(hi, v)); }
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
