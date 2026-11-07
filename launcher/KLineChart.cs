using System;
using System.Collections.Generic;
using System.Linq;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Windows.Forms;

namespace StockPool
{
    // 指标副图（lower 面板）数据结构：一个面板含若干序列（线 / 柱）
    internal sealed class ChartSeries
    {
        public string Name;
        public string Kind;          // "line" | "bar"
        public List<double> Data;    // 与 K 线等长（按日期对齐），NaN 表示缺失
        public Color Color;
        public float Width = 1.4f;   // 线宽（通达信 LINETHICK2 → 2f）
        // 「阈值以上转红」高亮：对齐通达信 RPS 副图里的
        // IF(RPS>=M, RPS, DRAWNULL), COLORRED；只在相邻两点都 ≥ 阈值时整段画红。
        public bool HasHi;
        public double HiThreshold;
        public Color HiColor;
    }

    internal sealed class LowerPanel
    {
        public string Id;
        public string Title;
        public int Weight = 1;
        public List<ChartSeries> Series = new List<ChartSeries>();
    }

    /// <summary>筹码分布的一根价格柱（后端 bins[] 的一项）。</summary>

    internal sealed class KLineChart : ChartControl
    {
        private const int LeftPad = 54;
        private const int RightPad = 10;
        private const int TopPad = 44;   // 顶部两行信息（图例 + 悬停 OHLC），把浮窗移到图外避免遮挡 K 线
        private const int BottomPad = 18;
        private const int VolRatio = 22;   // 成交量区占纵向比例（%）

        private List<KBar> _bars = new List<KBar>();      // 当前展示口径（指向 _qfq / _raw）
        private readonly List<KBar> _qfq = new List<KBar>();
        private readonly List<KBar> _raw = new List<KBar>();
        private string _adjust = "qfq";                    // qfq 前复权 / raw 不复权
        private readonly List<KMark> _marks = new List<KMark>();
        private List<LowerPanel> _indicators = new List<LowerPanel>();   // 指标副图（lower）
        private List<ChartSeries> _mainIndicators = new List<ChartSeries>();   // 主图叠加指标（main 面板）
        private int _range = 250;                          // 可见 K 线根数，0 = 全部
        private int _offset = 0;                           // 平移：从最新端向左偏移的根数
        private bool _dragging = false;
        private int _dragStartX = 0;
        private int _dragStartOffset = 0;
        private readonly ToolTip _tip = new ToolTip();
        private int _hoverIndex = -1;
        private int _hoverY = -1;          // 鼠标 Y（悬停水平十字线 + 左轴价格标签用）
        private int _wstart;                          // 当前可见窗口的全局起始索引（图例取光标数值用）
        public string Period = "day";                      // K 线周期（图例显示 日K / 周K / 月K）
        public Action<int> OnRangeChanged;                 // 缩放改变范围时通知外部刷新高亮
        /// <summary>可见窗口**平移**（_offset 变、_range 不变，如键盘光标到边缘 auto-pan / 拖动）时通知外部，
        /// 让右侧筹码按新的可见窗口重算——否则平移到更早日期后，光标会落到筹码快照之外对不上。</summary>
        public Action OnViewChanged;
        /// <summary>光标所在 K 线的日期（移出时保留最后一次）——右侧筹码窗口据此切换到那一天。</summary>
        public Action<string> OnHoverDate;
        private string _hoverDate = null;

        /// <summary>最近一次光标所在的交易日；移出图表不清空，便于重算后回到原处。</summary>
        public string CursorDate { get { return _hoverDate; } }

        // 价格轴几何（每次 OnPaint 记录）：右侧「筹码分布」窗口按同一价格轴对齐，
        // 使筹码峰的高度位置与 K 线的价格坐标严格对应。
        private double _axisLo, _axisHi;
        private int _axisTop, _axisBottom;
        private bool _axisReady;
        public event Action PriceAxisChanged;

        /// <summary>取当前价格轴：[lo, hi] 映射到纵向 [top, bottom]；无数据时返回 false。</summary>
        public bool TryGetPriceAxis(out double lo, out double hi, out int top, out int bottom)
        {
            lo = _axisLo; hi = _axisHi; top = _axisTop; bottom = _axisBottom;
            return _axisReady;
        }

        private void PublishAxis(double lo, double hi, int top, int bottom)
        {
            bool changed = !_axisReady || Math.Abs(_axisLo - lo) > 1e-9
                || Math.Abs(_axisHi - hi) > 1e-9 || _axisTop != top || _axisBottom != bottom;
            _axisLo = lo; _axisHi = hi; _axisTop = top; _axisBottom = bottom;
            _axisReady = true;
            if (changed && PriceAxisChanged != null) PriceAxisChanged();
        }

        private void ClearAxis()
        {
            bool changed = _axisReady;
            _axisReady = false;
            if (changed && PriceAxisChanged != null) PriceAxisChanged();
        }

        public KLineChart()
        {
            // SetStyle 已上移到基类 ChartControl（第 07 项）
            Height = 470;
            _tip.AutoPopDelay = 5000;
            _tip.InitialDelay = 120;
            _tip.ReshowDelay = 120;
            DoubleBuffered = true;
        }

        public void SetData(List<KBar> qfq, List<KBar> raw, List<KMark> marks)
        {
            _qfq.Clear();
            if (qfq != null) _qfq.AddRange(qfq);
            _raw.Clear();
            if (raw != null) _raw.AddRange(raw);
            _marks.Clear();
            if (marks != null) _marks.AddRange(marks);
            _offset = 0;
            // ⭐ 换股 / 刷新数据前关掉悬停浮窗：数据整批换掉后旧浮窗毫无意义，
            // 且 _tip.Show 没给时长参数，不主动 Hide 会一直挂着（深色下即白块）。
            _tip.Hide(this);
            ApplyAdjust();
        }

        public void SetAdjust(string a)
        {
            if (_adjust == a) return;
            _adjust = a;
            _offset = 0;
            ApplyAdjust();
        }

        private void ApplyAdjust()
        {
            _bars = (_adjust == "raw" && _raw.Count > 0) ? _raw : _qfq;
            Invalidate();
        }

        public void SetRange(int r)
        {
            _range = r;
            _offset = 0;
            Invalidate();
        }

        /// <summary>设置下方指标副图（lower 面板）。传空列表即清除。</summary>
        public void SetIndicators(List<LowerPanel> panels)
        {
            _indicators = panels ?? new List<LowerPanel>();
            Invalidate();
        }

        /// <summary>设置主图叠加指标（main 面板）。传空列表即清除。
        /// MA 等主图指标都走这一条通道（与副图同一套规则），不再单独处理。</summary>
        public void SetMainIndicators(List<ChartSeries> series)
        {
            _mainIndicators = series ?? new List<ChartSeries>();
            Invalidate();
        }

        /// <summary>当前显示口径下的交易日序列（副图数据按此对齐）。</summary>
        public List<string> GetDates()
        {
            var d = new List<string>();
            foreach (var b in _bars) d.Add(b.Date);
            return d;
        }

        // GetPreferredSize 已上移到基类 ChartControl（第 07 项）

        protected override void OnMouseDown(MouseEventArgs e)
        {
            base.OnMouseDown(e);
            if (e.Button == MouseButtons.Left && _bars.Count > 0)
            {
                _dragging = true;
                _dragStartX = e.X;
                _dragStartOffset = _offset;
                this.Capture = true;
            }
        }

        protected override void OnMouseUp(MouseEventArgs e)
        {
            base.OnMouseUp(e);
            if (_dragging) { _dragging = false; this.Capture = false; }
        }

        protected override void OnMouseEnter(EventArgs e)
        {
            base.OnMouseEnter(e);
            FocusForKeys();
        }

        /// <summary>聚焦图表以便接收**滚轮缩放与方向键**；但外层 AutoScroll 容器会顺手把图表
        /// 滚入视口，导致上方查询区被顶出屏幕——聚焦后把滚动位置还原回去。</summary>
        public void FocusForKeys()
        {
            if (!this.TabStop || this.Focused) return;
            ScrollableControl host = FindScrollHost();
            Point saved = host != null ? host.AutoScrollPosition : Point.Empty;
            this.Focus();
            if (host != null)
                host.AutoScrollPosition = new Point(-saved.X, -saved.Y);
        }

        private ScrollableControl FindScrollHost()
        {
            Control c = this.Parent;
            while (c != null)
            {
                ScrollableControl s = c as ScrollableControl;
                if (s != null && s.AutoScroll) return s;
                c = c.Parent;
            }
            return null;
        }

        protected override void OnMouseMove(MouseEventArgs e)
        {
            base.OnMouseMove(e);
            int n = VisibleCount();
            if (n <= 0) { _hoverIndex = -1; _tip.Hide(this); return; }
            int plotW = Math.Max(20, Width - LeftPad - RightPad);

            if (_dragging)
            {
                int dx = e.X - _dragStartX;
                int total = _bars.Count;
                int deltaBars = (int)Math.Round(dx * (double)n / plotW);   // 右拖露出更早期数据
                _offset = Math.Max(0, Math.Min(_dragStartOffset + deltaBars, total - n));
                _hoverIndex = -1;
                _hoverY = -1;
                _tip.Hide(this);
                Invalidate();
                if (OnViewChanged != null) OnViewChanged();   // 拖动平移：让筹码按新窗口重算（前端防抖）
                return;
            }

            double slot = (double)plotW / n;
            int idx = (int)((e.X - LeftPad) / slot);
            if (idx < 0) idx = 0;
            if (idx >= n) idx = n - 1;
            _hoverIndex = idx;
            _hoverY = e.Y;
            Invalidate();
            PublishHoverDate();
            ShowHoverTip(e.X, e.Y);
        }

        // ---- 键盘：↑↓ 缩放、←→ 移动光标（与鼠标滚轮 / 悬停同一套实现）----

        protected override void OnPreviewKeyDown(PreviewKeyDownEventArgs e)
        {
            base.OnPreviewKeyDown(e);
            // 方向键默认被容器当成「移动焦点」吞掉，这里声明由图表自己处理
            if (e.KeyCode == Keys.Up || e.KeyCode == Keys.Down
                || e.KeyCode == Keys.Left || e.KeyCode == Keys.Right)
                e.IsInputKey = true;
        }

        protected override void OnKeyDown(KeyEventArgs e)
        {
            base.OnKeyDown(e);
            if (_bars.Count == 0) return;
            if (e.KeyCode == Keys.Up) { Zoom(-20); e.Handled = true; e.SuppressKeyPress = true; }
            else if (e.KeyCode == Keys.Down) { Zoom(20); e.Handled = true; e.SuppressKeyPress = true; }
            else if (e.KeyCode == Keys.Left) { MoveCursor(-1); e.Handled = true; e.SuppressKeyPress = true; }
            else if (e.KeyCode == Keys.Right) { MoveCursor(1); e.Handled = true; e.SuppressKeyPress = true; }
        }

        /// <summary>缩放：step&lt;0 放大（更少根），step&gt;0 缩小（更多根）。滚轮与 ↑↓ 共用。
        /// 以光标处 K 线为锚点缩放，避免「窗口总往最新端缩、看不到光标附近」。</summary>
        private void Zoom(int step)
        {
            int total = _bars.Count;
            if (total == 0) return;
            // ⭐ 悬停浮窗随缩放关闭（bug 修复）：_tip.Show 没给时长参数，会一直挂着；
            // 缩放会改变每根 K 线的屏幕位置，不关的话浮窗就冻结在旧位置、显示旧数据，
            // 在深色主题下就是一块醒目的白色小方块（用户报的「白块」症状之一）。
            _tip.Hide(this);
            int nOld = VisibleCount();
            // 锚定全局索引：优先光标处，未悬停时用窗口中心
            int anchor = (_hoverIndex >= 0 ? _hoverIndex : nOld / 2);
            int curGi = _wstart + anchor;
            double frac = nOld > 0 ? (anchor + 0.5) / nOld : 0.5;
            int cur = _range > 0 ? _range : total;
            int next = Math.Max(20, Math.Min(total, cur + step));
            _range = (next >= total) ? 0 : next;
            int nNew = VisibleCount();
            if (nNew > 0)
            {
                // 让锚定 K 线缩放后仍落在屏幕同一相对位置（frac）
                int newWstart = (int)Math.Round(curGi - frac * nNew);
                _offset = Math.Max(0, Math.Min(total - nNew, total - nNew - newWstart));
            }
            if (_hoverIndex >= nNew) _hoverIndex = nNew - 1;      // 缩小后光标可能越界
            Invalidate();
            if (OnRangeChanged != null) OnRangeChanged(_range);
            if (OnViewChanged != null) OnViewChanged();           // 缩放也改了可见窗口，让筹码重算
        }

        /// <summary>光标左右移动一根；走到可视区边缘时**自动平移窗口**（与行情软件一致）。</summary>
        private void MoveCursor(int delta)
        {
            int n = VisibleCount();
            if (n <= 0) return;
            int total = _bars.Count;
            if (_hoverIndex < 0) _hoverIndex = n - 1;        // 首次按键从最新一根起步
            int next = _hoverIndex + delta;
            int oldOffset = _offset;
            if (next < 0)
            {   // 已在最左：把窗口往早期挪，露出更老的 K 线
                if (_offset < total - n) _offset = Math.Min(_offset + 1, total - n);
                next = 0;
            }
            else if (next >= n)
            {   // 已在最右：把窗口往最新挪
                if (_offset > 0) _offset -= 1;
                next = n - 1;
            }
            _hoverIndex = next;
            _hoverY = -1;
            Invalidate();
            if (_offset != oldOffset && OnViewChanged != null) OnViewChanged();   // 平移了：让筹码按新窗口重算
            PublishHoverDate();
            ShowHoverTip();
        }

        /// <summary>把光标所在交易日广播出去（右侧筹码窗口据此切换）。</summary>
        private void PublishHoverDate()
        {
            var vis = VisibleBars();
            int idx = _hoverIndex;
            if (idx < 0 || idx >= vis.Count) return;
            _hoverDate = vis[idx].Date;
            if (OnHoverDate != null) OnHoverDate(_hoverDate);
        }

        /// <summary>悬停信息改为 OnPaint 顶部信息条绘制（避免浮窗遮挡 K 线），这里只清理旧浮窗。</summary>
        private void ShowHoverTip(int mouseX = -1, int mouseY = -1)
        {
            _tip.Hide(this);
        }

        protected override void OnMouseWheel(MouseEventArgs e)
        {
            base.OnMouseWheel(e);
            Zoom(e.Delta > 0 ? -20 : 20);   // 上滚放大（更少根），下滚缩小
        }

        protected override void OnMouseLeave(EventArgs e)
        {
            base.OnMouseLeave(e);
            _hoverIndex = -1;
            _hoverY = -1;
            _tip.Hide(this);
            Invalidate();
        }

        /// <summary>双击重置视图：回到默认近 1 年（250 根），清空光标/平移。</summary>
        protected override void OnMouseDoubleClick(MouseEventArgs e)
        {
            base.OnMouseDoubleClick(e);
            if (_bars.Count == 0) return;
            _range = 250;
            _offset = 0;
            _hoverIndex = -1;
            _hoverY = -1;
            _tip.Hide(this);
            Invalidate();
            if (OnRangeChanged != null) OnRangeChanged(_range);
            if (OnViewChanged != null) OnViewChanged();
        }

        private int VisibleCount()
        {
            return _range > 0 ? Math.Min(_range, _bars.Count) : _bars.Count;
        }

        private void VisibleWindow(out int start, out int count)
        {
            int total = _bars.Count;
            int n = VisibleCount();
            if (n <= 0) { start = 0; count = 0; return; }
            int off = Math.Max(0, Math.Min(_offset, total - n));
            start = Math.Max(0, total - n - off);
            count = Math.Min(n, total - start);
        }

        /// <summary>当前可见窗口起点到最新一根之间的根数（= total - start，含被平移藏到右侧的最新段）。
        /// 右侧筹码窗口用它作 days，frames 才能覆盖 K 线当前可见窗口（含键盘 / 拖动平移到更早的部分），
        /// 避免光标日期早于筹码快照起点时对不上。</summary>
        public int ViewBarsToLatest()
        {
            int total = _bars.Count;
            if (total <= 0) return 0;
            int s, c;
            VisibleWindow(out s, out c);
            return total - s;
        }

        private List<KBar> VisibleBars()
        {
            int s, c;
            VisibleWindow(out s, out c);
            if (c <= 0) return new List<KBar>();
            return _bars.GetRange(s, c);
        }

        private static string VolumeText(double v)
        {
            if (v >= 10000) return (v / 10000).ToString("F2") + "万";
            return v.ToString("F0");
        }

        /// <summary>价格轴标签智能格式化：高价股少小数位，避免「1234.56」越界挤压绘图区。</summary>
        private static string PriceText(double p)
        {
            double a = Math.Abs(p);
            if (a >= 1000) return p.ToString("F0");
            if (a >= 100) return p.ToString("F1");
            return p.ToString("F2");
        }

        // ---- 主图标记图标（marker）：前端预设常用图标，后端只传【图标序号】 ----
        // 序号约定（前后端共享，务必同步）：
        //   0 = 无   1 = 上箭头(红)   2 = 下箭头(绿)   3 = 钱袋(金)   4 = 菱形(黄)
        //   5 = 叉(警示红)            6 = 圆点(灰)
        private static readonly Color[] _iconColors = new Color[]
        {
            Color.Transparent,                      // 0 无
            Color.FromArgb(255, 152, 0),            // 1 上箭头（橙，区别于涨跌红绿）
            Color.FromArgb(38, 198, 218),           // 2 下箭头（青，区别于涨跌红绿）
            Color.FromArgb(212, 175, 55),           // 3 钱袋（金）
            C.RpsYellow,                            // 4 菱形（黄）
            C.UpErr,                                // 5 叉（警示红）
            C.FlatWarm,                             // 6 圆点（灰）
        };

        private static void DrawIcon(Graphics g, int iconId, int x, int y, int size)
        {
            if (iconId <= 0 || iconId >= _iconColors.Length) return;
            Color c = _iconColors[iconId];
            switch (iconId)
            {
                case 1: // 上箭头
                    using (var br = new SolidBrush(c))
                        g.FillPolygon(br, new Point[] {
                            new Point(x, y - size * 2), new Point(x - size, y), new Point(x + size, y) });
                    break;
                case 2: // 下箭头
                    using (var br = new SolidBrush(c))
                        g.FillPolygon(br, new Point[] {
                            new Point(x, y + size * 2), new Point(x - size, y), new Point(x + size, y) });
                    break;
                case 3: // 钱袋
                    DrawMoneyBag(g, x, y, size, c);
                    break;
                case 4: // 菱形
                    using (var br = new SolidBrush(c))
                        g.FillPolygon(br, new Point[] {
                            new Point(x, y - size * 2), new Point(x + size, y),
                            new Point(x, y + size * 2), new Point(x - size, y) });
                    break;
                case 5: // 叉
                    using (var pen = new Pen(c, 2f))
                    {
                        g.DrawLine(pen, x - size, y - size, x + size, y + size);
                        g.DrawLine(pen, x + size, y - size, x - size, y + size);
                    }
                    break;
                case 6: // 圆点
                    using (var br = new SolidBrush(c))
                        g.FillEllipse(br, x - size, y - size, size * 2, size * 2);
                    break;
            }
        }

        private static void DrawMoneyBag(Graphics g, int x, int y, int size, Color c)
        {
            using (var br = new SolidBrush(c))
            using (var pen = new Pen(c, 2f))
            {
                int w = size * 2, h = size * 2;
                g.FillEllipse(br, x - w / 2, y - h / 2 + 2, w, h - 2);          // 袋身
                g.DrawLine(pen, x - size / 2, y - h / 2 + 2, x + size / 2, y - h / 2 + 2);  // 束口
            }
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            try { OnPaintCore(e); }
            catch (Exception)
            {
                // 绘制异常兜底：绝不显示系统红叉，用背景色覆盖
                try
                {
                    using (var bg = new SolidBrush(BackColor))
                        e.Graphics.FillRectangle(bg, 0, 0, Width, Height);
                }
                catch { }
            }
        }

        private void OnPaintCore(PaintEventArgs e)
        {
            base.OnPaint(e);
            var g = e.Graphics;
            BeginPaint(g);                 // 抗锯齿统一由基类设置（第 07 项）

            bool dark = (BackColor.R + BackColor.G + BackColor.B) < 200;
            Color grid = dark ? Color.FromArgb(54, 58, 68) : Color.FromArgb(222, 224, 228);
            Color text = ForeColor;

            using (var bg = new SolidBrush(BackColor))
                g.FillRectangle(bg, 0, 0, Width, Height);

            var vis = VisibleBars();
            if (vis.Count == 0)
            {
                ClearAxis();
                DrawLegend(g, text, -1);
                using (var b = new SolidBrush(text))
                {
                    var area = new RectangleF(0, 24, Width, Math.Max(0, Height - 24));
                    g.DrawString("请输入股票代码开始分析", Font, b, area, FmtCenter);
                }
                return;
            }

            int n = vis.Count;
            int plotW = Math.Max(20, Width - LeftPad - RightPad);
            int totalH = Height - TopPad - BottomPad;
            // 面板栈：主图（价格 + MA）→ 下方区（成交量 + 指标副图 lower）
            double mainShare = 0.58;
            int plotTop = TopPad;
            int plotBottom = plotTop + (int)(totalH * mainShare);
            int lowerTop = plotBottom + 8;
            int lowerBottom = Height - BottomPad;
            int lowerH = Math.Max(10, lowerBottom - lowerTop);
            int indCount = _indicators.Count;
            int volH = (int)(lowerH * 0.42);
            int volTop = lowerTop;
            int volBottom = volTop + volH;
            int indTop = volBottom + 6;
            int indBottom = lowerBottom;
            // 指标副图矩形（每个均分指标区）
            var indRects = new List<Rectangle>();
            if (indCount > 0)
            {
                int gap = 6;
                int eachH = (indBottom - indTop - gap * (indCount - 1)) / indCount;
                int y = indTop;
                for (int p = 0; p < indCount; p++)
                {
                    indRects.Add(new Rectangle(LeftPad, y, plotW, Math.Max(12, eachH)));
                    y += eachH + gap;
                }
            }
            int bottomMost = (indCount > 0) ? indBottom : volBottom;

            // 价格区间（先按 K 线）
            double pmin = double.MaxValue, pmax = double.MinValue;
            foreach (KBar b in vis) { if (b.L < pmin) pmin = b.L; if (b.H > pmax) pmax = b.H; }
            int wstart, wcount;
            VisibleWindow(out wstart, out wcount);
            _wstart = wstart;
            // 主图叠加指标（MA / CYC 等）一并纳入价格区间，避免线画到图外（与副图同一规则）
            // marker 是信号值（+1/-1/0），不是价格，必须排除，否则价格轴会被撑到 -1
            foreach (ChartSeries s in _mainIndicators)
            {
                if (s.Data == null || s.Kind == "marker") continue;
                for (int i = 0; i < n; i++)
                {
                    int gi = wstart + i;
                    if (gi >= s.Data.Count) continue;
                    double v = s.Data[gi];
                    if (double.IsNaN(v)) continue;
                    if (v < pmin) pmin = v; if (v > pmax) pmax = v;
                }
            }
            if (pmax <= pmin) pmax = pmin + 1;
            double pad = (pmax - pmin) * 0.04;
            pmin -= pad; pmax += pad;
            PublishAxis(pmin, pmax, plotTop, plotBottom);   // 供右侧筹码窗口对齐

            double maxVol = 0;
            foreach (KBar b in vis) if (b.V > maxVol) maxVol = b.V;
            if (maxVol <= 0) maxVol = 1;

            double slot = (double)plotW / n;
            double bodyW = Math.Max(1, slot * 0.66);
            double span = pmax - pmin;
            Func<double, double> yOf = p => plotBottom - (p - pmin) / span * (plotBottom - plotTop);
            Func<int, double> xOf = i => LeftPad + slot * (i + 0.5);

            // 网格 + 价格刻度
            using (var pen = new Pen(grid))
            using (var b2 = new SolidBrush(text))
            {
                int gridN = 5;
                for (int i = 0; i <= gridN; i++)
                {
                    double p = pmin + span * i / gridN;
                    int y = (int)yOf(p);
                    g.DrawLine(pen, LeftPad, y, LeftPad + plotW, y);
                    g.DrawString(PriceText(p), Font, b2,
                        new RectangleF(2, y - 7, LeftPad - 4, Font.Height + 2), FmtRight);
                }
                g.DrawLine(pen, LeftPad, volBottom, LeftPad + plotW, volBottom);
            }

            // 蜡烛
            for (int i = 0; i < vis.Count; i++)
            {
                KBar b = vis[i];
                int x = (int)xOf(i);
                bool up = b.C >= b.O;
                Color c = up ? C.Up : C.Down;
                using (var pen = new Pen(c))
                using (var br = new SolidBrush(c))
                {
                    int yH = (int)yOf(b.H), yL = (int)yOf(b.L);
                    int yO = (int)yOf(b.O), yC = (int)yOf(b.C);
                    g.DrawLine(pen, x, yH, x, yL);
                    int top = Math.Min(yO, yC);
                    int h = Math.Max(1, Math.Abs(yC - yO));
                    g.FillRectangle(br, (int)(x - bodyW / 2), top, (int)bodyW, h);
                }
            }

            // 主图叠加指标（main 面板）：在价格轴范围内绘制线 / 柱 / 箭头
            foreach (ChartSeries s in _mainIndicators)
            {
                if (s.Data == null) continue;
                if (s.Kind == "marker")
                {
                    // 后端传图标序号（0=无），前端按 _iconColors 预设渲染。
                    // 锚点约定：上箭头(1)的 y 是【底边】，下箭头(2)的 y 是【顶边】；
                    // 箭头整体落在 K 线外侧并留 4px 间隙，避免与影线重合。
                    int size = 4;
                    for (int i = 0; i < n; i++)
                    {
                        int gi = wstart + i;
                        if (gi >= s.Data.Count) continue;
                        double v = s.Data[gi];
                        if (double.IsNaN(v) || v == 0) continue;
                        int iconId = (int)Math.Round(v);
                        int x = (int)xOf(i);
                        KBar kb = vis[i];
                        int y;
                        if (iconId == 2)
                        {
                            // 下箭头：顶边在最高价上方 4 + 2*size，整体在高点上方
                            y = (int)yOf(kb.H) - 4 - size * 2;
                            if (y < plotTop) y = plotTop;
                        }
                        else
                        {
                            // 上箭头：底边在最低价下方 4 + 2*size，整体在低点下方
                            y = (int)yOf(kb.L) + 4 + size * 2;
                            if (y > plotBottom) y = plotBottom;
                        }
                        DrawIcon(g, iconId, x, y, size);
                    }
                    continue;
                }
                else if (s.Kind == "bar")
                {
                    using (var br = new SolidBrush(s.Color))
                    {
                        for (int i = 0; i < n; i++)
                        {
                            int gi = wstart + i;
                            if (gi >= s.Data.Count) continue;
                            double v = s.Data[gi];
                            if (double.IsNaN(v)) continue;
                            int x = (int)xOf(i);
                            int y = (int)yOf(v);
                            int y0 = plotBottom;
                            int top = Math.Min(y, y0), hh = Math.Max(1, Math.Abs(y - y0));
                            g.FillRectangle(br, (int)(x - bodyW / 2), top, (int)bodyW, hh);
                        }
                    }
                }
                else
                {
                    using (var pen = new Pen(s.Color, 1.4f))
                    {
                        bool penUp = false;
                        for (int i = 0; i < n; i++)
                        {
                            int gi = wstart + i;
                            if (gi >= s.Data.Count) { penUp = false; continue; }
                            double v = s.Data[gi];
                            if (double.IsNaN(v)) { penUp = false; continue; }
                            int x = (int)xOf(i);
                            int y = (int)yOf(v);
                            if (penUp)
                            {
                                double pv = s.Data[wstart + i - 1];
                                g.DrawLine(pen, (int)xOf(i - 1), (int)yOf(pv), x, y);
                            }
                            penUp = true;
                        }
                    }
                }
            }

            // 成交量
            for (int i = 0; i < vis.Count; i++)
            {
                KBar b = vis[i];
                int x = (int)xOf(i);
                bool up = b.C >= b.O;
                Color c = up ? C.Up : C.Down;
                using (var br = new SolidBrush(c))
                {
                    int h = (int)(b.V / maxVol * (volBottom - volTop));
                    if (h < 1) h = 1;
                    g.FillRectangle(br, (int)(x - bodyW / 2), volBottom - h, (int)bodyW, h);
                }
            }

            // 指标副图（lower 面板）：与主图共享 x 轴 / 缩放 / 平移 / 悬停
            for (int p = 0; p < indCount; p++)
            {
                Rectangle rect = indRects[p];
                // 顶部预留一条图例带：标题 + 各序列「名称 当前值」画在这里，
                // 曲线 / 柱子只在图例带下方绘制 → 字与线不再重叠。
                const int LegendH = 14;
                Rectangle plot = new Rectangle(rect.X, rect.Y + LegendH,
                    rect.Width, Math.Max(10, rect.Height - LegendH));
                LowerPanel panel = _indicators[p];
                double ipmin = double.MaxValue, ipmax = double.MinValue;
                foreach (ChartSeries s in panel.Series)
                    for (int i = 0; i < n; i++)
                    {
                        int gi = wstart + i;
                        if (gi >= s.Data.Count) continue;
                        double v = s.Data[gi];
                        if (double.IsNaN(v)) continue;
                        if (v < ipmin) ipmin = v;
                        if (v > ipmax) ipmax = v;
                    }
                if (ipmax <= ipmin) ipmax = ipmin + 1;
                double ppad = (ipmax - ipmin) * 0.12; ipmin -= ppad; ipmax += ppad;
                double ispan = ipmax - ipmin;
                Func<double, double> yInd = val => plot.Bottom - (val - ipmin) / ispan * plot.Height;
                using (var pen = new Pen(grid))
                {
                    int gN = 2;
                    for (int i = 0; i <= gN; i++)
                    {
                        double v = ipmin + ispan * i / gN;
                        int y = (int)yInd(v);
                        g.DrawLine(pen, LeftPad, y, LeftPad + plotW, y);
                        using (var b2 = new SolidBrush(text)) g.DrawString(v.ToString("F2"), Font, b2, 2, y - 7);
                    }
                    g.DrawLine(pen, LeftPad, plot.Bottom, LeftPad + plotW, plot.Bottom);
                }
                foreach (ChartSeries s in panel.Series)
                {
                    if (s.Kind == "bar")
                    {
                        for (int i = 0; i < n; i++)
                        {
                            int gi = wstart + i;
                            if (gi >= s.Data.Count) continue;
                            double v = s.Data[gi];
                            if (double.IsNaN(v)) continue;
                            int x = (int)xOf(i);
                            Color c = v >= 0 ? C.Up : C.Down;
                            using (var br = new SolidBrush(c))
                            {
                                int zero = (int)yInd(0);
                                int top = v >= 0 ? (int)yInd(v) : zero;
                                int h = Math.Abs((int)yInd(v) - zero);
                                if (h < 1) h = 1;
                                g.FillRectangle(br, (int)(x - bodyW / 2), top, (int)bodyW, h);
                            }
                        }
                    }
                    else
                    {
                        using (var pen = new Pen(s.Color, s.Width))
                        {
                            bool penUp = false;
                            for (int i = 0; i < n; i++)
                            {
                                int gi = wstart + i;
                                if (gi >= s.Data.Count) { penUp = false; continue; }
                                double v = s.Data[gi];
                                if (double.IsNaN(v)) { penUp = false; continue; }
                                int x = (int)xOf(i);
                                int y = (int)yInd(v);
                                if (penUp)
                                {
                                    double pv = s.Data[wstart + i - 1];
                                    // 对齐 IF(RPS>=M,RPS,DRAWNULL),COLORRED：相邻两点都在阈值之上才整段转红
                                    pen.Color = (s.HasHi && pv >= s.HiThreshold && v >= s.HiThreshold)
                                        ? s.HiColor : s.Color;
                                    g.DrawLine(pen, (int)xOf(i - 1), (int)yInd(pv), x, y);
                                }
                                penUp = true;
                            }
                        }
                    }
                }
                // 悬停：在副图上标注光标处各序列数值（跟随光标，对应竖线交点画一个圆点）
                if (_hoverIndex >= 0 && _hoverIndex < n)
                {
                    int gi = wstart + _hoverIndex;
                    int hx = (int)xOf(_hoverIndex);
                    foreach (ChartSeries s in panel.Series)
                    {
                        if (s.Data == null || gi >= s.Data.Count) continue;
                        double v = s.Data[gi];
                        if (double.IsNaN(v)) continue;
                        int yv = (int)yInd(v);
                        using (var dot = new SolidBrush(s.Color))
                            g.FillEllipse(dot, hx - 3, yv - 3, 6, 6);
                    }
                }
                int lx = LeftPad + 4; int ly = rect.Top + 2;
                g.SetClip(rect);                 // 图例过长时裁剪在本面板内，不溢出到右侧筹码窗口
                using (var b = new SolidBrush(text))
                {
                    g.DrawString(panel.Title, Font, b, lx, ly);
                    lx += (int)(g.MeasureString(panel.Title, Font).Width) + 16;
                    // 图例实时显示光标所在交易日的指标值（移出图表则只显示名称）
                    int gi = _hoverIndex >= 0 ? wstart + _hoverIndex : -1;
                    foreach (ChartSeries s in panel.Series)
                    {
                        using (var pen = new Pen(s.Color, Math.Max(2f, s.Width))) g.DrawLine(pen, lx, ly + 7, lx + 14, ly + 7);
                        string label = s.Name;
                        if (gi >= 0 && s.Data != null && gi < s.Data.Count && !double.IsNaN(s.Data[gi]))
                            label += " " + s.Data[gi].ToString("F2");
                        g.DrawString(label, Font, b, lx + 18, ly);
                        lx += (int)(g.MeasureString(label, Font).Width) + 18;
                    }
                }
                g.ResetClip();
            }
            // 主图标记（入池/出池/事件的菱形、三角标注）已按需求移除

            // 悬停：十字线（竖线贯穿全图；水平线仅在主图价格区内）+ 左轴价格标签（带背景框）
            if (_hoverIndex >= 0 && _hoverIndex < vis.Count)
            {
                int x = (int)xOf(_hoverIndex);
                Color cross = dark ? Color.FromArgb(210, 215, 130) : Color.FromArgb(90, 95, 30);
                using (var pen = new Pen(cross, 1.2f))
                {
                    g.DrawLine(pen, x, plotTop, x, bottomMost);
                    if (_hoverY >= plotTop && _hoverY <= plotBottom)
                    {
                        g.DrawLine(pen, LeftPad, _hoverY, LeftPad + plotW, _hoverY);
                        double price = pmin + (plotBottom - _hoverY) / (plotBottom - plotTop) * span;
                        var tag = PriceText(price);
                        var tsz = g.MeasureString(tag, Font);
                        using (var bb = new SolidBrush(BackColor))
                            g.FillRectangle(bb, 2, _hoverY - 7, (int)tsz.Width + 2, Font.Height + 2);
                        using (var bb2 = new SolidBrush(cross))
                            g.DrawString(tag, Font, bb2,
                                new RectangleF(2, _hoverY - 7, LeftPad - 4, Font.Height + 2), FmtRight);
                    }
                }
            }

            // 日期刻度（跨年时显示 YY-MM-DD，否则 MM-DD）
            int labelN = Math.Min(8, vis.Count);
            if (labelN > 0)
            {
                bool crossYear = vis.Count > 1
                    && vis[0].Date.Length >= 4 && vis[vis.Count - 1].Date.Length >= 4
                    && vis[0].Date.Substring(0, 4) != vis[vis.Count - 1].Date.Substring(0, 4);
                using (var b3 = new SolidBrush(text))
                {
                    int step = labelN > 1 ? (vis.Count - 1) / (labelN - 1) : 0;
                    for (int i = 0; i < labelN; i++)
                    {
                        int idx = i < labelN - 1 ? i * step : vis.Count - 1;
                        string d = vis[idx].Date;
                        if (crossYear) { if (d.Length >= 7) d = d.Substring(2); }   // YY-MM-DD
                        else if (d.Length >= 5) d = d.Substring(5);                 // MM-DD
                        int x = (int)xOf(idx);
                        int labelW = step > 0 ? step : 1;   // 标签间距（根），避免矩形过窄被裁
                        g.DrawString(d, Font, b3, new RectangleF((float)(x - labelW * slot / 2), (float)(bottomMost + 2), (float)(labelW * slot), (float)BottomPad), FmtCenter);
                    }
                }
            }

            // 悬停时在下轴跟随光标显示该 K 线日期（联动坐标轴，独立于固定刻度，绘于最上层）
            if (_hoverIndex >= 0 && _hoverIndex < vis.Count)
            {
                string dd = vis[_hoverIndex].Date;
                var dsz = g.MeasureString(dd, Font);
                int x = (int)xOf(_hoverIndex);
                int dx = Math.Max(2, Math.Min(Width - (int)dsz.Width - 6, x - (int)dsz.Width / 2));
                int dy = bottomMost + 2;
                Color cross = dark ? Color.FromArgb(210, 215, 130) : Color.FromArgb(90, 95, 30);
                using (var bb = new SolidBrush(cross))
                    g.FillRectangle(bb, dx, dy, (int)dsz.Width + 4, Font.Height + 2);
                using (var bb2 = new SolidBrush(dark ? Color.Black : Color.White))
                    g.DrawString(dd, Font, bb2, dx + 2, dy);
            }

            DrawLegend(g, text, _hoverIndex);
            DrawHoverInfo(g, text);
            // 右下角的「滚轮缩放 · 拖动平移 · 前复权」提示已按需求移除；
            // 周期 / 复权状态看工具栏按钮的高亮即可（图例里也标了 日K / 周K / 月K）。
        }

        /// <summary>顶部图例：周期 + 主图叠加指标（MA / CYC 等，统一走 _mainIndicators）。
        /// hoverIdx &gt;= 0 时，在名称后实时显示光标所在交易日的数值（跟随光标）。</summary>
        private void DrawLegend(Graphics g, Color text, int hoverIdx)
        {
            int x = LeftPad + 4;
            int y = 4;
            using (var b = new SolidBrush(text))
            {
                // 周期写进图例：周线下这里就是「周K」，MA5 也是 5 周均线，别让人误读
                string label = (Period == "week") ? "周K" : (Period == "month") ? "月K" : "日K";
                g.DrawString(label, Font, b, x, y); x += 34;
                // 主图叠加指标（MA / CYC 等）：名称 + 光标处数值 —— 全部走同一套规则
                foreach (ChartSeries s in _mainIndicators)
                {
                    if (s.Kind == "marker")
                    {
                        // 图例：画一个代表性图标（hover 时取当日图标序号，否则用 1=上箭头）
                        int iconId = 1;
                        if (hoverIdx >= 0 && s.Data != null && _wstart + hoverIdx < s.Data.Count)
                        {
                            double hv = s.Data[_wstart + hoverIdx];
                            if (!double.IsNaN(hv) && hv != 0) iconId = (int)Math.Round(hv);
                        }
                        DrawIcon(g, iconId, x + 7, y + 5, 5);
                        g.DrawString(s.Name, Font, b, x + 18, y);
                        x += (int)(g.MeasureString(s.Name, Font).Width) + 14;
                        continue;
                    }
                    double v = double.NaN;
                    if (hoverIdx >= 0 && s.Data != null && _wstart + hoverIdx < s.Data.Count)
                        v = s.Data[_wstart + hoverIdx];
                    using (var pen = new Pen(s.Color, 2f)) g.DrawLine(pen, x, y + 7, x + 14, y + 7);
                    string lbl = s.Name;
                    if (!double.IsNaN(v)) lbl += " " + v.ToString("F2");
                    g.DrawString(lbl, Font, b, x + 18, y);
                    x += (int)(g.MeasureString(lbl, Font).Width) + 14;
                }
            }
        }

        /// <summary>顶部第二行：悬停时显示光标 K 线的 OHLC / 量 / 涨跌幅 / 振幅（不遮挡主图）。</summary>
        private void DrawHoverInfo(Graphics g, Color text)
        {
            var vis = VisibleBars();
            int idx = _hoverIndex;
            if (idx < 0 || idx >= vis.Count) return;
            KBar b = vis[idx];
            double chg = b.O != 0 ? (b.C - b.O) / b.O : 0;
            double amp = b.O != 0 ? (b.H - b.L) / b.O : 0;
            string s = string.Format(
                "{0}   开 {1:F2}  收 {2:F2}  高 {3:F2}  低 {4:F2}   量 {5}   涨跌 {6}{7:0.00%}  振幅 {8:0.00%}",
                b.Date, b.O, b.C, b.H, b.L, VolumeText(b.V), chg >= 0 ? "+" : "", chg, amp);
            using (var br = new SolidBrush(text))
                g.DrawString(s, Font, br, LeftPad + 4, 22);
        }
    }

    // ================= 自绘日 K 蜡烛图 =================
    internal sealed class KBar
    {
        public string Date;
        public double O, C, L, H, V;
    }

    internal sealed class KMark
    {
        public string Date;
        public string Kind;   // in / out / event
        public Color Color;
        public string Text;
    }
}
