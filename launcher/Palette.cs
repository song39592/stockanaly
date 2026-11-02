using System.Collections.Generic;
using System.Drawing;

namespace StockPool
{
    /// <summary>
    /// 调色板 · **语义色**（第 01 项常量化 + 后期待办「统一为用户本地设置文件」的配色可配）。
    ///
    /// 默认 RGB 与历史逐值一致，视觉零变化；用户可在 settings.json 的 <c>palette</c> 里按
    /// **字段名**（如 <c>Up</c> / <c>Down</c> / <c>Accent</c>）覆盖任意语义色，
    /// 通过 <see cref="ApplyOverrides"/> 生效，所有引用点（<c>C.Up</c> 等）无需改动即读到覆盖值。
    /// </summary>
    internal static class C
    {
        // ---- 涨（红）----
        private static readonly Color _Up = Color.FromArgb(239, 83, 80);
        public static Color Up { get { return Get("Up", _Up); } }
        private static readonly Color _UpErr = Color.FromArgb(208, 57, 59);
        public static Color UpErr { get { return Get("UpErr", _UpErr); } }
        private static readonly Color _UpSoft = Color.FromArgb(130, 210, 140);
        public static Color UpSoft { get { return Get("UpSoft", _UpSoft); } }

        // ---- 跌 / 成（绿）----
        private static readonly Color _Down = Color.FromArgb(63, 185, 80);
        public static Color Down { get { return Get("Down", _Down); } }
        private static readonly Color _DownSoft = Color.FromArgb(60, 160, 90);
        public static Color DownSoft { get { return Get("DownSoft", _DownSoft); } }
        private static readonly Color _DownDeep = Color.FromArgb(30, 126, 52);
        public static Color DownDeep { get { return Get("DownDeep", _DownDeep); } }
        private static readonly Color _OkGreen = Color.FromArgb(46, 204, 113);
        public static Color OkGreen { get { return Get("OkGreen", _OkGreen); } }

        // ---- 平 / 灰 ----
        private static readonly Color _Flat = Color.FromArgb(150, 158, 172);
        public static Color Flat { get { return Get("Flat", _Flat); } }
        private static readonly Color _FlatDeep = Color.FromArgb(120, 124, 132);
        public static Color FlatDeep { get { return Get("FlatDeep", _FlatDeep); } }
        private static readonly Color _FlatWarm = Color.FromArgb(125, 124, 120);
        public static Color FlatWarm { get { return Get("FlatWarm", _FlatWarm); } }
        private static readonly Color _FlatSoft = Color.FromArgb(150, 150, 160);
        public static Color FlatSoft { get { return Get("FlatSoft", _FlatSoft); } }

        // ---- 警告（琥珀 / 橙）----
        private static readonly Color _Warn = Color.FromArgb(201, 133, 0);
        public static Color Warn { get { return Get("Warn", _Warn); } }
        private static readonly Color _WarnMid = Color.FromArgb(233, 154, 53);
        public static Color WarnMid { get { return Get("WarnMid", _WarnMid); } }
        private static readonly Color _WarnDeep = Color.FromArgb(180, 83, 9);
        public static Color WarnDeep { get { return Get("WarnDeep", _WarnDeep); } }

        // ---- 主题蓝 / 强调 ----
        private static readonly Color _Accent = Color.FromArgb(64, 120, 192);
        public static Color Accent { get { return Get("Accent", _Accent); } }
        private static readonly Color _AccentDeep = Color.FromArgb(0, 120, 215);
        public static Color AccentDeep { get { return Get("AccentDeep", _AccentDeep); } }

        // ---- 副图指标线（RPS / DIF / DEA / 筹码）----
        private static readonly Color _LineCyan = Color.FromArgb(110, 190, 220);
        public static Color LineCyan { get { return Get("LineCyan", _LineCyan); } }
        private static readonly Color _RpsCyan = Color.FromArgb(120, 220, 220);
        public static Color RpsCyan { get { return Get("RpsCyan", _RpsCyan); } }
        private static readonly Color _LineDif = Color.FromArgb(240, 160, 60);
        public static Color LineDif { get { return Get("LineDif", _LineDif); } }
        private static readonly Color _LineDea = Color.FromArgb(57, 135, 229);
        public static Color LineDea { get { return Get("LineDea", _LineDea); } }
        private static readonly Color _LineViolet = Color.FromArgb(144, 133, 233);
        public static Color LineViolet { get { return Get("LineViolet", _LineViolet); } }
        private static readonly Color _LinePink = Color.FromArgb(213, 81, 129);
        public static Color LinePink { get { return Get("LinePink", _LinePink); } }
        private static readonly Color _LineGold = Color.FromArgb(240, 170, 60);
        public static Color LineGold { get { return Get("LineGold", _LineGold); } }
        private static readonly Color _LineGoldDeep = Color.FromArgb(240, 160, 40);
        public static Color LineGoldDeep { get { return Get("LineGoldDeep", _LineGoldDeep); } }
        private static readonly Color _RpsYellow = Color.FromArgb(240, 200, 60);
        public static Color RpsYellow { get { return Get("RpsYellow", _RpsYellow); } }
        private static readonly Color _RpsYellowLight = Color.FromArgb(240, 200, 80);
        public static Color RpsYellowLight { get { return Get("RpsYellowLight", _RpsYellowLight); } }
        private static readonly Color _RpsWhite = Color.FromArgb(238, 242, 248);
        public static Color RpsWhite { get { return Get("RpsWhite", _RpsWhite); } }

        // ---- 筹码峰（深浅两份必须都保留，不能折中成一个值）----
        private static readonly Color _ProfitDark = Color.FromArgb(228, 96, 84);
        public static Color ProfitDark { get { return Get("ProfitDark", _ProfitDark); } }
        private static readonly Color _ProfitLight = Color.FromArgb(216, 74, 62);
        public static Color ProfitLight { get { return Get("ProfitLight", _ProfitLight); } }
        private static readonly Color _LockedDark = Color.FromArgb(72, 152, 214);
        public static Color LockedDark { get { return Get("LockedDark", _LockedDark); } }
        private static readonly Color _LockedLight = Color.FromArgb(52, 132, 194);
        public static Color LockedLight { get { return Get("LockedLight", _LockedLight); } }

        // ---- 覆盖层：settings.json 的 palette 字段名 → 颜色 ----
        private static Dictionary<string, Color> _overrides = new Dictionary<string, Color>();

        /// <summary>把 settings.json 的 palette（字段名 → #RRGGBB）应用为覆盖；null/空则清空覆盖。</summary>
        public static void ApplyOverrides(Dictionary<string, string> hex)
        {
            _overrides.Clear();
            if (hex == null) return;
            foreach (var kv in hex)
            {
                if (string.IsNullOrEmpty(kv.Key) || string.IsNullOrEmpty(kv.Value)) continue;
                try { _overrides[kv.Key] = ColorTranslator.FromHtml(kv.Value); } catch { }
            }
        }

        /// <summary>导出当前覆盖为 settings.json 用的 #RRGGBB 表（无覆盖则返回空表）。</summary>
        public static Dictionary<string, string> ExportOverrides()
        {
            var d = new Dictionary<string, string>();
            foreach (var kv in _overrides)
                d[kv.Key] = "#" + kv.Value.R.ToString("X2") + kv.Value.G.ToString("X2") + kv.Value.B.ToString("X2");
            return d;
        }

        /// <summary>设定 / 覆盖某个语义色（字段名）。</summary>
        public static void SetOverride(string name, Color c) { _overrides[name] = c; }

        /// <summary>清空所有覆盖，回到默认配色。</summary>
        public static void ClearOverrides() { _overrides.Clear(); }

        internal static Color Get(string name, Color def)
        {
            Color v;
            return _overrides.TryGetValue(name, out v) ? v : def;
        }
    }
}
