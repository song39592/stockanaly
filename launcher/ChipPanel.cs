using System;
using System.Collections.Generic;
using System.Linq;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Windows.Forms;

namespace StockPool
{
    /// <summary>筹码分布的一根价格柱（后端 bins[] 的一项）。</summary>
    internal sealed class ChipBin
    {
        public double Lo;
        public double Hi;
        public double Price;
        public double Pct;          // 占比（%，全部合计 100）
    }

    /// <summary>筹码分布统计（后端 stats）。字段可空：后端算不出时为 null。</summary>

    /// <summary>筹码分布统计（后端 stats）。字段可空：后端算不出时为 null。</summary>
    internal sealed class ChipStats
    {
        public double? AvgCost;
        public double? PeakPrice;
        public double? ProfitRatio;
        public double? Scr90;
        public double Close;            // 该帧当日的收盘价（获利/套牢的分界）
    }

    /// <summary>
    /// 筹码分布窗口：位于 K 线**右侧**，横轴是筹码占比、纵轴是价格（与 K 线价格轴对齐）。
    /// 与指标体系（按日期的序列）不同，这里是「某个时点上各价位堆了多少筹码」的直方图，
    /// 故单独成一个窗口渲染，不走 KLineChart 的副图面板。
    /// 数据来自 GET /api/chip/dist（换手率衰减 + 三角形分布，见 chip_dist_service.py）。
    /// </summary>

    /// <summary>
    /// 筹码分布窗口：位于 K 线**右侧**，横轴是筹码占比、纵轴是价格（与 K 线价格轴对齐）。
    /// 与指标体系（按日期的序列）不同，这里是「某个时点上各价位堆了多少筹码」的直方图，
    /// 故单独成一个窗口渲染，不走 KLineChart 的副图面板。
    /// 数据来自 GET /api/chip/dist（换手率衰减 + 三角形分布，见 chip_dist_service.py）。
    /// </summary>
    internal sealed class ChipPanel : ChartControl
    {
        private const int LeftPad = 6;
        private const int RightPad = 8;
        private const int GridCount = 5;        // 与 K 线一致的横向网格数，便于对齐

        public KLineChart Owner;                // 取价格轴几何（与 K 线对齐用）
        private List<ChipBin> _bins = new List<ChipBin>();          // 价格轴（各分箱的价格区间）
        private List<string> _dates = new List<string>();           // 逐日快照的日期
        private List<double[]> _frames = new List<double[]>();      // 逐日快照的占比（与 _bins 同序）
        private List<double[]> _grayFrames = null;                  // EMA 锁仓：逐日锁仓占比（与 _frames 同序；null=非锁仓模型）
        private List<double[]> _redFrames = null;                   // 红叠层：逐日强换手占比（与 _frames 同序；null=非红叠模型）
        private List<ChipStats> _frameStats = new List<ChipStats>();
        private Dictionary<string, int> _dateIdx = new Dictionary<string, int>();
        private int _frame = -1;                // 当前显示的帧（默认最新）
        private string _status = "待加载";
        private string _note = "";              // 口径提示（如锁仓未修正）
        private bool _failed = false;

        public ChipPanel()
        {
            // SetStyle 已上移到基类 ChartControl（第 07 项）
            DoubleBuffered = true;
            Width = 172;
        }

        /// <summary>装入计算结果。dates / frames / frameStats 三者一一对应（逐日快照）。</summary>
        public void SetData(List<ChipBin> bins, List<string> dates,
                            List<double[]> frames, List<ChipStats> frameStats, string note,
                            List<double[]> grayFrames = null, List<double[]> redFrames = null)
        {
            _bins = bins ?? new List<ChipBin>();
            _dates = dates ?? new List<string>();
            _frames = frames ?? new List<double[]>();
            _grayFrames = (grayFrames != null && grayFrames.Count == _frames.Count) ? grayFrames : null;
            _redFrames = (redFrames != null && redFrames.Count == _frames.Count) ? redFrames : null;
            _frameStats = frameStats ?? new List<ChipStats>();
            _dateIdx = new Dictionary<string, int>();
            for (int i = 0; i < _dates.Count; i++)
            {
                if (!_dateIdx.ContainsKey(_dates[i])) _dateIdx[_dates[i]] = i;
            }
            _note = note ?? "";
            _status = "";
            _failed = false;
            _frame = _frames.Count - 1;          // 默认显示最新一天
            Invalidate();
        }

        /// <summary>
        /// 光标跟随：切到光标所在交易日那一帧。日期不在快照区间内时保持原帧不动
        /// （例如滚轮缩小后光标落到更早的日期——等防抖重算完成后即可覆盖）。
        /// </summary>
        public void SetCursorDate(string date)
        {
            if (string.IsNullOrEmpty(date) || _dateIdx.Count == 0) return;
            int idx;
            if (!_dateIdx.TryGetValue(date, out idx))
            {
                // 日期不在快照内（光标落到快照区间之外，如滚轮放大后、或筹码与 K 线
                // 可见范围暂不一致）：二分夹到**最近的边界帧**，而不是保持旧帧不动——
                // 否则早于快照起点的日期会一直停在同一个帧，看起来「某日之前筹码峰不变」。
                // _dates 为升序（YYYY-MM-DD，字典序即时间序）。
                int lo = 0, hi = _dates.Count - 1, ans = 0;
                while (lo <= hi)
                {
                    int mid = (lo + hi) >> 1;
                    if (string.CompareOrdinal(_dates[mid], date) <= 0) { ans = mid; lo = mid + 1; }
                    else hi = mid - 1;
                }
                idx = ans;
            }
            if (idx == _frame) return;
            _frame = idx;
            Invalidate();
        }

        /// <summary>加载中 / 失败提示。</summary>
        public void SetStatus(string status, bool failed)
        {
            _bins = new List<ChipBin>();
            _dates = new List<string>();
            _frames = new List<double[]>();
            _grayFrames = null;
            _redFrames = null;
            _frameStats = new List<ChipStats>();
            _dateIdx = new Dictionary<string, int>();
            _frame = -1;
            _status = status ?? "";
            _failed = failed;
            Invalidate();
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            base.OnPaint(e);
            var g = e.Graphics;
            BeginPaint(g);                 // 抗锯齿统一由基类设置（第 07 项）
            bool dark = (BackColor.R + BackColor.G + BackColor.B) < 200;
            Color grid = dark ? Color.FromArgb(54, 58, 68) : Color.FromArgb(222, 224, 228);
            Color text = ForeColor;
            using (var bg = new SolidBrush(BackColor))
                g.FillRectangle(bg, 0, 0, Width, Height);
            using (var tb = new SolidBrush(text))
            using (var titleFont = new Font(Font, FontStyle.Bold))
            {
                g.DrawString("筹码分布", titleFont, tb, 2, 2);
                // 标题右侧标出当前帧的日期：光标回溯时才知道看的是哪一天
                if (_frame >= 0 && _frame < _dates.Count)
                {
                    g.DrawString(_dates[_frame], Font, tb, Width - RightPad, 3, FmtRight);
                }
                if (_bins.Count == 0 || _frames.Count == 0)
                {
                    using (var mb = new SolidBrush(_failed ? C.UpErr : text))
                        g.DrawString(_status, Font, mb,
                            new RectangleF(2, 22, Width - 4, Height - 24), FmtLeft);                        return;
                }

                // 价格轴：优先与 K 线完全一致（同一 lo/hi 映射到同一纵向区间）
                double lo = 0, hi = 1;
                int top = 20, bottom = Math.Max(30, Height - 18);
                bool aligned = (Owner != null)
                    && Owner.TryGetPriceAxis(out lo, out hi, out top, out bottom);
                if (!aligned)
                {   // K 线还没画过（或已清空）：退回按筹码自身的价格范围铺满
                    lo = double.MaxValue; hi = double.MinValue;
                    foreach (ChipBin b in _bins) { if (b.Lo < lo) lo = b.Lo; if (b.Hi > hi) hi = b.Hi; }
                    if (hi <= lo) hi = lo + 1;
                    top = 20; bottom = Math.Max(30, Height - 18);
                }
                // 仅在**无法与 K 线价格轴对齐**（K 线未绘制 / 已清空）时，才把纵向范围收紧到有筹码的区间；
                // 一旦对齐成功，必须沿用 K 线的 lo/hi —— 否则筹码会被纵向拉伸到整个面板高度，
                // 与 K 线价位严重错位（出现「筹码铺满整段价格、套牢蓝被顶到最高价」的假象）。
                if (!aligned && _frames != null && _frames.Count > 0)
                {
                    int fi = (_frame >= 0 && _frame < _frames.Count) ? _frame : _frames.Count - 1;
                    double[] fp = _frames[fi];
                    double[] fg = (_grayFrames != null && fi < _grayFrames.Count) ? _grayFrames[fi] : null;
                    double clo = double.MaxValue, chi = double.MinValue;
                    for (int i = 0; i < _bins.Count && i < fp.Length; i++)
                    {
                        double v = fp[i] + (fg != null && i < fg.Length ? fg[i] : 0.0);
                        if (v <= 1e-9) continue;              // 无筹码箱跳过
                        if (_bins[i].Lo < clo) clo = _bins[i].Lo;
                        if (_bins[i].Hi > chi) chi = _bins[i].Hi;
                    }
                    if (chi > clo)
                    {
                        double cpad = (chi - clo) * 0.03;
                        lo = clo - cpad; hi = chi + cpad;
                    }
                }
                int plotH = Math.Max(10, bottom - top);
                int plotW = Math.Max(10, Width - LeftPad - RightPad);
                double span = hi - lo;
                if (span <= 0) span = 1;
                Func<double, double> yOf = p => bottom - (p - lo) / span * plotH;

                using (var pen = new Pen(grid))
                {
                    for (int i = 0; i <= GridCount; i++)
                    {
                        double p = lo + span * i / GridCount;
                        g.DrawLine(pen, LeftPad - 3, (int)yOf(p), Width - RightPad, (int)yOf(p));
                    }
                }

                // 当前帧：光标回溯时取光标所在交易日那一帧
                if (_frame < 0 || _frame >= _frames.Count) _frame = _frames.Count - 1;
                double[] pct = _frames[_frame];
                ChipStats st = (_frame >= 0 && _frame < _frameStats.Count) ? _frameStats[_frame] : null;
                double refClose = (st != null) ? st.Close : 0;   // 获利/套牢以**该日**收盘价为界

                // 筹码分布：成本低于当日收盘 = 获利盘（暖色），高于 = 套牢盘（冷色）
                // 画面形状画成**三角形分布**：每个分箱取其占比换算的宽度与中心价，
                // 相邻分箱的顶点**直线相连**成峰形轮廓（不再是一格格的方块阶梯）。
                // 占比为 0 的分箱也参与连线（宽度 0），这样峰与峰之间的谷能真正归零。
                // 蓝/红/灰模型（ema_lock：蓝=TRI、灰=蓝×R、红=强换手叠加）：有 red 帧则以「蓝底 + 灰折旧段 + 红外沿叠层」渲染
                bool brgMode = (_redFrames != null && _grayFrames != null
                                && _frame >= 0 && _frame < _redFrames.Count);
                // EMA 锁仓模型分支：有 gray 帧则画「红底 + 灰叠层」，否则走原盈亏两色逻辑
                bool emaMode = (!brgMode && _grayFrames != null && _frame >= 0 && _frame < _grayFrames.Count);
                double[] gray = (brgMode || emaMode) ? _grayFrames[_frame] : null;
                double[] red = brgMode ? _redFrames[_frame] : null;

                // 横轴满宽基准：一律用 pct（蓝底）峰值。
                // ⚠️ 不能改用灰峰：灰 = 蓝 × gray_ratio（默认 0.5）恒为蓝的子集，
                // 用灰峰做分母会把蓝放大到约 2× 并被截断，视觉上灰就「反超」蓝，
                // 与指标定义（灰 ≤ 蓝）相悖。
                double maxPct = 0;
                for (int i = 0; i < pct.Length; i++) if (pct[i] > maxPct) maxPct = pct[i];
                if (maxPct <= 0) maxPct = 1;

                if (brgMode)
                {
                    // 红/灰都从左侧（底部）起画，不溢出面板；maxPct 仍用 pct 峰值即可
                    Color blueC = C.LockedDark;                     // 蓝：base 原始筹码
                    Color grayC = Color.FromArgb(150, 156, 168);    // 灰：沉淀折旧
                    Color redC = C.Up;                              // 红：强换手叠层（外沿）
                    var idxs = new List<int>();
                    var baseWs = new List<int>();
                    var grayWs = new List<int>();
                    var redWs = new List<int>();
                    var ys = new List<int>();
                    for (int i = 0; i < pct.Length && i < _bins.Count; i++)
                    {
                        ChipBin b = _bins[i];
                        if (b.Hi < lo || b.Lo > hi) continue;
                        int w = (int)Math.Round(pct[i] / maxPct * plotW);
                        if (w < 0) w = 0; if (w > plotW) w = plotW;
                        int yc = (int)yOf(b.Price);
                        if (yc < top) yc = top; if (yc > bottom) yc = bottom;
                        idxs.Add(i); baseWs.Add(w); ys.Add(yc);
                        int gw = (int)Math.Round((i < gray.Length ? gray[i] : 0) / maxPct * plotW);
                        if (gw < 0) gw = 0; if (gw > w) gw = w;   // 灰是蓝的沉淀子集，绝不宽于蓝
                        grayWs.Add(gw);
                        int rw = (int)Math.Round((i < red.Length ? red[i] : 0) / maxPct * plotW);
                        if (rw < 0) rw = 0; if (rw > plotW) rw = plotW;
                        if (rw > w) rw = w;   // 红是叠加在蓝上的叠层，绝不宽于蓝
                        redWs.Add(rw);
                    }
                    if (idxs.Count > 0)
                    {
                        // 蓝底（base 全量堆积）
                        var poly = new List<Point>();
                        poly.Add(new Point(LeftPad, ys[0]));
                        for (int t = 0; t < idxs.Count; t++) poly.Add(new Point(LeftPad + baseWs[t], ys[t]));
                        poly.Add(new Point(LeftPad, ys[idxs.Count - 1]));
                        using (var br = new SolidBrush(blueC)) g.FillPolygon(br, poly.ToArray());

                        // 灰折旧段：base 内左段 [0, grayW]，表示这部分蓝已沉淀
                        var gp = new List<Point>();
                        gp.Add(new Point(LeftPad, ys[0]));
                        for (int t = 0; t < idxs.Count; t++) gp.Add(new Point(LeftPad + grayWs[t], ys[t]));
                        gp.Add(new Point(LeftPad, ys[idxs.Count - 1]));
                        using (var br = new SolidBrush(grayC)) g.FillPolygon(br, gp.ToArray());

                        // 红叠层：从左侧（底部）起覆盖蓝底左段 [0, redW]，与灰段同侧起点；
                        // 强换手档位显示红、沉淀档位显示灰、其余为蓝
                        var rp = new List<Point>();
                        rp.Add(new Point(LeftPad, ys[0]));
                        for (int t = 0; t < idxs.Count; t++) rp.Add(new Point(LeftPad + redWs[t], ys[t]));
                        rp.Add(new Point(LeftPad, ys[idxs.Count - 1]));
                        using (var br = new SolidBrush(redC)) g.FillPolygon(br, rp.ToArray());

                        // 蓝色轮廓细线：灰铺满后蓝被覆盖，用蓝色描边保证「蓝至少一条细线」始终可见；
                        // 只在**有筹码**（baseWs>0）的档位描边，无筹码档跳过，避免空白区画出竖线。
                        using (var pen = new Pen(blueC, 1.5f))
                        {
                            var bp = new List<Point>();
                            for (int t = 0; t < idxs.Count; t++)
                            {
                                // 以「真实占比 > 0」为据：极薄但有筹码的价位（之前因像素宽舍入=0 被误杀）
                                // 仍连入轮廓，避免峰形中间断开；纯空白档（真无筹码）才跳过。
                                if (pct[idxs[t]] <= 0) continue;
                                bp.Add(new Point(LeftPad + baseWs[t], ys[t]));
                            }
                            if (bp.Count >= 2) g.DrawLines(pen, bp.ToArray());
                        }
                    }
                }
                else if (emaMode)
                {
                    // 先画红底（= 全量分布：强换手+锁仓），再在红底上叠灰（锁仓）。
                    // gray 与 pct 同一归一尺度、单箱 ≤ pct，故灰宽永不超过红宽。
                    Color active = C.Up;                          // 红：活跃/强换手
                    Color lockedC = Color.FromArgb(150, 156, 168); // 灰：锁仓（中性灰）
                    var gIdx = new List<int>(); var gWs = new List<int>(); var gYs = new List<int>();
                    for (int i = 0; i < pct.Length && i < _bins.Count; i++)
                    {
                        ChipBin b = _bins[i];
                        if (b.Hi < lo || b.Lo > hi) continue;
                        int w = (int)Math.Round(pct[i] / maxPct * plotW);
                        if (w < 0) w = 0; if (w > plotW) w = plotW;
                        int yc = (int)yOf(b.Price);
                        if (yc < top) yc = top; if (yc > bottom) yc = bottom;
                        gIdx.Add(i); gWs.Add(w); gYs.Add(yc);
                    }
                    if (gIdx.Count > 0)
                    {
                        var poly = new List<Point>();
                        poly.Add(new Point(LeftPad, gYs[0]));
                        for (int t = 0; t < gIdx.Count; t++) poly.Add(new Point(LeftPad + gWs[t], gYs[t]));
                        poly.Add(new Point(LeftPad, gYs[gIdx.Count - 1]));
                        using (var br = new SolidBrush(active)) g.FillPolygon(br, poly.ToArray());
                        var gp = new List<Point>();
                        gp.Add(new Point(LeftPad, gYs[0]));
                        for (int t = 0; t < gIdx.Count; t++)
                        {
                            int w = (int)Math.Round(gray[gIdx[t]] / maxPct * plotW);
                            if (w < 0) w = 0; if (w > plotW) w = plotW;
                            gp.Add(new Point(LeftPad + w, gYs[t]));
                        }
                        gp.Add(new Point(LeftPad, gYs[gIdx.Count - 1]));
                        using (var br = new SolidBrush(lockedC)) g.FillPolygon(br, gp.ToArray());
                    }
                }
                else
                {
                    Color profit = dark ? C.ProfitDark : C.ProfitLight;
                    Color locked = dark ? C.LockedDark : C.LockedLight;

                    // 先收集价格轴内各分箱的「顶点」：(左侧轴 + 占比宽度, 该箱中心价)
                    var idxs = new List<int>();
                    var ws = new List<int>();
                    var ys = new List<int>();
                    var profs = new List<bool>();
                    for (int i = 0; i < pct.Length && i < _bins.Count; i++)
                    {
                        ChipBin b = _bins[i];
                        if (b.Hi < lo || b.Lo > hi) continue;       // 价格轴之外的分箱不画（会盖住统计区）
                        int w = (int)Math.Round(pct[i] / maxPct * plotW);
                        if (w < 0) w = 0;
                        if (w > plotW) w = plotW;
                        int yc = (int)yOf(b.Price);
                        if (yc < top) yc = top;
                        if (yc > bottom) yc = bottom;
                        idxs.Add(i); ws.Add(w); ys.Add(yc); profs.Add(b.Price <= refClose);
                    }
                    // 逐段成面：连续且同色的一段连成一个多边形，避免逐个填充产生接缝。
                    // 配色按价格相对收盘价单调划分，故实际最多两段（获利段 / 套牢段）。
                    int k2 = 0;
                    while (k2 < idxs.Count)
                    {
                        bool isProfit = profs[k2];
                        int j = k2;
                        while (j + 1 < idxs.Count && profs[j + 1] == isProfit
                               && idxs[j + 1] == idxs[j] + 1) j++;
                        var poly = new List<Point>();
                        poly.Add(new Point(LeftPad, ys[k2]));            // 左轴上端点
                        for (int t = k2; t <= j; t++)                    // 峰形外轮廓
                            poly.Add(new Point(LeftPad + ws[t], ys[t]));
                        poly.Add(new Point(LeftPad, ys[j]));             // 左轴下端点，闭合
                        using (var br = new SolidBrush(isProfit ? profit : locked))
                            g.FillPolygon(br, poly.ToArray());
                        k2 = j + 1;
                    }
                }

                // 当日收盘 / 平均成本参考线
                using (var penClose = new Pen(C.LineGold))
                {
                    penClose.DashStyle = DashStyle.Dash;
                    int yc = (int)yOf(refClose);
                    if (yc >= top && yc <= bottom) g.DrawLine(penClose, LeftPad - 3, yc, Width - RightPad, yc);
                }
                if (st != null && st.AvgCost != null)
                {
                    using (var penAvg = new Pen(C.FlatSoft))
                    {
                        penAvg.DashStyle = DashStyle.Dot;
                        int ya = (int)yOf(st.AvgCost.Value);
                        if (ya >= top && ya <= bottom) g.DrawLine(penAvg, LeftPad - 3, ya, Width - RightPad, ya);
                    }
                }

                // 横坐标刻度：横轴满宽 = 最高峰占比，四等分画短刻度。
                // 峰值占比的具体百分比不再在此标注（改由 PEAK 指标统一呈现），避免与指标数字打架。
                if (plotH > 30)
                {
                    using (var penTick = new Pen(grid))
                    {
                        for (int t = 0; t <= 4; t++)
                        {
                            int xt = LeftPad + (int)Math.Round(plotW * t / 4.0);
                            g.DrawLine(penTick, xt, bottom, xt, bottom + 3);
                        }
                    }
                }

                // 统计区（价格区之下）：平均成本 / 获利比例 / 峰位 / 集中度
                var lines = new List<string>();
                if (st != null)
                {
                    if (st.AvgCost != null) lines.Add("平均成本 " + st.AvgCost.Value.ToString("F2"));
                    if (st.ProfitRatio != null) lines.Add("获利比例 " + st.ProfitRatio.Value.ToString("F1") + "%");
                    if (st.PeakPrice != null) lines.Add("峰位价 " + st.PeakPrice.Value.ToString("F2"));
                    if (st.Scr90 != null) lines.Add("集中度90 " + (st.Scr90.Value * 100).ToString("F1"));
                }
                lines.Add("收盘 " + refClose.ToString("F2"));
                if (!string.IsNullOrEmpty(_note)) lines.Add(_note);
                float y = bottom + 6;
                foreach (string s in lines)
                {
                    using (var b2 = new SolidBrush(s == _note && !string.IsNullOrEmpty(_note)
                        ? C.Flat : text))
                    {
                        g.DrawString(s, Font, b2, 2, y);
                    }
                    y += 15;
                    if (y > Height - 2) break;
                }
            }
        }
    }
}
