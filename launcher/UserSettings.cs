using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Text;
using System.Web.Script.Serialization;

namespace StockPool
{
    /// <summary>统一用户本地设置文件 <c>settings.json</c>：非机密偏好的唯一源。</summary>
    /// <remarks>
    /// 取代原先散落在 <c>settings.ini</c>（主题 / 路径）、前端硬编码（下载年份）、窗口硬编码
    /// 的偏好。密钥**只存只读标记** <see cref="SecretStatus"/>，绝不写明文（明文仍在
    /// <c>.env</c> + DPAPI 通道）。旧 <c>settings.ini</c> 仅作主题 / python / node 的回退读取。
    /// </remarks>
    internal class WindowData
    {
        public int? w = null;
        public int? h = null;
        public int? x = null;
        public int? y = null;
    }

    internal class SecretStatus
    {
        public bool data_secret = false;
        public bool llm_key = false;
    }

    /// <summary>上次关闭前的界面状态：只记「看了什么 / 怎么显示」，不记行情或筹码矩阵等数据。</summary>
    internal class LastSessionData
    {
        public string code = "";              // 当前个股代码（空 = 未打开）
        public string period = "day";         // day / week / month
        public string main_indicator = "ma";  // 主图指标 id（空 = 关闭）
        public string sub_indicator = "";     // 副图指标 id（空 = 无）
        public string chip_formula = "";      // 筹码公式 id（空 = 后端默认）
        public int? range_idx = null;         // 范围档位 0/1/2/-1（-1 = 滚轮自定义）
        public int? range_bars = null;        // 自定义时的可见根数（仅 range_idx = -1 用）
        public string adjust = "qfq";         // qfq 前复权 / raw 不复权
    }

    internal class SettingsData
    {
        public string Theme = "dark";
        public Dictionary<string, string> Palette = new Dictionary<string, string>();
        public WindowData Window = new WindowData();
        public int? DownloadStartYear = null;
        /// <summary>下载页「仅补最新」勾选状态。
        /// <para><b>三态</b>：null = 用户从未显式选过（默认增量；但起始年份被改到
        /// 比已覆盖起点更早时，会自动切完整下载并把结果记下来）。
        /// true = 用户明确只要增量（此后不再自动切换）；false = 明确要完整下载。</para>
        /// <para>它决定「起始年份生不生效」：勾选时按定义只补库内最新日之后的新增，
        /// 起始年份不参与；取消勾选才是完整下载（按起始年份补齐更早历史）。
        /// 属于用户的下载意图，必须跨会话保留，否则每次进页面都退回默认值、
        /// 用户改好的起始年份又被静默吞掉。</para></summary>
        public bool? DownloadIncremental = null;
        public string EnvPython = "";
        public string EnvNode = "";
        /// <summary>回测的「基准（指数）」选择（形如 sh000300；空 = 不对比）。
        /// <para>决定净值图上叠加的指数收盘曲线，以及 alpha / beta / IR 的计算；
        /// 等权全池那条线已于 2026-11-21 移除（只留「等权全池收益」指标卡）。
        /// 跨会话保留：否则每次进页面都要重选一次，而它恰恰是**下次回测**才生效的选项。</para></summary>
        public string BenchCode = "";
        public Dictionary<string, object> CustomThemes = new Dictionary<string, object>();
        public SecretStatus SecretStatus = new SecretStatus();
        public LastSessionData LastSession = new LastSessionData();
    }

    internal static class UserSettings
    {
        public static SettingsData Current = new SettingsData();

        public static string SettingsDir
        {
            get
            {
                return Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                    "StockPoolLauncher");
            }
        }

        public static string SettingsPath { get { return Path.Combine(SettingsDir, "settings.json"); } }
        public static string IniPath { get { return Path.Combine(SettingsDir, "settings.ini"); } }

        /// <summary>读 settings.json（坏文件 / 缺字段 → 默认值）；再用旧 settings.ini 回退主题 / 路径。</summary>
        public static void Load()
        {
            var d = new SettingsData();
            try
            {
                if (File.Exists(SettingsPath))
                {
                    var ser = new JavaScriptSerializer();
                    var obj = ser.Deserialize<SettingsData>(File.ReadAllText(SettingsPath, Encoding.UTF8));
                    if (obj != null) d = obj;
                }
            }
            catch { }
            try
            {
                if (File.Exists(IniPath))
                {
                    foreach (var line in File.ReadAllLines(IniPath, Encoding.UTF8))
                    {
                        var kv = line.Split(new char[] { '=' }, 2);
                        if (kv.Length != 2) continue;
                        var k = kv[0].Trim();
                        var v = kv[1].Trim();
                        if (k == "theme" && string.IsNullOrEmpty(d.Theme)) d.Theme = (v == "light" ? "light" : "dark");
                        else if (k == "python" && string.IsNullOrEmpty(d.EnvPython)) d.EnvPython = v;
                        else if (k == "node" && string.IsNullOrEmpty(d.EnvNode)) d.EnvNode = v;
                    }
                }
            }
            catch { }
            if (d.Palette == null) d.Palette = new Dictionary<string, string>();
            if (d.Window == null) d.Window = new WindowData();
            if (d.SecretStatus == null) d.SecretStatus = new SecretStatus();
            if (d.LastSession == null) d.LastSession = new LastSessionData();
            Current = d;
            C.ApplyOverrides(Current.Palette);
        }

        /// <summary>原子写 settings.json（先写 .tmp 再 replace，保留 .bak）。</summary>
        public static void Save()
        {
            try
            {
                if (!Directory.Exists(SettingsDir)) Directory.CreateDirectory(SettingsDir);
                Current.Palette = C.ExportOverrides();
                var ser = new JavaScriptSerializer();
                var json = ser.Serialize(Current);
                var tmp = SettingsPath + ".tmp";
                File.WriteAllText(tmp, json, Encoding.UTF8);
                try { if (File.Exists(SettingsPath + ".bak")) File.Delete(SettingsPath + ".bak"); } catch { }
                try { if (File.Exists(SettingsPath)) File.Move(SettingsPath, SettingsPath + ".bak"); } catch { }
                File.Move(tmp, SettingsPath);
            }
            catch { }
        }

        /// <summary>从 backend_fastapi/.env 推断密钥是否已配置（只写布尔标记，不碰明文）。</summary>
        public static void DetectSecretStatus(string repoRoot)
        {
            bool ds = false, llm = false;
            try
            {
                var env = Path.Combine(repoRoot, "backend_fastapi", ".env");
                if (File.Exists(env))
                {
                    foreach (var line in File.ReadAllLines(env, Encoding.UTF8))
                    {
                        var s = line.Trim();
                        if (s.Length == 0 || s.StartsWith("#")) continue;
                        int idx = s.IndexOf('=');
                        if (idx < 0) continue;
                        var key = s.Substring(0, idx).Trim();
                        var val = s.Substring(idx + 1).Trim().Trim('"');
                        if (key.Equals("DATA_SECRET_SEALED", StringComparison.OrdinalIgnoreCase)
                            && !string.IsNullOrWhiteSpace(val)) ds = true;
                        else if (key.Equals("LLM_API_KEY", StringComparison.OrdinalIgnoreCase)
                            && !string.IsNullOrWhiteSpace(val)) llm = true;
                    }
                }
            }
            catch { }
            Current.SecretStatus = new SecretStatus { data_secret = ds, llm_key = llm };
        }
    }
}
