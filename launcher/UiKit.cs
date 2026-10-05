using System;
using System.Collections.Generic;
using System.Drawing;
using System.Windows.Forms;

namespace StockPool
{
    /// <summary>
    /// 启动器公共件 · **页面骨架与控件工厂**。
    ///
    /// 背景：`NewPage / Stack / Group / Row / AddRow / MiniBtn / Lbl / Mute / Dot / Check`
    /// 原先散落在 `StockPoolLauncher.cs`（骨架文件）里，和「拉起服务 / 换肤 / 各页装配」
    /// 混在一起，读起来像「骨架文件的东西被各页依赖」。第 26 项把它们归到独立文件。
    ///
    /// **为什么拆分是零风险的**：这些都是 `partial class MainForm` 的成员，
    /// 搬到新文件后调用点一行都不用改；唯一要动的是 `build_exe.bat` 的源文件清单。
    ///
    /// 同批次的其它公共件落点（在做对应项时顺手落，不预先搬空壳）：
    ///   GridKit.cs  表格（已完成）
    ///   Palette.cs  色彩常量（第 01 项）
    ///   J.cs        JSON 工具（第 02 项）
    ///   Http.cs     HTTP 封装（第 03 项）
    ///   Cards.cs    KPI 卡片（第 06 项）
    /// </summary>
    internal sealed partial class MainForm
    {
        private Panel NewPage(string title)
        {
            int index = _tabPages.Count;

            var p = new Panel();
            p.Dock = DockStyle.Fill;
            p.Visible = (index == 0);
            p.AutoScroll = true;
            // AutoScroll 容器里 AutoSize 的子控件偶尔比客户区宽 1px，会凭空冒出系统滚动条
            // （深色主题下是一片浅色）；这里把宽度上限钉住。
            p.Resize += delegate
            {
                foreach (Control child in p.Controls)
                    child.MaximumSize = new Size(Math.Max(160, p.ClientSize.Width - p.Padding.Horizontal - 2), 0);
            };
            p.Padding = new Padding(16, 12, 16, 16);
            p.Margin = new Padding(0);
            p.Tag = "tabpage";
            _tabPages.Add(p);
            _tabBody.Controls.Add(p);

            var b = new Button();
            b.Text = title;
            b.Tag = "tabbtn";
            b.AutoSize = false;
            b.Height = 30;
            b.Width = Math.Max(74, TextRenderer.MeasureText(title, Font).Width + 26);
            b.FlatStyle = FlatStyle.Flat;
            b.FlatAppearance.BorderSize = 0;
            b.Font = new Font("Microsoft YaHei UI", 9f);
            b.TabStop = false;
            b.Margin = new Padding(0, 0, 2, 0);
            int idx = index;
            b.Click += delegate { SelectTab(idx); };
            b.Paint += delegate(object s, PaintEventArgs e)
            {
                if (idx != _tabIndex) return;
                var btn = (Control)s;
                using (var pen = new Pen(C.Accent, 2f))
                    e.Graphics.DrawLine(pen, 0, btn.Height - 1, btn.Width, btn.Height - 1);
            };
            _tabBtns.Add(b);
            _tabBar.Controls.Add(b);
            return p;
        }

        /// <summary>纵向堆叠容器：单列、宽度撑满内容区、行高自动。</summary>
        private static TableLayoutPanel Stack()
        {
            var t = new TableLayoutPanel();
            t.ColumnCount = 1;
            t.AutoSize = true;
            t.AutoSizeMode = AutoSizeMode.GrowAndShrink;
            t.Dock = DockStyle.Top;
            t.Margin = new Padding(0);
            t.Padding = new Padding(0);
            t.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100f));
            return t;
        }

        private static void AddRow(TableLayoutPanel t, Control row)
        {
            row.Margin = new Padding(0, 0, 0, 8);
            t.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            t.Controls.Add(row);
        }

        /// <summary>横向排布一行控件（不换行，随内容自适应）。</summary>
        private static FlowLayoutPanel Row(params Control[] items)
        {
            var f = new FlowLayoutPanel();
            f.FlowDirection = FlowDirection.LeftToRight;
            f.WrapContents = false;
            f.AutoSize = true;
            f.AutoSizeMode = AutoSizeMode.GrowAndShrink;
            f.Margin = new Padding(0);
            f.Padding = new Padding(0);
            foreach (var c in items)
            {
                c.Margin = new Padding(0, 0, 8, 0);
                f.Controls.Add(c);
            }
            return f;
        }

        /// <summary>自适应宽度分组框，body 为其内部纵向堆叠容器。</summary>
        private static GroupBox Group(string title, out TableLayoutPanel body)
        {
            var g = new GroupBox();
            g.Text = title;
            g.AutoSize = true;
            g.AutoSizeMode = AutoSizeMode.GrowAndShrink;
            g.Dock = DockStyle.Top;
            g.Margin = new Padding(0);
            g.Padding = new Padding(12, 6, 12, 10);

            body = new TableLayoutPanel();
            body.ColumnCount = 1;
            body.AutoSize = true;
            body.AutoSizeMode = AutoSizeMode.GrowAndShrink;
            body.Dock = DockStyle.Fill;
            body.Margin = new Padding(0);
            body.Padding = new Padding(0);
            body.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100f));
            g.Controls.Add(body);
            return g;
        }

        private static Label Dot(Control parent, string text)
        {
            var l = new Label();
            l.Text = "● " + text;
            l.AutoSize = true;
            l.ForeColor = Color.Gray;
            l.Tag = "dot";
            l.Font = new Font("Microsoft YaHei UI", 9.5f);
            l.Margin = new Padding(0, 4, 14, 0);
            parent.Controls.Add(l);
            return l;
        }

        /// <summary>标记为次要说明文字：换肤时用它对应的弱色。</summary>
        private static Label Mute(Label l)
        {
            l.Tag = "muted";
            return l;
        }

        private static Label Lbl(string text)
        {
            var l = new Label();
            l.Text = text;
            l.AutoSize = true;
            l.ForeColor = Color.Black;
            l.Margin = new Padding(0, 5, 8, 0);
            return l;
        }

        private static Button MiniBtn(string text, EventHandler click)
        {
            return MiniBtn(text, click, 0);
        }

        private static Button MiniBtn(string text, EventHandler click, int width)
        {
            var b = new Button();
            b.Text = text;
            b.Height = 26;
            b.FlatStyle = FlatStyle.Flat;
            b.BackColor = C.Accent;
            b.ForeColor = Color.White;
            b.FlatAppearance.BorderSize = 0;
            b.AutoSize = (width <= 0);
            b.AutoSizeMode = AutoSizeMode.GrowAndShrink;
            if (width > 0) b.Width = width;
            b.MinimumSize = new Size(64, 26);
            b.Padding = new Padding(6, 0, 6, 0);
            b.Margin = new Padding(0, 0, 8, 0);
            b.Click += click;
            return b;
        }

        private static CheckBox Check(string text, bool value)
        {
            var c = new CheckBox();
            c.Text = text;
            c.AutoSize = true;
            c.Checked = value;
            c.Margin = new Padding(0, 4, 8, 0);
            return c;
        }
    }
}
