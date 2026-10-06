using System.Drawing;
using System.Drawing.Drawing2D;
using System.Windows.Forms;

namespace StockPool
{
    internal sealed partial class MainForm
    {
        /// <summary>
        /// 自绘表面基类（第 07 项）。
        ///
        /// 背景：5 个自绘表面各自重复「初始化 + 尺寸 + 抗锯齿」样板 ——
        /// `SetStyle(...)` 逐字抄了 4 份、`GetPreferredSize` 抄了 3 份、
        /// 抗锯齿则**有的开有的没开**（K 线与筹码面板开了，条形图/分段条/净值曲线没开），
        /// 观感不一致。这里把共同部分收到基类，各控件只留自己独有的绘制逻辑。
        ///
        /// **收进基类的只有 4 项**：SetStyle / GetPreferredSize / 抗锯齿 / StringFormat 缓存。
        /// 网格色、值域映射、min-max 扫描**刻意不收**：网格色随第 01 项（Palette）统一，
        /// 映射与扫描各只有一行、抽出去反而更难读。
        ///
        /// **例外**：`StEquityPaint` 是 `PictureBox` 的事件处理器、不是 Control 子类，
        /// 进不了本基类，只手工调一次 <see cref="BeginPaint"/>。
        ///
        /// 注：各控件保留 `sealed` 没问题 —— `sealed` 禁止的是「被别人继承」，
        /// 不影响「继承本基类」。
        /// </summary>
        private class ChartControl : Control
        {
            /// <summary>
            /// 复用的 StringFormat。`StringFormat` 不可变但非线程安全，
            /// 所以**做静态缓存是正确的** —— 别改回每帧 new（筹码面板原先每帧 new 3 个）。
            /// 默认 `LineAlignment` 就是 Center，所以只写 Alignment 的老代码与这三个等价。
            /// </summary>
            protected static readonly StringFormat FmtLeft =
                new StringFormat { Alignment = StringAlignment.Near, LineAlignment = StringAlignment.Center };
            protected static readonly StringFormat FmtRight =
                new StringFormat { Alignment = StringAlignment.Far, LineAlignment = StringAlignment.Center };
            protected static readonly StringFormat FmtCenter =
                new StringFormat { Alignment = StringAlignment.Center, LineAlignment = StringAlignment.Center };

            protected ChartControl()
            {
                SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint
                    | ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true);
            }

            /// <summary>OnPaint 开头调一次：统一开抗锯齿。</summary>
            protected void BeginPaint(Graphics g)
            {
                g.SmoothingMode = SmoothingMode.AntiAlias;
            }

            /// <summary>在 AutoSize 容器（TableLayoutPanel）里，行高按 PreferredSize 计算，
            /// 不重写的话行高会偏小、控件顶部被裁（首行压没）。</summary>
            public override Size GetPreferredSize(Size proposedSize)
            {
                return new Size(proposedSize.Width, Height);
            }
        }
    }
}
