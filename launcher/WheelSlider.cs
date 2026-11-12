using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Windows.Forms;

namespace StockPool
{
    /// <summary>
    /// 竖直滑条：数字滚轮右侧的**大范围**调节手段。
    /// 滚轮一格一格拨，值域大时（如 0~5000）太慢；滑条一次能拖很远，再用滚轮微调。
    /// 方向：**顶端 = 最大值，底端 = 最小值**（与数字滚轮同向：向上 = 数值大）。
    /// 拖动时按 step 吸附，保证取到的值永远是合法刻度。
    /// 自绘而不用原生 ScrollBar：原生滚动条被拉伸会画成白底怪横条，也不跟随深色主题（实测）。
    /// </summary>
    internal sealed class WheelSlider : Control
    {
        private const int ArrowH = 18;      // 上下箭头区高度
        private const int ThumbH = 26;      // 滑块高度
        private const int TrackW = 8;       // 轨道宽度
        private const int ThumbW = 20;      // 滑块宽度

        private readonly decimal _min, _max, _step;
        private decimal _value;
        private readonly Color _trackColor, _thumbColor, _arrowColor;
        private bool _dragging;

        /// <summary>用户操作导致的值变化（外部赋值不触发，避免与滚轮互相回灌）。</summary>
        public event EventHandler ValueChanged;

        public WheelSlider(decimal min, decimal max, decimal step, decimal value,
                           Color bg, Color track, Color thumb, Color arrow)
        {
            _min = min;
            _max = max < min ? min : max;
            _step = step <= 0 ? 1m : step;
            _value = ClampTo(value);

            _trackColor = track;
            _thumbColor = thumb;
            _arrowColor = arrow;

            SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.UserPaint
                   | ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true);
            SetStyle(ControlStyles.Selectable, true);
            TabStop = true;
            Cursor = Cursors.Hand;
            // 不能用 Color.Transparent：自定义 Control 默认不支持透明底色。
            BackColor = bg;
        }

        public decimal Value
        {
            get { return _value; }
            set
            {
                decimal v = ClampTo(value);
                if (v == _value) { Invalidate(); return; }
                _value = v;
                Invalidate();
            }
        }

        public decimal Minimum { get { return _min; } }
        public decimal Maximum { get { return _max; } }
        public decimal Step { get { return _step; } }

        private decimal ClampTo(decimal v)
        {
            if (v < _min) return _min;
            if (v > _max) return _max;
            return v;
        }

        // ---- 几何 ----
        private int TrackTop { get { return ArrowH + 6; } }
        private int TrackBottom { get { return Height - ArrowH - 6; } }
        private int TrackHeight { get { return Math.Max(1, TrackBottom - TrackTop); } }

        /// <summary>值 → 比例：1 = 顶端（最大），0 = 底端（最小）。</summary>
        private double Frac()
        {
            if (_max == _min) return 1;
            double p = (double)(_value - _min) / (double)(_max - _min);
            if (p < 0) p = 0;
            if (p > 1) p = 1;
            return p;
        }

        private int ThumbTop
        {
            get
            {
                int span = Math.Max(1, TrackHeight - ThumbH);
                return TrackTop + (int)Math.Round((1 - Frac()) * span);
            }
        }

        private int ThumbLeft { get { return (Width - ThumbW) / 2; } }

        // ---- 绘制 ----
        protected override void OnPaint(PaintEventArgs e)
        {
            Graphics g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;

            int cx = Width / 2;
            int x0 = cx - TrackW / 2;

            using (var br = new SolidBrush(_trackColor))
            using (GraphicsPath path = RoundedRect(x0, TrackTop, TrackW, TrackHeight, TrackW / 2))
                g.FillPath(br, path);

            int ty = ThumbTop;
            using (var br = new SolidBrush(_thumbColor))
            using (GraphicsPath path = RoundedRect(ThumbLeft, ty, ThumbW, ThumbH, 6))
                g.FillPath(br, path);

            DrawChevron(g, cx, ArrowH / 2 + 2, false);
            DrawChevron(g, cx, Height - ArrowH / 2 - 2, true);
        }

        private void DrawChevron(Graphics g, int cx, int cy, bool down)
        {
            using (var pen = new Pen(_arrowColor, 1.6f))
            {
                pen.StartCap = LineCap.Round;
                pen.EndCap = LineCap.Round;
                if (down)
                    g.DrawLines(pen, new Point[] { new Point(cx - 4, cy - 2), new Point(cx, cy + 3), new Point(cx + 4, cy - 2) });
                else
                    g.DrawLines(pen, new Point[] { new Point(cx - 4, cy + 2), new Point(cx, cy - 3), new Point(cx + 4, cy + 2) });
            }
        }

        private static GraphicsPath RoundedRect(int x, int y, int w, int h, int r)
        {
            GraphicsPath p = new GraphicsPath();
            int d = Math.Min(r * 2, Math.Min(w, h));
            p.AddArc(x, y, d, d, 180, 90);
            p.AddArc(x + w - d, y, d, d, 270, 90);
            p.AddArc(x + w - d, y + h - d, d, d, 0, 90);
            p.AddArc(x, y + h - d, d, d, 90, 90);
            p.CloseFigure();
            return p;
        }

        // ---- 交互 ----
        /// <summary>按 step 微调（正数 = 数值变大）。</summary>
        public void Nudge(int n)
        {
            SetValue(_value + n * _step, true);
        }

        private void SetValue(decimal v, bool notify)
        {
            decimal nv = ClampTo(v);
            bool changed = nv != _value;
            _value = nv;
            Invalidate();
            if (changed && notify && ValueChanged != null) ValueChanged(this, EventArgs.Empty);
        }

        /// <summary>位置 → 值（按 step 吸附）。</summary>
        private void SetFromY(int y)
        {
            int span = Math.Max(1, TrackHeight - ThumbH);
            double f = 1 - (y - TrackTop - ThumbH / 2.0) / span;
            if (f < 0) f = 0;
            if (f > 1) f = 1;
            int steps = (int)Math.Round((double)(_max - _min) / (double)_step);
            int idx = (int)Math.Round(f * steps);
            if (idx < 0) idx = 0;
            if (idx > steps) idx = steps;
            SetValue(_min + idx * _step, true);
        }

        protected override void OnMouseWheel(MouseEventArgs e)
        {
            base.OnMouseWheel(e);
            int n = e.Delta / 120;
            if (n == 0) return;
            Nudge(n);
        }

        protected override void OnMouseDown(MouseEventArgs e)
        {
            base.OnMouseDown(e);
            Focus();
            if (e.Y <= ArrowH) { Nudge(1); return; }
            if (e.Y >= Height - ArrowH) { Nudge(-1); return; }
            int ty = ThumbTop;
            if (e.Y >= ty && e.Y <= ty + ThumbH)
            {
                _dragging = true;
                Capture = true;
            }
            else
            {
                SetFromY(e.Y);          // 点轨道 = 跳到该位置
            }
        }

        protected override void OnMouseMove(MouseEventArgs e)
        {
            base.OnMouseMove(e);
            if (!_dragging) return;
            SetFromY(e.Y);
        }

        protected override void OnMouseUp(MouseEventArgs e)
        {
            base.OnMouseUp(e);
            _dragging = false;
            Capture = false;
        }

        protected override void OnKeyDown(KeyEventArgs e)
        {
            base.OnKeyDown(e);
            if (e.KeyCode == Keys.Up) { Nudge(1); e.Handled = true; }
            else if (e.KeyCode == Keys.Down) { Nudge(-1); e.Handled = true; }
            else if (e.KeyCode == Keys.PageUp) { Nudge(5); e.Handled = true; }
            else if (e.KeyCode == Keys.PageDown) { Nudge(-5); e.Handled = true; }
        }

        protected override bool IsInputKey(Keys keyData)
        {
            if (keyData == Keys.Up || keyData == Keys.Down
                || keyData == Keys.PageUp || keyData == Keys.PageDown) return true;
            return base.IsInputKey(keyData);
        }
    }
}
