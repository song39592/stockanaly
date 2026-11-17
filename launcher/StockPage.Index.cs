using System;
using System.Collections.Generic;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;
using System.Windows.Forms;

namespace StockPool
{
    /// <summary>
    /// 指数模式：上证 / 深证 / 沪深300 … **像个股一样看 K 线**。
    ///
    /// ## 为什么复用个股分析页，而不是新开一页
    ///
    /// 指数要画的图与个股**完全同构**：OHLCV 蜡烛 + 主图 / 副图指标叠加 +
    /// 周期（日 / 周 / 月）+ 范围（近6月 / 近1年 / 全部）。
    /// 图表组件 `KLineChart` 只认 `KBar` 列表、**与 code 无关** → 复用零成本；
    /// 新开一页反而要把「周期 / 范围 / 指标 / 渲染」整套逻辑复制一遍 = 第二份口径。
    ///
    /// ## 指数**没有**的东西 → 藏起来，而不是显示空表
    ///
    /// 个股专属页（基本信息 / 记录·消息 / 估值 / 扫雷 / AI 分析 / 偏好设置）
    /// 与 K 线右侧的筹码分布窗口，对指数都没有意义（指数没有股本、没有入池轨迹、
    /// 没有公告消息面、没有筹码）。指数模式下把这些页签藏起来，
    /// 而不是让它们显示一片空白 —— 空白会被当成「数据丢了」。
    ///
    /// ## 数据通道
    ///
    ///   GET /api/history/indices       → 指数清单（code / 中文名 / 是否已落盘）
    ///   GET /api/history/index/{code}  → 指数 K 线（与 /api/history/stock/{code} **同构**）
    ///   POST /api/indicators/batch     → 指标叠加（后端已支持指数 code）
    ///
    /// 清单**不在前端硬编码**：名称与「哪些指数本地有」一律从后端取，
    /// 两份清单迟早漂移（后端加了新指数、前端还在旧列表 → 用户输入代码却搜不到）。
    /// </summary>
    internal sealed partial class MainForm
    {
        /// <summary>指数代码形态：市场前缀（2 位小写字母）+ 6 位数字。
        ///
        /// 与后端 `core/indices.py::is_index_code` 是**同一存储约定的镜像**
        /// （指数 code 带市场前缀，个股 code 是裸 6 位数字 → 天然不冲突）。
        /// 这里只做形态判定；名称与清单走 /api/history/indices。
        /// </summary>
        private static readonly Regex IndexCodeRx = new Regex("^[a-z]{2}[0-9]{6}$");

        /// <summary>当前打开的是指数（不是个股）。</summary>
        private bool _stockIsIndex = false;

        /// <summary>指数清单是否已取过（取过就不再打后端）。</summary>
        private bool _stockIndexListLoaded = false;

        /// <summary>清单是否正在取（避免并发重复请求）。</summary>
        private bool _stockIndexLoading = false;

        /// <summary>等清单回来的回调队列（搜索联想 + 打开指数可能同时要）。</summary>
        private readonly List<Action> _stockIndexWaiters = new List<Action>();

        /// <summary>指数清单（来自 /api/history/indices）。</summary>
        private readonly List<IndexOpt> _stockIndexOpts = new List<IndexOpt>();

        private sealed class IndexOpt
        {
            public string Code = "";
            public string Name = "";
            /// <summary>本地已落盘（能立刻画图）；false = 点「更新」才现拉。</summary>
            public bool Available = false;
        }

        /// <summary>这个输入是指数代码吗？（形态判定，不查清单）</summary>
        private static bool StockIsIndexCode(string code)
        {
            if (string.IsNullOrEmpty(code)) return false;
            return IndexCodeRx.IsMatch(code.Trim().ToLowerInvariant());
        }

        /// <summary>取指数清单（一次即可；失败不挡路 —— 只是显示不出中文名）。
        ///
        /// 多个调用点可能同时要清单（搜索联想 + 打开指数）：用「等待者队列」收口，
        /// 只发一次请求，回来后统一回调 —— 否则每次按键都打一次后端。
        /// </summary>
        private void StockLoadIndexList(Action done)
        {
            if (_stockIndexListLoaded) { if (done != null) done(); return; }
            if (done != null) _stockIndexWaiters.Add(done);
            if (_stockIndexLoading) return;          // 已在取 → 等它回来即可
            _stockIndexLoading = true;
            RunUi<System.Collections.ArrayList>(
                delegate
                {
                    string body;
                    if (!GetText("http://127.0.0.1:8000/api/history/indices", 20000, out body)
                        || string.IsNullOrEmpty(body))
                        return new System.Collections.ArrayList();
                    var j = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(body);
                    var arr = J.Arr(J.Get(j, "items"));
                    return arr ?? new System.Collections.ArrayList();
                },
                delegate(System.Collections.ArrayList arr)
                {
                    _stockIndexOpts.Clear();
                    foreach (Dictionary<string, object> d in arr)
                    {
                        var o = new IndexOpt();
                        o.Code = J.Str(J.Get(d, "code"));
                        o.Name = J.Str(J.Get(d, "name"));
                        object av = J.Get(d, "available");
                        o.Available = av is bool && (bool)av;
                        if (!string.IsNullOrEmpty(o.Code)) _stockIndexOpts.Add(o);
                    }
                    _stockIndexListLoaded = true;
                    _stockIndexLoading = false;
                    StockFlushIndexWaiters();
                },
                delegate(Exception ex)
                {
                    // 失败也标记为「取过了」：否则每次输入都会重试打后端
                    _stockIndexListLoaded = true;
                    _stockIndexLoading = false;
                    StockFlushIndexWaiters();
                },
                null);
        }

        /// <summary>清单回来后统一回调所有等待者。</summary>
        private void StockFlushIndexWaiters()
        {
            if (_stockIndexWaiters.Count == 0) return;
            var list = new List<Action>(_stockIndexWaiters);
            _stockIndexWaiters.Clear();
            foreach (Action a in list)
            {
                try { a(); } catch (Exception) { }
            }
        }

        /// <summary>指数中文名（清单里没有就返回空串）。</summary>
        private string StockIndexNameOf(string code)
        {
            if (string.IsNullOrEmpty(code)) return "";
            foreach (IndexOpt o in _stockIndexOpts)
                if (string.Equals(o.Code, code, StringComparison.OrdinalIgnoreCase)) return o.Name;
            return "";
        }

        /// <summary>指数是否本地已落盘（没落盘 → 首次加载会是空图，提示点「更新」现拉）。</summary>
        private bool StockIndexAvailable(string code)
        {
            foreach (IndexOpt o in _stockIndexOpts)
                if (string.Equals(o.Code, code, StringComparison.OrdinalIgnoreCase)) return o.Available;
            return false;
        }

        /// <summary>打开指数：切到 K 线页 → 藏起个股专属页签 → 加载指数 K 线。</summary>
        private void StockOpenIndex(string code)
        {
            code = code.Trim().ToLowerInvariant();
            _stockIsIndex = true;
            _quickActiveCode = code;              // 异步守卫用（与输入框文本解耦）
            _stockCurrent = code;

            StockApplyIndexMode();                // 页签可见性 / 复权按钮 / 文案

            // 切到 K 线页（指数只有这一页有内容）
            if (_stockStrip != null) _stockStrip.Select(0);

            // 清掉上一只的图，否则异步加载期间旧图还停在屏幕上（看起来像「没切过来」）
            if (_stockKline != null)
            {
                _stockKline.SetData(new List<KBar>(), new List<KBar>(), new List<KMark>());
                _stockKline.SetIndicators(null);
                _stockKline.SetMainIndicators(null);
            }

            _stockHint.Text = "正在加载指数 " + code + " 的K线…";
            _stockHint.Tag = "muted";
            _stockHint.ForeColor = C.Flat;

            // 名称：清单在后端，前端只显示它给的（清单没取到就显示代码本身）
            StockLoadIndexList(delegate
            {
                Ui(delegate
                {
                    if (_quickActiveCode != code) return;
                    string name = StockIndexNameOf(code);
                    _stockName.Text = string.IsNullOrEmpty(name) ? code : name + " · " + code;
                    StockAddHistory(code, name);
                    if (!StockIndexAvailable(code))
                    {
                        _stockStatus.Text = "本地暂无该指数数据，点「更新指数行情」现拉";
                        _stockStatus.Tag = "muted";
                        _stockStatus.ForeColor = C.Flat;
                    }
                });
            });

            StockLoadHistory(code, false);
        }

        /// <summary>指数 / 个股两种模式的界面差异（页签可见性、复权按钮、文案）。</summary>
        private void StockApplyIndexMode()
        {
            bool idx = _stockIsIndex;
            if (_stockStrip != null)
            {
                // 个股专属页对指数无意义 → 藏起来（顺序见 StockPage.BuildStockPage 的 Add 调用）：
                //   0 K线 / 1 基本信息 / 2 记录·消息 / 3 估值 / 4 扫雷 / 5 AI分析 / 6 偏好设置 / 7 指标索引
                _stockStrip.SetTabVisible(1, !idx);
                _stockStrip.SetTabVisible(2, !idx);
                _stockStrip.SetTabVisible(3, !idx);
                _stockStrip.SetTabVisible(4, !idx);
                _stockStrip.SetTabVisible(5, !idx);
                _stockStrip.SetTabVisible(6, !idx);
            }
            // 复权对指数无意义（指数没有除权除息）→ 按钮与标签一起藏
            foreach (Button b in _stockAdjustMap.Keys) b.Visible = !idx;
            if (_stockAdjustCap != null) _stockAdjustCap.Visible = !idx;
            // 筹码窗口：指数没有筹码 → 整列收起（否则右侧留一条空白，看着像控件坏了）
            if (_stockKlineLayout != null && _stockKlineLayout.ColumnStyles.Count > 1)
                _stockKlineLayout.ColumnStyles[1].Width = idx ? 0f : 178f;
            if (_stockChip != null) _stockChip.Visible = !idx;
            if (_stockNameCap != null) _stockNameCap.Text = idx ? "当前指数" : "当前个股";
            if (_stockRefresh != null) _stockRefresh.Text = idx ? "↻ 更新指数行情" : "↻ 更新行情/消息";
            _stockHint.Text = idx
                ? "指数模式：K线 / 主副图指标 / 周期 / 范围 均可用；筹码与个股专属页已隐藏"
                : "敲任意数字 / 字母 → 右下角输入框：6 位代码打开个股；sh000001 等指数代码打开指数K线；输指标英文代码（ma / macd / rsi）回车切换主图 / 副图指标";
            _stockHint.Tag = "muted";
            _stockHint.ForeColor = C.Flat;
        }

        /// <summary>打开指数（从联想下拉点选时用）：切到个股分析页 → 指数模式。</summary>
        private void OpenIndex(string code)
        {
            if (!StockIsIndexCode(code)) return;
            if (_tabIndex != _stockTabIndex) _returnTabIndex = _tabIndex;
            SelectTab(_stockTabIndex);
            StockOpen(code);          // StockOpen 内按代码形态分流到指数模式
        }

        /// <summary>指数 K 线的取数地址（指数与个股**不同路由**：个股的 6 位数字校验与
        /// 消息面 / 股票池轨迹都是个股语义，混在一起会让「哪些字段对指数有意义」变含糊）。</summary>
        private string StockHistoryUrl(string code, bool refresh)
        {
            string baseUrl = _stockIsIndex
                ? "http://127.0.0.1:8000/api/history/index/" + code
                : "http://127.0.0.1:8000/api/history/stock/" + code;
            return baseUrl
                 + "?refresh=" + (refresh ? "true" : "false")
                 + "&period=" + (string.IsNullOrEmpty(_stockPeriod) ? "day" : _stockPeriod);
        }
    }
}
