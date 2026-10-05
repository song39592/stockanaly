using System.Drawing;
using System.Windows.Forms;

namespace StockPool
{
    /// <summary>
    /// 启动器公共件 · **KPI 卡片与卡片行**（第 06 项）。
    ///
    /// 背景：这两个原先住在 `ValuationPage.cs`，却被估值页、个股页、盘面页共 24 处调用
    /// —— 事实上已经是公共件，只是**放错了文件**，造成「估值页的东西被别的页依赖」的耦合错觉。
    ///
    /// **本项是零行为改动的纯搬家**：实现逐字照搬，调用点一行未改（`partial class`）。
    ///
    /// **刻意不并入的两组**（合并 = 视觉回归，收益不抵风险）：
    ///   - `StKpiCard` / `StCardRow`（StrategyPage.cs）：150×66、**有边框**、
    ///     值在上 / 标签在下（**排版方向相反**）。并进来会让原本无边框的 24 张卡长出边框。
    ///   - `MktRow`（MarketPage.cs）：单行「名称…值」，形态不同。
    /// 两个行容器的 `WrapContents` / `MaximumSize` 差异是**有意为之**，不要统一。
    /// </summary>
    internal sealed partial class MainForm
    {
        /// <summary>KPI 卡片行容器：不换行（每行横排到底）、底部留 6px。</summary>
        private static FlowLayoutPanel VKpiRow()
        {
            var f = new FlowLayoutPanel();
            f.FlowDirection = FlowDirection.LeftToRight;
            f.WrapContents = false;
            f.AutoSize = true;
            f.AutoSizeMode = AutoSizeMode.GrowAndShrink;
            f.Margin = new Padding(0, 0, 0, 6);
            f.Padding = new Padding(0);
            return f;
        }

        /// <summary>一张 KPI 卡片（换肤时按 tag="kpi" 上卡片底色）。</summary>
        private static void AddKpi(FlowLayoutPanel row, string l, string v, string s)
        {
            var card = new Panel();
            card.Tag = "kpi";
            card.Width = 230;
            card.Height = 66;
            card.Margin = new Padding(0, 0, 8, 0);
            card.Padding = new Padding(0);

            var l1 = new Label();
            l1.Text = l;
            l1.AutoSize = true;
            l1.Location = new Point(10, 7);
            l1.Tag = "muted";

            var v1 = new Label();
            v1.Text = v;
            v1.Font = new Font("Consolas", 12.5f);
            v1.AutoSize = true;
            v1.Location = new Point(10, 23);
            v1.MaximumSize = new Size(212, 0);

            var s1 = new Label();
            s1.Text = s;
            s1.AutoSize = true;
            s1.Location = new Point(10, 46);
            s1.MaximumSize = new Size(212, 0);
            s1.Tag = "muted";

            card.Controls.Add(l1);
            card.Controls.Add(v1);
            card.Controls.Add(s1);
            row.Controls.Add(card);
        }
    }
}
