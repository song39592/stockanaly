using System;
using System.Collections.Generic;
using System.Drawing;
using System.Windows.Forms;
using System.Web.Script.Serialization;

namespace StockPool
{
    /// <summary>
    /// 盘面及板块分析（原生内嵌标签页）：MainForm 的拆分文件，替代 frontend/market-sector.html。
    /// 数据来自本机后端 http://127.0.0.1:8000 的 /api/market/{global,capital,sectors,limit-up,big-loss}。
    /// 网页里用 echarts 画的条形图，原生版改为同数据表展示（数值一致，不做图形）。
    /// </summary>
    internal sealed partial class MainForm
    {
        // ---- 盘面及板块分析页 ----
        private int _mktTabIndex = -1;
        private DateTimePicker _mktDate;
        private Label _mktStatus, _mktGlobalStatus, _mktCapStatus, _mktSectorStatus, _mktLuStatus, _mktBlStatus;
        private Label _mktCapAdv;
        private FlowLayoutPanel _mktCapKpi, _mktLuKpi;
        private DataGridView _mktGlobalGrid;                                  // ① 外围环境
        private DataGridView _mktIndGrid, _mktConGrid, _mktSwTopGrid, _mktSwBotGrid;  // ③ 板块β
        private DataGridView _mktLuStruct, _mktLuPromo, _mktLuStocks;         // ④ 连板梯队
        private DataGridView _mktBlasted, _mktLimitDown;                      // ⑤ 大面股

        /// <summary>① 外围环境 → ⑤ 大面股，五块各自一个分组框；顶部选交易日 + 刷新，手动触发加载。</summary>
        private Panel BuildMarketPage()
        {
            var p = NewPage("盘面及板块");
            _mktTabIndex = _tabPages.Count - 1;
            var stack = Stack();

            var title = Lbl("📊 盘面及板块分析");
            title.Font = new Font("Microsoft YaHei UI", 14f, FontStyle.Bold);
            title.Margin = new Padding(0, 0, 0, 2);
            AddRow(stack, Row(title));

            var sub = Mute(Lbl("① 外围环境 · ② 大盘资金 · ③ 板块β · ④ 连板梯队 · ⑤ 大面股｜数据源：新浪行情 / 同花顺 / 乐咕乐股 / 申万 / 东方财富（公开接口，失败自动降级）"));
            sub.AutoSize = true;
            sub.MaximumSize = new Size(760, 0);
            AddRow(stack, Row(sub));

            // 顶部操作区
            TableLayoutPanel b0;
            var g0 = Group("操作", out b0);
            _mktDate = new DateTimePicker();
            _mktDate.Width = 130;
            _mktDate.Format = DateTimePickerFormat.Custom;
            _mktDate.CustomFormat = "yyyy-MM-dd";
            _mktDate.ShowCheckBox = true;
            _mktDate.Checked = false;          // 不勾选 = 实时；勾选 = 按所选交易日取数
            _mktStatus = Mute(Lbl("点「刷新」加载数据"));
            AddRow(b0, Row(Lbl("交易日"), _mktDate,
                MiniBtn("刷新", delegate { MktLoadAll(); }, 80),
                MiniBtn("实时", delegate { _mktDate.Checked = false; MktLoadAll(); }, 80),
                _mktStatus));
            AddRow(stack, g0);

            // ① 外围环境
            TableLayoutPanel b1;
            var g1 = Group("① 外围环境（美股 / 韩日 / 港股 / 大宗商品 / 费城半导体）", out b1);
            _mktGlobalStatus = Mute(Lbl("—"));
            AddRow(b1, Row(_mktGlobalStatus));
            _mktGlobalGrid = MktGrid(190, false, new string[] { "分组", "名称", "现值", "涨跌幅" });
            AddRow(b1, _mktGlobalGrid);
            AddRow(stack, g1);

            // ② 大盘资金
            TableLayoutPanel b2;
            var g2 = Group("② 大盘资金（主力净流向 / 特大单 / 两市成交 / 涨跌家数）", out b2);
            _mktCapStatus = Mute(Lbl("—"));
            AddRow(b2, Row(_mktCapStatus));
            _mktCapKpi = VKpiRow();
            AddRow(b2, _mktCapKpi);
            _mktCapAdv = Mute(Lbl(""));
            _mktCapAdv.AutoSize = true;
            _mktCapAdv.MaximumSize = new Size(740, 0);
            AddRow(b2, Row(_mktCapAdv));
            AddRow(stack, g2);

            // ③ 板块β
            TableLayoutPanel b3;
            var g3 = Group("③ 板块β（行业 / 概念资金流 Top10 · 申万一级行业）", out b3);
            _mktSectorStatus = Mute(Lbl("—"));
            AddRow(b3, Row(_mktSectorStatus));
            AddRow(b3, Row(Mute(Lbl("行业板块资金净额 Top10（净流入 / 净流出，单位：亿）"))));
            _mktIndGrid = MktGrid(170, false, new string[] { "板块", "净额（亿）" });
            AddRow(b3, _mktIndGrid);
            AddRow(b3, Row(Mute(Lbl("概念板块资金净额 Top10（净流入 / 净流出，单位：亿）"))));
            _mktConGrid = MktGrid(170, false, new string[] { "板块", "净额（亿）" });
            AddRow(b3, _mktConGrid);
            AddRow(b3, Row(Mute(Lbl("申万一级行业 · 领涨 Top10"))));
            _mktSwTopGrid = MktGrid(150, false, new string[] { "行业", "涨跌幅" });
            AddRow(b3, _mktSwTopGrid);
            AddRow(b3, Row(Mute(Lbl("申万一级行业 · 领跌 Top10"))));
            _mktSwBotGrid = MktGrid(150, false, new string[] { "行业", "涨跌幅" });
            AddRow(b3, _mktSwBotGrid);
            AddRow(stack, g3);

            // ④ 连板梯队
            TableLayoutPanel b4;
            var g4 = Group("④ 连板梯队（晋级率与结构）", out b4);
            _mktLuStatus = Mute(Lbl("—"));
            AddRow(b4, Row(_mktLuStatus));
            _mktLuKpi = VKpiRow();
            AddRow(b4, _mktLuKpi);
            AddRow(b4, Row(Mute(Lbl("连板结构（各连板高度家数）"))));
            _mktLuStruct = MktGrid(150, false, new string[] { "连板高度", "家数" });
            AddRow(b4, _mktLuStruct);
            AddRow(b4, Row(Mute(Lbl("晋级率（昨日 N 板 → 今日 N+1 板）"))));
            _mktLuPromo = MktGrid(150, false, new string[] { "路径", "昨日家数", "晋级家数", "晋级率" });
            AddRow(b4, _mktLuPromo);
            AddRow(b4, Row(Mute(Lbl("涨停明细（点击表头可排序）"))));
            _mktLuStocks = MktGrid(240, true, new string[] { "代码", "名称", "连板", "行业", "涨幅", "封板资金", "换手", "首封", "涨停统计" });
            AddRow(b4, _mktLuStocks);
            AddRow(stack, g4);

            // ⑤ 大面股
            TableLayoutPanel b5;
            var g5 = Group("⑤ 大面股（炸板 / 跌停）", out b5);
            _mktBlStatus = Mute(Lbl("—"));
            AddRow(b5, Row(_mktBlStatus));
            AddRow(b5, Row(Mute(Lbl("炸板股（相对涨停价回撤）"))));
            _mktBlasted = MktGrid(200, true, new string[] { "代码", "名称", "涨跌幅", "回撤", "振幅", "炸板次数", "行业" });
            AddRow(b5, _mktBlasted);
            AddRow(b5, Row(Mute(Lbl("跌停股"))));
            _mktLimitDown = MktGrid(180, true, new string[] { "代码", "名称", "涨跌幅", "连续跌停", "开板次数", "行业" });
            AddRow(b5, _mktLimitDown);
            AddRow(stack, g5);

            p.Controls.Add(stack);
            return p;
        }

        /// <summary>统一风格的只读数据表：宽度撑满、高度固定、可选点击表头排序。</summary>
        private static DataGridView MktGrid(int height, bool sortable, string[] cols)
        {
            var g = new DataGridView();
            g.Dock = DockStyle.Top;
            g.Height = height;
            g.ReadOnly = true;
            g.AllowUserToAddRows = false;
            g.AllowUserToDeleteRows = false;
            g.RowHeadersVisible = false;
            g.BorderStyle = BorderStyle.None;
            g.Tag = "grid";
            g.AutoSizeColumnsMode = DataGridViewAutoSizeColumnsMode.Fill;
            g.ColumnHeadersHeightSizeMode = DataGridViewColumnHeadersHeightSizeMode.AutoSize;
            foreach (string cn in cols)
            {
                var col = new DataGridViewTextBoxColumn();
                col.HeaderText = cn;
                col.SortMode = sortable ? DataGridViewColumnSortMode.Automatic : DataGridViewColumnSortMode.NotSortable;
                g.Columns.Add(col);
            }
            return g;
        }

        /// <summary>A 股配色：涨红、跌绿、平灰。只给单元格上色，换肤不会覆盖（单元格样式优先于 DefaultCellStyle）。</summary>
        private static void MktColor(DataGridViewCell cell, double? v)
        {
            if (v == null) return;
            if (v.Value > 0) cell.Style.ForeColor = Color.FromArgb(239, 83, 80);
            else if (v.Value < 0) cell.Style.ForeColor = Color.FromArgb(63, 185, 80);
            else cell.Style.ForeColor = Color.FromArgb(150, 158, 172);
        }

        // ---- 加载 ----
        private void MktLoadAll()
        {
            string date = _mktDate.Checked ? _mktDate.Value.ToString("yyyyMMdd") : "";
            _mktStatus.Text = "加载中…";
            _mktStatus.Tag = "muted";

            string[][] jobs = new string[][]
            {
                new string[] { "global",  "/api/market/global" },
                new string[] { "capital", "/api/market/capital" },
                new string[] { "sectors", "/api/market/sectors" },
                new string[] { "limitup", "/api/market/limit-up" },
                new string[] { "bigloss", "/api/market/big-loss" },
            };

            foreach (string[] job in jobs)
            {
                string name = job[0];
                string path = job[1];
                string url = "http://127.0.0.1:8000" + path + (date == "" ? "" : "?date=" + date);
                System.Threading.Tasks.Task.Run(delegate
                {
                    try
                    {
                        string resp = VRequest(url, null);
                        var j = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(resp);
                        Invoke((Action)delegate { MktRender(name, j); });
                    }
                    catch (Exception ex)
                    {
                        string msg = "失败：" + ex.Message;
                        try { Invoke((Action)delegate { MktFail(name, msg); }); }
                        catch (Exception) { }
                    }
                });
            }
        }

        private void MktFail(string name, string msg)
        {
            Label target = MktStatusOf(name);
            if (target != null)
            {
                target.Text = msg;
                target.ForeColor = Color.FromArgb(208, 57, 59);
            }
            _mktStatus.Text = "部分数据加载失败";
            _mktStatus.Tag = "muted";
        }

        private Label MktStatusOf(string name)
        {
            if (name == "global") return _mktGlobalStatus;
            if (name == "capital") return _mktCapStatus;
            if (name == "sectors") return _mktSectorStatus;
            if (name == "limitup") return _mktLuStatus;
            if (name == "bigloss") return _mktBlStatus;
            return null;
        }

        private void MktRender(string name, Dictionary<string, object> j)
        {
            if (name == "global") MktRenderGlobal(j);
            else if (name == "capital") MktRenderCapital(j);
            else if (name == "sectors") MktRenderSectors(j);
            else if (name == "limitup") MktRenderLimitUp(j);
            else if (name == "bigloss") MktRenderBigLoss(j);
        }

        // ---- ① 外围环境 ----
        private void MktRenderGlobal(Dictionary<string, object> j)
        {
            _mktGlobalGrid.Rows.Clear();
            var groups = VArr(VSafe(j, "groups"));
            int n = 0;
            if (groups != null)
            {
                foreach (Dictionary<string, object> g in groups)
                {
                    string gname = VStr(VSafe(g, "name"));
                    var items = VArr(VSafe(g, "items"));
                    if (items == null) continue;
                    foreach (Dictionary<string, object> it in items)
                    {
                        string session = VStr(VSafe(it, "session"));
                        string mark = session == "open" ? " 交易中" : (session == "closed" ? " 休市" : "");
                        double? pct = VNum(VSafe(it, "pct"));
                        int row = _mktGlobalGrid.Rows.Add(
                            gname,
                            VStr(VSafe(it, "name")) + mark,
                            VFmt(VNum(VSafe(it, "value"))),
                            VFmtPct(pct));
                        MktColor(_mktGlobalGrid.Rows[row].Cells[3], pct);
                        n++;
                    }
                }
            }
            bool hist = (VSafe(j, "historical") is bool) && (bool)VSafe(j, "historical");
            _mktGlobalStatus.Text = (hist ? VStr(VSafe(j, "trade_date")) + " 收盘" : VStr(VSafe(j, "as_of"))) + "（" + n + " 项）";
        }

        // ---- ② 大盘资金 ----
        private void MktRenderCapital(Dictionary<string, object> j)
        {
            _mktCapKpi.Controls.Clear();
            var mf = VMap(VSafe(j, "main_flow"));
            var t = VMap(VSafe(j, "turnover"));
            var ad = VMap(VSafe(j, "adv_dec"));

            string src = mf != null ? VStr(VSafe(mf, "source")) : "";
            if (src == "") src = "东方财富";
            double? bigNet = null;
            if (mf != null)
            {
                bigNet = VNum(VSafe(mf, "super_net"));
                if (bigNet == null) bigNet = VNum(VSafe(mf, "big_net"));
            }
            double? mainNet = mf != null ? VNum(VSafe(mf, "main_net")) : null;

            AddKpi(_mktCapKpi, "沪深主力净流入", mainNet != null ? VFmt(mainNet) + " 亿" : "—",
                mf != null ? (VNum(VSafe(mf, "main_pct")) != null ? "净占比 " + VFmtPct(VNum(VSafe(mf, "main_pct"))) : "个股资金流汇总（近似口径）") : "资金流数据源暂不可用");
            AddKpi(_mktCapKpi, "特大单（超大单）", bigNet != null ? VFmt(bigNet) + " 亿" : "—",
                mf != null ? "方向：" + VStr(VSafe(mf, "super_direction")) : "—");
            AddKpi(_mktCapKpi, "数据口径", src, mf != null ? "数据日 " + VStr(VSafe(mf, "date")) : "—");
            AddKpi(_mktCapKpi, "两市成交额", (t != null && VNum(VSafe(t, "total")) != null) ? VFmt(VNum(VSafe(t, "total"))) + " 亿" : "—",
                MktTurnoverDetail(t));

            if (ad != null)
            {
                int up = MktInt(VSafe(ad, "up"));
                int down = MktInt(VSafe(ad, "down"));
                int flat = MktInt(VSafe(ad, "flat"));
                int total = up + down + flat;
                if (total <= 0) total = 1;
                _mktCapAdv.Text = "上涨 / 下跌 / 平盘：" + up + " / " + down + " / " + flat
                    + "（上涨占比 " + (up * 100.0 / total).ToString("F1") + "%）"
                    + "　涨停 / 跌停：" + MktText(VSafe(ad, "limit_up")) + " / " + MktText(VSafe(ad, "limit_down"))
                    + (VSafe(ad, "suspend") != null ? "　停牌：" + MktText(VSafe(ad, "suspend")) : "");
            }
            else
            {
                _mktCapAdv.Text = "";
            }

            bool hist = (VSafe(j, "historical") is bool) && (bool)VSafe(j, "historical");
            _mktCapStatus.Text = hist ? VStr(VSafe(j, "trade_date")) + " 数据" : VStr(VSafe(j, "as_of"));
        }

        private static string MktTurnoverDetail(Dictionary<string, object> t)
        {
            if (t == null) return "—";
            var items = VArr(VSafe(t, "items"));
            if (items == null || items.Count == 0) return "—";
            var parts = new List<string>();
            foreach (Dictionary<string, object> it in items)
            {
                parts.Add(VStr(VSafe(it, "name")) + " " + VFmt(VNum(VSafe(it, "amount")), 0) + "亿");
            }
            return string.Join(" · ", parts.ToArray());
        }

        // ---- ③ 板块β ----
        private void MktRenderSectors(Dictionary<string, object> j)
        {
            var ind = VMap(VSafe(j, "industry"));
            var con = VMap(VSafe(j, "concept"));
            var sw = VMap(VSafe(j, "sw_first"));

            MktFillFlow(_mktIndGrid, ind);
            MktFillFlow(_mktConGrid, con);

            _mktSwTopGrid.Rows.Clear();
            _mktSwBotGrid.Rows.Clear();
            if (sw != null)
            {
                MktFillPct(_mktSwTopGrid, VArr(VSafe(sw, "top")));
                MktFillPct(_mktSwBotGrid, VArr(VSafe(sw, "bottom")));
            }

            bool hist = (VSafe(j, "historical") is bool) && (bool)VSafe(j, "historical");
            _mktSectorStatus.Text = hist ? VStr(VSafe(j, "trade_date")) + " 收盘" : VStr(VSafe(j, "as_of"));
        }

        /// <summary>行业 / 概念资金流：净流入 + 净流出合并后按净额升序（流出在前、流入在后，与网页图表一致）。</summary>
        private static void MktFillFlow(DataGridView g, Dictionary<string, object> block)
        {
            g.Rows.Clear();
            if (block == null) return;
            var rows = new List<Dictionary<string, object>>();
            var inArr = VArr(VSafe(block, "top_in"));
            var outArr = VArr(VSafe(block, "top_out"));
            if (inArr != null) foreach (Dictionary<string, object> x in inArr) rows.Add(x);
            if (outArr != null) foreach (Dictionary<string, object> x in outArr) rows.Add(x);
            rows.Sort(delegate(Dictionary<string, object> a, Dictionary<string, object> b)
            {
                double va = VNum(VSafe(a, "net")) ?? 0;
                double vb = VNum(VSafe(b, "net")) ?? 0;
                return va.CompareTo(vb);
            });
            foreach (Dictionary<string, object> x in rows)
            {
                double? net = VNum(VSafe(x, "net"));
                int row = g.Rows.Add(VStr(VSafe(x, "name")), VFmt(net));
                MktColor(g.Rows[row].Cells[1], net);
            }
        }

        private static void MktFillPct(DataGridView g, System.Collections.ArrayList list)
        {
            if (list == null) return;
            foreach (Dictionary<string, object> x in list)
            {
                double? pct = VNum(VSafe(x, "pct"));
                int row = g.Rows.Add(VStr(VSafe(x, "name")), VFmtPct(pct));
                MktColor(g.Rows[row].Cells[1], pct);
            }
        }

        // ---- ④ 连板梯队 ----
        private void MktRenderLimitUp(Dictionary<string, object> j)
        {
            _mktLuKpi.Controls.Clear();
            _mktLuStruct.Rows.Clear();
            _mktLuPromo.Rows.Clear();
            _mktLuStocks.Rows.Clear();

            object okv;
            bool ok = (j != null && j.TryGetValue("ok", out okv) && okv is bool && (bool)okv);
            if (!ok)
            {
                _mktLuStatus.Text = VStr(VSafe(j, "trade_date")) + " 未获取到涨停池数据";
                _mktLuStatus.ForeColor = Color.FromArgb(208, 57, 59);
                return;
            }

            var structure = VArr(VSafe(j, "structure"));
            var promo = VArr(VSafe(j, "promotion"));
            var stocks = VArr(VSafe(j, "stocks"));

            int ge2 = 0, first = 0;
            if (structure != null)
            {
                foreach (Dictionary<string, object> s in structure)
                {
                    int level = MktInt(VSafe(s, "level"));
                    int count = MktInt(VSafe(s, "count"));
                    _mktLuStruct.Rows.Add(level + " 板", count);
                    if (level >= 2) ge2 += count;
                    if (level == 1) first = count;
                }
            }

            AddKpi(_mktLuKpi, "今日涨停家数", MktText(VSafe(j, "total")), "数据日 " + VStr(VSafe(j, "trade_date")));
            AddKpi(_mktLuKpi, "最高连板", MktText(VSafe(j, "max_level")) + " 板", "结构：" + MktStructureText(structure));
            AddKpi(_mktLuKpi, "连板梯队（≥2 板）", ge2.ToString(), "首板 " + first + " 家");

            if (promo != null)
            {
                foreach (Dictionary<string, object> p in promo)
                {
                    int from = MktInt(VSafe(p, "from"));
                    double? rate = VNum(VSafe(p, "rate"));
                    int row = _mktLuPromo.Rows.Add(
                        from + " 板 → " + (from + 1) + " 板",
                        MktText(VSafe(p, "total")),
                        MktText(VSafe(p, "promoted")),
                        rate != null ? rate.Value.ToString("F1") + "%" : "—");
                    MktColor(_mktLuPromo.Rows[row].Cells[3], rate);
                }
            }

            if (stocks != null)
            {
                foreach (Dictionary<string, object> s in stocks)
                {
                    double? pct = VNum(VSafe(s, "pct"));
                    int row = _mktLuStocks.Rows.Add(
                        VStr(VSafe(s, "code")),
                        VStr(VSafe(s, "name")),
                        MktText(VSafe(s, "level")) + " 板",
                        VStr(VSafe(s, "industry")),
                        VFmtPct(pct),
                        VFmt(VNum(VSafe(s, "seal_fund"))) + " 亿",
                        VFmt(VNum(VSafe(s, "turnover"))) + "%",
                        VStr(VSafe(s, "first_seal")),
                        VStr(VSafe(s, "stat")));
                    MktColor(_mktLuStocks.Rows[row].Cells[4], pct);
                }
            }

            _mktLuStatus.Text = VStr(VSafe(j, "trade_date")) + "（涨停 " + MktText(VSafe(j, "total")) + " 家）";
        }

        private static string MktStructureText(System.Collections.ArrayList structure)
        {
            if (structure == null || structure.Count == 0) return "—";
            var parts = new List<string>();
            foreach (Dictionary<string, object> s in structure)
                parts.Add(MktInt(VSafe(s, "level")) + "板×" + MktInt(VSafe(s, "count")));
            return string.Join(" · ", parts.ToArray());
        }

        // ---- ⑤ 大面股 ----
        private void MktRenderBigLoss(Dictionary<string, object> j)
        {
            _mktBlasted.Rows.Clear();
            _mktLimitDown.Rows.Clear();

            object okv;
            bool ok = (j != null && j.TryGetValue("ok", out okv) && okv is bool && (bool)okv);
            if (!ok)
            {
                _mktBlStatus.Text = VStr(VSafe(j, "trade_date")) + " 未获取到大面股数据";
                _mktBlStatus.ForeColor = Color.FromArgb(208, 57, 59);
                return;
            }

            var blasted = VArr(VSafe(j, "blasted"));
            var limitDown = VArr(VSafe(j, "limit_down"));

            if (blasted != null)
            {
                foreach (Dictionary<string, object> s in blasted)
                {
                    double? pct = VNum(VSafe(s, "pct"));
                    double? dd = VNum(VSafe(s, "drawdown"));
                    int row = _mktBlasted.Rows.Add(
                        VStr(VSafe(s, "code")),
                        VStr(VSafe(s, "name")),
                        VFmtPct(pct),
                        dd != null ? dd.Value.ToString("F2") + "%" : "—",
                        VFmt(VNum(VSafe(s, "amplitude"))) + "%",
                        MktText(VSafe(s, "blasted_times")),
                        VStr(VSafe(s, "industry")));
                    MktColor(_mktBlasted.Rows[row].Cells[2], pct);
                }
            }

            if (limitDown != null)
            {
                foreach (Dictionary<string, object> s in limitDown)
                {
                    double? pct = VNum(VSafe(s, "pct"));
                    int row = _mktLimitDown.Rows.Add(
                        VStr(VSafe(s, "code")),
                        VStr(VSafe(s, "name")),
                        VFmtPct(pct),
                        MktText(VSafe(s, "continuous")),
                        MktText(VSafe(s, "open_times")),
                        VStr(VSafe(s, "industry")));
                    MktColor(_mktLimitDown.Rows[row].Cells[2], pct);
                }
            }

            _mktBlStatus.Text = VStr(VSafe(j, "trade_date")) + "（炸板 " + (blasted != null ? blasted.Count : 0)
                + " 只 · 跌停 " + (limitDown != null ? limitDown.Count : 0) + " 只）";
        }

        // ---- 小工具 ----
        private static int MktInt(object o)
        {
            double? v = VNum(o);
            if (v == null) return 0;
            return (int)v.Value;
        }

        private static string MktText(object o)
        {
            if (o == null) return "—";
            double? v = VNum(o);
            if (v != null) return ((int)v.Value).ToString();
            string s = VStr(o);
            return s == "" ? "—" : s;
        }
    }
}
