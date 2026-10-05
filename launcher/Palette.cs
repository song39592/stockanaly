using System.Drawing;

namespace StockPool
{
    /// <summary>
    /// 调色板 · **语义色常量**（第 01 项）。
    ///
    /// 背景：`Color.FromArgb(...)` 曾在 launcher 各页硬编码 171 处，同一语义存在多套 RGB
    /// （涨红 4 套、跌绿 3 套、平灰 2 套、警告橙 2 套），想调一个颜色要满仓库找。
    ///
    /// **本项只做「常量化」，不改任何取值** —— 全部按原 RGB 逐值替换，所以视觉零变化。
    /// 为什么保留同语义的多套值（`Up` 与 `UpErr` 都是红的）：
    /// 统一成一个值就是**改设计**，那需要另外决策，不该在一次「搬常量」里夹带。
    ///
    /// **暂不参与换肤**：`BuildPalette()` 维护的 `_cBg/_cPanel/_cSub/_cText` 是主题底色，
    /// 与这里的语义色是两套东西；把它们接进换肤是后续工作，不在本项范围。
    ///
    /// 命名约定：语义 + 深浅（`Dark`/`Light`/`Soft`/`Deep`），看名字就知道对应哪一档。
    /// </summary>
    internal static class C
    {
        // ---- 涨（红）----
        /// <summary>涨 / 强调红。RPS 高亮(≥90)、盘面涨盘、筹码提示等。</summary>
        public static readonly Color Up = Color.FromArgb(239, 83, 80);
        /// <summary>涨红 / 错误红**共用**的一档（原代码同值兼两用，故名不取死）。</summary>
        public static readonly Color UpErr = Color.FromArgb(208, 57, 59);
        /// <summary>涨的浅一档（列表里的涨幅文字）。</summary>
        public static readonly Color UpSoft = Color.FromArgb(130, 210, 140);

        // ---- 跌 / 成（绿）----
        /// <summary>跌绿。RPS120、盘面跌盘。</summary>
        public static readonly Color Down = Color.FromArgb(63, 185, 80);
        /// <summary>浮亏绿（策略页那一档略深的绿）。</summary>
        public static readonly Color DownSoft = Color.FromArgb(60, 160, 90);
        /// <summary>成 / 低估深绿。</summary>
        public static readonly Color DownDeep = Color.FromArgb(30, 126, 52);
        /// <summary>成功 / 通过（服务状态灯）。</summary>
        public static readonly Color OkGreen = Color.FromArgb(46, 204, 113);

        // ---- 平 / 灰 ----
        /// <summary>平 / 次要文字灰（全项目用得最多的灰）。</summary>
        public static readonly Color Flat = Color.FromArgb(150, 158, 172);
        /// <summary>平灰的深一档（RatioBar 的「平」段）。</summary>
        public static readonly Color FlatDeep = Color.FromArgb(120, 124, 132);
        /// <summary>带暖调的灰。</summary>
        public static readonly Color FlatWarm = Color.FromArgb(125, 124, 120);
        /// <summary>最浅的一档灰。</summary>
        public static readonly Color FlatSoft = Color.FromArgb(150, 150, 160);

        // ---- 警告（琥珀 / 橙）----
        /// <summary>警告琥珀。</summary>
        public static readonly Color Warn = Color.FromArgb(201, 133, 0);
        /// <summary>警告橙（中风险）。</summary>
        public static readonly Color WarnMid = Color.FromArgb(233, 154, 53);
        /// <summary>警告深橙。</summary>
        public static readonly Color WarnDeep = Color.FromArgb(180, 83, 9);

        // ---- 主题蓝 / 强调 ----
        /// <summary>主题蓝：选中态、聚焦。</summary>
        public static readonly Color Accent = Color.FromArgb(64, 120, 192);
        /// <summary>按钮 / 链接蓝。</summary>
        public static readonly Color AccentDeep = Color.FromArgb(0, 120, 215);

        // ---- 副图指标线（RPS / DIF / DEA / 筹码）----
        /// <summary>青蓝（线系）。</summary>
        public static readonly Color LineCyan = Color.FromArgb(110, 190, 220);
        /// <summary>浅青（RPS10）。</summary>
        public static readonly Color RpsCyan = Color.FromArgb(120, 220, 220);
        /// <summary>DIF 线。</summary>
        public static readonly Color LineDif = Color.FromArgb(240, 160, 60);
        /// <summary>DEA 线。</summary>
        public static readonly Color LineDea = Color.FromArgb(57, 135, 229);
        /// <summary>指标默认紫（其余副图指标线）。</summary>
        public static readonly Color LineViolet = Color.FromArgb(144, 133, 233);
        /// <summary>粉（另一条副图线）。</summary>
        public static readonly Color LinePink = Color.FromArgb(213, 81, 129);
        /// <summary>金（线系）。</summary>
        public static readonly Color LineGold = Color.FromArgb(240, 170, 60);
        /// <summary>深金（策略页线系）。</summary>
        public static readonly Color LineGoldDeep = Color.FromArgb(240, 160, 40);
        /// <summary>黄（RPS50）。</summary>
        public static readonly Color RpsYellow = Color.FromArgb(240, 200, 60);
        /// <summary>浅黄（RPS50 亮档）。</summary>
        public static readonly Color RpsYellowLight = Color.FromArgb(240, 200, 80);
        /// <summary>白（RPS250）。</summary>
        public static readonly Color RpsWhite = Color.FromArgb(238, 242, 248);

        // ---- 筹码峰（深浅两份必须都保留，不能折中成一个值）----
        /// <summary>筹码获利盘（深色主题）。</summary>
        public static readonly Color ProfitDark = Color.FromArgb(228, 96, 84);
        /// <summary>筹码获利盘（浅色主题）。</summary>
        public static readonly Color ProfitLight = Color.FromArgb(216, 74, 62);
        /// <summary>筹码套牢盘（深色主题）。</summary>
        public static readonly Color LockedDark = Color.FromArgb(72, 152, 214);
        /// <summary>筹码套牢盘（浅色主题）。</summary>
        public static readonly Color LockedLight = Color.FromArgb(52, 132, 194);
    }
}
