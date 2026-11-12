using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Text;
using System.Windows.Forms;

namespace StockPool
{
    /// <summary>
    /// 数字滚轮（picker wheel）：一列数字上下滚动，**中间一行高亮 = 当前选中值**。
    ///
    /// 形态说明：这里刻意**不是**「轨道 + 滑块」那种滚动条 —— 用户要的是「滚数字」，
    /// 所以画的是一列可拨动的数字（上下淡出，中间一行加粗并带上下分隔线）。
    ///
    /// 交互：
    ///   · 鼠标滚轮：每格一个 step（向上滚数值变大）
    ///   · 拖动：按住中间一行上下拨，数字跟着走，松手吸附到最近一行
    ///   · 点击某一行：直接跳到那一行
    ///   · ↑↓ / PgUp / PgDn：微调
    ///
    /// 值域很大也没关系：只渲染当前值附近的几行，不把整个值域铺出来。
    /// </summary>
    internal sealed class WheelRoller : Control
    {
        private const int RowH = 28;          // 每行高度
        private const int VisibleRows = 7;    // 可见行数（奇数：中间一行是选中行）

        private readonly decimal _min, _max, _step;
        private readonly int _totalRows;      // 刻度总数 = (max-min)/step
        private decimal _value;

        private double _pos;                  // 选中行位置（行索引，可为小数 = 滚动中）
        private int _target;                  // 目标行索引

        private readonly Color _bg, _selBg, _divider, _textSel, _textDim;
        private readonly Font _font, _fontBold;

        private bool _dragging, _moved;
        private double _dragStartY, _dragStartPos;
        private int _pressedRow;

        private readonly Timer _anim;

        /// <summary>选中值变化（点/滚/拖动到新行时立刻触发，便于即时回写主界面）。</summary>
        public event EventHandler ValueChanged;

        public WheelRoller(decimal min, decimal max, decimal step, decimal value,
                           Color bg, Color selBg, Color divider, Color textSel, Color textDim)
        {
            _min = min;
            _max = max < min ? min : max;
            _step = step <= 0 ? 1m : step;
            _totalRows = (int)Math.Round((double)(_max - _min) / (double)_step);
            if (_totalRows < 0) _totalRows = 0;

            _bg = bg;
            _selBg = selBg;
            _divider = divider;
            _textSel = textSel;
            _textDim = textDim;

            _font = new Font("Microsoft YaHei UI", 10f);
            _fontBold = new Font("Microsoft YaHei UI", 12f, FontStyle.Bold);

            SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.UserPaint
                   | ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true);
            SetStyle(ControlStyles.Selectable, true);
            TabStop = true;
            Cursor = Cursors.Hand;
            // ⚠️ 不能用 Color.Transparent：自定义 Control 默认不支持透明底色，
            // 赋 Transparent 会抛「控件不支持透明的背景色」（实测：浮层直接弹不出来）。
            BackColor = bg;

            _value = ClampTo(value);
            _target = IndexOf(_value);
            _pos = _target;

            _anim = new Timer();
            _anim.Interval = 16;
            _anim.Tick += delegate
            {
                double d = _target - _pos;
                if (Math.Abs(d) < 0.002)
                {
                    _pos = _target;
                    _anim.Stop();
                }
                else
                {
                    _pos += d * 0.35;
                }
                Invalidate();
            };
        }

        public decimal Value
        {
            get { return _value; }
            set
            {
                decimal v = ClampTo(value);
                bool changed = v != _value;
                _value = v;
                int idx = IndexOf(v);
                _target = idx;
                _pos = idx;
                if (changed && ValueChanged != null) ValueChanged(this, EventArgs.Empty);
                Invalidate();
            }
        }

        public decimal Step { get { return _step; } }

        protected override void Dispose(bool disposing)
        {
            if (disposing)
            {
                _anim.Stop();
                _anim.Dispose();
                _font.Dispose();
                _fontBold.Dispose();
            }
            base.Dispose(disposing);
        }

        // ---- 值 <-> 行索引 ----
        private decimal ClampTo(decimal v)
        {
            if (v < _min) return _min;
            if (v > _max) return _max;
            return v;
        }

        private int IndexOf(decimal v)
        {
            int i = (int)Math.Round((double)(v - _min) / (double)_step);
            if (i < 0) i = 0;
            if (i > _totalRows) i = _totalRows;
            return i;
        }

        private decimal ValueAt(int row)
        {
            if (row < 0) row = 0;
            if (row > _totalRows) row = _totalRows;
            return _min + row * _step;
        }

        private static Color Blend(Color a, Color b, double t)
        {
            int r = (int)Math.Round(a.R + (b.R - a.R) * t);
            int g = (int)Math.Round(a.G + (b.G - a.G) * t);
            int bl = (int)Math.Round(a.B + (b.B - a.B) * t);
            return Color.FromArgb(r, g, bl);
        }

        // ---- 绘制 ----
        protected override void OnPaint(PaintEventArgs e)
        {
            Graphics g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;
            g.TextRenderingHint = TextRenderingHint.ClearTypeGridFit;

            using (var br = new SolidBrush(_bg))
                g.FillRectangle(br, 0, 0, Width, Height);

            int mid = VisibleRows / 2;
            float centerY = Height / 2f;

            // 中间高亮带（选中行）+ 上下分隔线
            float bandTop = centerY - RowH / 2f;
            using (var br = new SolidBrush(_selBg))
                g.FillRectangle(br, 0, bandTop, Width, RowH);
            using (var pen = new Pen(_divider))
            {
                g.DrawLine(pen, 3, bandTop, Width - 3, bandTop);
                g.DrawLine(pen, 3, bandTop + RowH, Width - 3, bandTop + RowH);
            }

            int first = (int)Math.Floor(_pos) - mid;
            using (var sf = new StringFormat
            {
                Alignment = StringAlignment.Center,
                LineAlignment = StringAlignment.Center,
                FormatFlags = StringFormatFlags.NoWrap
            })
            {
                for (int k = 0; k <= VisibleRows; k++)
                {
                    int row = first + k;
                    if (row < 0 || row > _totalRows) continue;
                    double dist = Math.Abs(row - _pos);
                    float y = centerY + (float)(row - _pos) * RowH;
                    if (y < -RowH || y > Height + RowH) continue;

                    Color c;
                    Font f;
                    if (dist < 0.5)
                    {
                        c = _textSel;
                        f = _fontBold;
                    }
                    else
                    {
                        double t = Math.Min(1.0, dist / mid);
                        c = Blend(_textDim, _bg, t * 0.85);
                        f = _font;
                    }
                    using (var br = new SolidBrush(c))
                    {
                        string txt = NumKit.Format(ValueAt(row), _step);
                        float avail = Width - 4;
                        SizeF sz = g.MeasureString(txt, f);
                        if (sz.Width > avail && sz.Width > 0)
                        {
                            // 列很窄时横向压扁，保证数字不越界
                            float scale = avail / sz.Width;
                            GraphicsState st = g.Save();
                            g.TranslateTransform(Width / 2f, y);
                            g.ScaleTransform(scale, 1f);
                            g.DrawString(txt, f, br,
                                         new RectangleF(-Width / 2f, -RowH / 2f, Width, RowH), sf);
                            g.Restore(st);
                        }
                        else
                        {
                            g.DrawString(txt, f, br,
                                         new RectangleF(0, y - RowH / 2f, Width, RowH), sf);
                        }
                    }
                }
            }
        }

        // ---- 交互 ----
        /// <summary>按 step 上下跳 n 格（正数 = 数值变大）。</summary>
        public void Nudge(int n)
        {
            SetTarget(_target + n);
        }

        private void SetTarget(int row)
        {
            if (row < 0) row = 0;
            if (row > _totalRows) row = _totalRows;
            bool changed = row != _target;
            _target = row;
            if (changed || _value != ValueAt(row))
            {
                _value = ValueAt(row);
                if (ValueChanged != null) ValueChanged(this, EventArgs.Empty);
            }
            if (_anim.Enabled) { _anim.Stop(); }
            _anim.Start();
            Invalidate();
        }

        protected override void OnMouseWheel(MouseEventArgs e)
        {
            base.OnMouseWheel(e);
            int n = e.Delta / 120;
            if (n == 0) return;
            Nudge(n);
        }

        private int RowAt(int y)
        {
            int row = (int)Math.Round(_pos + (y - Height / 2.0) / RowH);
            if (row < 0) row = 0;
            if (row > _totalRows) row = _totalRows;
            return row;
        }

        protected override void OnMouseDown(MouseEventArgs e)
        {
            base.OnMouseDown(e);
            Focus();
            _dragging = true;
            _moved = false;
            _dragStartY = e.Y;
            _dragStartPos = _pos;
            _pressedRow = RowAt(e.Y);
        }

        protected override void OnMouseMove(MouseEventArgs e)
        {
            base.OnMouseMove(e);
            if (!_dragging) return;
            double dy = e.Y - _dragStartY;
            if (Math.Abs(dy) > 3) _moved = true;
            if (!_moved) return;

            double p = _dragStartPos - dy / RowH;   // 向上拨 = 数值变大
            if (p < 0) p = 0;
            if (p > _totalRows) p = _totalRows;
            _pos = p;
            _anim.Stop();
            int row = (int)Math.Round(p);
            if (row != _target)
            {
                _target = row;
                _value = ValueAt(row);
                if (ValueChanged != null) ValueChanged(this, EventArgs.Empty);
            }
            Invalidate();
        }

        protected override void OnMouseUp(MouseEventArgs e)
        {
            base.OnMouseUp(e);
            if (!_dragging) return;
            _dragging = false;
            if (_moved) SetTarget((int)Math.Round(_pos));
            else SetTarget(_pressedRow);
        }

        protected override void OnKeyDown(KeyEventArgs e)
        {
            base.OnKeyDown(e);
            if (e.KeyCode == Keys.Up) { Nudge(1); e.Handled = true; }
            else if (e.KeyCode == Keys.Down) { Nudge(-1); e.Handled = true; }
            else if (e.KeyCode == Keys.PageUp) { Nudge(3); e.Handled = true; }
            else if (e.KeyCode == Keys.PageDown) { Nudge(-3); e.Handled = true; }
        }

        protected override bool IsInputKey(Keys keyData)
        {
            if (keyData == Keys.Up || keyData == Keys.Down
                || keyData == Keys.PageUp || keyData == Keys.PageDown) return true;
            return base.IsInputKey(keyData);
        }
    }
}
