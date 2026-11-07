using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Windows.Forms;

namespace StockPool
{
    /// <summary>
    /// 启动遮罩 · **后端拉起前的加载界面**（待办：后端拉起前显示加载界面）。
    ///
    /// 背景：<c>Boot()</c> 跑在后台线程上，从「检查运行环境」到「后端 /health 就绪」
    /// 最长要等 45 秒（首次装依赖更久），而这段时间主窗体已经显示、却只有顶部状态灯在变，
    /// 用户看到的是「双击了没反应」。本遮罩把这段空白变成明确反馈：
    /// 阶段文本 + 跑马灯 + 秒表，失败时还能重试 / 看日志。
    ///
    /// 设计要点：
    /// - **做成主窗体内的全屏 Panel**（不是独立窗口）：不抢焦点、不多一个任务栏按钮、
    ///   天然跟着主题换肤；
    /// - 阶段文本由 <see cref="SetStage"/> 驱动、秒表由自己的 Timer 驱动 —— 后台线程
    ///   只报阶段，不必高频回调（避免跨线程刷 UI）；
    /// - 失败切错误态：停跑马灯、亮出「重试 / 查看日志」，至于怎么重试由外部决定。
    /// </summary>
    internal sealed class BootOverlay : Panel
    {
        private readonly Panel _card;
        private readonly Label _lbTitle;
        private readonly Label _lbStage;
        private readonly Label _lbMeta;
        private readonly BootMarquee _bar;
        private readonly Button _btnRetry;
        private readonly Button _btnLog;
        private readonly System.Windows.Forms.Timer _timer;

        private DateTime _bootAt;    // 整个启动流程的开始时刻
        private DateTime _stageAt;   // 当前阶段的开始时刻
        private int _limit;          // 当前阶段上限秒数（0 = 未知上限）
        private bool _failed;

        private Color _textColor = Color.FromArgb(32, 34, 40);
        private Color _subColor = Color.FromArgb(105, 112, 124);

        /// <summary>用户点了「重试」。</summary>
        public event Action Retry;
        /// <summary>用户点了「查看日志」。</summary>
        public event Action ViewLog;

        public BootOverlay(string appTitle)
        {
            Dock = DockStyle.Fill;
            Tag = "boot-overlay";
            BackColor = Color.FromArgb(245, 246, 248);

            _card = new Panel();
            _card.Size = new Size(560, 244);
            _card.Anchor = AnchorStyles.None;
            _card.Tag = "boot-card";
            _card.BackColor = Color.White;
            _card.Location = new Point(120, 120);
            Controls.Add(_card);

            _lbTitle = new Label();
            _lbTitle.Text = appTitle;
            _lbTitle.Font = new Font("Microsoft YaHei UI", 15f, FontStyle.Bold);
            _lbTitle.AutoSize = true;
            _lbTitle.Location = new Point(32, 26);
            _card.Controls.Add(_lbTitle);

            var sub = new Label();
            sub.Text = "正在准备启动，请稍候…";
            sub.ForeColor = Color.FromArgb(105, 112, 124);
            sub.Tag = "muted";
            sub.AutoSize = true;
            sub.Location = new Point(34, 62);
            _card.Controls.Add(sub);

            _lbStage = new Label();
            _lbStage.Text = "";
            _lbStage.Font = new Font("Microsoft YaHei UI", 11f);
            _lbStage.AutoSize = true;
            _lbStage.Location = new Point(32, 100);
            _card.Controls.Add(_lbStage);

            _bar = new BootMarquee();
            _bar.Tag = "boot-bar";
            _bar.Location = new Point(32, 136);
            _bar.Width = 496;
            _bar.Height = 6;
            _card.Controls.Add(_bar);

            _lbMeta = new Label();
            _lbMeta.Text = "";
            _lbMeta.ForeColor = Color.FromArgb(105, 112, 124);
            _lbMeta.Tag = "muted";
            _lbMeta.AutoSize = true;
            _lbMeta.Location = new Point(32, 154);
            _card.Controls.Add(_lbMeta);

            _btnRetry = MakeBtn("重试", 96);
            _btnRetry.Location = new Point(32, 192);
            _btnRetry.Visible = false;
            _btnRetry.Click += delegate { var h = Retry; if (h != null) h(); };
            _card.Controls.Add(_btnRetry);

            _btnLog = MakeBtn("查看日志", 108);
            _btnLog.Location = new Point(140, 192);
            _btnLog.Visible = false;
            _btnLog.Click += delegate { var h = ViewLog; if (h != null) h(); };
            _card.Controls.Add(_btnLog);

            _timer = new System.Windows.Forms.Timer();
            _timer.Interval = 500;
            _timer.Tick += delegate { UpdateMeta(); };

            Resize += delegate { CenterCard(); };
            Visible = false;
        }

        private static Button MakeBtn(string text, int width)
        {
            var b = new Button();
            b.Text = text;
            b.Width = width;
            b.Height = 30;
            b.FlatStyle = FlatStyle.Flat;
            b.FlatAppearance.BorderSize = 0;
            b.BackColor = Color.FromArgb(64, 120, 192);
            b.ForeColor = Color.White;
            b.Font = new Font("Microsoft YaHei UI", 9.5f);
            return b;
        }

        /// <summary>复位成「加载中」状态（**不显示**）：重置秒表、清错误态。</summary>
        public void BeginLoading()
        {
            _failed = false;
            _limit = 0;
            _bootAt = DateTime.Now;
            _stageAt = DateTime.Now;
            if (_lbStage != null) { _lbStage.Text = ""; _lbStage.Tag = ""; _lbStage.ForeColor = _textColor; }
            if (_btnRetry != null) _btnRetry.Visible = false;
            if (_btnLog != null) _btnLog.Visible = false;
            if (_bar != null) _bar.Start();
            UpdateMeta();
        }

        /// <summary>显示遮罩。</summary>
        public void ShowOverlay()
        {
            CenterCard();
            Visible = true;
            BringToFront();
            if (_timer != null && !_timer.Enabled) _timer.Start();
            UpdateMeta();
        }

        /// <summary>隐藏遮罩（启动完成 / 用户转去看日志）。</summary>
        public void HideOverlay()
        {
            if (_timer != null) _timer.Stop();
            Visible = false;
        }

        /// <summary>设置当前阶段文本；<paramref name="limit"/> 为该阶段上限秒数（0 = 未知）。</summary>
        public void SetStage(string text, int limit)
        {
            _stageAt = DateTime.Now;
            _limit = limit;
            if (_lbStage != null) { _lbStage.Text = text ?? ""; _lbStage.Tag = ""; _lbStage.ForeColor = _textColor; }
            UpdateMeta();
        }

        /// <summary>切错误态：停进度条、亮出「重试 / 查看日志」。</summary>
        public void ShowError(string reason)
        {
            _failed = true;
            if (_lbStage != null)
            {
                _lbStage.Text = reason ?? "启动失败";
                _lbStage.Tag = "boot-err";
                _lbStage.ForeColor = C.UpErr;
            }
            if (_bar != null) _bar.Stop();
            if (_btnRetry != null) _btnRetry.Visible = true;
            if (_btnLog != null) _btnLog.Visible = true;
            UpdateMeta();
        }

        /// <summary>按主题配色（由 <c>SkinColors</c> 调用）。</summary>
        public void ApplyTheme(Color bg, Color card, Color text, Color sub, Color track)
        {
            _textColor = text;
            _subColor = sub;
            BackColor = bg;
            if (_card != null) _card.BackColor = card;
            if (_lbTitle != null) _lbTitle.ForeColor = text;
            if (_lbStage != null) _lbStage.ForeColor = _failed ? C.UpErr : text;
            if (_lbMeta != null) _lbMeta.ForeColor = sub;
            if (_bar != null) _bar.ApplyTheme(track, C.Accent);
        }

        /// <summary>秒表 / 倒计时文案。</summary>
        private void UpdateMeta()
        {
            if (_lbMeta == null) return;
            int total = (int)(DateTime.Now - _bootAt).TotalSeconds;
            if (total < 0) total = 0;
            if (_failed)
            {
                _lbMeta.Text = "已用时 " + total + " 秒";
                return;
            }
            if (_limit > 0)
            {
                int left = _limit - (int)(DateTime.Now - _stageAt).TotalSeconds;
                if (left < 0) left = 0;
                _lbMeta.Text = "已用时 " + total + " 秒 · 本阶段预计还需 " + left + " 秒";
            }
            else
            {
                _lbMeta.Text = "已用时 " + total + " 秒";
            }
        }

        /// <summary>卡片居中（窗口尺寸变化 / 首次显示时调用）。</summary>
        private void CenterCard()
        {
            if (_card == null) return;
            int x = (ClientSize.Width - _card.Width) / 2;
            int y = (ClientSize.Height - _card.Height) / 2;
            if (x < 0) x = 0;
            if (y < 0) y = 0;
            _card.Location = new Point(x, y);
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing && _timer != null) { _timer.Stop(); _timer.Dispose(); }
            base.Dispose(disposing);
        }
    }

    /// <summary>
    /// 不确定进度条（跑马灯）· **自绘**。
    ///
    /// 为什么不用 <see cref="ProgressBar"/>：系统进度条的 Marquee 颜色不可控，
    /// 深色主题下会是一块突兀的亮绿；自绘可以跟着主题走。
    /// </summary>
    internal sealed class BootMarquee : Control
    {
        private readonly System.Windows.Forms.Timer _timer;
        private double _offset;
        private Color _track = Color.FromArgb(232, 235, 240);
        private Color _bar = Color.FromArgb(64, 120, 192);

        public BootMarquee()
        {
            SetStyle(ControlStyles.UserPaint | ControlStyles.OptimizedDoubleBuffer
                     | ControlStyles.ResizeRedraw, true);
            _timer = new System.Windows.Forms.Timer();
            _timer.Interval = 40;
            _timer.Tick += delegate { _offset += 2.4; Invalidate(); };
        }

        public void Start() { if (_timer != null && !_timer.Enabled) _timer.Start(); }
        public void Stop() { if (_timer != null && _timer.Enabled) _timer.Stop(); }

        /// <summary>按主题配色（轨道色 / 滑块色）。</summary>
        public void ApplyTheme(Color track, Color bar)
        {
            _track = track;
            _bar = bar;
            Invalidate();
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            var g = e.Graphics;
            g.SmoothingMode = SmoothingMode.AntiAlias;
            using (var br = new SolidBrush(_track))
                g.FillRectangle(br, new Rectangle(0, 0, Width, Height));
            int seg = Math.Max(30, Width / 4);
            double span = Width + seg;
            double x = _offset % span;
            using (var br = new SolidBrush(_bar))
                g.FillRectangle(br, (float)(x - seg), 0, seg, Height);
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing && _timer != null) { _timer.Stop(); _timer.Dispose(); }
            base.Dispose(disposing);
        }
    }
}
