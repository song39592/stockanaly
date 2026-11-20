using System;
using System.Collections.Generic;
using System.Drawing;
using System.Windows.Forms;

namespace StockPool
{
    /// <summary>
    /// 策略回测页的「基准（指数）对比」——**从「回测范围」页搬到了「回测结果」页**。
    /// </summary>
    /// <remarks>
    /// 为什么搬（三条理由，缺一条就不该搬）：
    ///   ① 基准是**结果的对照物**，不是回测的输入条件 —— 范围页里混着「池子 / 时间 /
    ///      资金」这些真正的输入，基准放那儿会让人以为「换个基准要重设回测条件」；
    ///   ② 基准只影响 alpha / beta / IR 的**计算**（净值图上那条金线**恒为**「等权全池
    ///      买入持有」，与选哪个指数无关），所以跑完再选也不影响已出的结果；
    ///   ③ 指数代码形态是「市场前缀 + 6 位数字」（sh000300），**裸 6 位会被后端当个股
    ///      去取**（000300 是只个股）→ alpha/beta 会算成个股的。旧默认值 "000300"
    ///      就是踩在这个坑上，且**界面上没有任何提示**（后端只是取不到就置 null）。
    ///
    /// ⚠ 口径边界：本模块**不改后端**。
    ///   · 请求体 `benchmark` = 本页选中的指数代码（没选 = null）；
    ///   · 后端用它算 alpha/beta/IR（`data.load_benchmark` → `stats` 的 `bench_close`），
    ///     而 `equity_benchmark` 曲线**恒为**等权全池买入持有 —— 两者别混；
    ///   · `scope.benchmark` 随报告落盘，历史重放按**报告里记录的**基准渲染，
    ///     不依赖当前页面选择（见 `StRenderResult` 读 `scope.benchmark`）。
    /// </remarks>
    internal sealed partial class MainForm
    {
        // ③ 回测结果：基准（指数）对比
        private ComboBox _stBenchIdx;
        /// <summary>与下拉项一一对应的指数代码（0 项 = 空 = 不对比）。</summary>
        private readonly List<string> _stBenchCodes = new List<string>();
        private Label _stBenchNote;
        /// <summary>本次**报告**用的基准（渲染时按报告里的 scope.benchmark 写）。</summary>
        private Label _stBenchReport;
        /// <summary>指数曲线的状态行（取不到行情等）。</summary>
        private Label _stIndexMsg;

        // ---- 指数收盘曲线（叠加到净值图上的那一条）----
        /// <summary>指数收盘价归一化后的曲线，与组合**同一纵轴、同一起始资金**。</summary>
        private List<double?> _stEqIndex;
        /// <summary>当前画出来的是哪个指数的曲线（空 = 没画）。</summary>
        private string _stEqIndexCode = "";
        /// <summary>指数日K 缓存（code → (日期, 收盘价)），换指数来回切时不再打后端。</summary>
        private readonly Dictionary<string, List<KeyValuePair<string, double>>> _stIndexCache =
            new Dictionary<string, List<KeyValuePair<string, double>>>();
        /// <summary>正在加载的指数代码（防重复请求）。</summary>
        private string _stIndexLoadingCode = "";
        /// <summary>报告的起始资金（指数曲线要用它归一化，才能与组合同轴比较）。</summary>
        private double _stInitialCapital;
        /// <summary>指数最后一条被用到的行情日期（用于说明末尾是否平线）。</summary>
        private string _stIndexLastDate = "";
        /// <summary>范围页上的**只读回显**：避免用户在范围页点「开始回测」时不知道带没带基准。</summary>
        private Label _stBenchEcho;
        private bool _stBenchListReady;
        /// <summary>SelectedIndexChanged 只订阅一次（重复订阅 → 保存偏好 / 重画曲线都跑多遍）。</summary>
        private bool _stBenchSubscribed = false;
        /// <summary>重填清单期间的中间态 → 抑制事件（否则清空 Items 会把已存的偏好清成空）。</summary>
        private bool _stBenchFilling = false;

        /// <summary>基准说明的默认文案（清单取不到时被替换，取到后要还原）。</summary>
        private const string BENCH_NOTE_DEFAULT =
            "图上那条金线恒为「等权全池买入持有」，与此处选择无关；"
            + "选指数只影响 alpha / beta / IR 的计算，且下次回测才生效。";

        /// <summary>结果页的「基准对比（指数）」分组（由 StBuildResult 插入）。</summary>
        private Control StBuildBenchGroup()
        {
            TableLayoutPanel body;
            var group = Group("基准对比（指数）", out body);

            _stBenchIdx = new ComboBox();
            _stBenchIdx.DropDownStyle = ComboBoxStyle.DropDownList;
            _stBenchIdx.FlatStyle = FlatStyle.Flat;
            _stBenchIdx.Width = 320;
            _stBenchIdx.Items.Add("不对比（只用等权全池买入持有）");
            _stBenchCodes.Add("");
            _stBenchIdx.SelectedIndex = 0;
            AddRow(body, Row(Lbl("对比基准"), _stBenchIdx));

            _stBenchNote = Mute(Lbl(BENCH_NOTE_DEFAULT));
            _stBenchNote.MaximumSize = new Size(900, 0);
            AddRow(body, Row(_stBenchNote));

            // 手动重载：后端晚起 / 清单取空时，用户能自己补一次（不必重启程序）。
            AddRow(body, Row(MiniBtn("重新载入指数清单", delegate { StReloadBenchList(); }, 150)));

            _stBenchReport = Mute(Lbl("本次报告：尚未回测"));
            _stBenchReport.MaximumSize = new Size(900, 0);
            AddRow(body, Row(_stBenchReport));

            _stIndexMsg = Mute(Lbl(""));
            _stIndexMsg.MaximumSize = new Size(900, 0);
            AddRow(body, Row(_stIndexMsg));

            StLoadBenchIndices();
            return group;
        }

        /// <summary>
        /// 取指数清单（复用个股页那一份 /api/history/indices，不重复实现）。
        ///
        /// ⚠ **失败不锁死**：启动器是「先起前端、再拉后端」，窗体构造阶段那一次请求
        ///   几乎必然失败（连接被拒）—— 锁死就等于「基准下拉里永远只有『不对比』」。
        ///   失败时把 _stBenchListReady 复位，等后端就绪后由 StReloadBenchList 重取。
        /// </summary>
        private void StLoadBenchIndices()
        {
            if (_stBenchIdx == null) return;
            if (_stBenchListReady) return;
            if (!IsHandleCreated)
            {
                // 窗体还没显示（句柄未建）→ 现在发请求几乎必然失败：
                // 启动器是「先起前端、后拉后端」，这会儿后端还没起来。
                // 等句柄建好（= 窗体已显示）再取，那会儿后端通常已经就绪。
                // 幂等：HandleCreated 可能触发多次，_stBenchListReady 守卫只取一次。
                HandleCreated += delegate { StLoadBenchIndices(); };
                return;
            }
            _stBenchListReady = true;
            StockLoadIndexList(delegate
            {
                // 清单是异步回来的，回调在 UI 线程（StockLoadIndexList 走 RunUi）。
                if (_stBenchIdx == null) return;
                bool got = _stockIndexOpts.Count > 0;
                _stBenchFilling = true;      // 重填期间的中间态 → 抑制事件
                try
                {
                    _stBenchIdx.Items.Clear();
                    _stBenchCodes.Clear();
                    _stBenchIdx.Items.Add("不对比（只用等权全池买入持有）");
                    _stBenchCodes.Add("");
                    foreach (var o in _stockIndexOpts)
                    {
                        if (string.IsNullOrEmpty(o.Code)) continue;
                        string label = o.Name + "（" + o.Code + "）";
                        // 本地没有行情的指数照样列出，但标出来 —— 选了它后端会取不到，
                        // alpha/beta 会是空（不标的话用户只会看到「指标不见了」）。
                        if (!o.Available) label += " · 本地无行情";
                        _stBenchIdx.Items.Add(label);
                        _stBenchCodes.Add(o.Code);
                    }
                    _stBenchIdx.SelectedIndex = 0;
                }
                finally { _stBenchFilling = false; }

                if (!got)
                {
                    // 取不到 → 不锁死（允许重试），并把原因说清楚
                    _stBenchListReady = false;
                    _stBenchNote.Text = "指数清单取不到（后端还没起来？点「重新载入指数清单」重试）；"
                        + "不选也能跑 —— 图上那条金线是「等权全池买入持有」。";
                }
                else
                {
                    _stBenchNote.Text = BENCH_NOTE_DEFAULT;
                    // 可观测：清单到底加载没加载成功，落在「运行日志」页里，
                    // 下次再出现「下拉里只有『不对比』」时能直接看证据，不用猜。
                    Log("系统", "基准对比：指数清单已加载 " + _stockIndexOpts.Count + " 项");
                }

                // 订阅放在清单填完之后：构造期设 SelectedIndex 会触发事件，
                // 那时用户上次的选择还没恢复 → 会把上次的偏好立刻清掉。
                // ⚠ 只订阅一次：重载清单会再走一遍这里，重复订阅会让
                //   「保存偏好 / 重画指数曲线」每次都跑多遍。
                if (!_stBenchSubscribed)
                {
                    _stBenchIdx.SelectedIndexChanged += delegate
                    {
                        if (_stBenchFilling) return;      // 重填清单的中间态 → 不动偏好
                        StSaveBenchPref();
                        StRefreshBenchEcho();
                        // 换指数 → **不重跑回测**，只重画那条指数曲线。
                        StLoadIndexCurve(StBenchCode());
                    };
                    _stBenchSubscribed = true;
                }
                StApplyBenchPref();
                StRefreshBenchEcho();
            });
        }

        /// <summary>
        /// 强制重载指数清单（后端就绪后 / 用户点「重新载入指数清单」时用）。
        ///
        /// 为什么要「强制」：清单请求失败时**不锁死**（见 StockLoadIndexList），
        /// 但仍受「失败后 3 秒内不重试」的限流约束 —— 后端刚起来时正好落在这个窗口里，
        /// 不强制就会白等一轮。
        /// </summary>
        internal void StReloadBenchList()
        {
            if (_stBenchIdx == null) return;             // 策略页还没构建（不需要）
            _stockIndexListLoaded = false;                // 允许重新取
            _stockIndexLastTry = DateTime.MinValue;       // 跳过重试间隔
            _stockIndexLoading = false;                   // 解锁：万一上一轮卡在「正在取」
            _stockIndexWaiters.Clear();                   // 清掉积压的等待者（避免重复刷下拉）
            _stBenchListReady = false;
            StLoadBenchIndices();
        }

        /// <summary>进入「回测结果」页时：清单还空着就重取一次（后端这时多半已就绪）。</summary>
        internal void StEnsureBenchList()
        {
            if (_stBenchIdx == null) return;
            if (_stockIndexOpts.Count > 0) return;
            StReloadBenchList();
        }

        /// <summary>恢复上次选的基准（settings.json 的 bench_code）。</summary>
        private void StApplyBenchPref()
        {
            if (_stBenchIdx == null) return;
            string want = UserSettings.Current.BenchCode;
            if (string.IsNullOrEmpty(want)) return;
            int i = _stBenchCodes.IndexOf(want);
            if (i > 0) _stBenchIdx.SelectedIndex = i;
        }

        private void StSaveBenchPref()
        {
            string code = StBenchCode();
            if (UserSettings.Current.BenchCode == code) return;
            UserSettings.Current.BenchCode = code;
            UserSettings.Save();
        }

        /// <summary>本次回测要用的基准指数代码（形如 sh000300）；没选 = null。</summary>
        private string StBenchCode()
        {
            if (_stBenchIdx == null || _stBenchIdx.SelectedIndex <= 0) return null;
            int i = _stBenchIdx.SelectedIndex;
            if (i >= _stBenchCodes.Count) return null;
            return _stBenchCodes[i];
        }

        /// <summary>基准的显示名（代码 + 中文名），用于回显与结果页标注。</summary>
        private string StBenchLabel(string code)
        {
            if (string.IsNullOrEmpty(code)) return "";
            string name = "";
            foreach (var o in _stockIndexOpts)
                if (o.Code == code) { name = o.Name; break; }
            return string.IsNullOrEmpty(name) ? code : (name + "（" + code + "）");
        }

        /// <summary>范围页上的只读回显：当前基准是什么（没选 = 不对比）。</summary>
        private void StRefreshBenchEcho()
        {
            if (_stBenchEcho == null) return;
            string code = StBenchCode();
            _stBenchEcho.Text = string.IsNullOrEmpty(code)
                ? "不对比（等权全池买入持有）"
                : StBenchLabel(code) + " · 下次回测生效";
        }

        /// <summary>
        /// 结果页的基准说明：**按报告里记录的** scope.benchmark 写，
        /// 不看「当前页面选了什么」—— 历史报告重放必须显示它当时用的基准。
        /// </summary>
        private string StBenchScopeNote(Dictionary<string, object> scope,
                                        Dictionary<string, object> metrics)
        {
            string code = "";
            if (scope != null) code = J.StrOr(J.Get(scope, "benchmark"), "");
            string head = string.IsNullOrEmpty(code)
                ? "未选指数基准（alpha / beta / IR 未计算；金线 = 等权全池买入持有）"
                : "基准 " + StBenchLabel(code)
                    + "（alpha / beta / IR 按该指数计算；金线仍是等权全池买入持有）";
            if (metrics == null) return head;
            // ⚠ 没选基准时**不能**说「该指数本地取不到行情」—— 压根就没有「该指数」，
            //   这句话会让用户以为是自己选的指数坏了（实测出现过这种误导文案）。
            if (string.IsNullOrEmpty(code)) return head;
            if (J.Get(metrics, "alpha") == null && J.Get(metrics, "beta") == null)
                return head + " —— ⚠ 该指数本地取不到行情，这三个指标为空";
            return head;
        }

        // ================ 指数收盘曲线（叠加到净值图上的那一条）================
        //
        // **只画收盘价、一条线** —— 指数是连续的、没有成交量也没有买卖盘，
        // 画成 K 线或多条叠加都只是徒增阅读成本。要的信息只有一条：
        // 「同一段时间里，指数涨了多少」→ 与组合同轴比。
        //
        // **数据来源**：前端自己算（`GET /api/history/index/{code}` 取日K 的收盘价）。
        // 为什么不在后端算：后端 `equity_benchmark` 的口径是「等权全池买入持有」
        // （`stats.py` 恒定如此），要让它变成「某条指数曲线」就是改后端口径 →
        // 影响所有历史报告的可比性；前端画一条**额外的**线则不碰任何口径。
        //
        // **归一化**：用报告的起始资金 `scope.initial_capital`，起点 = 报告区间内
        // 指数的**第一个有行情的收盘价** → 与组合同起点、同纵轴，可直接比高低。
        //
        // **日期对齐**：报告日期是 A 股交易日，指数日历偶尔不一致 → 用
        // **前向填充**（取 ≤ 该日的最近一根收盘价），不插值、不外推。
        //
        // **换指数不重跑回测**：曲线是前端叠加的，换下拉即可重画。

        /// <summary>指数中文名（不含代码，给图例用）。</summary>
        private string StBenchShortName(string code)
        {
            if (string.IsNullOrEmpty(code)) return "";
            foreach (var o in _stockIndexOpts)
                if (o.Code == code && !string.IsNullOrEmpty(o.Name)) return o.Name;
            return code;
        }

        /// <summary>加载并叠加某个指数的收盘曲线（不重跑回测）。</summary>
        private void StLoadIndexCurve(string code)
        {
            if (string.IsNullOrEmpty(code))
            {
                _stEqIndex = null;
                _stEqIndexCode = "";
                if (_stIndexMsg != null) _stIndexMsg.Text = "";
                if (_stEquityBox != null) _stEquityBox.Invalidate();
                return;
            }
            if (_stEqDates == null || _stEqDates.Count < 2)
            {
                // 还没有结果可对齐 → 等报告出来再画（StRenderResult 会再调一次）。
                return;
            }
            if (_stEqIndexCode == code && _stEqIndex != null) return;
            List<KeyValuePair<string, double>> bars;
            if (_stIndexCache.TryGetValue(code, out bars))
            {
                StApplyIndexCurve(code, bars);
                return;
            }
            if (_stIndexLoadingCode == code) return;
            _stIndexLoadingCode = code;
            if (_stIndexMsg != null)
                _stIndexMsg.Text = "正在取 " + StBenchShortName(code) + " 的日K…";
            RunUi<List<KeyValuePair<string, double>>>(
                delegate
                {
                    string body;
                    if (!GetText("http://127.0.0.1:8000/api/history/index/" + code
                                 + "?refresh=false&period=day", 20000, out body)
                        || string.IsNullOrEmpty(body))
                        return new List<KeyValuePair<string, double>>();
                    var j = J.Ser().Deserialize<Dictionary<string, object>>(body);
                    var arr = J.Arr(J.Get(j, "bars"));
                    var list = new List<KeyValuePair<string, double>>();
                    if (arr != null)
                        foreach (Dictionary<string, object> d in arr)
                        {
                            string dt = J.Str(J.Get(d, "trade_date"));
                            double c = J.NumOrNull(J.Get(d, "close")) ?? 0;
                            if (!string.IsNullOrEmpty(dt) && c > 0)
                                list.Add(new KeyValuePair<string, double>(dt, c));
                        }
                    list.Sort(delegate(KeyValuePair<string, double> a, KeyValuePair<string, double> b)
                    {
                        return string.CompareOrdinal(a.Key, b.Key);
                    });
                    return list;
                },
                delegate(List<KeyValuePair<string, double>> list)
                {
                    _stIndexLoadingCode = "";
                    if (list == null || list.Count < 2) { StIndexCurveFailed(code); return; }
                    _stIndexCache[code] = list;
                    StApplyIndexCurve(code, list);
                },
                delegate(Exception ex) { _stIndexLoadingCode = ""; StIndexCurveFailed(code); },
                null);
        }

        /// <summary>指数曲线应用到图上。</summary>
        private void StApplyIndexCurve(string code, List<KeyValuePair<string, double>> bars)
        {
            StAlignIndexCurve(bars);
            _stEqIndexCode = code;
            if (_stIndexMsg != null)
            {
                string tail = "";
                // 指数行情往往比报告区间短（实测只到最后一个已落盘的交易日）。
                // 末尾那几天按「不外推」处理 → 是平线；不说明的话会被当成「指数没涨」。
                if (!string.IsNullOrEmpty(_stIndexLastDate) && _stEqDates != null && _stEqDates.Count > 0
                    && string.CompareOrdinal(_stIndexLastDate, _stEqDates[_stEqDates.Count - 1]) < 0)
                    tail = "；指数行情只到 " + _stIndexLastDate + "，末尾几天是平的（不外推）";
                _stIndexMsg.Text = "已叠加 " + StBenchShortName(code) + "（" + code
                    + "，收盘价，与组合同轴）" + tail;
            }
            if (_stEquityBox != null) _stEquityBox.Invalidate();
        }

        /// <summary>指数行情取不到：说清楚为什么没线，别让图「莫名其妙少一条」。</summary>
        private void StIndexCurveFailed(string code)
        {
            _stEqIndex = null;
            _stEqIndexCode = "";
            if (_stIndexMsg != null)
                _stIndexMsg.Text = "⚠ " + StBenchShortName(code)
                    + " 的日K 取不到（本地没有该指数的行情）→ 没法叠加它的收盘曲线；"
                    + "alpha / beta / IR 也会是空";
            if (_stEquityBox != null) _stEquityBox.Invalidate();
        }

        /// <summary>把指数收盘价对齐到报告的交易日序列（**前向填充**，不插值不外推）。</summary>
        private void StAlignIndexCurve(List<KeyValuePair<string, double>> bars)
        {
            _stEqIndex = null;
            if (bars == null || bars.Count < 2 || _stEqDates == null || _stEqDates.Count < 2)
                return;
            double capital = _stInitialCapital > 0 ? _stInitialCapital : 0;
            if (capital <= 0 && _stEq != null && _stEq.Count > 0 && _stEq[0] != null)
                capital = _stEq[0].Value;      // 老报告没有 scope.initial_capital → 用首日净值兜底
            if (capital <= 0) return;

            var outv = new List<double?>();
            int k = 0;
            double last = 0, base0 = 0;
            bool got = false;
            string d0 = _stEqDates[0];
            for (int i = 0; i < _stEqDates.Count; i++)
            {
                string d = _stEqDates[i];
                // 前向填充：取 ≤ d 的最近一根收盘价（指数与 A 股日历偶尔不一致）
                while (k < bars.Count && string.CompareOrdinal(bars[k].Key, d) <= 0)
                {
                    last = bars[k].Value;
                    k++;
                    // ⚠ 归一化基准必须是**报告区间内**的第一根收盘价，
                    //   **不是**指数序列的第一根 —— 指数序列能到 2005 年，
                    //   拿它当基准会让整条曲线放大几十倍（实测 383513 而不是 100000）。
                    if (!got && last > 0 && string.CompareOrdinal(bars[k - 1].Key, d0) >= 0)
                    {
                        base0 = last;
                        got = true;
                    }
                }
                if (!got || last <= 0) { outv.Add(null); continue; }
                outv.Add(capital * last / base0);
            }
            _stEqIndex = outv;
            // 最后一条被用到的行情日期（k-1 是最后一个 ≤ 报告末日的 bar）
            _stIndexLastDate = (k > 0) ? bars[k - 1].Key : "";
        }
    }
}
