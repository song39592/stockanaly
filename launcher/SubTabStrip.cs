// =============================================================================
// 二级页（SubTab）机制（第 05 项）
//
// 个股页 / 盘面页 / 策略页原先各写了一套「顶部一排小按钮 + 一个内容容器，
// 点按钮切换页面」的二级页机制：三份近乎逐字重复的 Add + Select + 换肤循环，
// 外加三份几乎一样的字段组（btns / pages / index / bar / body）与标签栏搭建代码。
// 本文件把它们收成一个控件，三页各持一个实例。
//
// **两个 Add 重载都保留，不要强行统一入参形态**：
//   Add(title, Panel page)              个股页形态 —— 7 个子页是外部先建好再传进来
//   Add(title, Action<TLP> build)        盘面 / 策略形态 —— 收 build 委托，内部套一层 Stack
// 改成委托式要动个股页 7 个调用点，纯亏。
//
// **两处「懒加载」特例靠 OnSelected 钩子承载，不要写死进 Select**：
//   盘面页 AI 子页：首次切到才生成，否则应用启动瞬间就会抢跑请求后端；
//   个股页 K 线子页：切过去要把键盘焦点交给图表（↑↓ 缩放 / ←→ 移光标）。
// 钩子设计不到位，AI 页就会在启动时开始生成。
// =============================================================================

using System;
using System.Collections.Generic;
using System.Drawing;
using System.Windows.Forms;

namespace StockPool
{
    internal sealed partial class MainForm
    {
        /// <summary>二级页：标签栏 + 内容容器，负责挂载当前页与标签配色。</summary>
        private sealed class SubTabStrip
        {
            private readonly MainForm _app;
            private readonly FlowLayoutPanel _bar;
            private readonly Panel _body;
            private readonly List<Button> _btns = new List<Button>();
            private readonly List<Panel> _pages = new List<Panel>();
            private readonly string _buttonTag;
            private int _index = -1;

            /// <summary>切页后的钩子，参数是被选中的下标。承载两处懒加载特例，见文件头说明。</summary>
            public Action<int> OnSelected;

            /// <summary>按钮是否用固定尺寸（高 28、宽随文字但下限 96）。
            /// 个股页 / 盘面页为 true；策略页历史上没设这两项、走 Button 默认的紧凑形态，
            /// 改成 true 会让策略页的按钮整体变大，属**视觉变化**，故保留差异。</summary>
            public bool FixedButtonSize { get; set; }

            private bool _bodyAutoScroll;
            /// <summary>内容容器是否自带滚动条。仅策略页为 true（个股页的 K 线页要撑满图表、
            /// 不能滚，否则滚轮缩放会与滚动打架）。构造后改也立即生效。</summary>
            public bool BodyAutoScroll
            {
                get { return _bodyAutoScroll; }
                set { _bodyAutoScroll = value; _body.AutoScroll = value; }
            }

            /// <summary>建一条二级页导航：标签栏挂在 host 的 barRow 行，内容容器挂在 bodyRow 行。</summary>
            public SubTabStrip(MainForm app, TableLayoutPanel host, int barRow, int bodyRow, string buttonTag)
            {
                _app = app;
                _buttonTag = buttonTag;
                FixedButtonSize = true;      // 默认给固定尺寸；策略页在初始化器里改成 false
                BodyAutoScroll = false;

                var bar = new FlowLayoutPanel();
                bar.Dock = DockStyle.Top;     // Fill 在 AutoSize 行里高度算不准，会留下大片空白
                bar.Height = 30;
                bar.FlowDirection = FlowDirection.LeftToRight;
                bar.WrapContents = false;
                bar.Margin = new Padding(0, 0, 0, 2);
                bar.Padding = new Padding(0);
                host.Controls.Add(bar, 0, barRow);
                _bar = bar;

                _body = new Panel();
                _body.Dock = DockStyle.Fill;
                _body.Margin = new Padding(0);
                host.Controls.Add(_body, 0, bodyRow);
            }

            /// <summary>已登记的二级页个数（调用方用它反查下标，如盘面页的「AI 分析」页）。</summary>
            public int Count { get { return _pages.Count; } }

            /// <summary>当前选中的下标；尚未 Select 过时为 -1。</summary>
            public int Index { get { return _index; } }

            /// <summary>内容容器。页面重新挂载后要 PerformLayout / Invalidate 时用。</summary>
            public Panel Body { get { return _body; } }

            /// <summary>标签栏上是否已有按钮。换肤挂钩用它判空，避免对未构建完的页调用。</summary>
            public bool HasTabs { get { return _btns.Count > 0; } }

            /// <summary>加一个二级页：内容面板由外部先建好再传入（个股页形态）。</summary>
            public void Add(string title, Panel page)
            {
                _bar.Controls.Add(NewTabButton(title, _pages.Count));
                page.Dock = DockStyle.Fill;
                page.Visible = false;
                page.Margin = new Padding(0);
                page.Tag = "tabpage";
                _pages.Add(page);
            }

            /// <summary>加一个二级页：内容面板由本控件新建，用 Stack 纵向堆叠（盘面 / 策略形态）。</summary>
            public void Add(string title, Action<TableLayoutPanel> build)
            {
                _bar.Controls.Add(NewTabButton(title, _pages.Count));
                var page = new Panel();
                page.Dock = DockStyle.Fill;
                page.Visible = false;
                page.AutoScroll = true;
                page.Margin = new Padding(0);
                page.Tag = "tabpage";
                var stack = Stack();
                build(stack);
                page.Controls.Add(stack);
                // 不在这里挂到 _body：由 Select 只挂载当前页，
                // 避免多个 Dock=Fill 叠放（叠放时切 Visible 会布局错乱）。
                _pages.Add(page);
            }

            /// <summary>切到第 index 个二级页。越界（含 -1）直接返回，行为与原三处一致。</summary>
            public void Select(int index)
            {
                if (index < 0 || index >= _pages.Count) return;
                _index = index;
                // 容器里只挂当前这一页：先摘掉旧的，再挂上新的。
                // 早期是所有页都 Dock=Fill 叠在容器里靠 Visible 切换，会出现
                // 「切过去没变、再切一次才对」的布局错乱。
                _body.Controls.Clear();
                var page = _pages[index];
                page.Visible = true;
                page.Dock = DockStyle.Fill;
                _body.Controls.Add(page);
                // 二级页只在被选中时才挂到控件树上，而全局换肤 Skin(this) 只递归挂载中的控件，
                // 所以这些页在首次显示前一直是系统默认配色（表格白底、列头浅灰），
                // 必须在挂载的同时按当前主题上色。
                _app.Skin(page);
                page.Invalidate(true);
                SkinTabs();
                if (OnSelected != null) OnSelected(index);
            }

            /// <summary>二级标签配色（跟随主题）：选中用卡片色，未选中用窗口底色。</summary>
            public void SkinTabs()
            {
                for (int i = 0; i < _btns.Count; i++)
                {
                    bool sel = (i == _index);
                    _btns[i].BackColor = sel ? _app._cPanel : _app._cBg;
                    _btns[i].ForeColor = sel ? _app._cText : _app._cSub;
                    _btns[i].Invalidate();
                }
            }

            /// <summary>建标签按钮并登记。尺寸三处原本略有差异，见 <see cref="FixedButtonSize"/>。</summary>
            private Button NewTabButton(string title, int idx)
            {
                var b = new Button();
                b.Text = title;
                b.Tag = _buttonTag;
                b.FlatStyle = FlatStyle.Flat;
                b.FlatAppearance.BorderSize = 0;
                b.Font = new Font("Microsoft YaHei UI", 9f);
                b.TabStop = false;
                b.Margin = new Padding(0, 0, 2, 0);
                if (FixedButtonSize)
                {
                    b.AutoSize = false;
                    b.Height = 28;
                    b.Width = Math.Max(96, TextRenderer.MeasureText(title, _app.Font).Width + 24);
                }
                b.Click += delegate { Select(idx); };
                _btns.Add(b);
                return b;
            }
        }
    }
}
