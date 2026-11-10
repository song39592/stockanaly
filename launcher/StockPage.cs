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
    /// <summary>
    /// 个股分析（原生内嵌标签页）：MainForm 的拆分文件，替代 frontend/stock-analysis.html。
    /// 数据来自本机后端：GET /api/history/stock/{code}（K线 / 入池出池轨迹 / 消息面），
    /// GET /api/stock/quote（名称与现价），POST /api/stock/valuation（估值），
    /// POST /api/stock/research（AI 个股调研，返回 markdown，需配置 LLM_API_KEY）。
    /// 呈现贴近原网页：在榜统计 KPI + 自绘日 K 蜡烛图（MA5/10/20/60 + 成交量 + 入池/出池/事件标记）
    /// + 入池出池表 + 消息面时间轴 + 内联估值（完整过程见「估值计算」标签页）。
    /// </summary>
    internal sealed partial class MainForm
    {
        // ---- 个股分析页 ----
        private int _stockTabIndex = -1;
        // ⚠️ 「代码 / 指标」输入入口已统一收敛到右下角隐藏输入框（见 QuickSearch.cs），
        // 个股页不再保留顶部输入框；以下只保留展示用字段。
        private Button _stockRefresh;
        private Label _stockName, _stockHint, _stockStatus, _stockValStatus, _stockIndStatus;
        private KLineChart _stockKline;
        private ChipPanel _stockChip;                      // K 线右侧的筹码分布窗口
        // 筹码模型不再用下拉框：与主图 / 副图指标一致，由右下角输入框键入**索引名**切换
        // （候选来自 /api/chip/dist/formulas，见 StockLoadChipFormulas）。
        private string _stockChipFormulaId = null;         // 当前筹码公式 id（null = 用后端默认 tri_decay）
        private readonly List<IndOpt> _stockChipOpts = new List<IndOpt>();   // 筹码公式候选（Id=纯字母索引名，Raw=公式 id）
        private System.Windows.Forms.Timer _chipTimer;     // 滚轮缩放的防抖重算
        private int _stockRange = 250;                     // 0 = 全部
        private string _stockAdjust = "qfq";               // qfq 前复权 / raw 不复权
        private readonly Dictionary<Button, string> _stockAdjustMap = new Dictionary<Button, string>();
        private readonly Dictionary<Button, string> _stockPeriodMap = new Dictionary<Button, string>();
        private string _stockPeriod = "day";                  // K线周期：day / week / month
        private int _stockRangeIdx = 1;                       // 范围档位：0=近6月 1=近1年 2=全部（-1=滚轮自定义）
        private Label _stockBasicStatus;                      // 基本信息状态行
        private FlowLayoutPanel _stockBasicKpi;               // 行业 / 市值 / 股本 KPI
        private FlowLayoutPanel _stockBasicLimitKpi;          // 涨停 / 连板 KPI
        private Label _stockBasicBus;                         // 主营业务
        private StockGrid _stockBasicHolders;                // 前十大股东表
        private Label _stockBlocks;                           // 基本信息页 · 所属板块（行业行）
        private Label _stockCptBlocks;                        // 所属板块（概念行）
        private Label _stockRgnBlocks;                        // 所属板块（地域行）
        private System.Windows.Forms.Timer _boardsTimer;      // 板块索引重建后的重试
        private int _stockBasicRound = 0;
        private StockGrid _stockTimeline;                   // 入池 / 出池记录
        private TableLayoutPanel _stockEvents;              // 消息面时间轴
        private System.Collections.ArrayList _stockEventData = new System.Collections.ArrayList();
        private FlowLayoutPanel _stockValKpi;               // 估值 KPI
        private StockGrid _stockValGrid;                    // 估值情景表
        private Label _stockSaoleiStatus;                   // 扫雷状态行
        private TableLayoutPanel _stockSaoleiList;          // 扫雷 · 风险清单 + 个股亮点
        private string _stockCurrent = null;
        // 主图指标：与副图一致，**固定只显示 1 个**，由右下角输入框键入指标名 / id 切换（再输一次同一指标 = 关闭）。
        // 主图指标（含 "ma" 均线）统一走后端指标通道，与副图同一套规则；null = 不显示任何主图指标。
        private string _stockMainIndicator = "ma";
        private readonly List<IndOpt> _stockMainOpts = new List<IndOpt>();           // 主图候选（名称 ↔ id，含 ma）
        // 副图指标：**固定只显示 1 个**。复用右下角输入框键入指标名（带输入联想）即可替换，
        // 不再用勾选框堆叠多个副图面板（多面板会把 K 线主图挤得太矮）。
        private string _stockSubIndicator = null;                                    // 当前副图指标 id（null = 无）
        private readonly List<IndOpt> _stockSubOpts = new List<IndOpt>();            // 副图候选（名称 ↔ id）
        // Raw：筹码公式的后端 id（仅筹码候选使用，其余为 null）
        private sealed class IndOpt { public string Id; public string Name; public string Raw; }
        // 副图指标的联想下拉逻辑见 QuickSearch.cs（与右下角输入框共用）。
        // AI 分析（调 /api/stock/research）
        private Label _stockAiStatus;                 // AI 分析状态
        private RichTextBox _stockAiBox;              // AI 分析 markdown 渲染框
        private string _stockAiMarkdown = "";         // 当前 AI 分析文本（换肤时重绘）
        // AI 分析偏好（作为 /api/stock/research 的投喂变量，持久化到本地 JSON）
        private string _aiDepth = "normal";           // concise | normal | detailed
        private string _aiHorizon = "mid";            // short | mid | long
        private List<string> _aiFocus = new List<string> { "板块", "估值", "走势", "大盘" };
        private List<CheckBox> _aiFocusCbs = new List<CheckBox>();   // 偏好页关注点勾选框
        private int _stockRound = 0;
        private readonly Dictionary<Button, int> _stockRangeMap = new Dictionary<Button, int>();

        // 二级导航：K线 / 记录·消息 / 估值 各一页（K线页不滚动，图表撑满，避免滚轮缩放与翻页冲突）
        private SubTabStrip _stockStrip;

        // 历史查看记录（最多 10 条，先进先出滚动覆盖）
        // 持久化到启动器自己的 %APPDATA%\StockPoolLauncher\history_stock_view.json：
        // 这是界面状态，不该写进行情数据目录（数据目录可能在别的盘、也随时会被改）。
        private class StockHistoryItem
        {
            public string Code = "";
            public string Name = "";
            public long Ts = 0;
        }
        private List<StockHistoryItem> _stockHistory = new List<StockHistoryItem>();
        private string _stockHistoryPath = null;
        private TableLayoutPanel _stockHistoryRoot = null;   // 个股页根（2 列），用于切换列宽
        private TableLayoutPanel _stockHistoryList = null;   // 历史项列表容器（可滚动）
        private Label _stockHistoryHeader = null;
        private Button _stockHistoryToggle = null;           // 折叠/展开按钮
        private bool _stockHistoryExpanded = true;

        #region 个股分析页（原生内嵌标签页）

        private Panel BuildStockPage()
        {
            var p = NewPage("个股分析");
            _stockTabIndex = _tabPages.Count - 1;
            p.AutoScroll = false;   // 内容分到二级页，整页不滚（K线页图表撑满）

            var root = new TableLayoutPanel();
            root.Dock = DockStyle.Fill;
            root.Margin = new Padding(0);
            root.Padding = new Padding(0);
            root.ColumnCount = 2;
            root.RowCount = 1;
            root.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 170f));   // 历史导航（可折叠）
            root.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100f));    // 主内容
            root.RowStyles.Add(new RowStyle(SizeType.Percent, 100f));
            p.Controls.Add(root);
            _stockHistoryRoot = root;

            // 主内容列（标题 / 查询 / 二级标签栏 / 二级页内容）
            var mainCol = new TableLayoutPanel();
            mainCol.Dock = DockStyle.Fill;
            mainCol.Margin = new Padding(0);
            mainCol.Padding = new Padding(16, 8, 16, 12);
            mainCol.ColumnCount = 1;
            mainCol.RowCount = 4;
            mainCol.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100f));
            mainCol.RowStyles.Add(new RowStyle(SizeType.AutoSize));       // 标题
            mainCol.RowStyles.Add(new RowStyle(SizeType.AutoSize));       // 查询工具区
            mainCol.RowStyles.Add(new RowStyle(SizeType.AutoSize));       // 二级标签栏
            mainCol.RowStyles.Add(new RowStyle(SizeType.Percent, 100f));  // 二级页内容

            // 历史查看导航（左侧，可折叠）
            root.Controls.Add(BuildStockHistoryPanel(), 0, 0);

            // ---- 标题 ----
            mainCol.Controls.Add(BuildStockHead(), 0, 0);

            // ---- 查询工具区（始终可见，不随二级页滚动）----
            mainCol.Controls.Add(BuildStockQueryBar(), 0, 1);

            // ---- 二级标签栏（K 线页不滚动，图表撑满）----
            _stockStrip = new SubTabStrip(this, mainCol, 2, 3, "stock-subtab");
            _stockStrip.OnSelected = delegate(int index)
            {
                // 切到 K线 页就把焦点交给图表，↑↓ 缩放 / ←→ 移光标立刻可用
                if (index == 0 && _stockKline != null) _stockKline.FocusForKeys();
                // 切到指标索引页：首次自动拉取目录自检
                if (index == _stockIndSubIndex) StockIndOnEnter();
            };

            // 主内容列挂到根布局的右列
            root.Controls.Add(mainCol, 1, 0);

            // ---- 二级页 ① K线：图表撑满、不滚动（滚轮只缩放，不与翻页冲突）----
            _stockStrip.Add("K线", BuildStockKlinePanel());

            // ---- 二级页 ② 基本信息：行业 / 市值 / 前十大股东 / 涨停与连板（/api/stock/profile）----
            _stockStrip.Add("基本信息", BuildStockBasicPanel());

            // ---- 二级页 ③ 记录 · 消息 ----
            _stockStrip.Add("记录 · 消息", BuildStockRecordsPanel());

            // ---- 二级页 ③ 估值（完整过程见估值计算标签页）----
            _stockStrip.Add("估值", BuildStockValuationPanel());

            // ---- 二级页 ③.5 扫雷（通达信「扫雷宝 · 个股亮点」，来自 /api/stock/saolei）----
            _stockStrip.Add("扫雷", BuildStockSaoleiPanel());

            // ---- 二级页 ④ AI 分析（调 /api/stock/research）----
            _stockStrip.Add("AI 分析", BuildStockAiPanel());

            // ---- 二级页 ⑤ AI 分析偏好（作为投喂变量，自动存本地）----
            _stockStrip.Add("偏好设置", BuildStockAiPrefPanel());

            // ---- 二级页 ⑥ 指标索引（目录自检：索引是否重复 + 输入输出数据是否正确）----
            _stockStrip.Add("指标索引", BuildStockIndicatorIndexPage());
            _stockIndSubIndex = _stockStrip.Count - 1;

            _stockStrip.Select(0);

            StockApplyRange();
            StockSetAdjustActive();
            StockSetPeriodActive();

            // 历史查看记录：从 stockanaly-data 加载并渲染左侧导航
            StockLoadHistoryFile();
            StockRenderHistoryNav();
            return p;
        }

        // ===== 个股页骨架：页头 + 查询工具区 =====
        // 各方法返回「已构建好的控件」，由 BuildStockPage() 负责组装与接线。
        // 各二级页的构建方法已按二级页分文件挪走：
        //   ① K线 → StockPage.Kline.cs      ② 基本信息 → StockPage.Profile.cs
        //   ③ 记录·消息 → StockPage.Records.cs   ③ 估值 → StockPage.Valuation.cs
        //   ③.5 扫雷 → StockPage.Saolei.cs      ④⑤ AI 分析 / 偏好 → StockPage.AI.cs

        /// <summary>标题行（图标 + 「个股分析」）。</summary>
        private Control BuildStockHead()
        {
            // 原先这里还有一行「前复权/不复权日K…｜K线页：滚轮缩放 · 拖动平移」的说明，
            // 已按需求移除：内容早已过时（现在有周/月周期与键盘操作），
            // 且省下的高度归 K 线（标题行是 AutoSize）。
            var head = Stack();
            var title = Lbl("📊 个股分析");
            title.Font = new Font("Microsoft YaHei UI", 14f, FontStyle.Bold);
            title.Margin = new Padding(0, 0, 0, 2);
            AddRow(head, Row(title));
            return head;
        }

        /// <summary>查询工具区：当前个股 / 提示行 / 范围·周期·复权·刷新（始终可见，不随二级页滚动）。</summary>
        private Control BuildStockQueryBar()
        {
            // 「代码 / 指标」输入入口已统一收敛到右下角隐藏输入框（见 QuickSearch.cs）：
            // 敲数字 → 打开个股；敲字母 → 输指标英文 id（macd/rsi/rps…）切换副图。本区只展示状态。
            TableLayoutPanel b0;
            var g0 = Group("查询", out b0);
            _stockName = Mute(Lbl(""));
            AddRow(b0, Row(Lbl("当前个股"), _stockName));

            _stockHint = Mute(Lbl("敲任意数字 / 字母 → 右下角输入框：6 位代码打开个股；输指标英文代码（ma / cyc / macd / rsi / rps）回车切换主图 / 副图指标"));

            _stockHint.AutoSize = true;
            _stockHint.MaximumSize = new Size(740, 0);
            AddRow(b0, Row(_stockHint));

            // 范围按钮 + 刷新 + 状态
            // 「范围」按**时间**定义（近6月 / 近1年 / 全部），存的是档位而非根数——
            // 换周期后根数要跟着变：日线近1年≈250根，周线≈52根，月线≈12根。
            // 若直接存根数，周线下按「近1年」会画出 5 年，标签就骗人了。
            var rangeLabels = new string[] { "近6月", "近1年", "全部" };
            var rangeVals = new int[] { 0, 1, 2 };
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
                    _stockRangeIdx = captured;
                    StockApplyRange();
                    StockSessionSave();            // 记住范围档位
                    if (_stockCurrent != null) StockLoadChip(_stockCurrent);   // 窗口变了，筹码重新算
                };
                _stockRangeMap[btn] = r;
            }
            _stockRefresh = MiniBtn("↻ 更新行情/消息", delegate { if (_stockCurrent != null) StockLoadHistory(_stockCurrent, true); }, 140);
            _stockStatus = Mute(Lbl("待加载"));
            _stockIndStatus = Mute(Lbl(""));

            // 复权切换（前复权 / 不复权）——先建按钮，再与「范围」并入同一行
            var adjustLabels = new string[] { "前复权", "不复权" };
            var adjustVals = new string[] { "qfq", "raw" };
            for (int ai = 0; ai < adjustVals.Length; ai++)
            {
                string a = adjustVals[ai];
                var btn = new Button();
                btn.Text = adjustLabels[ai];
                btn.Tag = "stock-adjust";
                btn.AutoSize = false;
                btn.Height = 26;
                btn.Width = Math.Max(72, TextRenderer.MeasureText(adjustLabels[ai], Font).Width + 22);
                btn.FlatStyle = FlatStyle.Flat;
                btn.FlatAppearance.BorderSize = 0;
                btn.Font = new Font("Microsoft YaHei UI", 9f);
                btn.TabStop = false;
                btn.Margin = new Padding(0, 0, 8, 0);
                string cap = a;
                btn.Click += delegate
                {
                    _stockAdjust = cap;
                    StockSetAdjustActive();
                    StockSessionSave();            // 记住复权口径
                    if (_stockKline != null)
                    {
                        _stockKline.SetAdjust(_stockAdjust);
                        _stockKline.FocusForKeys();
                    }
                    if (_stockCurrent != null) StockLoadChip(_stockCurrent);   // 价格口径变了，筹码跟着变
                };
                _stockAdjustMap[btn] = a;
            }

            // ---- 周期（日 / 周 / 月）----
            // 周线 / 月线由后端在读取时把日线合样（日线是唯一落库口径），
            // 指标与筹码也必须按同一周期重算，否则周线蜡烛会配上日线 MA。
            var periodLabels = new string[] { "日线", "周线", "月线" };
            var periodVals = new string[] { "day", "week", "month" };
            for (int pi = 0; pi < periodVals.Length; pi++)
            {
                string pv = periodVals[pi];
                var btn = new Button();
                btn.Text = periodLabels[pi];
                btn.Tag = "stock-period";
                btn.AutoSize = false;
                btn.Height = 26;
                btn.Width = Math.Max(52, TextRenderer.MeasureText(periodLabels[pi], Font).Width + 18);
                btn.FlatStyle = FlatStyle.Flat;
                btn.FlatAppearance.BorderSize = 0;
                btn.Font = new Font("Microsoft YaHei UI", 9f);
                btn.TabStop = false;
                btn.Margin = new Padding(0, 0, 8, 0);
                string cap = pv;
                btn.Click += delegate { StockSetPeriod(cap); };
                _stockPeriodMap[btn] = pv;
            }

            // 范围 + 周期 + 复权 + 刷新同处一行
            var rangeRow = Row(Mute(Lbl("范围")));
            foreach (Button b in _stockRangeMap.Keys) rangeRow.Controls.Add(b);
            rangeRow.Controls.Add(Mute(Lbl("周期")));
            foreach (Button b in _stockPeriodMap.Keys) rangeRow.Controls.Add(b);
            rangeRow.Controls.Add(Mute(Lbl("复权")));
            foreach (Button b in _stockAdjustMap.Keys) rangeRow.Controls.Add(b);
            rangeRow.Controls.Add(_stockRefresh);
            rangeRow.Controls.Add(_stockStatus);
            rangeRow.Controls.Add(_stockIndStatus);
            rangeRow.Controls.Add(Mute(Lbl("  ↑↓ 缩放 · ←→ 移动光标")));
            AddRow(b0, rangeRow);
            return g0;
        }

        // ---- 打开（输入来自右下角隐藏输入框；见 QuickSearch.cs）----
        //
        // ⚠️ 代码**显式传进来**，不再从 `_quickSearch.Text` 里读：
        //   输入框文本会被 `SelectTab` 内部的 `HideQuickSearch()` 清空（那是「切页时
        //   顺手收起输入框」的副作用）→ 于是「先设文本 → SelectTab → StockOpen」这条
        //   老链路读到的其实是**空串** → 走错分支（当成指标名）→ 股票根本没换。
        //   2026-11-10 实测：输入 600812，IND 日志仍是上一只 002285、页面也没切。
        //   文本只当「用户输入的内容」，不当「传参通道」。
        private void StockOpen() { StockOpen(null); }

        /// <summary>打开个股 / 切换指标。`code` 为 null 时取输入框文本（兼容旧调用方）。</summary>
        private void StockOpen(string code)
        {
            if (code == null) code = _quickSearch.Text;
            code = (code ?? "").Trim();
            _quickActiveCode = code;                    // 记入激活字段，供异步守卫使用（与输入框文本解耦）
            if (!Regex.IsMatch(code, "^[0-9]{6}$"))
            {
                // 非 6 位代码：当作「指标名称 / id」尝试切换主图 / 副图指标
                bool isMain;
                string id = StockResolveAnyId(code, out isMain);
                if (id != null)
                {
                    string nm = StockIndicatorNameOf(id) ?? id;
                    _stockHint.Tag = "muted";
                    _stockHint.ForeColor = C.Flat;
                    if (isMain)
                    {
                        if (_stockMainIndicator == id)
                        {   // 再输一次同一主图指标 → 关闭
                            _stockMainIndicator = null;
                            _stockHint.Text = "主图指标已关闭（曾用：" + nm + "）";
                        }
                        else
                        {
                            _stockMainIndicator = id;
                            _stockHint.Text = "主图指标已切换为：" + nm + "（再输一次可关闭；输入 6 位代码则打开个股）";
                        }
                    }
                    else
                    {
                        _stockSubIndicator = id;
                        _stockHint.Text = "副图指标已切换为：" + nm + "（输入 6 位代码则打开个股）";
                    }
                    StockSessionSave();   // 记住主图 / 副图指标选择
                    if (_stockCurrent != null) StockLoadIndicators(_stockCurrent);
                    StockPersistSelection();
                    return;
                }
                // 筹码模型（独立通道）：索引名 → 切换 K 线右侧筹码窗口的公式
                string chipId = StockResolveChipId(code);
                if (chipId != null)
                {
                    _stockChipFormulaId = chipId;
                    _stockHint.Tag = "muted";
                    _stockHint.ForeColor = C.Flat;
                    _stockHint.Text = "筹码模型已切换为：" + (StockChipNameOf(chipId) ?? chipId)
                                    + "（输入 6 位代码则打开个股）";
                    StockSessionSave();   // 记住筹码公式选择
                    if (_stockCurrent != null) StockLoadChip(_stockCurrent);
                    StockPersistSelection();
                    return;
                }
                _stockHint.Text = "股票代码须是 6 位数字（如 600519）；或输入已注册指标名以切换主图 / 副图指标";
                _stockHint.Tag = "bad";
                _stockHint.ForeColor = C.UpErr;
                _quickSearch.Focus();
                return;
            }
            if ((_stockHint.Tag as string) != "bad")
            {
                _stockHint.Tag = "muted";
                _stockHint.ForeColor = C.Flat;
                _stockHint.Text = "数据加载中…";
            }
            _stockCurrent = code;
            // 切换股票：先把上一只的 K线 / 指标 / 筹码清掉，否则异步加载期间
            // 旧图还停在屏幕上，看起来像「没切过来」（指标同理）。
            if (_stockKline != null)
            {
                _stockKline.SetData(new List<KBar>(), new List<KBar>(), new List<KMark>());
                _stockKline.SetIndicators(null);
                _stockKline.SetMainIndicators(null);
            }
            if (_stockChip != null) _stockChip.SetStatus("筹码未加载", false);
            StockSessionSave();            // 记住上次打开的个股
            StockRefreshIndicatorList();   // 每次打开同步后端指标清单（后端可能刚启动）
            StockAddHistory(code, null);   // 记录查看历史（名称稍后补全）
            StockLoadName(code);
            // 先用库里已有数据立即加载（秒出 K线 / 筹码 / 基本面），避免打开后白屏等补齐行情。
            StockLoadRest(code);
            // 第 24 项（改为后台补齐）：上面那串已先出图；这里后台确认 / 补齐最新行情
            // （陈旧时 sync 约 2 秒），补齐完再走一遍 StockLoadRest 刷新到最新。
            StockEnsureFresh(code, delegate { StockLoadRest(code); });
        }

        // ---- 上次界面状态（settings.json · last_session）----
        // 只记界面偏好（个股 / 周期 / 主副图指标 / 筹码公式 / 范围 / 复权），绝不记行情或筹码矩阵。
        // 写入点全是低频用户操作（切股 / 切周期 / 换指标 / 换筹码 / 点范围 / 点复权），
        // 不在滚轮缩放等高频事件里落盘。

        /// <summary>把当前个股页状态写进 settings.json 的 last_session。</summary>
        private void StockSessionSave()
        {
            try
            {
                UserSettings.Current.LastSession = new LastSessionData
                {
                    code = _stockCurrent ?? "",
                    period = string.IsNullOrEmpty(_stockPeriod) ? "day" : _stockPeriod,
                    main_indicator = _stockMainIndicator ?? "",
                    sub_indicator = _stockSubIndicator ?? "",
                    chip_formula = _stockChipFormulaId ?? "",
                    range_idx = _stockRangeIdx,
                    range_bars = _stockRange,
                    adjust = string.IsNullOrEmpty(_stockAdjust) ? "qfq" : _stockAdjust,
                };
                UserSettings.Save();
            }
            catch { }
        }

        private bool _stockSessionRestored;

        /// <summary>后端就绪后恢复上次界面状态（只做一次；无记录或指标已删则静默跳过）。</summary>
        private void StockRestoreSession()
        {
            if (_stockSessionRestored) return;
            var ls = UserSettings.Current.LastSession;
            if (ls == null) return;
            _stockSessionRestored = true;
            try
            {
                if (ls.period == "day" || ls.period == "week" || ls.period == "month")
                    _stockPeriod = ls.period;
                _stockMainIndicator = string.IsNullOrEmpty(ls.main_indicator) ? null : ls.main_indicator;
                _stockSubIndicator = string.IsNullOrEmpty(ls.sub_indicator) ? null : ls.sub_indicator;
                _stockChipFormulaId = string.IsNullOrEmpty(ls.chip_formula) ? null : ls.chip_formula;
                if (ls.adjust == "qfq" || ls.adjust == "raw") _stockAdjust = ls.adjust;
                if (ls.range_idx.HasValue) _stockRangeIdx = ls.range_idx.Value;
                if (_stockRangeIdx == -1 && ls.range_bars.HasValue && ls.range_bars.Value > 0)
                    _stockRange = ls.range_bars.Value;
                else if (ls.range_idx.HasValue)
                    _stockRange = StockRangeBars();
                // 按钮高亮 / 图表口径与恢复值对齐
                StockSetPeriodActive();
                StockSetRangeActive();
                StockSetAdjustActive();
                if (_stockKline != null) _stockKline.SetAdjust(_stockAdjust);
                // 启动早期后端未就绪，指标 / 筹码清单首次拉取会失败，这里补一次
                StockRefreshIndicatorList();
                StockLoadChipFormulas();
                // 打开上次的个股：仅在还没打开任何票时，避免覆盖用户刚做的操作
                if (_stockCurrent == null && !string.IsNullOrEmpty(ls.code) && Regex.IsMatch(ls.code, "^[0-9]{6}$"))
                {
                    StockOpen(ls.code);
                }
            }
            catch { }
        }

        /// <summary>
        /// 第 24 项：确保该票的行情数据新到应有交易日，陈旧则**同步补齐**后回调。
        /// 后端 `sync-code` 实测约 2 秒；失败不阻塞后续加载（宁可显示旧数据，
        /// 也不要整页打不开），只在状态行提示。
        /// </summary>
        private void StockEnsureFresh(string code, Action then)
        {
            System.Threading.Tasks.Task.Run(delegate
            {
                bool need = false;
                try
                {
                    string url = "http://127.0.0.1:8000/api/history/download/freshness?codes=" + code;
                    var fj = new JavaScriptSerializer()
                        .Deserialize<Dictionary<string, object>>(VRequest(url, null, 20000));
                    need = fj.ContainsKey("need_refresh") && fj["need_refresh"] is bool
                           && (bool)fj["need_refresh"];
                }
                catch (Exception) { need = false; }   // 检查失败就当不需要，不挡路
                if (!need) { Ui(then); return; }

                Invoke((Action)(() =>
                {
                    _stockHint.Text = "数据已过期，正在补齐 " + code + " 的最新行情（约 2~5 秒）…";
                    _stockHint.Tag = "muted";
                    _stockHint.ForeColor = _cSub;
                }));
                try
                {
                    VRequest("http://127.0.0.1:8000/api/history/download/sync-code",
                             "{\"code\":\"" + code + "\"}", 120000);
                }
                catch (Exception ex)
                {
                    string m = ex.Message;
                    Invoke((Action)(() =>
                    {
                        _stockHint.Text = "补齐数据失败（仍显示已有数据）：" + m;
                        _stockHint.Tag = "bad";
                        _stockHint.ForeColor = C.UpErr;
                    }));
                }
                Ui(then);
            });
        }

        /// <summary>第 24 项：个股页「确认数据新鲜之后」要加载的那一串。</summary>
        private void StockLoadRest(string code)
        {
            if (_stockCurrent != code) return;      // 期间又切了别的票就作废
            StockLoadHistory(code, false);
            StockLoadChipFormulas();   // 每次打开同步公式清单（后端可能刚加过公式）
            StockLoadChip(code);
            StockLoadBasic(code);      // 基本信息（行业 / 市值 / 前十大股东 / 涨停连板）
            StockLoadBoards(code);     // K 线上方板块标注（行业/概念/地域）
            StockLoadValuation(code);
            StockLoadSaolei(code);
            StockLoadAi(code);
        }

        private void StockLoadName(string code)
        {
            FetchStockName(code,
                delegate(string name, string display)
                {
                    if (_quickActiveCode != code) return;          // 竞态守卫：已换别的代码 / 输入框已清空
                    _stockName.Text = display;
                    StockAddHistory(code, name);                  // 补全历史记录中的名称
                },
                delegate(string err)
                {
                    if (_quickActiveCode != code) return;
                    _stockName.Text = err;
                });
        }

        // ---- 历史（K线 / 轨迹 / 消息）----

        private void StockFail(string msg)
        {
            _stockStatus.Text = "加载失败：" + msg;
            _stockStatus.Tag = "bad";
            _stockStatus.ForeColor = C.UpErr;
            _stockKline.SetData(new List<KBar>(), new List<KBar>(), new List<KMark>());
            // 右侧筹码窗口同步清空，避免还留着上一只股票的分布
            if (_stockChip != null) _stockChip.SetStatus("筹码未加载", false);
        }

        // ---- 指标：主图 / 副图均固定单指标，由右下角输入框键入指标名 / id 替换 → 后端计算 → 按日期对齐 → 叠加到 K 线 ----
        // 解析与替换的实际逻辑在 StockOpen() 内（复用同一输入框，无需独立方法）。

        /// <summary>输入 → 指标 id（主图或副图）。先精确（名称、id），再前缀，最后包含；
        /// 同层副图优先（macd / rsi 等长 id 不被主图短 id 截胡）。isMain 区分主图 / 副图。</summary>
        private string StockResolveAnyId(string typed, out bool isMain)
        {
            isMain = false;
            if (_stockMainOpts.Count == 0 && _stockSubOpts.Count == 0) return null;   // 清单未就绪：不接受非代码输入
            foreach (var o in _stockSubOpts) if (string.Equals(o.Name, typed, StringComparison.OrdinalIgnoreCase) || string.Equals(o.Id, typed, StringComparison.OrdinalIgnoreCase)) return o.Id;
            foreach (var o in _stockMainOpts) if (string.Equals(o.Name, typed, StringComparison.OrdinalIgnoreCase) || string.Equals(o.Id, typed, StringComparison.OrdinalIgnoreCase)) { isMain = true; return o.Id; }
            foreach (var o in _stockSubOpts)
                if (o.Name.StartsWith(typed, StringComparison.OrdinalIgnoreCase)) return o.Id;
            foreach (var o in _stockSubOpts)
                if (o.Id.StartsWith(typed, StringComparison.OrdinalIgnoreCase)) return o.Id;
            foreach (var o in _stockMainOpts)
                if (o.Name.StartsWith(typed, StringComparison.OrdinalIgnoreCase) || o.Id.StartsWith(typed, StringComparison.OrdinalIgnoreCase))
                { isMain = true; return o.Id; }
            foreach (var o in _stockSubOpts)
                if (o.Name.IndexOf(typed, StringComparison.OrdinalIgnoreCase) >= 0) return o.Id;
            foreach (var o in _stockMainOpts)
                if (o.Name.IndexOf(typed, StringComparison.OrdinalIgnoreCase) >= 0)
                { isMain = true; return o.Id; }
            return null;
        }

        /// <summary>把当前的指标 / 筹码选择写入 settings（复用 Launcher 的 _cfg* 字段 + SaveSettingsQuiet，
        /// 与 theme/scope 走同一条持久化链路，避免「只存不读」导致重启后不生效）。</summary>
        private void StockPersistSelection()
        {
            _cfgChipFormula = _stockChipFormulaId;
            _cfgMainIndicator = _stockMainIndicator;
            _cfgSubIndicator = _stockSubIndicator;
            SaveSettingsQuiet();
        }

        /// <summary>输入 → 筹码公式 id（独立于指标通道）。匹配顺序同指标：先精确（索引名 / 名称 / 公式 id），
        /// 再前缀，最后按名称包含；命中返回**后端公式 id**（拼 &amp;formula= 用），未命中返回 null。</summary>
        private string StockResolveChipId(string typed)
        {
            if (_stockChipOpts.Count == 0) return null;
            foreach (var o in _stockChipOpts)
                if (string.Equals(o.Id, typed, StringComparison.OrdinalIgnoreCase)
                 || string.Equals(o.Name, typed, StringComparison.OrdinalIgnoreCase)
                 || string.Equals(o.Raw, typed, StringComparison.OrdinalIgnoreCase))
                    return o.Raw ?? o.Id;
            foreach (var o in _stockChipOpts)
                if (o.Id.StartsWith(typed, StringComparison.OrdinalIgnoreCase)
                 || (o.Raw != null && o.Raw.StartsWith(typed, StringComparison.OrdinalIgnoreCase)))
                    return o.Raw ?? o.Id;
            foreach (var o in _stockChipOpts)
                if (o.Name.IndexOf(typed, StringComparison.OrdinalIgnoreCase) >= 0)
                    return o.Raw ?? o.Id;
            return null;
        }

        /// <summary>筹码公式 id → 显示名（找不到返回 null）。</summary>
        private string StockChipNameOf(string rawId)
        {
            if (string.IsNullOrEmpty(rawId)) return null;
            foreach (var o in _stockChipOpts)
                if (o.Raw == rawId || o.Id == rawId) return o.Name;
            return null;
        }

        /// <summary>指标 id → 规范名称（主图 + 副图一起查，找不到返回 null）。</summary>

        // 指标联想下拉逻辑见 QuickSearch.cs（与右下角输入框共用 StockSug* 方法）。

        // 从后端自动枚举指标清单：panel=main → 主图候选；panel≠main/none → 副图候选（灌进输入框联想下拉）。

        /// <summary>后端指标清单 → 主图 / 副图候选（供右下角输入框解析与联想下拉，不再生成勾选框）。</summary>

        // ---- 筹码分布（K 线右侧窗口）----
        // 与指标体系的「按日期对齐的序列」不同，筹码分布是「按价位分布的直方图」，
        // 因此不走指标面板，单独向 /api/chip/dist 取数、单独渲染。
        // ---- 所属板块（基本信息页，通达信 HYBLOCK/GNBLOCK/DYBLOCK 风格）----
        // 数据来自 /api/stock/boards：新浪板块体系预计算索引（行业 / 概念 / 地域），
        // 每类内按成分股数量升序——成分越少越「专属」，第一个即最相关板块（加 ★）。
        // 用 Label 而非 FlowLayoutPanel：后者的 AutoSize 首选高度在表格里测量不稳，
        // 会把 Group 撑出一大块空白；Label + MaximumSize 限宽换行的高度计算是可靠的。

        /// <summary>索引正在后台重建时，隔几秒重查一次直到建好（重建约 1 分钟）。</summary>
        private void ScheduleBoardsRetry()
        {
            Debounce(ref _boardsTimer, 6000, delegate
            {
                if (_stockCurrent != null) StockLoadBoards(_stockCurrent);
            });
        }

        private void StockRebuildBoards(object sender, EventArgs e)
        {
            if (_stockCurrent != null) StockLoadBoards(_stockCurrent, true);
        }

        /// <summary>拼一行「标题： ★最相关 其余…」。板块很多时截断，超出部分显示省略。</summary>

        // ---- 基本信息（/api/stock/profile）：行业 / 市值 / 前十大股东 / 涨停与连板 ----

        // ---- 筹码模型：后端 /api/chip/dist/formulas 枚举 ----
        // 公式是「一个文件一个公式、文件名即 id」，新增公式无需改前端，这里自动同步成
        // 右下角输入框的候选（键入索引名即切换，原下拉框已移除）。

        /// <summary>滚轮缩放会连续改变可见根数，防抖后再重算筹码，避免每滚一格就发一次请求。</summary>

        /// <summary>
        /// 解析逐日快照 frames：dates[i] / pct[i] / stats[i] 一一对应，
        /// 光标左右移动时前端只切索引，不再回服务端。
        /// 后端没给 frames（老版本 / 异常）时，用最后一帧兜底成单帧，不至于开天窗。
        /// </summary>

        // 把后端返回的指标序列按 K 线日期对齐为等长数组（缺失填 NaN）

        // RPS 副图高亮阈值 M：本项目约定 M = 90（RPS ≥ 90 = 全市场前 10% 强势）。
        private const double RpsHiThreshold = 90.0;
        private static readonly Color RpsHiRed = C.Up;

        /// <summary>
        /// 按通达信 RPS 副图公式给序列配色：120 绿 / 250 白 / 50 黄 / 20 灰 / 10 浅青，
        /// 并开启「数值 ≥ M(90) 转红」高亮；其余指标沿用 <see cref="LineColor"/>。
        /// 对齐公式：RPS120:...,COLORGREEN; IF(RPS120&gt;=M,RPS120,DRAWNULL),COLORRED;
        /// </summary>

        // ---- 扫雷（通达信个股亮点，内联）----

        /// <summary>扫雷清单里的一个分类列：标题 + 条目（名称 / 有·无）。</summary>

        /// <summary>把 JSON 里的整数（可能是 int / decimal / string）安全转成 int。</summary>

        // ---- 估值（内联）----

        // ---- AI 分析（调 /api/stock/research，返回 AI 生成的 markdown）----

        // ---- AI 分析偏好：本地持久化（JSON，存于 ApplicationData\StockPoolLauncher）----

        private void StockLoadAiPrefs()
        {
            try
            {
                string p = AiPrefsPath();
                if (File.Exists(p))
                {
                    var d = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(p, Encoding.UTF8));
                    if (d != null)
                    {
                        if (d.ContainsKey("depth"))
                        {
                            var ds = d["depth"] as string;
                            if (ds != null && (ds == "concise" || ds == "normal" || ds == "detailed")) _aiDepth = ds;
                        }
                        if (d.ContainsKey("horizon"))
                        {
                            var hs = d["horizon"] as string;
                            if (hs != null && (hs == "short" || hs == "mid" || hs == "long")) _aiHorizon = hs;
                        }
                        if (d.ContainsKey("focus"))
                        {
                            var fa = d["focus"] as System.Collections.ArrayList;
                            if (fa != null)
                            {
                                var lst = new List<string>();
                                foreach (var x in fa) { var s = x as string; if (s != null) lst.Add(s); }
                                _aiFocus = lst;
                            }
                        }
                    }
                }
            }
            catch { }
        }

        private void StockSaveAiPrefs()
        {
            try
            {
                var d = new Dictionary<string, object> { { "depth", _aiDepth }, { "horizon", _aiHorizon }, { "focus", _aiFocus } };
                File.WriteAllText(AiPrefsPath(), new JavaScriptSerializer().Serialize(d), Encoding.UTF8);
            }
            catch { }
        }

        private void StockAiPrefChanged()
        {
            if (_stockCurrent != null) StockLoadAi(_stockCurrent, true);
        }

        private void StockRadioGroup(TableLayoutPanel b, string[] labels, string[] values, string current, Action<string> onPick)
        {
            for (int i = 0; i < labels.Length; i++)
            {
                var rb = new RadioButton();
                rb.Text = labels[i];
                rb.AutoSize = true;
                rb.Checked = (values[i] == current);
                var v = values[i];
                rb.CheckedChanged += delegate { if (rb.Checked) onPick(v); };
                AddRow(b, rb);
            }
        }

        /// <summary>把 AI 调研的 markdown 渲染进只读框；换肤时由 Skin 再调一次，用新配色重排。</summary>

        /// <summary>按 **加粗** 标记分段写入 RichTextBox（行内加粗）。</summary>

        /// <summary>跳到「估值计算」标签页并把代码填进去重算（完整过程在那一页）。</summary>

        // ---- 范围按钮配色（跟随主题）----
        private void StockSetRangeActive()
        {
            foreach (KeyValuePair<Button, int> kv in _stockRangeMap)
            {
                bool act = kv.Value == _stockRangeIdx;      // 比的是档位，不是根数
                kv.Key.BackColor = act ? C.Accent : _cPanel;
                kv.Key.ForeColor = act ? Color.White : _cSub;
                kv.Key.Invalidate();
            }
        }

        /// <summary>「范围」档位 → 当前周期下的根数（档位 2 = 全部 → 0 根，即不限）。</summary>
        private int StockRangeBars()
        {
            if (_stockRangeIdx == 2) return 0;
            bool half = _stockRangeIdx == 0;                // 近6月 : 近1年
            if (_stockPeriod == "week") return half ? 26 : 52;
            if (_stockPeriod == "month") return half ? 6 : 12;
            return half ? 120 : 250;
        }

        /// <summary>范围档位或周期变化后重算实际根数并应用到图表。</summary>
        private void StockApplyRange()
        {
            _stockRange = StockRangeBars();
            StockSetRangeActive();
            if (_stockKline != null)
            {
                _stockKline.SetRange(_stockRange);
                _stockKline.FocusForKeys();                 // 点完按钮焦点还给图表，方向键接着用
            }
        }

        private void StockSetAdjustActive()
        {
            foreach (KeyValuePair<Button, string> kv in _stockAdjustMap)
            {
                bool act = kv.Value == _stockAdjust;
                kv.Key.BackColor = act ? C.Accent : _cPanel;
                kv.Key.ForeColor = act ? Color.White : _cSub;
                kv.Key.Invalidate();
            }
        }

        private void StockSetPeriodActive()
        {
            foreach (KeyValuePair<Button, string> kv in _stockPeriodMap)
            {
                bool act = kv.Value == _stockPeriod;
                kv.Key.BackColor = act ? C.Accent : _cPanel;
                kv.Key.ForeColor = act ? Color.White : _cSub;
                kv.Key.Invalidate();
            }
        }

        /// <summary>切换 K 线周期（日 / 周 / 月）：K线、指标、筹码三处都要用同一周期重算，
        /// 否则周线蜡烛会配上日线 MA（MA5 变 5 日）或日线筹码。</summary>
        private void StockSetPeriod(string period)
        {
            if (_stockPeriod == period) return;
            _stockPeriod = period;
            StockSetPeriodActive();
            StockSessionSave();            // 记住 K 线周期
            if (_stockCurrent != null)
            {
                StockLoadHistory(_stockCurrent, false);   // 会顺带重算指标（见 StockRenderHistory）
                StockLoadChip(_stockCurrent);
            }
            if (_stockKline != null) _stockKline.FocusForKeys();
        }

        /// <summary>F8 循环切换 K 线周期：日 → 周 → 月 → 日（K线、指标、筹码随周期一起重算）。</summary>
        private void StockCyclePeriod()
        {
            string next = _stockPeriod == "day" ? "week" : (_stockPeriod == "week" ? "month" : "day");
            StockSetPeriod(next);
        }

        // ---- 历史查看记录（左侧可折叠导航，持久化到 stockanaly-data）----

        private void StockToggleHistory()
        {
            _stockHistoryExpanded = !_stockHistoryExpanded;
            if (_stockHistoryRoot != null)
            {
                _stockHistoryRoot.ColumnStyles[0] = new ColumnStyle(SizeType.Absolute, _stockHistoryExpanded ? 170f : 32f);
                _stockHistoryRoot.PerformLayout();
            }
            if (_stockHistoryHeader != null) _stockHistoryHeader.Visible = _stockHistoryExpanded;
            if (_stockHistoryList != null) _stockHistoryList.Visible = _stockHistoryExpanded;
            if (_stockHistoryToggle != null) _stockHistoryToggle.Text = _stockHistoryExpanded ? "‹" : "›";
        }

        private void StockRenderHistoryNav()
        {
            if (_stockHistoryList == null) return;
            _stockHistoryList.Controls.Clear();
            _stockHistoryList.RowStyles.Clear();
            if (_stockHistory == null || _stockHistory.Count == 0)
            {
                var empty = Mute(Lbl("暂无查看记录"));
                AddRow(_stockHistoryList, empty);
                return;
            }
            foreach (StockHistoryItem h in _stockHistory)
            {
                var btn = new Button();
                btn.Dock = DockStyle.Top;
                btn.Height = 38;
                btn.FlatStyle = FlatStyle.Flat;
                btn.FlatAppearance.BorderSize = 0;
                btn.Font = new Font("Microsoft YaHei UI", 9f);
                btn.TextAlign = ContentAlignment.MiddleLeft;
                btn.TabStop = false;
                btn.Tag = h.Code;
                bool cur = (_stockCurrent == h.Code);
                btn.BackColor = cur ? C.Accent : _cPanel;
                btn.ForeColor = cur ? Color.White : _cText;
                string name = string.IsNullOrEmpty(h.Name) ? "" : ("  " + h.Name);
                btn.Text = h.Code + name;
                btn.Click += delegate { StockOpen(h.Code); _stockStrip.Select(0); };
                btn.MouseUp += delegate(object s, MouseEventArgs e)
                {
                    if (e.Button == MouseButtons.Right) StockRemoveHistory(h.Code);
                };
                AddRow(_stockHistoryList, btn);
            }
            var clear = MiniBtn("清空历史", delegate { StockClearHistory(); }, 0);
            clear.Dock = DockStyle.Top;
            clear.Margin = new Padding(0, 0, 0, 8);
            AddRow(_stockHistoryList, clear);
        }

        private void StockLoadHistoryFile()
        {
            _stockHistory = new List<StockHistoryItem>();
            try
            {
                _stockHistoryPath = Path.Combine(LauncherStateDir(), "history_stock_view.json");
                // 老版本把它写在数据目录里：新位置还没有时先读老位置，
                // 免得升级后「最近查看」凭空清空（下一次保存就会落到新位置）。
                string from = _stockHistoryPath;
                if (!File.Exists(from))
                {
                    // 老位置有两个：根目录（最早）与 config/（第 21 项分区后的位置），都试一遍
                    string root = ResolveDataDirSetting();
                    string[] legacyPaths = {
                        Path.Combine(root, "history_stock_view.json"),
                        Path.Combine(root, "config", "history_stock_view.json"),
                    };
                    foreach (string legacy in legacyPaths)
                    {
                        if (File.Exists(legacy)) { from = legacy; break; }
                    }
                }
                if (File.Exists(from))
                {
                    string txt = File.ReadAllText(from, Encoding.UTF8);
                    var arr = new JavaScriptSerializer().Deserialize<System.Collections.ArrayList>(txt);
                    if (arr != null)
                    {
                        foreach (Dictionary<string, object> d in arr)
                        {
                            var it = new StockHistoryItem();
                            it.Code = J.Str(J.Get(d, "code"));
                            it.Name = J.Str(J.Get(d, "name"));
                            object ts;
                            if (d.TryGetValue("ts", out ts) && ts != null)
                            {
                                try { it.Ts = Convert.ToInt64(ts); } catch (Exception) { it.Ts = 0; }
                            }
                            if (!string.IsNullOrEmpty(it.Code)) _stockHistory.Add(it);
                        }
                    }
                }
            }
            catch (Exception) { }
        }

        private void StockSaveHistoryFile()
        {
            try
            {
                if (string.IsNullOrEmpty(_stockHistoryPath)) return;
                string dir = Path.GetDirectoryName(_stockHistoryPath);
                if (!Directory.Exists(dir)) Directory.CreateDirectory(dir);
                var arr = new System.Collections.ArrayList();
                foreach (StockHistoryItem h in _stockHistory)
                {
                    var d = new Dictionary<string, object>();
                    d["code"] = h.Code;
                    d["name"] = h.Name ?? "";
                    d["ts"] = h.Ts;
                    arr.Add(d);
                }
                File.WriteAllText(_stockHistoryPath, new JavaScriptSerializer().Serialize(arr), Encoding.UTF8);
            }
            catch (Exception) { }
        }

        private void StockAddHistory(string code, string name)
        {
            if (string.IsNullOrEmpty(code) || !Regex.IsMatch(code, "^[0-9]{6}$")) return;
            StockHistoryItem ex = _stockHistory.Find(h => h.Code == code);
            if (ex != null) _stockHistory.Remove(ex);
            _stockHistory.Insert(0, new StockHistoryItem { Code = code, Name = name ?? "", Ts = DateTimeOffset.UtcNow.ToUnixTimeSeconds() });
            while (_stockHistory.Count > 10) _stockHistory.RemoveAt(_stockHistory.Count - 1);  // 滚动覆盖：超过 10 条丢弃最旧
            StockSaveHistoryFile();
            StockRenderHistoryNav();
        }

        private void StockRemoveHistory(string code)
        {
            StockHistoryItem ex = _stockHistory.Find(h => h.Code == code);
            if (ex != null) _stockHistory.Remove(ex);
            StockSaveHistoryFile();
            StockRenderHistoryNav();
        }

        private void StockClearHistory()
        {
            _stockHistory.Clear();
            StockSaveHistoryFile();
            StockRenderHistoryNav();
        }

        private void StockOnEnter()
        {
            // 个股页需要代码才能加载，进入时不自动拉取；仅确保图表按当前主题重绘。
            if (_stockKline != null) _stockKline.Invalidate();
            // 从别的顶级标签切进来时，重新挂载当前二级页并刷新，避免首屏不刷新
            if (_stockStrip != null)
            {
                _stockStrip.Select(_stockStrip.Index);
                _stockStrip.Body.PerformLayout();
                _stockStrip.Body.Invalidate(true);
            }
            StockRenderHistoryNav();   // 刷新左侧历史导航的当前项高亮
        }

        #endregion

    }
}
