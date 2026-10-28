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

    internal class SettingsData
    {
        public string Theme = "dark";
        public Dictionary<string, string> Palette = new Dictionary<string, string>();
        public WindowData Window = new WindowData();
        public int? DownloadStartYear = null;
        public string EnvPython = "";
        public string EnvNode = "";
        public Dictionary<string, object> CustomThemes = new Dictionary<string, object>();
        public SecretStatus SecretStatus = new SecretStatus();
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
