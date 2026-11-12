using System;
using System.Drawing;
using System.Windows.Forms;

namespace StockPool
{
    /// <summary>
    /// 「滚轮数值块」：取代 NumericUpDown 的数字输入框。
    ///
    /// 形态：**只显示数值，点击弹出滚轮选择器**（<see cref="WheelPickerForm"/>）。
    /// 没有任何可键入的文本框 —— 数值只能靠滚轮调（用户明确要求取消输入功能）。
    ///
    /// 为什么不用 NumericUpDown：它的内部输入框会抢走焦点（用户反馈「数字输入会直接到
    /// 右下角输入框」），而且 WinForms 自带的滚轮会按「系统滚动行数(3) × Increment」
    /// 一次跳 3 格（实测），要修正还得把 Increment 置 0 —— 那又会废掉箭头与方向键。
    /// 与其在一堆坑上打补丁，不如换成「显示块 + 弹窗滚轮」，语义也正好是用户要的样子。
    ///
    /// ⚠️ 为什么继承 Label 而不是 Panel：Panel 默认不可选中（ControlStyles.Selectable=false），
    /// **根本不产生 Click 事件**（实测：真实鼠标点数值块弹不出滚轮）。Label 自带 Click 支持，
    /// 继承它最省事，也没有子控件抢鼠标事件的问题。
    /// </summary>
    internal sealed class WheelValueBox : Label
    {
        private readonly decimal _min, _max, _step;
        private decimal _value;
        private Color _pickerBg = Color.FromArgb(56, 60, 68);
        private Color _pickerFg = Color.White;
        private WheelPickerForm _picker;

        public WheelValueBox(decimal min, decimal max, decimal step, decimal value)
        {
            _min = min;
            _max = max < min ? min : max;
            _step = step <= 0 ? 1m : step;
            _value = ClampTo(value);

            AutoSize = false;
            Height = 24;
            Width = 100;
            BackColor = Color.FromArgb(56, 60, 68);
            ForeColor = Color.White;
            TextAlign = ContentAlignment.MiddleCenter;
            Font = new Font("Microsoft YaHei UI", 9.5f);
            Cursor = Cursors.Hand;
            Text = NumKit.Format(_value, _step);

            Click += delegate { OpenPicker(); };
        }

        public decimal Minimum { get { return _min; } }
        public decimal Maximum { get { return _max; } }
        public decimal Step { get { return _step; } }

        public decimal Value
        {
            get { return _value; }
            set { SetValue(value, true); }
        }

        /// <summary>值变化时通知（弹窗里滚动即时回写到主界面用）。</summary>
        public event EventHandler ValueChanged;

        /// <summary>配色（由换肤写入，跟随深/浅色主题）。</summary>
        public void ApplyTheme(Color bg, Color fg)
        {
            _pickerBg = bg;
            _pickerFg = fg;
            BackColor = bg;
            ForeColor = fg;
        }

        private decimal ClampTo(decimal v)
        {
            if (v < _min) return _min;
            if (v > _max) return _max;
            return v;
        }

        private void SetValue(decimal v, bool notify)
        {
            decimal nv = ClampTo(v);
            _value = nv;
            Text = NumKit.Format(_value, _step);
            if (notify && ValueChanged != null) ValueChanged(this, EventArgs.Empty);
        }

        /// <summary>
        /// 打开滚轮浮层。
        ///
        /// ⚠️ 用**非模态** Show 而不是 ShowDialog：在鼠标事件回调里弹模态窗，
        /// 窗体刚显示就被系统收掉（实测：点击后浮层一闪即逝，OpenForms 里都查不到；
        /// 而同样的调用放在顶层逻辑里就正常）。非模态没有这个问题，
        /// 关闭交给浮层上的「完成」按钮或 Esc。
        /// </summary>
        private void OpenPicker()
        {
            if (_picker != null && !_picker.IsDisposed)
            {
                _picker.Close();
                _picker = null;
            }
            // 浮层宽度 = 数值块宽度：弹层正好落在数值块下方、左右对齐
            var dlg = new WheelPickerForm(_min, max: _max, step: _step, value: _value,
                                          bg: _pickerBg, fg: _pickerFg, width: Width);
            dlg.ValuePicked += delegate(decimal v) { SetValue(v, true); };
            dlg.FormClosed += delegate { if (_picker == dlg) _picker = null; };
            dlg.PlaceNear(this);
            _picker = dlg;
            dlg.Show(FindForm());
            dlg.Activate();          // 立刻可交互：否则第一次点击只是把浮层激活
        }
    }

    /// <summary>
    /// 滚轮选择器浮层：点数值后弹出的**数字滚轮**。
    ///
    /// 交互：顶部大号显示当前值 → 左边一列数字上下滚（滚轮 / 拖动拨动 / 点某一行），
    /// 中间一行高亮 = 当前选中值；**右边一根竖直滑条**用于大范围拖动（两者联动）；
    /// **改完立即回写**到原控件；按 Esc 或点「完成」关闭。
    ///
    /// ⚠️ 这是**非模态**浮层（Show 而非 ShowDialog）：在鼠标事件回调里弹模态窗，
    /// 窗体刚显示就会被系统收掉（实测），非模态没这个问题。
    /// </summary>
    internal sealed class WheelPickerForm : Form
    {
        private readonly WheelRoller _wheel;
        private readonly WheelSlider _slider;
        private readonly Label _value;
        private readonly decimal _min, _max, _step;
        private readonly Font _fontBig, _fontMid, _fontSmall;
        private bool _syncing;         // 滚轮 ↔ 滑条 联动时防止互相回灌

        public event Action<decimal> ValuePicked;

        /// <param name="width">浮层宽度 = 上方数值块的宽度（两者对齐才不突兀）。</param>
        public WheelPickerForm(decimal min, decimal max, decimal step, decimal value,
                               Color bg, Color fg, int width)
        {
            _min = min;
            _max = max < min ? min : max;
            _step = step <= 0 ? 1m : step;

            int w = Math.Max(88, width);

            FormBorderStyle = FormBorderStyle.None;
            ShowInTaskbar = false;
            StartPosition = FormStartPosition.Manual;
            ClientSize = new Size(w, 300);
            BackColor = bg;
            KeyPreview = true;
            Padding = new Padding(0);

            _value = new Label();
            _value.Dock = DockStyle.Top;
            _value.Height = 54;
            _value.TextAlign = ContentAlignment.MiddleCenter;
            _fontBig = new Font("Microsoft YaHei UI", 17f, FontStyle.Bold);
            _fontMid = new Font("Microsoft YaHei UI", 14f, FontStyle.Bold);
            _fontSmall = new Font("Microsoft YaHei UI", 11f, FontStyle.Bold);
            _value.Font = _fontBig;
            _value.ForeColor = fg;
            _value.BackColor = bg;
            _value.Text = NumKit.Format(value, _step);

            // 配色：底 = 浮层底色；选中带 = 底色略亮；分隔线 = 前景色淡；选中数字 = 前景色；
            //       未选中数字 = 前景色偏暗（越远越淡）
            _wheel = new WheelRoller(_min, _max, _step, value,
                                     bg, Blend(bg, fg, 0.13), Blend(bg, fg, 0.34), fg, Blend(bg, fg, 0.62));
            _wheel.Size = new Size(Math.Max(36, w - 38), 196);
            _wheel.ValueChanged += delegate { SyncSlider(); ApplyFromWheel(); };

            // 右侧滑条：大范围调节（滚轮一格一格太慢时用）
            _slider = new WheelSlider(_min, _max, _step, value,
                                      bg, Blend(bg, fg, 0.24), Blend(bg, fg, 0.62), fg);
            _slider.Size = new Size(26, 196);
            _slider.ValueChanged += delegate { SyncWheel(); ApplyFromWheel(); };

            var done = new Button();
            done.Text = "完成";
            done.Dock = DockStyle.Bottom;
            done.Height = 30;
            done.FlatStyle = FlatStyle.Flat;
            done.FlatAppearance.BorderSize = 0;
            done.BackColor = Blend(bg, fg, 0.22);
            done.ForeColor = fg;
            done.Click += delegate { Close(); };

            var body = new Panel();
            body.Dock = DockStyle.Fill;
            body.BackColor = bg;
            body.Controls.Add(_wheel);
            body.Controls.Add(_slider);
            body.Resize += delegate
            {
                int by = Math.Max(0, (body.ClientSize.Height - _wheel.Height) / 2);
                _wheel.Location = new Point(4, by);
                _slider.Location = new Point(body.ClientSize.Width - _slider.Width - 4, by);
            };

            Controls.Add(body);
            Controls.Add(done);
            Controls.Add(_value);

            KeyDown += delegate(object sender, KeyEventArgs e)
            {
                if (e.KeyCode == Keys.Escape) { Close(); e.Handled = true; }
                else if (e.KeyCode == Keys.Up) { _wheel.Nudge(1); e.Handled = true; }
                else if (e.KeyCode == Keys.Down) { _wheel.Nudge(-1); e.Handled = true; }
            };
            // 点浮层外（焦点回到主窗）→ 关闭。非模态下不会「刚显示就被收掉」。
            Deactivate += delegate { if (!IsDisposed) Close(); };
            // 布局完成后按实际宽度定顶部字号
            Load += delegate { SetValueText(_wheel.Value); };
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing)
            {
                _fontBig.Dispose();
                _fontMid.Dispose();
                _fontSmall.Dispose();
            }
            base.Dispose(disposing);
        }

        /// <summary>把浮层摆到锚点控件下方；下方放不下时改放上方。</summary>
        public void PlaceNear(Control anchor)
        {
            if (anchor == null) return;
            Point p = anchor.PointToScreen(new Point(0, anchor.Height));
            Screen area = Screen.FromControl(anchor);
            int x = p.X;
            if (x + Width > area.WorkingArea.Right)
                x = Math.Max(area.WorkingArea.Left, area.WorkingArea.Right - Width);
            int y = p.Y;
            if (y + Height > area.WorkingArea.Bottom)
                y = Math.Max(area.WorkingArea.Top, p.Y - anchor.Height - Height);
            Location = new Point(x, y);
        }

        /// <summary>滚轮 → 滑条（滚轮动了，滑块跟着走）。</summary>
        private void SyncSlider()
        {
            if (_syncing) return;
            _syncing = true;
            _slider.Value = _wheel.Value;
            _syncing = false;
        }

        /// <summary>滑条 → 滚轮（拖滑块，数字滚轮跳到对应刻度）。</summary>
        private void SyncWheel()
        {
            if (_syncing) return;
            _syncing = true;
            _wheel.Value = _slider.Value;
            _syncing = false;
        }

        private void ApplyFromWheel()
        {
            decimal v = _wheel.Value;
            SetValueText(v);
            if (ValuePicked != null) ValuePicked(v);
        }

        /// <summary>顶部大号数值（浮层窄，字号按宽度自动降档，避免溢出）。</summary>
        private void SetValueText(decimal v)
        {
            string s = NumKit.Format(v, _step);
            _value.Text = s;
            float avail = Math.Max(16, _value.ClientSize.Width - 6);
            Font f = _fontBig;
            using (Graphics g = _value.CreateGraphics())
            {
                if (g.MeasureString(s, _fontBig).Width > avail) f = _fontMid;
                if (g.MeasureString(s, _fontMid).Width > avail) f = _fontSmall;
            }
            _value.Font = f;
        }

        /// <summary>把 b 按比例混到 a 上（t=0 → a，t=1 → b）。</summary>
        private static Color Blend(Color a, Color b, double t)
        {
            int r = (int)Math.Round(a.R + (b.R - a.R) * t);
            int g = (int)Math.Round(a.G + (b.G - a.G) * t);
            int bl = (int)Math.Round(a.B + (b.B - a.B) * t);
            return Color.FromArgb(r, g, bl);
        }
    }
}
