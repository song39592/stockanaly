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
        private TextBox _stComm;
        // 单股仓位上限：两个入口取更严者（后端折算）；留空 = 不限 = 按策略自己给的仓位执行
        private TextBox _stCapPct, _stCapAmt;
        // 买入价上限（元，未复权）：信号日收盘价高于此值放弃建仓；留空/0 = 不限。
        // 只管建仓，已持有不受影响。属执行层约束，与范围口径无关。
        private TextBox _stMaxBuyPrice;
        // 「涨停开盘应对」三档（全局生效，只作用于回测）：下拉框项序 → 后端 policy key
        private ComboBox _stLimitOpen;
        private static readonly string[] StLimitOpenKeys = { "keep", "cancel", "reseal" };
        // 买入时滤除异动：四个独立开关，默认全关（不勾 = 与旧回测结果完全一致）。
        // 口径唯一来源在 formulas.abnormal（price/turnover/severe/near），前端只用 key。
        private CheckBox _stAbPrice, _stAbTurnover, _stAbSevere, _stAbNearPrice, _stAbNearSevere;
        private Label _stScopeHint;
        private DateTimePicker _stStart, _stEnd;
        // 回测范围的「口径说明」气泡：判定基准日 + 前视偏差声明 + 数据限制
        private readonly ToolTip _stScopeTip = new ToolTip();
        // 剔除特征 → 中文名（与后端 universe.FEATURE_LABELS 一一对应）
        private static readonly Dictionary<string, string> StFeatureLabels =
            new Dictionary<string, string>
        {
            { "chinext", "创业板" }, { "star", "科创板" }, { "bse", "北交所" },
            { "st", "ST" }, { "star_st", "*ST" }, { "ipo", "新股" },
            { "sub_new", "次新股" }, { "micro", "微盘股" },
        };
        private WheelValueBox _stCapital;
        private int _stLocalCodeCount;

        // ③ 回测结果
        private Label _stResultHeader, _stResultStatus;

        // ---------------- 统一任务区（第 27 项）----------------
        // 完整回测 / 近期回测 / 当日策略**共用一套进度显示**：原来三条流程各有一套
        // timer / runId / busy / 状态标签，后果是：
        //   ① 进度条只挂在「回测结果」页 —— 另两条流程跑全市场（实测 761s / 419s）
        //      时用户完全看不到进度，也**没有暂停/停止可点**（后端其实早就支持）；
        //   ② 三套 timer 各自轮询同一个 /backtest/progress，逻辑重复三遍。
        // 现在收敛成「一个任务槽」：同一时刻只允许一个任务（互斥闸门 _stJobBusy），
        // 进度条 + 暂停/继续/停止 + 文案统一挂在**顶部「操作」框**里，六个子页都看得见。
        private const int ST_JOB_NONE = 0;
        private const int ST_JOB_FULL = 1;      // 完整回测
        private const int ST_JOB_RECENT = 2;    // 近期回测
        private const int ST_JOB_RECO = 3;      // 当日策略（推荐）
        private int _stJobKind = ST_JOB_NONE;  // 当前占用任务区的流程
        private int _stJobRunId;                // 任务代号：旧任务的响应据此丢弃
        private bool _stJobBusy;                // 有任务在跑（互斥闸门）
        private bool _stJobPollBusy;            // 有一次轮询请求在飞（防tick 重叠）
        private Timer _stJobTimer;              // 唯一的进度轮询表
        private int _stJobTotal;                // 本次任务总只数（完成文案用）
        private double _stJobElapsed;           // 本次任务耗时秒（完成文案用）
        private ProgressBar _stProgress;
        private Label _stProgressText;
        private FlowLayoutPanel _stMetricCards;
        private PictureBox _stEquityBox;
        private StockGrid _stTradesGrid;                             // 成交明细表（一行 = 一次买卖）
        private List<Dictionary<string, object>> _stTradesData;     // 逐笔成交（后端内联下发）
        private int _stTradesTotal;                                 // 真实总笔数（表内可能被上限截断）
        private int _stTradesTruncated;                             // 被上限截掉的笔数
        private Label _stTradesTip;                                 // 表头上方的口径/笔数说明

        // 建仓失败（隔日一字涨停 → 买不进）：这些建仓**没成交**，所以不在「成交明细」
        // 里（那里一行 = 一次买卖）。**不单开明细表**、也**不加说明行** —— 逐条列进
        // 成交明细表，收益 0、备注「买不进」，表本身就说清楚了。
        // 清仓延后（隔日一字跌停 → 卖不出，顺延到开板日卖出）同理：那笔是**真成交了**，
        // 只是晚了几天 → 写进成交明细那一行的备注列（延后几天 + 计划清仓日）。


        // ④ 近期策略成果（最近 N 个交易日回测）
        private WheelValueBox _stRecentDays;
        private Label _stRecentStatus;
        private FlowLayoutPanel _stRecentCards;
        private StockGrid _stRecentGrid;

        // ⑤ 单日策略（买入 / 卖出 / 持股）
        private Label _stRecoStatus;
        private FlowLayoutPanel _stRecoCards;
        private StockGrid _stBuyGrid, _stSellGrid, _stHoldGrid;
        private GroupBox _stBuyGroup, _stSellGroup;   // 标题含「执行日」，渲染时改写
        // 目标执行日（日期控件，可任选一天）：收盘前默认「今天」（信号=上一交易日），
        // 收盘后默认「下一交易日」（信号=今天）。默认日期与「选了哪天→信号日是哪天、
        // 数据齐不齐」都由后端 /date-context 下发，前端不自己猜交易日 / 收盘时点。
        private DateTimePicker _stRecoDate;
        private Label _stRecoDateHint;
        private string _stRecoSignalDate = "";   // 当前选中执行日对应的信号日
        private string _stRecoExecDate = "";     // 顺延后的真实执行日（非交易日→下一交易日）
        private bool _stRecoDateLoading;
        private bool _stRecoDateInit;           // 首次加载：把控件置为后端给的默认执行日
        // 首次置值会触发 ValueChanged → 那次「按控件新值重查」是多余的
        // （后端首次返回的上下文就是按默认执行日算的），用它抑制掉，省一次往返。
        private bool _stRecoSkipValueChanged;
        // 点「生成当前推荐」时上下文还没回来 → 不该让用户「点一次被拒、再点一次」：
        // 记下意图，等日期上下文到位后自动续跑（_stRecoAutoRunRetry 限次，防跑飞）。
        private bool _stRecoAutoRun;
        private int _stRecoAutoRunRetry;
        // 后端这轮没拿到交易日历 → 过几秒补取一次（等后台把日历拉好）
        private Timer _stRecoRefreshTimer;
        private int _stRecoRefreshCount;
        // ⚠ 上下文与控件的**对应关系**：_stRecoExecDate 是异步回填的，填好之前它
        //   可能还停留在上一次的值上。记住它对应的是控件里的哪一天，提交时据此
        //   判断上下文是否已跟上 —— 否则会静默提交上一次的执行日，后端跑出同一份
        //   结果，用户看到的是「表格没更新」（实测现象②）。
        private string _stRecoCtxDate = "";      // _stRecoExecDate 对应的「控件里的那天」
        private string _stRecoDatePending = "";  // 取上下文途中又改了日期 → 记下来，取完再补一次
        private string _stRecoSubmittedDate = "";// 本次实际提交给后端的目标执行日（取结果时回验）
        private string _stFreshExpected = "";   // 本次新鲜度守卫的信号日（单日策略专用）
        // 「结果是否还对应着所选执行日」：改了目标执行日**不会自动重跑**，
        // 若不比对，结果区会静默留着上一次的旧结果（用户会当成"没更新"）。
        // 记下当前显示结果的执行日，与所选执行日不一致时在提示行里明确说。
        private string _stRecoResultExecDate = "";
        private Dictionary<string, object> _stRecoDateCtx;   // 最近一次 /date-context 响应
        private Color _stRecoDateHintBase = SystemColors.ControlText;   // 提示行常态色（结果过期时改红）
        private string _stRecoPickedDate = "";        // 用户在控件上选的那一天（后端可能顺延）

        // ⑥ 历史结果（已保存回测报告的独立页签）
        private ListView _stHistList;
        private Label _stHistStatus;
        private List<string> _stEqDates;
        private List<double?> _stEq;
        // 回测结果图表：净值曲线 / 每日收益柱状图（纯前端切换，不碰后端口径）
        private ComboBox _stChartMode;
        private ComboBox _stBarWindow;                      // 柱状图显示窗口：全历史几千天挤成 1px，默认只看最近 250 日
        private DateTimePicker _stBarFrom, _stBarTo;        // 自定义区间起止（勾选「自定义区间」时生效）
        private const string BAR_INDEX_CODE = "sh000001";   // 柱状图默认指数：上证（未选基准时自动叠加）
        // 全市场推荐 / 近期回测都是长任务（全市场实测 419s / 761s），已改为后端异步任务，
        // 进度统一由上面的「统一任务区」跟进（_stJobTimer / _stJobKind）。
        private CheckBox _stRecentRuinChk;       // 近期回测：「破产截断」开关
        private WheelValueBox _stRecentRuinPct;  // 破产阈值（%，0=净值归零）
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
                WheelValueBox wv = Ctrl as WheelValueBox;
                if (wv != null)
                    return Type == "int" ? (object)(int)wv.Value : (object)(double)wv.Value;
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
            // ⚠️ _stResultStatus **不**挂在这里。它是「回测结果」页的状态行，
            //   而 WinForms 一个控件只能有一个父容器：下面 StBuildResult 会把它
            //   Add 到结果页的容器里 —— 同一个控件被二次 Add 会从**原父容器**里
            //   悄悄消失，操作框这行的状态文字于是凭空不见了（顶部看不到任务的
            //   老毛病之一）。
            //
            //   「刷新清单」也已搬走：它只作用于「策略选择与编辑」页那张清单，
            //   放在顶部常驻框里离上下文太远（用户反馈：按钮要跟着列表走）。
            //   现在放在策略列表正上方，见 StBuildSelect。

            // ---- 统一任务区（第 27 项）：进度条 + 暂停/继续/停止 + 状态文案 ----
            // 放在**顶部「操作」框**里（而不是某个子页）：策略页六个子页共用这一块，
            // 跑完整回测 / 近期回测 / 当日策略时，切到别的子页也照样看得见进度。
            // 第 1 行：进度条（Dock=Top 在 TableLayoutPanel 里会铺满分组框宽度）。
            _stProgress = new ProgressBar();
            _stProgress.Height = 14;
            _stProgress.Dock = DockStyle.Top;
            _stProgress.Minimum = 0;
            _stProgress.Maximum = 1;
            _stProgress.Value = 0;
            AddRow(b0, _stProgress);
            // 第 2 行：控制键 + 文案。文案限宽 560，超长自动换行 —— FlowLayoutPanel
            // 不换行，文案过长会把右边的控件顶出可视区。
            _stProgressText = Mute(Lbl("空闲 · 尚无运行中的任务"));
            _stProgressText.MaximumSize = new Size(560, 0);
            _stBtnPause = MiniBtn("暂停", delegate { StControlTask("pause"); }, 64);
            _stBtnResume = MiniBtn("继续", delegate { StControlTask("resume"); }, 64);
            _stBtnStop = MiniBtn("停止", delegate { StControlTask("cancel"); }, 64);
            AddRow(b0, Row(_stBtnPause, _stBtnResume, _stBtnStop, _stProgressText));
            StSetControlButtons(false, false);      // 空闲时三个控制键全隐

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
            _stStrip.Add("单日策略", StBuildReco);   // 原名「当前策略推荐」：只算一个交易日的动作
            _stStrip.Add("历史结果", StBuildHistory);

            // 「开始回测」提到页签栏，挂在「回测范围」后面（页签下标 1）：
            // 改完范围顺手就能跑，不用翻到「回测结果」页底去找埋在下面的按钮。
            // 点击 = 先跳到「回测结果」页（进度条 / 指标卡在那儿），再发起回测 —— 行为与原按钮一致。
            _stStrip.InsertActionAfter(1, "开始回测", delegate { StStartBacktestFromBar(); });

            // 切到「回测结果」页（页签下标 2）时：指数清单还空着就重取一次。
            // 构造阶段那次请求必然落在「后端还没起来」的窗口里（启动器先起前端、后拉后端），
            // 用户进到结果页时后端通常已经就绪 —— 这时补一次最自然。
            _stStrip.OnSelected = delegate(int idx) { if (idx == 2) StEnsureBenchList(); };

            _stStrip.Select(0);
            return p;
        }

        private void StOnEnter()
        {
            if (!_stLoaded) StLoadStrategies();
            // 收盘时点会变（15:00 前后默认执行日不同），每次进策略页重拉一次日期上下文。
            StLoadRecoDates();
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
            // 「刷新清单」放在**列表正上方**（与「历史结果」页同一做法）：
            // 它只作用于这张清单，入口紧挨着要操作的表格 —— 用户反馈「刷新清单」
            // 放在页顶「操作」框里离上下文太远，看不出它刷新的是哪张表。
            // 刷新会保住当前选中的策略（见 StLoadStrategies）。
            AddRow(b, Row(
                MiniBtn("刷新清单", delegate { StLoadStrategies(); }, 90),
                Mute(Lbl("清单来自后端 strategies/ 目录；新增策略后点此刷新（会保住当前选中的策略）。"))));
            _stList = new ListBox();
            _stList.Width = 340;
            _stList.Height = 200;
            _stList.Font = new Font("Microsoft YaHei UI", 10f);
            _stList.SelectedIndexChanged += delegate { StOnSelectStrategy(); };
            // 说明文字限宽换行：原来是一条长单行，把整个分组框横向撑得比页面还宽，
            // 右侧空一大片。限宽后它固定成列表右边的一栏，版面才紧凑。
            var listTip = Mute(Lbl("单选：一次只运行列表里选中的那一个策略（不是全列表一起跑）。"
                + "选中后可在下方「策略说明与参数」里调参；新增策略请在后端 strategies/ 加 <id>.py 文件。"));
            listTip.MaximumSize = new Size(430, 0);
            AddRow(b, Row(_stList, listTip));
            AddRow(stack, g1);

            var g2 = Group("策略说明与参数", out b);
            _stDesc = Lbl("—");
            _stDesc.MaximumSize = new Size(720, 0);
            _stDesc.AutoSize = true;
            AddRow(b, _stDesc);
            AddRow(b, Mute(Lbl("数字参数：点数值弹出滚轮来调（已取消直接键入）。")));
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

        /// <summary>第 23 项：刷新「单日策略」页顶部的说明行。</summary>
        private void StRefreshRecoTitle()
        {
            if (_stRecoTitle == null) return;
            _stRecoTitle.Text = "当前策略：" + StCurStrategyName()
                + "　—　单日策略：按「目标执行日」前一交易日的收盘信号给出该日操作建议；"
                + "范围 / 资金 / 佣金沿用「回测范围」页的设置。";
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
                double mn = J.NumOr(p["min"], int.MinValue);
                double mx = J.NumOr(p["max"], int.MaxValue);
                decimal lo = (decimal)Math.Max(int.MinValue, mn);
                decimal hi = (decimal)Math.Min(int.MaxValue, mx);
                decimal def = (decimal)Clamp(J.NumOr(p["default"], 0), (double)lo, (double)hi);
                var n = new WheelValueBox(lo, hi, 1m, def);
                n.Width = 100;
                ctrl = n;
            }
            else if (type == "float")
            {
                double mn = J.NumOr(p["min"], double.MinValue);
                double mx = J.NumOr(p["max"], double.MaxValue);
                decimal lo = (decimal)mn;
                decimal hi = (decimal)mx;
                decimal def = (decimal)Clamp(J.NumOr(p["default"], 0), mn, mx);
                // 大范围浮点参数（JWZZ 触发阈值 ‰：-500~2000）按 1 步进：0.01 在滚轮里几乎调不动。
                // 小范围系数（仓位 / 倍量等）保持 0.01 细调。
                decimal step = (mx - mn >= 1000) ? 1m : 0.01m;
                var n = new WheelValueBox(lo, hi, step, def);
                n.Width = 100;
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
            _stCapital = new WheelValueBox(1000m, 100000000m, 10000m, 100000m);
            _stCapital.Width = 120;
            _stComm = new TextBox();
            _stComm.Width = 100;
            _stComm.Text = "0.0003";
            AddRow(b, Row(Lbl("初始资金(元)"), _stCapital, Mute(Lbl("滚轮 1 万/格"))));
            AddRow(b, Row(Lbl("单边佣金比例"), _stComm, Mute(Lbl("如 0.0003 = 万三"))));
            // 单股仓位上限：默认留空 = 不限（完全按策略自己给的仓位跑）。
            // 填了之后策略给出的目标权重会被逐票压到这个上限以下（只压低、不抬高）。
            _stCapPct = new TextBox();
            _stCapPct.Width = 100;
            _stCapAmt = new TextBox();
            _stCapAmt.Width = 100;
            // ⚠️ .NET Framework 4.x 的 TextBox 没有 PlaceholderText（那是 .NET 5+ 的），
            //    「留空 = 不限」只能靠旁边的说明文字表达。
            AddRow(b, Row(Lbl("单股仓位上限"), _stCapPct, Mute(Lbl("% 占组合净值")),
                          Lbl("或金额(元)"), _stCapAmt, Mute(Lbl("留空=不限；两者取更严者"))));
            // 买入价上限：信号日收盘价（未复权）高于此值放弃建仓，不递补、已持有不受影响。
            _stMaxBuyPrice = new TextBox();
            _stMaxBuyPrice.Width = 100;
            AddRow(b, Row(Lbl("买入价上限(元)"), _stMaxBuyPrice,
                          Mute(Lbl("信号日收盘价 > 此值不买；留空=不限"))));
            AddRow(stack, g3);

            // 基准（指数）对比的输入框在「**回测结果**」页（基准是结果的对照物，
            // 不是回测的输入条件 —— 理由见 StrategyPage.Bench.cs）。这里只做**只读回显**，
            // 免得用户在本页点「开始回测」时不知道到底带没带基准。
            _stBenchEcho = Mute(Lbl("基准：不对比"));
            AddRow(stack, Row(Mute(Lbl("基准对比在「回测结果」页选")),
                              MiniBtn("去选择", delegate { _stStrip.Select(2); }, 80),
                              _stBenchEcho));
            StRefreshBenchEcho();

            // ---- 「涨停开盘应对」：隔夜单在涨停开盘时怎么办（全局生效，只作用于回测）----
            // 口径在 backend_fastapi/core/limit_price.py，三档：
            //   不撤单（照常买入）/ 撤单（涨停开盘当日不买）/ 涨停条件单（炸板后回封才进）。
            // ⚠️ 撤单 = **该份额度当日为现金、不重分配给其他票**（与「一字涨停买不进」
            //    同一口径，但归因不同：一个是策略主动放弃，一个是市场物理约束）。
            var g5 = Group("涨停开盘应对", out b);
            _stLimitOpen = new ComboBox();
            _stLimitOpen.DropDownStyle = ComboBoxStyle.DropDownList;
            _stLimitOpen.FlatStyle = FlatStyle.Flat;
            _stLimitOpen.Width = 250;
            _stLimitOpen.Items.AddRange(new string[] {
                "不撤单（照常买入）",
                "撤单（涨停开盘当日不买）",
                "涨停条件单（炸板后回封才进）",
            });
            _stLimitOpen.SelectedIndex = 0;
            AddRow(b, Row(Lbl("隔夜单涨停开盘时"), _stLimitOpen,
                Mute(Lbl("全局生效；撤单 = 该份额度当日为现金、不重分配"))));
            AddRow(stack, g5);

            // ---- 买入时滤除异动（默认仅勾选 near_severe；与一字板/涨停撤单分开归因）----
            // 判定在购买执行日做，用的是上一交易日收盘后的异动状态（无前视）。
            // 拦下的份额当日为现金、不重分配；判不出来（数据不足）默认放行并计数。
            var g6 = Group("买入时滤除异动", out b);
            var abRow = new FlowLayoutPanel();
            abRow.AutoSize = true;
            abRow.WrapContents = true;
            abRow.Margin = new Padding(0, 4, 0, 0);
            _stAbPrice = ExChk("价格已触发(±20/30%)");
            _stAbTurnover = ExChk("换手率异动");
            _stAbSevere = ExChk("严重异动(10/30日)");
            _stAbNearPrice = ExChk("将触发价格(20/30%)");
            _stAbNearSevere = ExChk("将触发严重(10/30日)");
            _stAbNearSevere.Checked = true;   // 默认只开这一档：风险调整后指标最好，其余仍全关
            abRow.Controls.AddRange(new Control[] { _stAbPrice, _stAbTurnover, _stAbSevere,
                _stAbNearPrice, _stAbNearSevere });
            AddRow(b, Row(abRow));
            AddRow(b, Row(Mute(Lbl("默认勾选「将触发严重」；用上一交易日收盘判定，判不出来时放行并在报告里计数"))));
            AddRow(stack, g6);

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

            // 进度条与暂停 / 继续 / 停止已移到**顶部「操作」框**（统一任务区）：
            // 三条流程共用一套，且任何子页都看得见（原来只有完整回测在「回测结果」页
            // 里有进度条，近年回测与当日策略全程没有任何进度显示）。
            // 这里只保留本页自己的状态行（跑完写「回测完成（参与统计 N 只）」等）。
            // ⚠️ 复用 BuildStrategyPage 里已经建好的那个控件，不要在这里重新 new ——
            //   两个同名控件会让「顶部操作框」那个永远显示不出东西。
            _stResultStatus.Text = "尚未回测";
            AddRow(stack, Row(_stResultStatus));

            _stMetricCards = new FlowLayoutPanel();
            _stMetricCards.FlowDirection = FlowDirection.LeftToRight;
            _stMetricCards.WrapContents = true;
            _stMetricCards.AutoSize = true;
            _stMetricCards.AutoSizeMode = AutoSizeMode.GrowAndShrink;
            _stMetricCards.MaximumSize = new Size(900, 0);
            _stMetricCards.Margin = new Padding(0, 0, 0, 8);
            AddRow(stack, _stMetricCards);

            // 基准（指数）对比：**从「回测范围」页搬到这里**（理由见 StrategyPage.Bench.cs）。
            AddRow(stack, StBuildBenchGroup());

            // 图表切换：净值曲线（默认）/ 每日收益柱状图。纯前端，不动后端口径。
            var chartRow = new FlowLayoutPanel();
            chartRow.AutoSize = true;
            chartRow.Margin = new Padding(0, 0, 0, 2);
            chartRow.Controls.Add(Mute(Lbl("图表：")));
            _stChartMode = new ComboBox();
            _stChartMode.DropDownStyle = ComboBoxStyle.DropDownList;
            _stChartMode.Width = 190;
            _stChartMode.Items.Add("净值曲线");
            _stChartMode.Items.Add("每日收益（指数涨跌 × 策略收益）");
            _stChartMode.SelectedIndex = 0;
            _stChartMode.SelectedIndexChanged += delegate { StOnChartModeChanged(); };
            chartRow.Controls.Add(_stChartMode);
            chartRow.Controls.Add(Mute(Lbl("　区间：")));
            _stBarWindow = new ComboBox();
            _stBarWindow.DropDownStyle = ComboBoxStyle.DropDownList;
            _stBarWindow.Width = 96;
            _stBarWindow.Items.Add("最近 250 日");
            _stBarWindow.Items.Add("最近 120 日");
            _stBarWindow.Items.Add("最近 500 日");
            _stBarWindow.Items.Add("全部交易日");
            _stBarWindow.Items.Add("自定义区间");
            _stBarWindow.SelectedIndex = 0;
            _stBarWindow.SelectedIndexChanged += delegate
            {
                bool custom = _stBarWindow.SelectedIndex == 4;
                if (_stBarFrom != null) _stBarFrom.Visible = custom;
                if (_stBarTo != null) _stBarTo.Visible = custom;
                if (_stEquityBox != null) _stEquityBox.Invalidate();
            };
            chartRow.Controls.Add(_stBarWindow);
            _stBarFrom = new DateTimePicker();
            _stBarFrom.Format = DateTimePickerFormat.Custom;
            _stBarFrom.CustomFormat = "yyyy-MM-dd";
            _stBarFrom.Width = 106;
            _stBarFrom.Visible = false;
            _stBarTo = new DateTimePicker();
            _stBarTo.Format = DateTimePickerFormat.Custom;
            _stBarTo.CustomFormat = "yyyy-MM-dd";
            _stBarTo.Width = 106;
            _stBarTo.Visible = false;
            EventHandler barDateChanged = delegate
            {
                if (_stEquityBox != null) _stEquityBox.Invalidate();
            };
            _stBarFrom.ValueChanged += barDateChanged;
            _stBarTo.ValueChanged += barDateChanged;
            chartRow.Controls.Add(_stBarFrom);
            chartRow.Controls.Add(_stBarTo);
            AddRow(stack, chartRow);

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
            var g = Group("成交明细（一行 = 一次买卖 · 点击代码/名称跳个股详情）", out bg);
            // ⚠️ 列名与顺序按**逐笔**口径写死：以前这张表是「一行 = 一只标的」
            //   （区间收益 / 交易次数 / 首建首清），用户会把它当成「一笔买入」，
            //   于是「同一天买好几只、其中多数是老票再买」看着像「每天只买一只」。
            //   现在一行 = 一次完整交易（买入 → 卖出），与指标卡「交易次数」同口径。
            _stTradesTip = Mute(Lbl(StTradesTipText()));
            _stTradesTip.MaximumSize = new Size(900, 0);
            AddRow(bg, _stTradesTip);
            _stTradesGrid = NewGrid(StTradesColumns());
            _stTradesGrid.SetSortable(true);
            StPlaceGrid(_stTradesGrid);
            // 兜底跳转：内置 OnCellClick 依赖列的 Jump 标记；若未接管（仍停在回测页），
            // 按代码列取值直接 OpenStock，已接管则不重复触发。
            _stTradesGrid.CellClick += delegate(object s, DataGridViewCellEventArgs e)
            {
                if (e.RowIndex < 0 || _tabIndex == _stockTabIndex) return;
                string c = Convert.ToString(_stTradesGrid.Rows[e.RowIndex].Cells[0].Value);
                if (!string.IsNullOrEmpty(c) && c.Trim().Length == 6) OpenStock(c.Trim());
            };
            AddRow(bg, _stTradesGrid);
            AddRow(stack, g);
        }

        // ---------------- ④ 近期策略成果 ----------------
        // 最近 N 个交易日的短窗口回测：区间由后端按本地日历自动定位（window），
        // 与「回测结果」共用同一套信号与统计口径，只是窗口固定为最近 N 日。
        private void StBuildRecent(TableLayoutPanel stack)
        {
            AddRow(stack, Mute(Lbl("用当前策略在「最近 N 个交易日」上跑一次回测，快速看近期表现；范围 / 资金 / 佣金沿用「回测范围」页的设置。")));
            _stRecentDays = new WheelValueBox(2m, 120m, 1m, 5m);
            _stRecentDays.Width = 70;
            AddRow(stack, Row(MiniBtn("运行近期回测", delegate { StRunRecent(); }, 120),
                Lbl("交易日数"), _stRecentDays, _stRecentStatus = Mute(Lbl("尚未运行"))));
            _stRecentRuinChk = new CheckBox();
            _stRecentRuinChk.Text = "破产截断";
            _stRecentRuinChk.Checked = true;
            _stRecentRuinChk.TabStop = false;
            _stRecentRuinPct = new WheelValueBox(0m, 100m, 1m, 0m);
            _stRecentRuinPct.Width = 60;
            AddRow(stack, Row(_stRecentRuinChk, Lbl("阈值%"), _stRecentRuinPct,
                Mute(Lbl("(0=净值归零即断)"))));

            _stRecentCards = StCardRow();
            AddRow(stack, _stRecentCards);

            TableLayoutPanel bg;
            var g = Group("成交明细（一行 = 一次买卖）", out bg);
            _stRecentGrid = NewGrid(StTradesColumns());
            StPlaceGrid(_stRecentGrid);
            _stRecentGrid.SetSortable(true);   // 第 20 项：可改按买入日期/收益等排序
            AddRow(bg, _stRecentGrid);
            AddRow(stack, g);
        }

        private void StBuildHistory(TableLayoutPanel stack)
        {
            AddRow(stack, Mute(Lbl("已保存的回测报告（每次回测自动落盘）。选中一份后点「加载所选」，按回测结果的样式展示指标卡 / 净值曲线 / 个股明细。")));

            // 操作按钮放在列表**上方**：入口紧挨着要操作的表格，不用鼠标往下拖到页面底部
            _stHistStatus = Mute(Lbl("尚未加载"));
            AddRow(stack, Row(
                MiniBtn("刷新列表", delegate { StHistRefresh(); }, 90),
                MiniBtn("加载所选", delegate { StHistLoad(); }, 90),
                MiniBtn("删除所选", delegate { StHistDelete(); }, 90),
                _stHistStatus));

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
            StHistRefresh();
        }

        private void StRunRecent()
        {
            if (!StJobGuard()) return;      // 互斥：顶部任务区只显示一个任务
            var meta = StSelectedMeta();
            if (meta == null)
            {
                StJobText("请先在「策略选择与编辑」里选一个策略", true);
                SetErr(_stRecentStatus, "请先在「策略选择与编辑」里选一个策略");
                return;
            }
            int days = (int)_stRecentDays.Value;
            double? ruin = (_stRecentRuinChk != null && _stRecentRuinChk.Checked)
                ? (double?)_stRecentRuinPct.Value / 100.0 : null;
            // 先占位再提交：启动请求在飞的那几十秒里也不许再点（否则会起两个全市场任务）
            StJobReserve(ST_JOB_RECENT);
            var req = StRequest(meta, days, ruin);
            _stRecentStatus.Text = "提交中…";
            _stRecentStatus.ForeColor = _cSub;
            string body = J.Ser().Serialize(req);
            System.Threading.Tasks.Task.Run(delegate
            {
                try
                {
                    // 只等「启动」立即返回；旧实现是同步 /backtest + 600s 固定超时，
                    // 全市场 3 年实测 761s 必然超时，这里不再继承那个坑。
                    string resp = VRequest("http://127.0.0.1:8000/api/strategies/backtest/start", body, 30000);
                    var j = J.Ser().Deserialize<Dictionary<string, object>>(resp);
                    Invoke((Action)(() =>
                    {
                        if (J.IsFailed(j))
                        {
                            StJobAbort("近期回测 · 启动失败：" + StErrMsg(j), true);
                            SetErr(_stRecentStatus, "无法启动：" + StErrMsg(j));
                            return;
                        }
                        StJobBegin(ST_JOB_RECENT);
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
                        var p = J.Ser().Deserialize<Dictionary<string, object>>(pj);
                        var st = (Dictionary<string, object>)J.Get(p, "state");
                        string state = st == null ? "" : J.StrAt(st, "state");
                        if (state == "running" || state == "ready")
                        {
                            adopted = true;
                            Invoke((Action)(() =>
                            {
                                StJobBegin(ST_JOB_RECENT);
                                StJobText("近期回测 · 已接管后台任务，读取进度…", false);
                            }));
                        }
                    }
                    catch { /* 探测失败就走普通报错 */ }
                    if (adopted) return;
                    Invoke((Action)(() =>
                    {
                        StJobAbort("近期回测 · 无法启动：" + msg, true);
                        SetErr(_stRecentStatus, "无法启动：" + msg);
                    }));
                }
            });
        }

        // ⚠️ 近期回测**不再有自己的轮询表**：进度统一由「统一任务区」跟进
        //   （StJobTick → /backtest/progress）。它与完整回测共用同一个后端任务槽
        //   与同一个结果端点，只是请求里多带一个 window，所以 URL 完全相同。
        //   原来这里另起一套 2 秒轮询 + 自己的 runId/busy，是同一份逻辑的第三份拷贝。

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

            // 成交明细（逐笔）：与回测结果页同一口径（一行 = 一次买卖）
            _stTradesData = new List<Dictionary<string, object>>();
            var tl = j["trades"] as System.Collections.IList;
            if (tl != null)
            {
                foreach (var it in tl)
                {
                    var d = it as Dictionary<string, object>;
                    if (d != null) _stTradesData.Add(d);
                }
            }
            _stTradesTotal = Math.Max(_stTradesData.Count, (int)StM(j, "trades_total"));
            _stTradesTruncated = Math.Max(0, (int)StM(j, "trades_truncated"));
            // 建仓失败（隔日一字涨停买不进）→ 逐条列进表里（收益 0、备注「买不进」）；
            // 清仓延后（隔日一字跌停卖不出）→ 写进成交行的备注列。
            var rScope = j["scope"] as Dictionary<string, object>;
            StFillTradesRows(_stRecentGrid, _stTradesData,
                StBuildFailItems(rScope), StSellDeferNotes(rScope));
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

        // ---------------- ⑤ 单日策略 ----------------
        // 按「目标执行日」前一交易日的收盘信号给出三档建议：
        // 买入（执行日建仓）/ 卖出（执行日清仓）/ 持股（继续持有，含建仓成本与浮盈亏）。
        private void StBuildReco(TableLayoutPanel stack)
        {
            // 第 23 项：标题行直接写出策略名。这一页的按钮是「生成当前推荐」，
            // 「当前」指的是列表选中项而不是上回测过的那个；不写出来用户很容易
            // 切了策略却以为在重跑上一个。下方状态行还会带出「策略：」前缀，双保险。
            _stRecoTitle = Mute(Lbl(""));
            AddRow(stack, _stRecoTitle);
            StRefreshRecoTitle();

            // ---- 目标执行日：收盘前默认今天、收盘后默认下一交易日 ----
            // 口径恒为「前一交易日收盘信号 → 本交易日执行」，默认值与「选了哪天
            // → 信号日是哪天、数据齐不齐」都由后端 /date-context 下发
            // （交易日历 / 是否收盘只在后端判一次）。
            _stRecoDate = new DateTimePicker();
            _stRecoDate.Width = 130;
            _stRecoDate.Format = DateTimePickerFormat.Custom;
            _stRecoDate.CustomFormat = "yyyy-MM-dd";
            // 可选范围：下界=本地数据覆盖起点（由后端 /date-context 下发），
            // 上界=今天+10 天（再往后信号日还没产生，算不出东西）。
            // 后端仍会再校验一次，这里只是不让用户选到明显无意义的日子。
            _stRecoDate.MinDate = DateTime.Today.AddYears(-1);
            _stRecoDate.MaxDate = DateTime.Today.AddDays(10);
            _stRecoDate.ValueChanged += delegate {
                // 首次把控件置为默认执行日时的那次变更：响应里已经带着这一天的
                // 上下文，不必再按新值重查一遍。
                if (_stRecoSkipValueChanged) { _stRecoSkipValueChanged = false; return; }
                StLoadRecoDates();
            };
            _stRecoDateHint = Mute(Lbl("正在获取交易日信息…"));
            _stRecoDateHintBase = _stRecoDateHint.ForeColor;   // 提示行的常态色（异常时改红）
            AddRow(stack, Row(Lbl("目标执行日"), _stRecoDate, _stRecoDateHint));

            AddRow(stack, Row(MiniBtn("生成当前推荐", delegate { StRunReco(); }, 130),
                _stRecoStatus = Mute(Lbl("尚未生成"))));

            // 卡片行放宽上限：七张 140 宽的卡要排成一行（900 装不下 7×148=1036）。
            _stRecoCards = StCardRow(1400);
            AddRow(stack, _stRecoCards);

            // ---- 三张表保持纵向堆叠（用户要求「其他不需要动」）----
            TableLayoutPanel b1;
            var g1 = Group("买入（执行日建仓）", out b1);
            _stBuyGroup = g1;
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
            var g2 = Group("卖出（执行日清仓）", out b2);
            _stSellGroup = g2;
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

            StLoadRecoDates();                  // 首次构建即填充下拉（StOnEnter 会再刷新一次）
        }

        /// <summary>
        /// 拉取「目标执行日」上下文：默认日期、当前选中日期对应的信号日、
        /// 该信号日数据是否齐。日期控件任选一天，所以每次变更都要重问后端
        /// （交易日历与收盘时点只在后端判一次）。
        /// </summary>
        private void StLoadRecoDates()
        {
            if (_stRecoDate == null) return;
            // ⚠ **不能因为「上一次还在取」就把本次变更丢掉**：日期控件连点两次时，
            //    第二次的变更会被这里的 return 吃掉 → `_stRecoExecDate` 停留在上一次的
            //    执行日 → 点「生成当前推荐」提交的是旧日期 → 后端跑出同一份结果 →
            //    用户看到「表格没更新」（实测现象②）。改成「记下最新请求，取完再补一次」。
            //
            // ⚠ **首次不带 target**：还不知道默认执行日，带 target=今天 会拿到
            //    「今天若非交易日则顺延」的另一套语义，还得再取一次（实测两次往返，
            //    用户干等一倍时间）。不带 target 时后端直接按**默认执行日**算好
            //    exec / signal / 数据齐否，一次往返就够。
            bool first = !_stRecoDateInit;
            string atCall = _stRecoDate.Value.ToString("yyyy-MM-dd");
            string picked = first ? "" : atCall;
            _stRecoDatePending = atCall;
            if (_stRecoDateLoading) return;
            _stRecoDateLoading = true;
            RunUi<Dictionary<string, object>>(
                delegate
                {
                    string body;
                    string url = "http://127.0.0.1:8000/api/history/download/date-context"
                        + (picked.Length > 0 ? "?target=" + picked : "");
                    if (!GetText(url, 20000, out body))
                        return null;
                    try { return J.Ser().Deserialize<Dictionary<string, object>>(body); }
                    catch { return null; }
                },
                delegate(Dictionary<string, object> j)
                {
                    _stRecoDateLoading = false;
                    if (j == null || !J.IsOk(j))
                    {
                        if (_stRecoDateHint != null) _stRecoDateHint.Text = "交易日信息获取失败，将按系统默认执行日运行";
                        return;
                    }
                    string def = J.StrOr(J.Get(j, "default_exec_date"), "");
                    // 下界用后端给的「数据覆盖起点」，别让用户选到没有行情的年份
                    string minDay = J.StrOr(J.Get(j, "min_exec_date"), "");
                    if (minDay.Length > 0)
                    {
                        try
                        {
                            DateTime md = DateTime.Parse(minDay);
                            if (md > _stRecoDate.MinDate) _stRecoDate.MinDate = md;
                        }
                        catch { }
                    }
                    // 取的过程中用户又改了日期 → 这次的结果对不上控件，丢掉并补取一次
                    if (_stRecoDate.Value.ToString("yyyy-MM-dd") != atCall)
                    {
                        // 首次请求途中用户已选了别的日期 → 认定「用户有明确选择」，
                        // 后续一律按 target 查，别再拿默认执行日覆盖他的选择。
                        if (first) _stRecoDateInit = true;
                        StLoadRecoDates();
                        return;
                    }
                    if (first)
                    {
                        // 首次：把控件置为后端给的默认执行日，并**直接用这份上下文**
                        // （后端就是按这一天算的信号日与数据齐否，不用再问一遍）。
                        _stRecoDateInit = true;
                        bool applied = false;
                        try
                        {
                            DateTime d = DateTime.Parse(def);
                            if (d >= _stRecoDate.MinDate && d <= _stRecoDate.MaxDate)
                            {
                                _stRecoSkipValueChanged = true;   // 设值会触发 ValueChanged
                                _stRecoDate.Value = d;
                                applied = true;
                            }
                        }
                        catch { }
                        _stRecoSkipValueChanged = false;   // 没触发（值相同）也别留给下一次真实变更
                        if (applied)
                        {
                            _stRecoDatePending = def;
                            StApplyRecoDateContext(j, def);
                            return;
                        }
                        // 默认执行日不可用（越界）→ 退回按控件当前值重查
                        StLoadRecoDates();
                        return;
                    }
                    StApplyRecoDateContext(j, picked);
                },
                delegate(Exception ex2)
                {
                    _stRecoDateLoading = false;
                    if (_stRecoDateHint != null) _stRecoDateHint.Text = "交易日信息获取失败，将按系统默认执行日运行";
                },
                null);
        }

        /// <summary>把后端返回的信号日 / 数据齐否写进提示行。</summary>
        private void StApplyRecoDateContext(Dictionary<string, object> j, string picked)
        {
            _stRecoDateCtx = j;
            _stRecoPickedDate = picked ?? "";
            _stRecoCtxDate = picked ?? "";    // 记下这次上下文对应的是控件里的哪一天
            StRenderRecoDateHint();
            // 后端这轮没拿到交易日历（按「工作日」降级，节假日可能判错）→ 过几秒
            // 补取一次：后台把日历拉好之后，日期就是准的了。
            // （字段缺失按「就绪」处理：老后端没有这个字段。）
            object calObj = J.Get(j, "calendar_ready");
            bool calReady = !(calObj is bool) || (bool)calObj;
            if (!calReady) StScheduleRecoDateRefresh();
            // 用户刚才点过「生成当前推荐」但上下文还没到 → 现在自动续跑，
            // 免得让人「点一次被拒、再点一次」。
            if (_stRecoAutoRun)
            {
                _stRecoAutoRun = false;
                StRunReco();
            }
        }

        /// <summary>稍后补取一次日期上下文（等后端后台把交易日历拉好）。</summary>
        private void StScheduleRecoDateRefresh()
        {
            // 限次：日历始终拉不到时不能无限补取（每轮都会再排一次）
            if (_stRecoRefreshTimer != null || _stRecoRefreshCount >= 3) return;
            _stRecoRefreshCount++;
            _stRecoRefreshTimer = new Timer();
            _stRecoRefreshTimer.Interval = 3000;
            _stRecoRefreshTimer.Tick += delegate
            {
                _stRecoRefreshTimer.Stop();
                _stRecoRefreshTimer = null;
                StLoadRecoDates();
            };
            _stRecoRefreshTimer.Start();
        }

        /// <summary>渲染提示行：信号日 → 执行日、数据齐否，以及「结果是否已过期」。</summary>
        private void StRenderRecoDateHint()
        {
            if (_stRecoDateHint == null) return;
            var j = _stRecoDateCtx;
            if (j == null)
            {
                _stRecoDateHint.Text = "—";
                _stRecoDateHint.ForeColor = _stRecoDateHintBase;
                return;
            }
            string picked = J.StrOr(J.Get(j, "picked_date"), _stRecoPickedDate);
            string sig = J.StrOr(J.Get(j, "signal_date"), "");
            string exec = J.StrOr(J.Get(j, "exec_date"), picked);
            object readyObj = J.Get(j, "data_ready");
            bool ready = readyObj is bool && (bool)readyObj;
            _stRecoSignalDate = sig;
            _stRecoExecDate = exec;
            if (sig.Length == 0)
            {
                _stRecoDateHint.Text = "—";
                _stRecoDateHint.ForeColor = _stRecoDateHintBase;
                return;
            }
            string tail = ready ? " · 数据已齐" : " · ⚠ " + sig + " 收盘数据未下载完成，无法生成该日策略";
            object kindObj = J.Get(j, "picked_is_trade_day");
            bool isTrade = !(kindObj is bool) || (bool)kindObj;
            string kind = isTrade ? "" : "（非交易日，已顺延到 " + exec + "）";
            string text = "信号日 " + sig + " 收盘 → " + exec + " 执行" + kind + tail;
            // ⚠️ 改了目标执行日**不会自动重跑**：若当前显示的结果是按别的执行日
            //    生成的，必须说清楚，否则用户会把上一次的旧结果当成"没更新"。
            bool stale = _stRecoResultExecDate.Length > 0 && _stRecoResultExecDate != exec;
            if (stale)
                text += " · ⚠ 当前显示的是 " + _stRecoResultExecDate + " 的结果，与所选执行日不一致，请重新点「生成当前推荐」";
            _stRecoDateHint.Text = text;
            _stRecoDateHint.ForeColor = stale ? C.UpErr : _stRecoDateHintBase;
        }

        private void StRunReco()
        {
            // ⚠️ 守卫必需：原来这里完全没有，用户等不及再点一次就会叠加多个
            // 全市场计算（后端并发上限 1，第二个会被拒，但两个都得等）。
            // 现在统一成「互斥闸门」：完整回测 / 近期回测 / 当日策略三者共用
            // 顶部那一个任务区，同时只允许一个在跑（进度条只显示一个任务）。
            if (!StJobGuard()) return;
            var meta = StSelectedMeta();
            if (meta == null)
            {
                StJobText("请先在「策略选择与编辑」里选一个策略", true);
                SetErr(_stRecoStatus, "请先在「策略选择与编辑」里选一个策略");
                return;
            }
            // 占位（含等待补齐数据的整段时间）：否则用户在新鲜度检查期间
            // 再点一次，就会起两个全市场推荐。
            StJobReserve(ST_JOB_RECO);
            // 目标执行日取**点击时**的快照：等待数据补齐可能长达一小时，
            // 期间用户改了日期，弹窗说的日子必须和真正提交的日子是同一个。
            //
            // ⚠ 快照必须与控件当前值**对得上**：`_stRecoExecDate` 是异步回填的，
            //    日期刚改、上下文还没回来时它还是上一次的执行日 —— 拿它去提交，
            //    后端就按旧日期跑出同一份结果，用户看到的是「表格没更新」（实测现象②）。
            //    对不上就**明确说清楚并补取一次**，绝不静默用旧日期提交。
            string pickedNow = _stRecoDate != null
                ? _stRecoDate.Value.ToString("yyyy-MM-dd") : "";
            if (_stRecoExecDate.Length == 0 || _stRecoCtxDate != pickedNow)
            {
                StJobRelease();               // 释放占位，别把闸门卡死
                // ⚠ 别让用户「点一次被拒、再点一次」：上下文到位后**自动续跑**。
                //    （此前只提示「请稍候再点」，用户不知道要点第几下，体验上
                //    就成了「日期怎么这么难获取」。）
                if (_stRecoAutoRunRetry < 3)
                {
                    _stRecoAutoRunRetry++;
                    _stRecoAutoRun = true;
                    StJobText("正在获取目标执行日信息，稍后自动运行", true);
                    SetErr(_stRecoStatus, "正在获取目标执行日信息（" + pickedNow
                        + "），稍后自动运行");
                    StLoadRecoDates();         // 取到就自动续跑
                    return;
                }
                StJobText("目标执行日信息还在刷新，请稍候再点「生成当前推荐」", true);
                SetErr(_stRecoStatus, "目标执行日信息还在刷新（" + pickedNow
                    + "），请稍候再点「生成当前推荐」");
                StLoadRecoDates();             // 立刻补取一次
                return;
            }
            _stRecoAutoRun = false;
            _stRecoAutoRunRetry = 0;
            string targetDate = _stRecoExecDate;
            string signalDate = _stRecoSignalDate;
            // 第 24 项：与回测共用同一个数据新鲜度守卫。
            // hard=true：数据不齐时不用旧数据冒充；仍弹窗给「等待补齐后自动运行」。
            // abort：用户放弃时释放任务槽，别把闸门卡死。
            StEnsureFresh(meta, delegate { StRunRecoInner(meta, targetDate); }, true,
                delegate { StJobRelease(); }, signalDate, targetDate);
        }

        private void StRunRecoInner(Dictionary<string, object> meta, string targetDate)
        {
            var req = StRequest(meta, 0);
            // 目标执行日：交给后端反推信号日并截断行情，避免「选了今天却读到
            // 明天的 K 线」。未选 / 后端不可用时不带该字段 → 后端按收盘前后自动选。
            if (targetDate.Length > 0) req["target_date"] = targetDate;
            _stRecoSubmittedDate = targetDate;   // 取结果时回验「结果是不是本次这一轮」
            _stRecoStatus.Text = "正在提交…（首次运行需联网取股票名称，约 1 分钟；"
                + "随后全市场计算约 7~10 分钟，可随时切走）";
            _stRecoStatus.ForeColor = _cSub;
            string body = J.Ser().Serialize(req);
            System.Threading.Tasks.Task.Run(delegate
            {
                try
                {
                    // 只等「启动」这一下（实测0.01s返回），不再等算完
                    string resp = VRequest("http://127.0.0.1:8000/api/strategies/recommend", body, 30000);
                    var j = J.Ser().Deserialize<Dictionary<string, object>>(resp);
                    Invoke((Action)(() =>
                    {
                        if (J.IsFailed(j))
                        {
                            StJobAbort("当日策略 · 启动失败：" + StErrMsg(j), true);
                            SetErr(_stRecoStatus, "无法启动：" + StErrMsg(j));
                            return;
                        }
                        StJobBegin(ST_JOB_RECO);
                    }));
                }
                catch (Exception ex)
                {
                    string msg = ex.Message;
                    Invoke((Action)(() =>
                    {
                        // ⚠️ 失败路径也必须释放闸门，否则按钮点一次之后永久卡死
                        StJobAbort("当日策略 · 无法启动：" + msg, true);
                        SetErr(_stRecoStatus, "无法启动：" + msg);
                    }));
                }
            });
        }

        // ⚠️ 当日策略**不再有自己的轮询表**：进度统一由「统一任务区」跟进
        //   （StJobTick → /recommend/progress）。后端的 task_state() 与
        //   reco_task_state() 字段完全同构，pause/resume/cancel 也同时覆盖两类任务，
        //   所以前端没必要分成两套 —— 原来那套是同一份逻辑的第二份拷贝。

        private void StRenderReco(Dictionary<string, object> j)
        {
            if (J.IsFailed(j))
            {
                SetErr(_stRecoStatus, "计算失败：" + StErrMsg(j));
                return;
            }
            var s = (Dictionary<string, object>)j["summary"];
            _stRecoCards.Controls.Clear();
            // ⚠️ 卡片宽度收窄（140）是为了让**七张卡排一行**：原来 150×7 放不下，
            // 「持股市值」「合计浮盈亏」被挤到第二行，页面因此高出一截（用户反馈：
            // 「持股市值和浮动盈也放在一行里」）。
            _stRecoCards.Controls.Add(StKpiCard("买入", J.Int(s["buy_count"]) + " 只", 140));
            _stRecoCards.Controls.Add(StKpiCard("买入金额", J.Money(J.Num(s["buy_amount"])), 140));
            _stRecoCards.Controls.Add(StKpiCard("卖出", J.Int(s["sell_count"]) + " 只", 140));
            _stRecoCards.Controls.Add(StKpiCard("卖出金额", J.Money(J.Num(s["sell_amount"])), 140));
            _stRecoCards.Controls.Add(StKpiCard("持股", J.Int(s["hold_count"]) + " 只", 140));
            _stRecoCards.Controls.Add(StKpiCard("持股市值", J.Money(J.Num(s["hold_amount"])), 140));
            _stRecoCards.Controls.Add(StKpiCard("合计浮盈亏", J.Money(J.Num(s["hold_pnl"])), 140));
            Skin(_stRecoCards);

            StFillRecoGrid(_stBuyGrid, j["buy"], false);
            StFillRecoGrid(_stSellGrid, j["sell"], false);
            StFillRecoGrid(_stHoldGrid, j["hold"], true);

            // 信号日 = 推荐所基于的收盘行情日；执行日 = 用户选中的目标交易日。
            // 两个都由后端按 target_date 反推后回显，前端不自己猜，避免口径不一致。
            string sigDate = J.StrOr(J.Get(j, "signal_date"), J.StrOr(J.Get(j, "date"), ""));
            string execDate = J.StrOr(J.Get(j, "execution_date"), sigDate);
            if (_stBuyGroup != null) _stBuyGroup.Text = "买入（" + execDate + " 建仓）";
            if (_stSellGroup != null) _stSellGroup.Text = "卖出（" + execDate + " 清仓）";
            _stRecoResultExecDate = execDate;   // 记下这次结果的执行日，供「结果是否过期」比对
            // 持仓基线：previous_recommend = 三档以上一次建议为基线（账本口径，
            // 「昨天买入的今天一定在持股或卖出里」）；signal_diff = 没有上一次建议，
            // 退回「策略信号相邻两日差分」。要显示出来 —— 否则用户看不懂
            // 「为什么这只票今天进了卖出」。
            string baseTxt = "";
            var bl = J.Get(j, "baseline") as Dictionary<string, object>;
            if (bl != null)
            {
                string bsrc = J.StrOr(J.Get(bl, "source"), "");
                string bdate = J.StrOr(J.Get(bl, "execution_date"), "");
                baseTxt = bsrc == "previous_recommend"
                    ? " · 持仓基线：" + bdate + " 的建议"
                    : " · 持仓基线：策略信号差分（无上一次建议）";
            }
            _stRecoStatus.Text = "策略：" + StCurStrategyName() + " · 信号日 " + sigDate
                    + " · 执行日 " + execDate
                    + " · 目标资金 " + J.Money(StM(j as Dictionary<string, object>, "target_capital"))
                    + baseTxt;
            // ⚠ 结果的**执行日**必须与本次提交的目标日一致：后端 /recommend/result
            //    返回的是「最近一次已完成」的结果，对不上就说明拿到的不是这一轮的。
            //    对不上要说清楚，不能让旧结果冒充新结果（实测现象②）。
            bool mismatch = _stRecoSubmittedDate.Length > 0 && _stRecoSubmittedDate != execDate;
            if (mismatch)
            {
                _stRecoStatus.Text += " · ⚠ 结果的执行日（" + execDate + "）与本次提交的目标日（"
                    + _stRecoSubmittedDate + "）不一致，请重新点「生成当前推荐」";
                _stRecoStatus.ForeColor = C.UpErr;
            }
            else
                _stRecoStatus.ForeColor = C.DownSoft;
            StRenderRecoDateHint();              // 重算提示行（清掉「结果已过期」的告警）
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
            // ⚠️ 后端 `exclude: list[str] = Query(...)` 是**重复参数**形式
            //    (?exclude=a&exclude=b)，不是逗号分隔。逗号拼接会被当成
            //    一个整体参数 "a,b"，过滤规则一个都不匹配 → 预览永远显示
            //    「剔除后 = 基池数」。
            //    ⚠️ 而且**第一个参数也要带键名**：以前写成
            //    "?" + string.Join("&exclude=", ex) → 得到 "?st&exclude=ipo..."
            //    → "st" 成了无值参数，第一个剔除项直接丢掉了。
            var parts = new List<string>();
            foreach (var e in ex) parts.Add("exclude=" + Uri.EscapeDataString(e));
            // 预览必须与实跑用**同一个 spec**：后端判定基准日 = 区间起点，
            // 不传 start → 预览按今天算、实跑按区间起点算 → 两边数字对不上
            // （实测预览显示「剔除后 = 基池」而实跑剔掉了一千多只）。
            string st = StDate(_stStart);
            string en = StDate(_stEnd);
            if (!string.IsNullOrEmpty(st)) parts.Add("start=" + Uri.EscapeDataString(st));
            if (!string.IsNullOrEmpty(en)) parts.Add("end=" + Uri.EscapeDataString(en));
            string q = parts.Count > 0 ? "?" + string.Join("&", parts) : "";
            RunUi<Dictionary<string, object>>(delegate
            {
                string body;
                // ⚠️ GetText 在 4xx 时**仍会把响应体读进 body**（只是返回 false）。
                //    后端「剔除所需的外部数据取不到」时给的是 400 + detail 文案
                //    （见 universe.ScopeDataUnavailable）—— 这段文案必须显示出来：
                //    丢了它，用户只看到「共 N 只」，会以为剔除没生效、或者根本没剔。
                GetText("http://127.0.0.1:8000/api/strategies/universe/summary" + q, 20000, out body);
                if (string.IsNullOrEmpty(body)) return null;
                try { return J.Ser().Deserialize<Dictionary<string, object>>(body); }
                catch { return null; }
            }, delegate(Dictionary<string, object> j)
            {
                if (j == null)
                {
                    _stScopeHint.Text = "共 " + _stLocalCodeCount + " 只";
                    return;
                }
                if (!J.IsOk(j))
                {
                    // 后端**明确中止**（口径无法兑现，如 ST 名称名单取不到）→
                    // detail 原样显示，**含可操作的下一步**（取消勾选 ST / 恢复网络）。
                    string det = J.StrOr(J.Get(j, "detail"), "");
                    if (string.IsNullOrEmpty(det))
                    {
                        var errObj = J.Map(J.Get(j, "error"));
                        if (errObj != null) det = J.StrOr(J.Get(errObj, "message"), "");
                    }
                    _stScopeHint.Text = string.IsNullOrEmpty(det)
                        ? "⚠ 范围预览失败（后端未给出原因）"
                        : "⚠ " + det.Replace("\n", " ");
                    _stScopeHint.ForeColor = C.UpErr;
                    _stScopeTip.SetToolTip(_stScopeHint, _stScopeHint.Text);
                    return;
                }
                int baseN = (int)J.Num(J.Get(j, "base_count"));
                int filtN = (int)J.Num(J.Get(j, "filtered_count"));
                string txt = "共 " + baseN + " 只 · 剔除后 " + filtN + " 只";
                // 每项剔了多少只 —— 必须让人看见：以前只显示两个总数，
                // 「勾了却显示没剔」的事故就是这么发生的。
                var dropped = J.Map(J.Get(j, "dropped"));
                if (dropped != null && dropped.Count > 0)
                {
                    var bits = new List<string>();
                    foreach (var kv in dropped)
                    {
                        int n = (int)J.Num(kv.Value);
                        if (n > 0)
                        {
                            string label;
                            if (!StFeatureLabels.TryGetValue(kv.Key, out label)) label = kv.Key;
                            bits.Add(label + " " + n);
                        }
                    }
                    if (bits.Count > 0) txt += "（" + string.Join("、", bits) + "）";
                }
                // 「可注入 N 只」= 剔除特征后、再按**区间内行情根数**核减后的数量
                // （根数不足算不出信号的票不参与）。它就是实跑参与统计的只数 ——
                // 预览与实跑必须一致，否则界面在骗人。区间没定时后端给 null → 不显示。
                object availObj = J.Get(j, "available_count");
                if (availObj != null)
                {
                    txt += " · 可注入 " + (int)J.Num(availObj) + " 只";
                }
                _stScopeHint.Text = txt;
                _stScopeHint.ForeColor = C.Flat;      // 恢复正常色（上次是错误态的红色）
                // 口径说明（判定基准日 / 前视偏差 / 数据限制）挂气泡
                string note = J.StrOr(J.Get(j, "note"), "");
                _stScopeTip.SetToolTip(_stScopeHint,
                    string.IsNullOrEmpty(note) ? txt : note + "\n" + txt);
            }, delegate(Exception ex2)
            {
                _stScopeHint.Text = "共 " + _stLocalCodeCount + " 只";
                _stScopeHint.ForeColor = C.Flat;
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
                // ⚠ 这里**不传 benchmark**：本请求走 /api/strategies/recommend，
                //   后端 start_reco_task 的签名里根本没有 benchmark 参数（传了也被忽略）。
                //   基准对比只有**完整回测**有意义（那里的 alpha/beta/IR 才按指数算）。
                { "ruin_threshold", ruinThreshold },
                { "position_cap_pct", StCapPct() },
                { "position_cap_amount", StCapAmt() },
                { "limit_open_policy", StLimitOpenPolicy() },
            };
        }

        /// <summary>单股仓位上限（占组合净值比例）：界面填百分数，留空 = 不限。</summary>
        private double? StCapPct()
        {
            string t = (_stCapPct == null ? "" : _stCapPct.Text).Trim();
            if (t.Length == 0) return null;
            double v = J.NumOr(t, 0.0);
            return v > 0 ? (double?)(v / 100.0) : null;
        }

        /// <summary>单股金额上限（元）：留空 = 不限；后端按初始资金折算成占比，
        /// 并与百分比上限**取更严的那个**。</summary>
        private double? StCapAmt()
        {
            string t = (_stCapAmt == null ? "" : _stCapAmt.Text).Trim();
            if (t.Length == 0) return null;
            double v = J.NumOr(t, 0.0);
            return v > 0 ? (double?)v : null;
        }

        /// <summary>买入价上限（元，未复权）：留空 / 非正数 = 不限（送 null 给后端）。</summary>
        private double? StMaxBuyPrice()
        {
            string t = (_stMaxBuyPrice == null ? "" : _stMaxBuyPrice.Text).Trim();
            if (t.Length == 0) return null;
            double v = J.NumOr(t, 0.0);
            return v > 0 ? (double?)v : null;
        }

        /// <summary>「涨停开盘应对」当前档位（keep / cancel / reseal）。
        /// 下拉框项序与 StLimitOpenKeys 一一对应；取不到就回落到「不撤单」。</summary>
        private string StLimitOpenPolicy()
        {
            int i = (_stLimitOpen == null) ? 0 : _stLimitOpen.SelectedIndex;
            if (i < 0 || i >= StLimitOpenKeys.Length) return StLimitOpenKeys[0];
            return StLimitOpenKeys[i];
        }

        /// <summary>买入时异动过滤的四个开关 → 后端 key 列表（空 = 默认关，不改旧结果）。</summary>
        private List<string> StAbnormalFilter()
        {
            var list = new List<string>();
            if (_stAbPrice != null && _stAbPrice.Checked) list.Add("price");
            if (_stAbTurnover != null && _stAbTurnover.Checked) list.Add("turnover");
            if (_stAbSevere != null && _stAbSevere.Checked) list.Add("severe");
            if (_stAbNearPrice != null && _stAbNearPrice.Checked) list.Add("near_price");
            if (_stAbNearSevere != null && _stAbNearSevere.Checked) list.Add("near_severe");
            return list;
        }

        /// <summary>结果头部的「买入时异动过滤」标注；没勾就不显示。</summary>
        private string StAbnormalText()
        {
            var f = StAbnormalFilter();
            return f.Count == 0 ? "" : " · 买入滤除异动[" + string.Join(",", f.ToArray()) + "]";
        }

        /// <summary>结果头部的「买入价上限」标注；留空/0 不显示。</summary>
        private string StMaxBuyPriceText()
        {
            double? v = StMaxBuyPrice();
            return v.HasValue ? " · 买入价≤" + v.Value.ToString("F2") + "元" : "";
        }

        /// <summary>结果头部的「涨停开盘应对」标注；默认档（不撤单）不显示
        /// （别让默认状态看起来像被限制过）。</summary>
        private string StLimitOpenText()
        {
            string p = StLimitOpenPolicy();
            if (p == "keep") return "";
            if (p == "cancel") return " · 涨停开盘撤单";
            return " · 涨停条件单（回封才进）";
        }

        /// <summary>结果头部的「单股上限」标注；没设上限就不显示（别让默认状态看起来像被限制过）。</summary>
        private string StCapText()
        {
            double? p = StCapPct();
            if (p != null) return " · 单股上限 " + p.Value.ToString("P1");
            double? a = StCapAmt();
            if (a != null) return " · 单股上限 " + a.Value.ToString("F0") + "元";
            return "";
        }

        private static FlowLayoutPanel StCardRow(int maxWidth = 900)
        {
            var f = new FlowLayoutPanel();
            f.FlowDirection = FlowDirection.LeftToRight;
            f.WrapContents = true;
            f.AutoSize = true;
            f.AutoSizeMode = AutoSizeMode.GrowAndShrink;
            f.MaximumSize = new Size(maxWidth, 0);
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
        ///
        /// ⚠️ 所有提示都写到**顶部任务区**（不是「回测结果」页）：
        ///   新鲜度检查是三条流程共用的前置步骤，可能在「近期成果」「当前策略推荐」
        ///   页发起 —— 写死到结果页的话，用户在别的子页根本看不到提示（老毛病）。
        ///
        /// `abort`：用户放弃（弹窗取消 / 数据不齐禁跑）时的收尾回调 —— 必须释放
        ///   统一任务区的互斥闸门，否则用户取消一次之后再也点不动任何运行按钮。
        /// </summary>
        private void StEnsureFresh(Dictionary<string, object> meta, Action then, bool hard = false,
                                   Action abort = null, string signalDate = null, string execDate = null)
        {
            // 单日策略：按选中执行日对应的**信号日**判定覆盖率，而不是笼统按
            // 「应有最新交易日」。否则收盘后选「今天执行」（信号=上一交易日、
            // 数据本已齐）也会被今日缺口误拦；选「明天执行」才需要今日数据齐。
            string expectedDay = signalDate ?? "";
            string fresUrl = "http://127.0.0.1:8000/api/history/download/freshness"
                + (expectedDay.Length > 0 ? "?expected=" + expectedDay : "");
            if (_stFreshBusy)
            {
                // ⚠️ 不能静默 return：上一次点击的检查还在飞时用户又选了个策略再点，
                // 这一下被吞掉，用户会以为「已按新选的策略重跑」，其实后台跑的还是上一次那个。
                StJobText("正在检查行情数据是否最新，请稍候…", true);
                SetErr(_stResultStatus, "正在检查行情数据是否最新，请稍候再点「运行回测」…");
                // 释放本次占位：否则这次点击会把互斥闸门锁死（检查还在飞、任务又没起来）
                if (abort != null) abort();
                return;
            }
            _stFreshBusy = true;
            // ⚠️ 必须在忙检查**之后**再落到字段：否则另一次点击（哪怕被忙检查
            //    挡下并 return）已经改掉了基准日，正在等待补齐的那一轮轮询就会
            //    拿错基准判「齐了」。
            _stFreshExpected = expectedDay;
            // 策略名取**点击时**的快照（而不是稍后 proceed 时再读列表选中项），
            // 保证「弹窗里写的策略」==「实际提交的策略」。
            string sid = J.StrOr(meta["name"], J.StrOr(meta["id"], "—"));
            Action proceed = delegate
            {
                _stFreshBusy = false;
                if (_stFreshTimer != null) { _stFreshTimer.Stop(); _stFreshTimer = null; }
                then();
            };
            Action giveup = delegate
            {
                _stFreshBusy = false;
                if (_stFreshTimer != null) { _stFreshTimer.Stop(); _stFreshTimer = null; }
                StJobText(StJobName(_stJobKind) + " · 已取消运行", false);
                if (abort != null) abort();
            };
            System.Threading.Tasks.Task.Run(delegate
            {
                Dictionary<string, object> f = null;
                try
                {
                    f = J.Ser().Deserialize<Dictionary<string, object>>(
                        VRequest(fresUrl, null, 60000));
                }
                catch (Exception ex)
                {
                    string msg = "无法检查行情数据是否最新：" + ex.Message;
                    Ui(delegate
                    {
                        _stFreshBusy = false;
                        StJobText(msg + "（将按现有数据继续）", true);
                        SetErr(_stResultStatus, msg + "（将按现有数据继续）");
                        then();
                    });
                    return;
                }
                bool need = f.ContainsKey("need_refresh") && f["need_refresh"] is bool
                            && (bool)f["need_refresh"];
                if (!need) { Ui(proceed); return; }

                string expected = J.StrOr(f["expected"], "?");
                int checkedCount = J.Int(f["checked"]);
                // 缺口明细：后端已把「本来就没有该日行情」的票豁免掉，gap 才是真缺口。
                int gap = J.Int(f["gap"]);
                int exSus = J.Int(f["exempt_suspended"]);
                int exNew = J.Int(f["exempt_not_listed"]);
                var gapArr = J.Arr(J.Get(f, "gap_codes"));
                var taskState = J.Map(J.Get(f, "task_state"));
                string gapList = "";
                if (gapArr != null && gapArr.Count > 0)
                {
                    var sb = new System.Text.StringBuilder();
                    int n = Math.Min(8, gapArr.Count);
                    for (int i = 0; i < n; i++)
                    {
                        if (i > 0) sb.Append("、");
                        sb.Append(J.Str(gapArr[i]));
                    }
                    if (gapArr.Count > n) sb.Append(" …");
                    gapList = sb.ToString();
                }
                string taskLine = "";
                if (taskState != null)
                {
                    object avail = J.Get(taskState, "available");
                    if (avail is bool && (bool)avail)
                    {
                        taskLine = "最近一次全市场下载：补到 " + J.StrOr(J.Get(taskState, "span_end"), "?")
                            + "（已完成，失败 " + J.Int(J.Get(taskState, "failed")) + " 只）。\r\n";
                    }
                }
                DialogResult dr = DialogResult.None;
                Ui(delegate
                {
                    // 单日策略（signalDate 非空）：把「缺哪天、影响哪天的策略」说清楚，
                    // 而不是笼统的「行情数据不是最新」。仍保留「等待补齐后自动运行」。
                    bool singleDay = _stFreshExpected.Length > 0;
                    string head = singleDay
                        ? "「" + sid + "」要生成 " + (string.IsNullOrEmpty(execDate) ? "目标日" : execDate)
                          + " 执行的单日策略，\r\n需要 " + _stFreshExpected + " 的收盘行情作为信号，\r\n"
                        : "「" + sid + "」要用全市场 " + checkedCount + " 只股票做筛选，\r\n";
                    string tail = hard
                        ? "· 是：等待后台补齐完成后**自动运行**（预计 60~80 分钟，可切走）\r\n"
                          + "· 否：取消本次运行（数据未齐时不会用旧数据冒充）\r\n\r\n"
                          + "请开启「每天自动更新数据」，或在「数据下载」手动补齐。"
                        : "· 现在补齐：预计 60~80 分钟（可随时关窗，断了也能从断点续上）\r\n"
                          + "· 用现有数据继续：结果基于各票现有数据（未补齐），可能不是最新行情";
                    dr = MessageBox.Show(this,
                        head
                        // 「差多少」必须用**缺口数**（gap），不能用 checked - covered：
                        // 后者把「停牌/退市、未上市」也算成缺口 —— 2026-11-20 实测 65 只
                        // 停牌/退市把 1% 阈值顶穿，明明数据已齐却被拦。现在后端把
                        // 「本来就没有该日行情」的票单独豁免并计数，弹框直接说清：
                        // 还差哪几只、另外豁免了多少只、依据是哪一轮下载。
                        + (gap > 0
                            ? "但还有 " + gap + " 只股票没有 " + expected + " 的收盘数据"
                              + (gapList.Length > 0 ? "：" + gapList : "") + "。\r\n"
                            : "")
                        + (exSus + exNew > 0
                            ? "（另有 " + exSus + " 只停牌/退市、" + exNew + " 只未上市 —— "
                              + "它们本来就没有该日行情，不计入。）\r\n"
                            : "")
                        + taskLine
                        + (singleDay ? "本地行情尚未更新到 " + expected + "，" : "")
                        + (singleDay ? "无法生成该日执行的单日策略。\r\n\r\n" : "")
                        + tail,
                        AppTitle + " - " + (singleDay ? "收盘数据未下载完成，无法运行" : "行情数据不是最新"),
                        hard ? MessageBoxButtons.YesNo : MessageBoxButtons.YesNoCancel,
                        MessageBoxIcon.Warning, MessageBoxDefaultButton.Button2);
                });
                if (dr == DialogResult.Cancel) { Ui(giveup); return; }
                // hard 模式：不齐禁止运算（不再提供「用现有数据继续」）
                if (hard && dr != DialogResult.Yes)
                {
                    string why = _stFreshExpected.Length > 0
                        ? _stFreshExpected + " 收盘数据未下载完成，无法生成 "
                          + (string.IsNullOrEmpty(execDate) ? "目标日" : execDate) + " 的单日策略；已取消运行。"
                        : "数据不齐，已禁止运行；请先在「数据下载」补齐或开启自动更新。";
                    Ui(delegate
                    {
                        _stFreshBusy = false;
                        StJobText(why, true);
                        SetErr(_stResultStatus, why);
                        if (abort != null) abort();
                    });
                    return;
                }
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
            // 等待补齐可能长达一小时，提示必须写在**顶部任务区**（常驻可见），
            // 写在结果页的话用户在别的子页根本不知道后台在等什么。
            string waitMsg = _stFreshExpected.Length > 0
                ? "正在等待 " + _stFreshExpected + " 收盘行情补齐…（" + sid + "，完成后自动运行）"
                : "正在等待行情数据补齐…（" + sid + "）";
            StJobText(waitMsg, false);
            SetErr(_stResultStatus, waitMsg);
            _stFreshTimer.Start();
            StPollFresh(then, sid);
        }

        private void StPollFresh(Action then, string sid)
        {
            // 与 StEnsureFresh 保持同一个基准日：等待补齐期间不能退回「应有最新
            // 交易日」，否则选「今天执行」时会一直等到今天数据补齐才放行。
            string fresUrl = "http://127.0.0.1:8000/api/history/download/freshness"
                + (_stFreshExpected.Length > 0 ? "?expected=" + _stFreshExpected : "");
            System.Threading.Tasks.Task.Run(delegate
            {
                Dictionary<string, object> f = null;
                try
                {
                    f = J.Ser().Deserialize<Dictionary<string, object>>(
                        VRequest(fresUrl, null, 60000));
                }
                catch (Exception) { return; }        // 单次失败下个周期再试
                bool need = f.ContainsKey("need_refresh") && f["need_refresh"] is bool
                            && (bool)f["need_refresh"];
                int gap = J.Int(f["gap"]);
                string latest = J.StrOr(f["latest"], "?");
                Ui(delegate
                {
                    if (!need)
                    {
                        StJobText("数据已补齐（至" + latest + "），继续运行…", false);
                        SetErr(_stResultStatus, "数据已补齐（至" + latest + "），继续运行…");
                        _stFreshTimer.Stop();
                        then();
                        return;
                    }
                    // 显示「还差几只」而不是 covered/total：covered 把停牌/退市、
                    // 未上市也算成没覆盖（后端已豁免，gap 才是真缺口）。
                    string w = string.Format(
                        "等待补齐中…还差 {0} 只（数据至 {1}）…可切到别的页签，补齐在后台继续",
                        gap, latest);
                    StJobText(w, false);
                    SetErr(_stResultStatus, w);
                });
            });
        }

        /// <summary>页签栏「开始回测」按钮的入口：先切到「回测结果」页，再发起回测。
        ///
        /// 切页在前、发起在后：**结果**（指标卡 / 净值曲线 / 个股明细）在「回测结果」页，
        /// 不切过去用户点了按钮只看到屏幕没变化，还以为没点上。
        /// 回测逻辑本身仍走 <see cref="StRunBacktest"/>，与原按钮完全一致。
        ///
        /// ⚠️ 进度条与暂停/停止**不在**这一页，而在顶部「操作」框（统一任务区）——
        ///   切不切页都看得见，这里切页只是为了让结果卡片露出来。
        /// </summary>
        private void StStartBacktestFromBar()
        {
            _stStrip.Select(2);        // 2 = 「回测结果」
            StRunBacktest();
        }

        private void StRunBacktest()
        {
            // 互斥：顶部任务区只显示一个任务，已有任务在跑时挡住新的运行。
            if (!StJobGuard()) return;
            // 点击时取一次快照：后面弹窗、等待补齐、真正提交统一用这一份。
            // 不能各步各读一次列表选中项 —— 中途清单被刷新 / 用户改了选中，
            // 就会出现「弹窗说筹码、实际跑 RPS」这类对不上的情况。
            var meta = StSelectedMeta();
            if (meta == null)
            {
                StJobText("请先在「策略选择与编辑」里选一个策略", true);
                SetErr(_stResultStatus, "请先在「策略选择与编辑」里选一个策略");
                return;
            }
            // 占位（含等待补齐数据的整段时间）：等待期间再点一次就会起两个全市场任务。
            StJobReserve(ST_JOB_FULL);
            // 第 24 项：数据新鲜度守卫在真正发起回测之前（数据陈旧时的弹窗与等待
            // 都在 `StEnsureFresh` 里）。用守卫包一层而不是就地判断，是为了让
            // 回测与「生成当前推荐」两条入口共用同一套逻辑与同一个弹窗。
            StEnsureFresh(meta, delegate { StRunBacktestInner(meta); }, false,
                delegate { StJobRelease(); });
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
                // 基准（指数）来自「回测结果」页的选择；没选 = null（后端就用等权全池买入持有）。
                { "benchmark", StBenchCode() },
                { "position_cap_pct", StCapPct() },
                { "position_cap_amount", StCapAmt() },
                { "limit_open_policy", StLimitOpenPolicy() },
                { "abnormal_filter", StAbnormalFilter() },
                { "max_buy_price", StMaxBuyPrice() },
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
                + J.NumOr(_stComm.Text, 0.0003).ToString("P4").Replace(" ", "")
                + StCapText() + StLimitOpenText() + StAbnormalText() + StMaxBuyPriceText();

            _stResultStatus.Text = "回测中：" + J.StrOr(meta["name"], sid) + "…（进度见顶部任务区）";
            _stResultStatus.ForeColor = _cSub;
            string body = J.Ser().Serialize(req);

            // 第 18 项：改**后台任务 + 轮询**。原来是一次同步请求 + 10 分钟超时，
            // 全市场（5000+ 只）冷缓存实测要 7 分钟以上，结构上仍会超时，
            // 而且中途白屏、看不到任何进展。现在启动接口 2 秒内返回，
            // 进度由统一任务区轮询 /backtest/progress 逐票汇报。
            System.Threading.Tasks.Task.Run(delegate
            {
                try
                {
                    string resp = VRequest("http://127.0.0.1:8000/api/strategies/backtest/start",
                                           body, 60000);
                    var j = J.Ser().Deserialize<Dictionary<string, object>>(resp);
                    Invoke((Action)(() =>
                    {
                        if (J.IsFailed(j))                       // 例如「已有回测在运行」
                        {
                            StJobAbort("完整回测 · 启动失败：" + J.ErrMsg(j), true);
                            SetErr(_stResultStatus, "回测失败：" + J.ErrMsg(j));
                            return;
                        }
                        StJobBegin(ST_JOB_FULL);
                    }));
                }
                catch (Exception ex)
                {
                    Invoke((Action)(() =>
                    {
                        StJobAbort("完整回测 · 启动失败：" + ex.Message, true);
                        SetErr(_stResultStatus, "回测失败：" + ex.Message);
                    }));
                }
            });
        }

        // ================ 统一任务区（完整回测 / 近期回测 / 当日策略共用）================
        // 一套进度条 + 一套暂停/继续/停止 + 一段文案，挂在顶部「操作」框里。
        // 三条流程的差别只有两处：**打哪个进度端点**、**结果渲染到哪个页面**。

        /// <summary>任务名（按类型）。</summary>
        private static string StJobName(int kind)
        {
            if (kind == ST_JOB_FULL) return "完整回测";
            if (kind == ST_JOB_RECENT) return "近期回测";
            if (kind == ST_JOB_RECO) return "当日策略";
            return "任务";
        }

        /// <summary>互斥闸门：已有任务在跑时挡住新任务，并说明当前在跑什么。</summary>
        private bool StJobGuard()
        {
            if (!_stJobBusy) return true;
            StJobText("已有任务在跑（" + StJobName(_stJobKind) + "）· 请等它结束，或点「停止」后再运行", true);
            return false;
        }

        /// <summary>占位：登记任务类型并锁住闸门（启动请求在飞的那几十秒也算占用）。</summary>
        private void StJobReserve(int kind)
        {
            _stJobKind = kind;
            _stJobBusy = true;
            _stJobRunId++;              // 换代号：上一个任务的迟到响应就此失效
            _stJobTotal = 0;
            _stJobElapsed = 0;
            if (_stProgress != null) { _stProgress.Maximum = 1; _stProgress.Value = 0; }
            StJobText(StJobName(kind) + " · 正在提交…", false);
        }

        /// <summary>任务已启动：开始统一轮询。</summary>
        private void StJobBegin(int kind)
        {
            _stJobKind = kind;
            _stJobBusy = true;
            StJobProgress(0, 1, StJobName(kind) + " · 正在启动…");
            StSetControlButtons(true, false);
            if (_stJobTimer == null)
            {
                _stJobTimer = new Timer();
                _stJobTimer.Interval = 1000;
                _stJobTimer.Tick += delegate { StJobTick(); };
            }
            _stJobTimer.Stop();
            _stJobTimer.Start();
            StJobTick();               // 立刻打一次，别等第一个周期
        }

        /// <summary>释放闸门（启动失败 / 用户取消 / 任务结束）：停表、隐藏控制键。</summary>
        private void StJobRelease()
        {
            if (_stJobTimer != null) _stJobTimer.Stop();
            _stJobBusy = false;
            StSetControlButtons(false, false);
        }

        /// <summary>放弃本次运行（启动失败 / 用户取消）：释放闸门并留下原因。</summary>
        private void StJobAbort(string text, bool err)
        {
            StJobRelease();
            StJobText(text, err);
        }

        /// <summary>写任务区文案。err=true 才标红 —— 进度用红字看着像报错。</summary>
        private void StJobText(string text, bool err)
        {
            if (_stProgressText == null) return;
            _stProgressText.Text = text;
            _stProgressText.ForeColor = err ? C.UpErr : _cSub;
        }

        /// <summary>更新进度条 + 文案。</summary>
        private void StJobProgress(int done, int total, string text)
        {
            if (_stProgress == null) return;
            _stProgress.Maximum = Math.Max(1, total);
            _stProgress.Value = Math.Min(Math.Max(0, done), Math.Max(1, total));
            StJobText(text, false);
        }

        /// <summary>把提示写到**当前任务所属页面**的状态行（各页仍保留自己的结果摘要）。</summary>
        private void StJobSetPageStatus(string text, bool err)
        {
            if (_stJobKind == ST_JOB_RECO)
            {
                if (err) SetErr(_stRecoStatus, text);
                else { _stRecoStatus.Text = text; _stRecoStatus.ForeColor = _cSub; }
            }
            else if (_stJobKind == ST_JOB_RECENT)
            {
                if (err) SetErr(_stRecentStatus, text);
                else { _stRecentStatus.Text = text; _stRecentStatus.ForeColor = _cSub; }
            }
            else
            {
                if (err) SetErr(_stResultStatus, text);
                else { _stResultStatus.Text = text; _stResultStatus.ForeColor = _cSub; }
            }
        }

        /// <summary>进度端点：完整 / 近期都走 /backtest/progress，当日策略走 /recommend/progress。</summary>
        private string StJobProgressUrl()
        {
            return _stJobKind == ST_JOB_RECO
                ? "http://127.0.0.1:8000/api/strategies/recommend/progress"
                : "http://127.0.0.1:8000/api/strategies/backtest/progress";
        }

        /// <summary>结果端点（与进度端点同族）。</summary>
        private string StJobResultUrl()
        {
            return _stJobKind == ST_JOB_RECO
                ? "http://127.0.0.1:8000/api/strategies/recommend/result"
                : "http://127.0.0.1:8000/api/strategies/backtest/result";
        }

        /// <summary>轮询一次进度。</summary>
        private void StJobTick()
        {
            int myRun = _stJobRunId;
            if (myRun == 0) return;
            if (_stJobPollBusy) return;                 // 上一次还没回来
            _stJobPollBusy = true;
            string url = StJobProgressUrl();
            RunUi<Dictionary<string, object>>(
                delegate
                {
                    string resp = VRequest(url, null, 30000);
                    return J.Ser().Deserialize<Dictionary<string, object>>(resp);
                },
                delegate(Dictionary<string, object> j)
                {
                    _stJobPollBusy = false;
                    if (myRun != _stJobRunId) return;       // 旧任务，直接不响应
                    StJobApply(j, myRun);
                },
                delegate(Exception ex)
                {
                    _stJobPollBusy = false;
                    if (myRun != _stJobRunId) return;
                    // 单次轮询失败（后端忙）不致命：让表继续打，下一次再说
                    StJobText("进度查询失败，正在重试…" + ex.Message, false);
                },
                null);
        }

        /// <summary>把进度状态画到任务区；ready 则取结果。</summary>
        private void StJobApply(Dictionary<string, object> j, int myRun)
        {
            if (J.IsFailed(j)) { StJobAbort("任务状态读取失败：" + J.ErrMsg(j), true); return; }
            var st = J.Get(j, "state") as Dictionary<string, object>;
            if (st == null) return;
            string state = J.Str(J.Get(st, "state"));
            string phase = J.Str(J.Get(st, "phase"));
            int done = (int)J.NumAt(st, "done");
            int total = (int)J.NumAt(st, "total");
            double elapsed = J.NumAt(st, "elapsed");
            string err = J.Str(J.Get(st, "error"));
            string name = StJobName(_stJobKind);
            _stJobTotal = total;
            _stJobElapsed = elapsed;

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
                {
                    // total 还是 0 = 正在解析股票池。这步可能**联网**（排除 ST 特征
                    // 要拉全市场股票名称，实测 30~60 秒），此时显示「0 / 0 只」像卡死
                    // —— 直说在做什么，别让用户以为程序没反应。
                    text = total > 0
                        ? name + " · 读取数据中 " + done + " / " + total + " 只（"
                          + (done * 100.0 / total).ToString("F1") + "%）· 已用 "
                          + ((int)elapsed) + " 秒"
                        : name + " · 正在准备股票池（含联网获取股票名称，约 1 分钟）· 已用 "
                          + ((int)elapsed) + " 秒";
                }
                else
                    // 策略计算阶段外部无法细分（done 保持 total），别编百分比
                    text = name + " · 计算信号中 " + done + " / " + total + " 只 · 已用 "
                           + ((int)elapsed) + " 秒";
                if (paused) text = "⏸ 已暂停 · " + text + " · 可点「继续」或「停止」";
                StJobProgress(total > 0 ? done : 0, total, text);
                return;
            }

            // ready / error / cancelled
            if (state == "cancelled")
            {
                // 用户主动停止：**中性提示**，不用错误态的红色。
                // 中途的结果不保留（后端已清空），所以不能接着去取结果。
                StJobRelease();
                StJobText(name + " · 已停止（结果不保留，可改参数后重跑）", false);
                StJobSetPageStatus("已停止（结果不保留，可改参数后重跑）", false);
                return;
            }
            if (state == "error")
            {
                string msg = string.IsNullOrEmpty(err) ? "未知错误" : err;
                StJobRelease();
                StJobText(name + " · 失败：" + msg, true);
                StJobSetPageStatus("失败：" + msg, true);
                return;
            }
            // ready：停表取结果（进度条先满格，取到结果后再定稿文案）
            if (_stJobTimer != null) _stJobTimer.Stop();
            StSetControlButtons(false, false);
            int n = total > 0 ? total : 1;
            StJobProgress(n, n, name + " · 计算完成，正在取结果…");
            StJobFetchResult(myRun);
        }

        /// <summary>取结果并渲染到各自的页面；任务区保留「已完成」标志。</summary>
        private void StJobFetchResult(int myRun)
        {
            string url = StJobResultUrl();
            int kind = _stJobKind;
            RunUi<Dictionary<string, object>>(
                delegate
                {
                    string resp = VRequest(url, null, 120000);
                    return J.Ser().Deserialize<Dictionary<string, object>>(resp);
                },
                delegate(Dictionary<string, object> j)
                {
                    if (myRun != _stJobRunId) return;
                    if (J.IsFailed(j))
                    {
                        StJobRelease();
                        StJobText(StJobName(kind) + " · 取结果失败：" + J.ErrMsg(j), true);
                        StJobSetPageStatus("取结果失败：" + J.ErrMsg(j), true);
                        return;
                    }
                    // 各流程各自渲染到自己的页面，任务区只负责「已完成」标志
                    if (kind == ST_JOB_RECO) StRenderReco(j);
                    else if (kind == ST_JOB_RECENT) StRenderRecent(j);
                    else StRenderResult(j);
                    int n = _stJobTotal > 0 ? _stJobTotal : 1;
                    StJobProgress(n, n, string.Format(
                        "{0} · ✅ 已完成 · {1}/{2} 只 · 耗时 {3:F0} 秒",
                        StJobName(kind), n, n, _stJobElapsed));
                    StJobRelease();
                },
                delegate(Exception ex)
                {
                    if (myRun != _stJobRunId) return;
                    StJobRelease();
                    StJobText(StJobName(kind) + " · 取结果失败：" + ex.Message, true);
                    StJobSetPageStatus("取结果失败：" + ex.Message, true);
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
            // ⚠ 这个指标**恒为**「等权全池买入持有」（后端 benchmark_return 的口径），
            //   与用户选的指数无关 —— 卡片名必须说清楚，否则会被当成「指数收益」。
            //   净值图上已不再画这条线（2026-11-21 移除），但收益数字仍有对照价值，保留卡片。
            if (metrics.TryGetValue("benchmark_return", out bret) && bret != null)
                _stMetricCards.Controls.Add(StKpiCard("等权全池收益", J.Pct100Plain(J.Num(bret))));
            // alpha / beta / IR：只有**选了指数基准**、且该指数本地取得到行情时后端才算得出。
            // 算不出来就**不显示** —— 摆一张空卡片比不摆更让人以为「结果是 0」。
            object valpha = J.Get(metrics, "alpha");
            if (valpha != null)
                _stMetricCards.Controls.Add(StKpiCard("Alpha", J.Num(valpha).ToString("F2")));
            object vbeta = J.Get(metrics, "beta");
            if (vbeta != null)
                _stMetricCards.Controls.Add(StKpiCard("Beta", J.Num(vbeta).ToString("F2")));
            object vir = J.Get(metrics, "ir");
            if (vir != null)
                _stMetricCards.Controls.Add(StKpiCard("IR", J.Num(vir).ToString("F2")));
            Skin(_stMetricCards);

            _stEqDates = ToStringList(j["dates"]);
            _stEq = ToNullableDoubleList(j["equity"]);
            // equity_benchmark（等权全池）的曲线已不再画：纵轴只按组合 + 选中指数定标，
            // 后端照旧下发该字段（report 口径不变），这里只是不消费、不渲染。
            if (_stChartMode != null && _stChartMode.SelectedIndex == 1) StOnChartModeChanged();
            _stEquityBox.Invalidate();

            // 成交明细（逐笔）：后端内联下发 trades（已按买入日期倒序裁过）。
            // trades_total / trades_truncated 如实给出总笔数与被丢掉的笔数 ——
            // 截断不标注就等于静默改数据。
            _stTradesData = new List<Dictionary<string, object>>();
            var tl = j["trades"] as System.Collections.IList;
            if (tl != null)
            {
                foreach (var it in tl)
                {
                    var d = it as Dictionary<string, object>;
                    if (d != null) _stTradesData.Add(d);
                }
            }
            _stTradesTotal = Math.Max(_stTradesData.Count, (int)StM(j, "trades_total"));
            _stTradesTruncated = Math.Max(0, (int)StM(j, "trades_truncated"));

            // 参与只数改从 scope.codes_count 取（metrics.num_stocks 已废止 —— 它其实是
            // 回测池大小，不是持仓数，显示出来会被当成「持仓了这么多只」）
            var scope = j["scope"] as Dictionary<string, object>;
            // 基准：按**报告里记录的** scope.benchmark 写，不看「当前页面选了什么」——
            // 历史报告重放必须显示它当时用的基准（验收点：不依赖当前页面选择）。
            if (_stBenchReport != null)
                _stBenchReport.Text = "本次报告：" + StBenchScopeNote(scope, metrics);
            // 买不进的建仓（隔日一字涨停买不进）→ 逐条列进表里（收益 0、备注「买不进」）；
            // 清仓延后（隔日一字跌停卖不出）→ 写进成交行的备注列。都不另开表、不加说明行。
            StFillTradesRows(_stTradesGrid, _stTradesData,
                StBuildFailItems(scope), StSellDeferNotes(scope));
            if (_stTradesTip != null) _stTradesTip.Text = StTradesTipText();
            int doneCount = scope != null ? J.Int(scope["codes_count"]) : 0;
            _stResultStatus.Text = "回测完成（参与统计 " + doneCount + " 只）";
            _stResultStatus.ForeColor = C.DownSoft;
            // 「区间内行情不足」剔掉的票数（股票池层的注入判定结论）——
            // 不显示的话，读者会以为少掉的那几只「本来就不在池里」。
            int skipped = 0;
            var excl = scope != null ? scope["exclusion"] as Dictionary<string, object> : null;
            if (excl != null) skipped = J.Int(J.Get(excl, "skipped_insufficient"));
            if (skipped > 0)
                _stResultStatus.Text = "回测完成（参与统计 " + doneCount
                    + " 只，区间内行情不足剔除 " + skipped + " 只）";
            object errs;
            if (j.TryGetValue("data_errors", out errs))
            {
                var el = errs as System.Collections.IList;
                if (el != null && el.Count > 0)
                    _stResultStatus.Text = "回测完成（参与统计 " + doneCount
                        + " 只，" + el.Count + " 只数据缺失/跳过"
                        + (skipped > 0 ? "，区间内行情不足剔除 " + skipped + " 只" : "") + "）";
            }
            // 「涨停开盘应对」的命中统计：撤单拦掉了多少笔（**与「买不进」分开**）。
            // 只在真撤了单时才显示 —— 默认档显示「0 次」只会让人以为被限制过。
            var lo = j["limit_open"] as Dictionary<string, object>;
            if (lo != null)
            {
                int loCanceled = (int)StM(lo, "canceled_builds");
                if (loCanceled > 0)
                    _stResultStatus.Text += " · 涨停开盘撤单 " + loCanceled + " 次";
            }

            // 指数曲线：拿起始资金（与组合同轴的基准），再叠加**当前下拉选的**那条指数。
            // ⚠ 图上画的是「当前选择」，而上面那行文字写的是「报告里记录的」——
            //   两者可以不同（用户换了下拉、或重放一份用别的指数跑出来的历史报告），
            //   各说各的、不混：文字=这份报告当时用的基准，图=你现在想对比的指数。
            _stInitialCapital = scope != null ? J.Num(J.Get(scope, "initial_capital")) : 0;
            StLoadIndexCurve(StBenchCode());
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
                    return J.Ser().Deserialize<Dictionary<string, object>>(resp);
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
            // ⚠️ 加载前**先清空结果区**：否则加载中（9 MB 的报告要读几秒）或加载失败时，
            // 屏幕上留着的**上一次回测**的指标卡与净值曲线会被当成这份历史报告的内容。
            // 金融数字误读的代价太大，宁可先给一片空白。
            StClearResultArea();
            RunUi<Dictionary<string, object>>(
                delegate
                {
                    string resp = VRequest(
                        "http://127.0.0.1:8000/api/strategies/backtest/reports/"
                        + System.Uri.EscapeDataString(file), null, 60000);
                    return J.Ser().Deserialize<Dictionary<string, object>>(resp);
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

        /// <summary>清空回测结果区（指标卡 / 净值曲线 / 成交明细）。
        /// 换一份结果前必须先清，否则旧结果的数字会被当成新结果的。</summary>
        private void StClearResultArea()
        {
            if (_stMetricCards != null) _stMetricCards.Controls.Clear();
            _stEqDates = null;
            _stEq = null;
            _stTradesData = new List<Dictionary<string, object>>();
            _stTradesTotal = 0;
            _stTradesTruncated = 0;
            if (_stEquityBox != null) _stEquityBox.Invalidate();
            if (_stTradesGrid != null) StFillTradesRows(_stTradesGrid, null);
            if (_stTradesTip != null) _stTradesTip.Text = StTradesTipText();
            // 基准行也要一起清：留着上一份报告的基准，会被当成这份报告用的基准。
            if (_stBenchReport != null) _stBenchReport.Text = "本次报告：—";
            // 指数曲线也要清：它按**上一份报告的交易日**对齐过，换一份结果就是错的。
            _stEqIndex = null;
            _stEqIndexCode = "";
            if (_stIndexMsg != null) _stIndexMsg.Text = "";
        }

        /// <summary>取「没成交的建仓」明细（后端已按执行日倒序、最多 2000 条）。
        /// 三种 reason：limit_up（物理买不进）/ limit_open_cancel（涨停开盘撤单）/
        /// abnormal（买入时异动过滤主动放弃）。</summary>
        private static List<Dictionary<string, object>> StBuildFailItems(
            Dictionary<string, object> scope)
        {
            var outList = new List<Dictionary<string, object>>();
            if (scope == null) return outList;
            var feas = scope["feasibility"] as Dictionary<string, object>;
            if (feas == null) return outList;
            var bf = feas["build_failures"] as Dictionary<string, object>;
            if (bf == null) return outList;
            var items = bf["items"] as System.Collections.IList;
            if (items == null) return outList;
            foreach (var it in items)
            {
                var d = it as Dictionary<string, object>;
                if (d != null) outList.Add(d);
            }
            return outList;
        }

        /// <summary>
        /// 买不进的建仓 → 成交明细里的**零收益行**：收益 0、盈亏 0、无买卖价，
        /// 备注「买不进」。日期用**执行日**（本该买入的那天）。
        /// ⚠️ 备注按 reason 分：limit_up = 物理买不进（一字板）；
        ///    limit_open_cancel = **策略主动撤单**（涨停开盘应对）；
        ///    abnormal = 买入时异动过滤主动放弃（与一字板/撤单分开归因）。
        ///    三者都是「没成交」，但归因不同 —— 混成同一句话就看不出是市场约束还是自己放弃的。
        /// </summary>
        private static Dictionary<string, object> StBuildFailRow(
            Dictionary<string, object> it)
        {
            string reason = Convert.ToString(J.Get(it, "reason"));
            string note;
            if (reason == "limit_open_cancel") note = "涨停开盘撤单";
            else if (reason == "abnormal")
            {
                var f = J.Get(it, "filter_types") as System.Collections.IList;
                var parts = new List<string>();
                if (f != null) foreach (var x in f) parts.Add(Convert.ToString(x));
                note = "买入时异动放弃(" + string.Join("/", parts.ToArray()) + ")";
            }
            else note = "买不进";
            return new Dictionary<string, object> {
                {"code", Convert.ToString(J.Get(it, "code"))},
                {"name", Convert.ToString(J.Get(it, "name"))},
                {"buy_date", Convert.ToString(J.Get(it, "date"))},
                {"ret", 0.0},
                {"pnl", 0.0},
                {"note", note},
                {"build_fail", true},
            };
        }

        /// <summary>清仓延后逐条明细（后端已按实际卖出日倒序）。</summary>
        private static List<Dictionary<string, object>> StSellDeferItems(
            Dictionary<string, object> scope)
        {
            var outList = new List<Dictionary<string, object>>();
            if (scope == null) return outList;
            var feas = scope["feasibility"] as Dictionary<string, object>;
            if (feas == null) return outList;
            var sd = feas["sell_deferrals"] as Dictionary<string, object>;
            if (sd == null) return outList;
            var items = sd["items"] as System.Collections.IList;
            if (items == null) return outList;
            foreach (var it in items)
            {
                var d = it as Dictionary<string, object>;
                if (d != null) outList.Add(d);
            }
            return outList;
        }

        /// <summary>
        /// 延后信息 → 成交明细「备注」列。key = 代码|实际卖出日。
        /// cancelled（延后期间改主意继续持有、最终没卖）**不挂行** —— 它没有成交，
        /// 那一行不存在，这种条目直接略过。
        /// </summary>
        private static Dictionary<string, string> StSellDeferNotes(
            Dictionary<string, object> scope)
        {
            var map = new Dictionary<string, string>();
            foreach (var it in StSellDeferItems(scope))
            {
                if (Convert.ToString(J.Get(it, "status")) == "cancelled") continue;
                string code = Convert.ToString(J.Get(it, "code"));
                string day = Convert.ToString(J.Get(it, "date"));
                string planned = Convert.ToString(J.Get(it, "planned_date"));
                int days = (int)StM(it, "deferred_days");
                string key = code + "|" + day;
                string txt = "延后 " + days + " 天（计划清仓 " + planned + "）";
                if (map.ContainsKey(key)) map[key] = map[key] + "、" + txt;
                else map[key] = txt;
            }
            return map;
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
                    return J.Ser().Deserialize<Dictionary<string, object>>(resp);
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
                    return J.Ser().Deserialize<Dictionary<string, object>>(resp);
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

        // ---------------- 成交明细（逐笔） ----------------
        // 一行 = 一次完整交易（买入 → 卖出），与指标卡「交易次数」同口径。
        // 逐笔明细由后端**内联**下发（run_backtest 的 trades 字段），不再靠
        // 「点主行展开」按需拉：以前那张表一行=一只标的，用户会把「一只票」
        // 当成「一笔买入」，于是「同一天买好几只、多数是老票再买」看着像
        // 「每天只买一只」（实测 JWZZ 3 年日均买 4.78 笔）。
        private const int ST_TRADES_MAX = 5000;   // 表格渲染上限（与后端 stats.MAX_TRADES_RESPONSE 同值）
        private const int ST_BUILD_FAIL_ROWS_MAX = 2000;  // 「买不进」行的渲染上限（与后端 MAX_BUILD_FAILURES 同值）

        /// <summary>成交明细表的列（回测结果页 / 近期策略成果页共用一份）。</summary>
        private static List<GridColumn> StTradesColumns()
        {
            return new List<GridColumn> {
                new GridColumn("代码", "code") { IsCode = true, Jump = true },
                NameColumn(),          // 名称列带 Jump：点名称同样可跳个股详情
                new GridColumn("买入日期", "buy_date"),
                new GridColumn("买入价", "buy_price", true),
                new GridColumn("卖出日期", "sell_date"),
                new GridColumn("卖出价", "sell_price", true),
                new GridColumn("收益", "ret", true),
                new GridColumn("盈亏", "pnl", true),
                new GridColumn("持有天数", "holding_days", true),
                new GridColumn("备注", "note"),      // 买不进的建仓：「买不进」/「涨停开盘撤单」
            };
        }

        /// <summary>表头上方的口径说明（含总笔数与截断标注 —— 截断必须说清）。</summary>
        private string StTradesTipText()
        {
            string s = "口径：一行 = 一次完整交易（买入 → 卖出），未平仓的显示「未平仓」；"
                + "收益/盈亏为权重口径（与净值同一套成本公式），价格为未复权收盘价。";
            if (_stTradesTotal > 0)
            {
                s += " 共 " + _stTradesTotal + " 笔成交";
                if (_stTradesTruncated > 0)
                    s += "，表内按买入日期倒序显示最近 " + ST_TRADES_MAX + " 笔（更早的见回测报告文件）";
                s += "。";
            }
            return s;
        }

        /// <summary>
        /// 填成交明细。
        /// · <paramref name="buildFails"/>：**买不进的建仓**（隔日一字涨停）—— 没成交，
        ///   但"想买没买成"必须看得见 → 逐条列进**同一张表**，收益 0、备注「买不进」。
        /// · <paramref name="tradeNotes"/>：**清仓延后**的笔（隔日一字跌停卖不出 →
        ///   顺延到开板日卖出）—— 那笔是**真成交了**，只是晚了几天 → 在它那一行的
        ///   备注列写明延后几天、计划清仓日。
        /// </summary>
        private void StFillTradesRows(StockGrid grid, List<Dictionary<string, object>> rows,
                                      List<Dictionary<string, object>> buildFails = null,
                                      Dictionary<string, string> tradeNotes = null)
        {
            if (grid == null) return;
            grid.Rows.Clear();

            // 按买入日期倒序（新的在前）+ 只留最近 ST_TRADES_MAX 笔。
            // 后端已按这个口径内联裁过，这里再兜一次：历史报告走的是落盘版
            // （code 序、最多 2 万笔），不裁会让 UI 卡住（实测填 2 万行要 4.3 秒）。
            var ordered = new List<Dictionary<string, object>>();
            if (rows != null) ordered.AddRange(rows);
            ordered.Sort(delegate(Dictionary<string, object> a, Dictionary<string, object> b)
            {
                return string.Compare(StTradeBuyDate(b), StTradeBuyDate(a), StringComparison.Ordinal);
            });
            if (ordered.Count > ST_TRADES_MAX)
                ordered.RemoveRange(ST_TRADES_MAX, ordered.Count - ST_TRADES_MAX);

            // 再把买不进的建仓加进来（后端已按执行日倒序 → 取最近若干条）。
            if (buildFails != null)
            {
                int k = 0;
                foreach (var it in buildFails)
                {
                    if (it == null) continue;
                    if (k >= ST_BUILD_FAIL_ROWS_MAX) break;
                    ordered.Add(StBuildFailRow(it));
                    k++;
                }
                // 合并后统一按买入日期倒序（买不进的按**执行日**排）。
                ordered.Sort(delegate(Dictionary<string, object> a, Dictionary<string, object> b)
                {
                    return string.Compare(StTradeBuyDate(b), StTradeBuyDate(a), StringComparison.Ordinal);
                });
            }
            if (ordered.Count == 0) return;

            grid.SuspendLayout();
            for (int i = 0; i < ordered.Count; i++)
            {
                var d = ordered[i];
                string code = Convert.ToString(J.Get(d, "code"));
                string name = Convert.ToString(J.Get(d, "name"));
                if (string.IsNullOrEmpty(name)) name = "—";
                bool isFail = J.Get(d, "build_fail") != null;   // 买不进的建仓
                string buyDate = StTradeBuyDate(d);
                // 买不进的没有卖出日 → 显示「—」，别写「未平仓」（它压根没建仓）
                string sellDate = isFail ? "—" : StTradeSellDate(d);
                var buyPxObj = J.Get(d, "buy_price");
                var sellPxObj = J.Get(d, "sell_price");
                var retObj = J.Get(d, "ret");
                var pnlObj = J.Get(d, "pnl");
                var daysObj = J.Get(d, "holding_days");
                string buyPx = (buyPxObj != null) ? J.Num(buyPxObj).ToString("F2") : "—";
                string sellPx = (sellPxObj != null) ? J.Num(sellPxObj).ToString("F2") : "—";
                string retTxt = (retObj != null) ? J.Pct100Plain(J.Num(retObj)) : "—";
                string pnlTxt = (pnlObj != null) ? J.Num(pnlObj).ToString("F0") : "—";
                string daysTxt = (daysObj != null) ? J.Int(daysObj).ToString() : "—";
                string note = Convert.ToString(J.Get(d, "note"));
                // 清仓延后的那一笔：备注写「延后 N 天（计划清仓 YYYY-MM-DD）」
                if (string.IsNullOrEmpty(note) && tradeNotes != null)
                {
                    string nkey = code + "|" + Convert.ToString(J.Get(d, "sell_date"));
                    string tn;
                    if (tradeNotes.TryGetValue(nkey, out tn)) note = tn;
                }
                int ri = grid.Rows.Add(code, name, buyDate, buyPx, sellDate, sellPx,
                    retTxt, pnlTxt, daysTxt, note);
                var row = grid.Rows[ri];
                row.DefaultCellStyle.SelectionBackColor = _cPanel;
                row.DefaultCellStyle.SelectionForeColor = _cText;
                // 盈亏着色（红涨绿跌）：收益、盈亏两列按正负上色。
                // 买不进的收益恒为 0 → 不着色（0 不是涨也不是跌）。
                if (!isFail)
                {
                    if (retObj != null) row.Cells[6].Style.ForeColor = (J.Num(retObj) >= 0) ? C.Up : C.Down;
                    if (pnlObj != null) row.Cells[7].Style.ForeColor = (J.Num(pnlObj) >= 0) ? C.Up : C.Down;
                }
            }
            grid.ResumeLayout();
            grid.Fit(240, 620);
        }

        /// <summary>买入日期（缺失返回空串，排序时排到后面）。</summary>
        private static string StTradeBuyDate(Dictionary<string, object> d)
        {
            return Convert.ToString(J.Get(d, "buy_date")) ?? "";
        }

        /// <summary>卖出日期：未平仓的笔没有该字段 → 显示「未平仓」。</summary>
        private static string StTradeSellDate(Dictionary<string, object> d)
        {
            string s = Convert.ToString(J.Get(d, "sell_date"));
            return string.IsNullOrEmpty(s) ? "未平仓" : s;
        }
        // ---------------- 净值曲线绘制 ----------------
        /// <summary>图表切换：柱状图模式下若还没有指数曲线，默认取上证用于当日涨跌判定。</summary>
        private void StOnChartModeChanged()
        {
            if (_stEquityBox == null) return;
            bool bar = _stChartMode != null && _stChartMode.SelectedIndex == 1;
            if (bar && (_stEqIndex == null || _stEqIndex.Count < 2)
                && _stEqDates != null && _stEqDates.Count >= 2)
                StLoadIndexCurve(BAR_INDEX_CODE);   // 复用已加载的基准曲线，不重复取数
            _stEquityBox.Invalidate();
        }

        /// <summary>单图双柱（纯前端算，不动后端口径）：每天两根并排柱 —— 策略每日收益 R[t]=eq[t]/eq[t-1]-1
        /// 与指数当日涨跌，同一天紧挨着，直接看出「指数涨但策略亏」的日子。
        /// <para>颜色只区分系列：策略红实、指数蓝实，均为实心；涨跌看柱子在 0% 基线上/下，两者共用该基线。
        /// 默认只画最近 250 日（全历史几千天会把柱挤成 1px），可选区间或自定义起止日期。
        /// 指数用当前叠加的基准曲线，未选则默认上证。</para></summary>
        private void StDailyBarPaint(PictureBox box, Graphics g)
        {
            var r = box.ClientRectangle;
            g.Clear(_cBg);
            if (_stEq == null || _stEq.Count < 2)
            {
                using (var b = new SolidBrush(_cSub))
                    g.DrawString("暂无每日收益（先运行回测）", this.Font, b, 10f, 10f);
                return;
            }

            int n = _stEq.Count;
            var rets = new double?[n];   // 策略当日收益
            var irets = new double?[n];  // 指数当日涨跌
            bool hasIdx = _stEqIndex != null && _stEqIndex.Count == n;
            for (int i = 1; i < n; i++)
            {
                if (_stEq[i] != null && _stEq[i - 1] != null && _stEq[i - 1].Value != 0)
                    rets[i] = _stEq[i].Value / _stEq[i - 1].Value - 1;
                if (hasIdx && _stEqIndex[i] != null && _stEqIndex[i - 1] != null
                    && _stEqIndex[i - 1].Value != 0)
                    irets[i] = _stEqIndex[i].Value / _stEqIndex[i - 1].Value - 1;
            }

            // 窗口范围：自定义起止日期按字符串比较（_stEqDates 是 yyyy-MM-dd）；其余按最近 N 日
            int from = 1, to = n;
            int win = _stBarWindow == null ? 0 : _stBarWindow.SelectedIndex;
            if (win == 4 && _stBarFrom != null && _stBarTo != null && _stEqDates != null)
            {
                string lo = _stBarFrom.Value.ToString("yyyy-MM-dd");
                string hi = _stBarTo.Value.ToString("yyyy-MM-dd");
                for (int i = 1; i < n; i++)
                {
                    string d = i < _stEqDates.Count ? _stEqDates[i] : "";
                    if (string.CompareOrdinal(d, lo) >= 0 && string.CompareOrdinal(d, hi) <= 0)
                    {
                        if (from == 1 && (i == 1 || string.CompareOrdinal(
                            i - 1 < _stEqDates.Count ? _stEqDates[i - 1] : "", lo) < 0)) from = i;
                        to = i + 1;
                    }
                }
                if (from >= to) { from = Math.Max(1, n - 250); to = n; }   // 区间无重叠 → 退回默认
            }
            else
            {
                int days = win == 1 ? 120 : win == 2 ? 500 : win == 3 ? int.MaxValue : 250;
                if (days < int.MaxValue && n - 1 > days) from = n - days;
            }
            int count = Math.Max(1, to - from);

            // 单图双柱：每天两根（策略 + 指数），共用一个纵轴跨度（取两者最大绝对值）
            double span = 0.005;
            for (int i = from; i < to; i++)
            {
                if (rets[i].HasValue) span = Math.Max(span, Math.Abs(rets[i].Value));
                if (irets[i].HasValue) span = Math.Max(span, Math.Abs(irets[i].Value));
            }
            span *= 1.15;

            int padL = 8, padR = 8, padT = 20, padB = 20;
            int x0 = padL;
            int w = Math.Max(1, r.Width - padL - padR);
            int h = Math.Max(1, r.Height - padT - padB);
            double y0 = padT + h / 2.0;
            Func<double, int> yy = v => (int)(y0 - v / span * (h / 2.0));

            using (var grid = new Pen(_light ? Color.FromArgb(222, 224, 228) : Color.FromArgb(58, 62, 72)))
            {
                for (int i = 0; i <= 4; i++)
                {
                    int y = padT + h * i / 4;
                    g.DrawLine(grid, x0, y, x0 + w, y);
                }
                g.DrawLine(grid, x0, (int)y0, x0 + w, (int)y0);   // 0% 基线
            }

            // 每天一个 slot，左半画策略、右半画指数；颜色只区分系列，涨跌看柱子在 0% 基线上下
            double slot = (double)w / Math.Max(1, to - from);
            int bw = Math.Max(1, (int)(slot / 2) - 1);
            using (var upBrush = new SolidBrush(C.Up))             // 策略：红实（上=涨 下=跌）
            using (var idxBrush = new SolidBrush(C.Accent))        // 指数：蓝实（上=涨 下=跌）
            {
                for (int i = from; i < to; i++)
                {
                    int slotX = x0 + (int)(slot * (i - from));
                    int baseY = (int)y0;

                    var rv = rets[i];
                    if (rv.HasValue && Math.Abs(rv.Value) >= 1e-12)
                    {
                        int yv = yy(rv.Value);
                        int top = Math.Min(yv, baseY), bh = Math.Max(1, Math.Abs(baseY - yv));
                        g.FillRectangle(upBrush, slotX, top, bw, bh);
                    }

                    var iv = irets[i];
                    if (iv.HasValue && Math.Abs(iv.Value) >= 1e-12)
                    {
                        int ix = slotX + bw + 1;
                        int yv = yy(iv.Value);
                        int top = Math.Min(yv, baseY), bh = Math.Max(1, Math.Abs(baseY - yv));
                        g.FillRectangle(idxBrush, ix, top, bw, bh);
                    }
                }
            }

            using (var b = new SolidBrush(_cText))
            using (var f = new Font("Microsoft YaHei UI", 9f))
            {
                string d0 = from < _stEqDates.Count ? _stEqDates[from] : "";
                string d1 = to - 1 < _stEqDates.Count ? _stEqDates[to - 1] : "";
                string bench = hasIdx ? StBenchShortName(_stEqIndexCode) : "未加载";
                g.DrawString(string.Format("{0} ~ {1}  共 {2} 日  跨度 ±{3:P1}  基准 {4}",
                    d0, d1, count, span, bench), f, b, (float)x0, 1f);
                g.DrawString("红=策略 蓝=指数（每天左红右蓝，均实心；柱在基线上=涨、下=跌）",
                    f, b, (float)x0, (float)(padT + h + 2));
            }
        }

        private void StEquityPaint(object sender, PaintEventArgs e)
        {
            var box = (PictureBox)sender;
            var g = e.Graphics;
            if (_stChartMode != null && _stChartMode.SelectedIndex == 1)
            {
                StDailyBarPaint(box, g);
                return;
            }
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
            if (_stEqIndex != null)
                foreach (var v in _stEqIndex) if (v != null) { min = Math.Min(min, v.Value); max = Math.Max(max, v.Value); }
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

            if (_stEqIndex != null)
            {
                // 指数：**只用收盘价**一条线（与组合同起点、同纵轴 → 可直接比高低）
                using (var pen = new Pen(C.LineViolet, 1.5f))
                    for (int i = 1; i < _stEqIndex.Count; i++)
                        if (_stEqIndex[i] != null && _stEqIndex[i - 1] != null)
                            g.DrawLine(pen, pt(i - 1, _stEqIndex[i - 1].Value), pt(i, _stEqIndex[i].Value));
            }
            using (var pen = new Pen(C.AccentDeep, 2f))
                for (int i = 1; i < _stEq.Count; i++)
                    if (_stEq[i] != null && _stEq[i - 1] != null)
                        g.DrawLine(pen, pt(i - 1, _stEq[i - 1].Value), pt(i, _stEq[i].Value));

            using (var bb = new SolidBrush(_cText))
            {
                g.DrawString("组合", new Font("Microsoft YaHei UI", 9f), new SolidBrush(C.AccentDeep), (float)padL, 0f);
                if (_stEqIndex != null)
                    g.DrawString("指数 " + StBenchShortName(_stEqIndexCode),
                        new Font("Microsoft YaHei UI", 9f), new SolidBrush(C.LineViolet), (float)(padL + 52), 0f);
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
                    var js = J.Ser().Deserialize<object[]>(rs);
                    var jc = J.Ser().Deserialize<Dictionary<string, object>>(rc);
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
                        // 勾选状态是从 settings.ini 恢复的（scope_exclude），
                        // 载入后必须重新拉一次预览，否则显示的仍是「剔除后 = 基池」。
                        StScopePreview();
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
                    return J.Ser().Deserialize<Dictionary<string, object>>(resp);
                },
                delegate(Dictionary<string, object> j)
                {
                    if (J.IsFailed(j))
                    {
                        StJobText("操作失败：" + J.ErrMsg(j), true);
                        StJobSetPageStatus("操作失败：" + J.ErrMsg(j), true);
                        return;
                    }
                    object pv;
                    bool paused = j.TryGetValue("paused", out pv) && pv is bool && (bool)pv;
                    // 非错误态：用普通子色写，别用 SetErr（那是红色错误态）。
                    // 暂停/继续是**正常操作**，标红会让人以为出错了。
                    string msg = J.StrOr(J.Get(j, "message"), "已处理");
                    StJobText(msg, false);
                    StJobSetPageStatus(msg, false);
                    StSetControlButtons(true, paused);
                },
                delegate(Exception ex)
                {
                    // 后端没起来是最常见的原因，给一句能照做的提示
                    StJobText("操作失败（确认后端已启动）：" + ex.Message, true);
                    StJobSetPageStatus("操作失败（确认后端已启动）：" + ex.Message, true);
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

        private static Panel StKpiCard(string title, string value, int width = 150)
        {
            var card = new Panel();
            card.Width = width;
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
