#region 说明
// 股票池追踪系统 · 原生 Windows 启动器（WinForms，不加载任何 HTML）
//
// 职责：
//   1) 内嵌拉起 FastAPI 后端（:8000）与 dsh AI 服务（:3080），不再依赖 .bat；
//   2) 一个窗口装下所有页（RPS 体系 / 筹码体系 / 分析工具 / 运行日志 / 设置 / 服务控制台），
//      业务网页用 Edge/Chrome 应用模式开成独立窗口（没有地址栏、没有标签页），重复点只切到最前；
//   3) 托盘常驻、状态灯轮询、退出时收尾子进程。
//
// 编译：见 launcher\build_exe.bat（调用系统自带 csc.exe，离线、无第三方依赖）
//
// 兼容性：代码按 C# 4 语法编写（不用 ?. / $"" / => 等新语法），
//         以便在任意版本的 .NET Framework csc 上都能编译通过。
#endregion

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Windows.Forms;
using System.Web.Script.Serialization;
using Microsoft.Win32;

namespace StockPool
{
    internal static class Program
    {
        [STAThread]
        private static void Main()
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.ThreadException += delegate(object s, ThreadExceptionEventArgs e)
            {
                MessageBox.Show(e.Exception.Message, "启动器异常", MessageBoxButtons.OK, MessageBoxIcon.Error);
            };
            Application.Run(new MainForm());
        }
    }

    /// <summary>日志条目（按来源分组，便于切换查看）。</summary>
    internal sealed class LogEntry
    {
        public string Tag;
        public string Text;
    }

    /// <summary>一个被管理的子进程（后端 / AI 服务）。</summary>
    internal sealed class ServiceItem
    {
        public string Name;
        public string Exe;
        public string Args;
        public string WorkDir;
        public string HealthUrl;
        public Process Proc;
        public bool Owned;      // 是否由本程序拉起（外部已运行时为 false，不抢占、不误杀）
        public bool Starting;

        public bool Alive
        {
            get
            {
                try { return Proc != null && !Proc.HasExited; }
                catch { return false; }
            }
        }

        public void Start(Action<string> log)
        {
            try
            {
                Starting = true;
                var psi = new ProcessStartInfo();
                psi.FileName = Exe;
                psi.Arguments = Args;
                psi.WorkingDirectory = WorkDir;
                psi.UseShellExecute = false;
                psi.CreateNoWindow = true;
                psi.RedirectStandardOutput = true;
                psi.RedirectStandardError = true;
                psi.StandardOutputEncoding = Encoding.UTF8;
                psi.StandardErrorEncoding = Encoding.UTF8;
                psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
                psi.EnvironmentVariables["PYTHONUTF8"] = "1";

                var p = new Process();
                p.StartInfo = psi;
                p.EnableRaisingEvents = true;
                p.OutputDataReceived += delegate(object s, DataReceivedEventArgs e)
                {
                    if (e.Data != null && log != null) log(e.Data);
                };
                p.ErrorDataReceived += delegate(object s, DataReceivedEventArgs e)
                {
                    if (e.Data != null && log != null) log("[err] " + e.Data);
                };
                p.Exited += delegate(object s, EventArgs e)
                {
                    if (log != null) log(Name + " 进程已退出");
                };
                p.Start();
                p.BeginOutputReadLine();
                p.BeginErrorReadLine();
                Proc = p;
                Owned = true;
            }
            catch (Exception ex)
            {
                Starting = false;
                if (log != null) log("启动失败：" + ex.Message);
                throw;
            }
        }

        public void Stop(Action<string> log)
        {
            Starting = false;
            if (Proc == null) return;
            if (!Owned) { Proc = null; return; }
            try
            {
                if (!Proc.HasExited)
                {
                    Proc.Kill();
                    Proc.WaitForExit(5000);
                }
            }
            catch (Exception ex)
            {
                if (log != null) log("停止 " + Name + " 时出错：" + ex.Message);
            }
            finally
            {
                try { Proc.Close(); } catch { }
                Proc = null;
                Owned = false;
            }
        }
    }

    /// <summary>把网页放进「自己的窗口」：用 Edge / Chrome 的应用模式（无地址栏、无标签页），并复用已开的窗口。</summary>
    internal static class WebWindow
    {
        /// <summary>本程序打开过的页面标题，退出时按标题把窗口关掉。</summary>
        private static readonly List<string> Opened = new List<string>();

        /// <summary>打开网页：已经开着的窗口直接切到最前，不会重复开窗。</summary>
        public static void Show(string title, string url)
        {
            var hint = HintOf(url);
            var h = Find(title, hint);
            if (h != IntPtr.Zero)
            {
                Focus(h);
                return;
            }

            var exe = FindBrowser();
            if (exe == null)
            {
                // 没有 Edge/Chrome 时退回默认浏览器（会带地址栏与标签页，但至少能打开）
                try { Process.Start(new ProcessStartInfo(url) { UseShellExecute = true }); }
                catch (Exception ex) { MessageBox.Show("打开失败：" + ex.Message); }
                return;
            }

            var psi = new ProcessStartInfo(exe);
            psi.UseShellExecute = false;
            psi.Arguments = "--app=\"" + url + "\""
                          + " --user-data-dir=\"" + ProfileDir() + "\""
                          + " --window-size=1440,960"
                          + " --no-first-run --no-default-browser-check";
            try
            {
                Process.Start(psi);
                if (!Opened.Contains(title)) Opened.Add(title);
                // 窗口和页面标题要一两秒才出来，等一下好把新窗口切到最前
                var hwnd = Wait(title, hint, 5000);
                if (hwnd != IntPtr.Zero) Focus(hwnd);
            }
            catch (Exception ex) { MessageBox.Show("打开失败：" + ex.Message); }
        }

        /// <summary>网页用的独立 Edge/Chrome 配置目录（与用户日常浏览器隔离）。</summary>
        public static string ProfileDir()
        {
            var profile = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                "StockPoolLauncher", "webprofile");
            try { if (!Directory.Exists(profile)) Directory.CreateDirectory(profile); }
            catch { }
            return profile;
        }

        /// <summary>关闭本程序打开过的全部网页窗口。</summary>
        public static void CloseAll()
        {
            for (int i = 0; i < Opened.Count; i++)
            {
                var h = Find(Opened[i], null);
                if (h == IntPtr.Zero) continue;
                Win32.PostMessage(h, Win32.WM_CLOSE, IntPtr.Zero, IntPtr.Zero);
            }
            Opened.Clear();
        }

        public static string FindBrowser()
        {
            var pf = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles);
            var pf86 = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86);
            var local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            var paths = new string[]
            {
                Path.Combine(pf, @"Microsoft\Edge\Application\msedge.exe"),
                Path.Combine(pf86, @"Microsoft\Edge\Application\msedge.exe"),
                Path.Combine(local, @"Microsoft\Edge\Application\msedge.exe"),
                Path.Combine(pf, @"Google\Chrome\Application\chrome.exe"),
                Path.Combine(pf86, @"Google\Chrome\Application\chrome.exe"),
                Path.Combine(local, @"Google\Chrome\Application\chrome.exe")
            };
            foreach (var f in paths) if (File.Exists(f)) return f;

            var keys = new string[]
            {
                @"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe",
                @"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"
            };
            foreach (var k in keys)
            {
                var v = RegPath(Registry.LocalMachine, k);
                if (v == null) v = RegPath(Registry.CurrentUser, k);
                if (v != null && File.Exists(v)) return v;
            }
            return null;
        }

        private static string RegPath(RegistryKey root, string sub)
        {
            try
            {
                using (var k = root.OpenSubKey(sub))
                {
                    if (k == null) return null;
                    var v = k.GetValue("") as string;
                    return string.IsNullOrEmpty(v) ? null : v.Trim('"');
                }
            }
            catch { return null; }
        }

        /// <summary>在 Edge / Chrome 的窗口里找页面已经打开的那个窗口。</summary>
        private static IntPtr Find(string title, string hint)
        {
            var list = Win32.ListChromeWindows();
            for (int i = 0; i < list.Count; i++)
            {
                var t = Win32.Title(list[i]);
                if (t.Length == 0) continue;
                if (t.IndexOf(title, StringComparison.OrdinalIgnoreCase) >= 0) return list[i];
                if (!string.IsNullOrEmpty(hint)
                    && t.IndexOf(hint, StringComparison.OrdinalIgnoreCase) >= 0) return list[i];
            }
            return IntPtr.Zero;
        }

        /// <summary>等新窗口出现（页面标题可能要等页面加载完才有）。</summary>
        private static IntPtr Wait(string title, string hint, int timeoutMs)
        {
            var deadline = Environment.TickCount + timeoutMs;
            while (Environment.TickCount < deadline)
            {
                var h = Find(title, hint);
                if (h != IntPtr.Zero) return h;
                Application.DoEvents();   // 等窗口期间别把界面卡住
                Thread.Sleep(150);
            }
            return IntPtr.Zero;
        }

        /// <summary>从 URL 里取页面文件名：标题还没设置好时靠它认窗口。</summary>
        private static string HintOf(string url)
        {
            var s = url;
            int q = s.IndexOf('?');
            if (q >= 0) s = s.Substring(0, q);
            int p = s.LastIndexOf('/');
            return p >= 0 ? s.Substring(p + 1) : s;
        }

        /// <summary>把窗口切到最前（最小化时先还原）。</summary>
        private static void Focus(IntPtr h)
        {
            try
            {
                if (h == IntPtr.Zero) return;
                if (Win32.IsIconic(h)) Win32.ShowWindow(h, Win32.SW_RESTORE);
                Win32.SetForegroundWindow(h);
            }
            catch { }
        }
    }

    /// <summary>网页窗口用到的 Win32 调用（找窗口、置前、关闭），统一收在这里。</summary>
    internal static class Win32
    {
        public const int SW_RESTORE = 9;
        public const uint WM_CLOSE = 0x0010;

        [DllImport("user32.dll")]
        private static extern bool EnumWindows(EnumProc cb, IntPtr lParam);

        [DllImport("user32.dll", EntryPoint = "GetWindowTextLengthW")]
        private static extern int GetWindowTextLength32(IntPtr hWnd);

        [DllImport("user32.dll", CharSet = CharSet.Unicode, EntryPoint = "GetWindowTextW")]
        private static extern int GetWindowText32(IntPtr hWnd, StringBuilder text, int count);

        [DllImport("user32.dll", CharSet = CharSet.Unicode, EntryPoint = "GetClassNameW")]
        private static extern int GetClassName32(IntPtr hWnd, StringBuilder text, int count);

        [DllImport("user32.dll")]
        public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

        [DllImport("user32.dll")]
        public static extern bool IsIconic(IntPtr hWnd);

        [DllImport("user32.dll")]
        public static extern bool SetForegroundWindow(IntPtr hWnd);

        [DllImport("user32.dll")]
        public static extern bool PostMessage(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);



        private delegate bool EnumProc(IntPtr hWnd, IntPtr lParam);

        public static string Title(IntPtr hWnd)
        {
            try
            {
                int n = GetWindowTextLength32(hWnd);
                if (n <= 0) return "";
                var sb = new StringBuilder(n + 2);
                GetWindowText32(hWnd, sb, sb.Capacity);
                return sb.ToString();
            }
            catch { return ""; }
        }

        public static string ClassName(IntPtr hWnd)
        {
            try
            {
                var sb = new StringBuilder(256);
                GetClassName32(hWnd, sb, sb.Capacity);
                return sb.ToString();
            }
            catch { return ""; }
        }

        /// <summary>当前所有 Edge / Chrome 的顶层窗口（Chromium 的窗口类名固定）。</summary>
        public static List<IntPtr> ListChromeWindows()
        {
            var list = new List<IntPtr>();
            try
            {
                EnumWindows(delegate(IntPtr h, IntPtr p)
                {
                    if (ClassName(h) == "Chrome_WidgetWin_1") list.Add(h);
                    return true;
                }, IntPtr.Zero);
            }
            catch { }
            return list;
        }
    }

    internal sealed class MainForm : Form
    {
        private const string AppTitle = "股票池追踪系统";
        private const string RunKey = @"SOFTWARE\Microsoft\Windows\CurrentVersion\Run";

        private readonly string _root;
        private readonly string _settingsPath;
        private readonly List<LogEntry> _logs = new List<LogEntry>();

        private readonly List<Button> _tabBtns = new List<Button>();
        private readonly List<Panel> _tabPages = new List<Panel>();
        private readonly FlowLayoutPanel _tabBar;
        private readonly Panel _tabBody;
        private int _tabIndex;
        private readonly Label _lbBackendDot;
        private readonly Label _lbAiDot;
        private readonly Label _lbApiVer;

        private RichTextBox _logBox;
        private ComboBox _logSource;
        private Label _lbBackendState;
        private Label _lbAiState;
        private Label _lbBackendAddr;
        private Label _lbAiAddr;
        private TextBox _tbPython;
        private TextBox _tbNode;
        private TextBox _tbRoot;
        private CheckBox _ckAutoStart;
        private CheckBox _ckMinimize;
        private CheckBox _ckStopOnExit;
        private CheckBox _ckRunOnBoot;
        private Label _lbEnvState;
        private Button _btnTheme;
        private bool _light;   // true = 浅色主题（网页 + 本窗口都跟着这个按钮走）

        // ---- 窗口自身的两套配色 ----
        private Color _cBg, _cPanel, _cText, _cSub, _cInput, _cInputText,
                      _cHead, _cHeadText, _cHeadSub, _cHeadBtn, _cHeadBtnText, _cLogBg, _cLogText;

        private ServiceItem _backend;
        private ServiceItem _dsh;
        private NotifyIcon _tray;
        private readonly System.Windows.Forms.Timer _timer;

        public MainForm()
        {
            _root = ResolveRoot();
            _settingsPath = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                "StockPoolLauncher", "settings.ini");

            Text = AppTitle;
            StartPosition = FormStartPosition.CenterScreen;
            Size = new Size(1020, 700);
            MinimumSize = new Size(880, 560);
            Font = new Font("Microsoft YaHei UI", 9f);
            BackColor = Color.FromArgb(245, 246, 248);

            // ---- 根布局：顶部状态条 + 页面区 ----
            // 只保留这一个窗口：页面直接以标签页形式装进来，不再另开「功能窗口」
            var root = new TableLayoutPanel();
            root.Dock = DockStyle.Fill;
            root.Margin = new Padding(0);
            root.Padding = new Padding(0);
            root.ColumnCount = 1;
            root.RowCount = 3;
            root.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100f));
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 56f));
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 36f));
            root.RowStyles.Add(new RowStyle(SizeType.Percent, 100f));
            root.BackColor = Color.FromArgb(245, 246, 248);
            Controls.Add(root);

            // ---- 顶部状态条：标题 + 服务状态灯（横向流式布局）----
            var head = new Panel();
            head.Dock = DockStyle.Fill;
            head.Margin = new Padding(0);
            head.BackColor = Color.FromArgb(32, 38, 48);
            head.Tag = "head";

            var headFlow = new FlowLayoutPanel();
            headFlow.Dock = DockStyle.Fill;
            headFlow.FlowDirection = FlowDirection.LeftToRight;
            headFlow.WrapContents = false;
            headFlow.Padding = new Padding(16, 14, 16, 0);
            headFlow.BackColor = Color.Transparent;
            headFlow.Margin = new Padding(0);
            head.Controls.Add(headFlow);

            var title = new Label();
            title.Text = AppTitle;
            title.ForeColor = Color.White;
            title.Font = new Font("Microsoft YaHei UI", 13f, FontStyle.Bold);
            title.AutoSize = true;
            title.Margin = new Padding(0, 2, 40, 0);
            title.Tag = "head-title";
            headFlow.Controls.Add(title);

            _lbBackendDot = Dot(headFlow, "后端 :8000");
            _lbAiDot = Dot(headFlow, "AI 服务 :3080");
            _lbApiVer = new Label();
            _lbApiVer.Text = "接口版本 -";
            _lbApiVer.ForeColor = Color.FromArgb(170, 178, 190);
            _lbApiVer.Tag = "head-muted";
            _lbApiVer.AutoSize = true;
            _lbApiVer.Margin = new Padding(0, 4, 0, 0);
            headFlow.Controls.Add(_lbApiVer);

            // ---- 状态条右侧：浅色 / 深色主题（网页页面跟着这个按钮走）----
            // Dock=Right 的容器要放在 Fill 的流式布局之后添加，才会占住右侧
            var themeHost = new Panel();
            themeHost.Dock = DockStyle.Right;
            themeHost.Width = 108;
            themeHost.Padding = new Padding(0, 13, 16, 0);
            themeHost.BackColor = Color.Transparent;
            _btnTheme = new Button();
            _btnTheme.Dock = DockStyle.Fill;
            _btnTheme.Tag = "theme-btn";
            _btnTheme.Text = "主题：深色";
            _btnTheme.FlatStyle = FlatStyle.Flat;
            _btnTheme.BackColor = Color.FromArgb(58, 66, 80);
            _btnTheme.ForeColor = Color.White;
            _btnTheme.FlatAppearance.BorderSize = 0;
            _btnTheme.Font = new Font("Microsoft YaHei UI", 9.5f);
            _btnTheme.Margin = new Padding(0);
            _btnTheme.Click += delegate { ToggleTheme(); };
            themeHost.Controls.Add(_btnTheme);
            head.Controls.Add(themeHost);

            root.Controls.Add(head, 0, 0);

            // ---- 导航标签行：自己画，才能跟着浅色 / 深色一起换（原生 TabControl 不认颜色）----
            _tabBar = new FlowLayoutPanel();
            _tabBar.Dock = DockStyle.Fill;
            _tabBar.FlowDirection = FlowDirection.LeftToRight;
            _tabBar.WrapContents = false;
            _tabBar.Margin = new Padding(0);
            _tabBar.Padding = new Padding(10, 6, 0, 0);
            _tabBar.BackColor = Color.FromArgb(30, 32, 38);
            root.Controls.Add(_tabBar, 0, 1);

            _tabBody = new Panel();
            _tabBody.Dock = DockStyle.Fill;
            _tabBody.Margin = new Padding(0);
            _tabBody.BackColor = Color.FromArgb(30, 32, 38);
            root.Controls.Add(_tabBody, 0, 2);

            BuildRpsPage();
            BuildChipPage();
            BuildToolPage();
            BuildLogPage();
            BuildSettingsPage();
            BuildServicePage();   // 服务控制台放最后一个标签

            _timer = new System.Windows.Forms.Timer();
            _timer.Interval = 3000;
            _timer.Tick += delegate { RefreshStatus(false); };
            _timer.Start();

            LoadSettings();
            InitServices();
            InitTray();

            Shown += delegate { Boot(); };
            Resize += OnResize;
            FormClosing += OnFormClosing;
        }

        #region 页面构建

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
                using (var pen = new Pen(Color.FromArgb(64, 120, 192), 2f))
                    e.Graphics.DrawLine(pen, 0, btn.Height - 1, btn.Width, btn.Height - 1);
            };
            _tabBtns.Add(b);
            _tabBar.Controls.Add(b);
            return p;
        }

        /// <summary>切换标签：只显示对应页面，并刷新标签行配色。</summary>
        private void SelectTab(int index)
        {
            if (index < 0 || index >= _tabPages.Count) return;
            _tabIndex = index;
            for (int i = 0; i < _tabPages.Count; i++) _tabPages[i].Visible = (i == index);
            SkinTabs();
        }

        /// <summary>标签行配色：选中用卡片色 + 蓝色下划线，未选中用窗口底色。</summary>
        private void SkinTabs()
        {
            if (_tabBar != null) _tabBar.BackColor = _cBg;
            for (int i = 0; i < _tabBtns.Count; i++)
            {
                bool sel = (i == _tabIndex);
                _tabBtns[i].BackColor = sel ? _cPanel : _cBg;
                _tabBtns[i].ForeColor = sel ? _cText : _cSub;
                _tabBtns[i].FlatAppearance.MouseOverBackColor = sel ? _cPanel : _cPanel;
                _tabBtns[i].Invalidate();
            }
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

        private Panel BuildServicePage()
        {
            var p = NewPage("服务控制台");
            var stack = Stack();

            TableLayoutPanel body1;
            var gb1 = Group("FastAPI 后端（127.0.0.1:8000）", out body1);
            _lbBackendState = Lbl("状态：检测中…");
            _lbBackendState.Font = new Font("Microsoft YaHei UI", 10f);
            _lbBackendAddr = Lbl("地址：http://127.0.0.1:8000");
            Mute(_lbBackendAddr);
            AddRow(body1, Row(_lbBackendState));
            AddRow(body1, Row(_lbBackendAddr));
            AddRow(body1, Row(
                MiniBtn("启动", delegate { StartBackend(true); }),
                MiniBtn("停止", delegate { StopBackend(true); }),
                MiniBtn("重启", delegate { Restart(_backend); }),
                MiniBtn("接口文档", delegate { WebWindow.Show("接口文档", "http://127.0.0.1:8000/docs"); }),
                MiniBtn("打开日志目录", delegate { OpenDir(Path.Combine(_root, "backend_fastapi")); }, 110)));
            AddRow(stack, gb1);

            TableLayoutPanel body2;
            var gb2 = Group("AI 服务 · dsh（127.0.0.1:3080）", out body2);
            _lbAiState = Lbl("状态：检测中…");
            _lbAiState.Font = new Font("Microsoft YaHei UI", 10f);
            _lbAiAddr = Lbl("地址：http://127.0.0.1:3080");
            Mute(_lbAiAddr);
            AddRow(body2, Row(_lbAiState));
            AddRow(body2, Row(_lbAiAddr));
            AddRow(body2, Row(
                MiniBtn("启动", delegate { StartDsh(true); }),
                MiniBtn("停止", delegate { StopDsh(true); }),
                MiniBtn("重启", delegate { Restart(_dsh); })));
            AddRow(stack, gb2);

            TableLayoutPanel body3;
            var gb3 = Group("常用操作", out body3);
            AddRow(body3, Row(
                MiniBtn("打开数据目录", delegate { OpenDir(DataDirGuess()); }, 120),
                MiniBtn("打开项目目录", delegate { OpenDir(_root); }, 120),
                MiniBtn("接口文档", delegate { WebWindow.Show("接口文档", "http://127.0.0.1:8000/docs"); }, 100),
                MiniBtn("关闭所有网页窗口", delegate { WebWindow.CloseAll(); }, 140)));
            var tip = Lbl("说明：RPS 体系、筹码体系、分析工具三个标签页各有一个 / 几个入口按钮，点开是自己的窗口"
                        + "（没有地址栏、没有标签页）；重复点同一个按钮只会把已开着的窗口切到最前。");
            Mute(tip);
            tip.AutoSize = false;
            tip.Width = 720;
            tip.Height = 40;
            AddRow(body3, Row(tip));
            AddRow(stack, gb3);

            TableLayoutPanel body4;
            var gb4 = Group("启动与退出", out body4);
            _ckAutoStart = Check("打开程序时自动拉起两个服务", true);
            _ckStopOnExit = Check("退出程序时停止由本程序启动的服务", true);
            AddRow(body4, Row(_ckAutoStart));
            AddRow(body4, Row(_ckStopOnExit));
            AddRow(stack, gb4);

            p.Controls.Add(stack);
            return p;
        }

        /// <summary>用独立窗口打开 frontend 下的页面（带上当前主题）。</summary>
        private void OpenWebPage(string fileName, string title)
        {
            var f = Path.Combine(_root, "frontend", fileName);
            if (!File.Exists(f)) { Msg("未找到 frontend\\" + fileName); return; }
            WebWindow.Show(title, UrlWithTheme(new Uri(f).AbsoluteUri));
        }

        /// <summary>切换浅色 / 深色：窗口本身 + 网页页面（包括已经打开的窗口）一起变。</summary>
        private void ToggleTheme()
        {
            _light = !_light;
            ApplyTheme();
            SaveSettingsQuiet();
        }

        /// <summary>把当前主题写到按钮上、给窗口换肤，并写给页面：页面打开时读它，运行中定时跟随。</summary>
        private void ApplyTheme()
        {
            BuildPalette();
            if (_btnTheme != null) _btnTheme.Text = _light ? "主题：浅色" : "主题：深色";
            Skin(this);
            SkinTabs();
            try
            {
                var f = Path.Combine(_root, "frontend", "theme-state.js");
                File.WriteAllText(f,
                    "/* Generated by StockPoolLauncher: light/dark theme follows the launcher button. */\r\n"
                    + "window.__LAUNCHER_THEME__ = \"" + (_light ? "light" : "dark") + "\";\r\n",
                    new UTF8Encoding(false));
            }
            catch { }
        }

        /// <summary>两套配色：浅色（白底深字）/ 深色（深底浅字）。</summary>
        private void BuildPalette()
        {
            if (_light)
            {
                _cBg = Color.FromArgb(245, 246, 248);
                _cPanel = Color.White;
                _cText = Color.FromArgb(32, 34, 40);
                _cSub = Color.FromArgb(105, 112, 124);
                _cInput = Color.White;
                _cInputText = Color.FromArgb(32, 34, 40);
                _cHead = Color.White;
                _cHeadText = Color.FromArgb(28, 32, 40);
                _cHeadSub = Color.FromArgb(105, 112, 124);
                _cHeadBtn = Color.FromArgb(233, 237, 243);
                _cHeadBtnText = Color.FromArgb(28, 32, 40);
                _cLogBg = Color.White;
                _cLogText = Color.FromArgb(32, 34, 40);
            }
            else
            {
                _cBg = Color.FromArgb(30, 32, 38);
                _cPanel = Color.FromArgb(38, 41, 48);
                _cText = Color.FromArgb(224, 228, 235);
                _cSub = Color.FromArgb(150, 158, 172);
                _cInput = Color.FromArgb(45, 49, 57);
                _cInputText = Color.FromArgb(224, 228, 235);
                _cHead = Color.FromArgb(24, 26, 32);
                _cHeadText = Color.White;
                _cHeadSub = Color.FromArgb(158, 166, 180);
                _cHeadBtn = Color.FromArgb(58, 66, 80);
                _cHeadBtnText = Color.White;
                _cLogBg = Color.FromArgb(28, 30, 36);
                _cLogText = Color.FromArgb(220, 224, 230);
            }
        }

        /// <summary>按当前配色给窗口及其所有控件上色（递归）。</summary>
        private void Skin(Control c)
        {
            if (c == null) return;
            string tag = c.Tag as string;

            if (tag == "dot")
            {
                // 状态灯的绿 / 橙 / 灰由服务状态决定，不参与换肤
            }
            else if (tag == "theme-btn")
            {
                c.BackColor = _cHeadBtn;
                c.ForeColor = _cHeadBtnText;
            }
            else if (tag == "head-title")
            {
                c.ForeColor = _cHeadText;
                c.BackColor = Color.Transparent;
            }
            else if (tag == "head-muted")
            {
                c.ForeColor = _cHeadSub;
                c.BackColor = Color.Transparent;
            }
            else if (tag == "head")
            {
                c.BackColor = _cHead;
            }
            else if (tag == "tabbtn")
            {
                // 标签按钮的配色由 SkinTabs 统一处理
            }
            else if (tag == "tabpage")
            {
                c.BackColor = _cBg;
                c.ForeColor = _cText;
            }
            else if (c is Button)
            {
                c.BackColor = Color.FromArgb(64, 120, 192);
                c.ForeColor = Color.White;
            }
            else if (c is Label)
            {
                c.ForeColor = (tag == "muted") ? _cSub : _cText;
                c.BackColor = Color.Transparent;
            }
            else if (c is CheckBox)
            {
                c.ForeColor = _cText;
                c.BackColor = Color.Transparent;
            }
            else if (c is TextBox || c is ComboBox)
            {
                c.BackColor = _cInput;
                c.ForeColor = _cInputText;
            }
            else if (c is RichTextBox)
            {
                c.BackColor = _cLogBg;
                c.ForeColor = _cLogText;
            }
            else if (c is GroupBox)
            {
                c.ForeColor = _cText;
                c.BackColor = _cPanel;
            }
            else if (c is Panel || c is TableLayoutPanel || c is FlowLayoutPanel)
            {
                // 透明容器（顶栏里的流式布局等）保持透明，跟随所在区域
                if (c.BackColor != Color.Transparent) c.BackColor = _cBg;
            }
            else
            {
                c.BackColor = _cBg;
                c.ForeColor = _cText;
            }

            foreach (Control child in c.Controls) Skin(child);
        }

        /// <summary>网页地址带上当前主题，页面一打开就是正确的颜色。</summary>
        private string UrlWithTheme(string url)
        {
            return url + (url.IndexOf('?') >= 0 ? "&" : "?") + "theme=" + (_light ? "light" : "dark");
        }

        /// <summary>RPS 体系：打开网页版「股票池追踪系统」。</summary>
        private Panel BuildRpsPage()
        {
            var p = NewPage("RPS 体系");
            var stack = Stack();

            var tip = Lbl("RPS（股价相对强度）体系在网页版「股票池追踪系统」里：导入今日股票池、导出备份、恢复数据、数据管理"
                        + "都在页面顶部的工具栏上。点下面的按钮会打开一个自己的窗口（没有地址栏、没有标签页）。");
            Mute(tip);
            tip.AutoSize = false;
            tip.Width = 720;
            tip.Height = 40;
            AddRow(stack, Row(tip));

            TableLayoutPanel body;
            var gb = Group("入口", out body);
            AddRow(body, Row(
                MiniBtn("股票池追踪系统", delegate { OpenWeb(); }, 160),
                MiniBtn("个股分析", delegate { OpenWebPage("stock-analysis.html", "个股分析"); }, 110)));
            var tip2 = Lbl("重复点同一个按钮只会把已开着的窗口切到最前，不会重复开窗。");
            Mute(tip2);
            AddRow(body, Row(tip2));
            AddRow(stack, gb);

            TableLayoutPanel body2;
            var gb2 = Group("相关目录", out body2);
            AddRow(body2, Row(
                MiniBtn("打开数据目录", delegate { OpenDir(DataDirGuess()); }, 120),
                MiniBtn("打开项目目录", delegate { OpenDir(_root); }, 120),
                MiniBtn("关闭所有网页窗口", delegate { WebWindow.CloseAll(); }, 140)));
            AddRow(stack, gb2);

            p.Controls.Add(stack);
            return p;
        }

        /// <summary>筹码体系：打开网页版「SCR 选股 · 筹码」。</summary>
        private Panel BuildChipPage()
        {
            var p = NewPage("筹码体系");
            var stack = Stack();

            var tip = Lbl("筹码体系在网页版「SCR 选股 · 筹码」里：打开后按页面上的条件选股，再看个股的筹码分布与 SCR 曲线。"
                        + "点下面的按钮会打开一个自己的窗口（没有地址栏、没有标签页）。");
            Mute(tip);
            tip.AutoSize = false;
            tip.Width = 720;
            tip.Height = 40;
            AddRow(stack, Row(tip));

            TableLayoutPanel body;
            var gb = Group("入口", out body);
            AddRow(body, Row(
                MiniBtn("SCR 选股 · 筹码", delegate { OpenWebPage("chip-scr.html", "SCR 选股 · 筹码体系"); }, 160)));
            var tip2 = Lbl("重复点同一个按钮只会把已开着的窗口切到最前，不会重复开窗。");
            Mute(tip2);
            AddRow(body, Row(tip2));
            AddRow(stack, gb);

            TableLayoutPanel body2;
            var gb2 = Group("相关目录", out body2);
            AddRow(body2, Row(
                MiniBtn("打开数据目录", delegate { OpenDir(DataDirGuess()); }, 120),
                MiniBtn("打开项目目录", delegate { OpenDir(_root); }, 120),
                MiniBtn("关闭所有网页窗口", delegate { WebWindow.CloseAll(); }, 140)));
            AddRow(stack, gb2);

            p.Controls.Add(stack);
            return p;
        }

        /// <summary>分析工具：三个工具各开一个自己的窗口。</summary>
        private Panel BuildToolPage()
        {
            var p = NewPage("分析工具");
            var stack = Stack();

            var tip = Lbl("三个分析工具各开一个自己的窗口（没有地址栏、没有标签页）：打开后按页面上的条件操作即可，"
                        + "需要后端 / AI 服务在跑。");
            Mute(tip);
            tip.AutoSize = false;
            tip.Width = 720;
            tip.Height = 40;
            AddRow(stack, Row(tip));

            TableLayoutPanel body;
            var gb = Group("入口", out body);
            AddRow(body, Row(
                MiniBtn("盘面及板块分析", delegate { OpenWebPage("market-sector.html", "盘面及板块分析"); }, 140),
                MiniBtn("大佬策略实验室", delegate { OpenWebPage("mentor-lab.html", "大佬策略实验室"); }, 140),
                MiniBtn("股票估值计算", delegate { OpenWebPage("valuation.html", "股票估值计算"); }, 140)));
            var tip2 = Lbl("重复点同一个按钮只会把已开着的窗口切到最前，不会重复开窗。");
            Mute(tip2);
            AddRow(body, Row(tip2));
            AddRow(stack, gb);

            p.Controls.Add(stack);
            return p;
        }

        private Panel BuildLogPage()
        {
            var p = NewPage("运行日志");

            _logBox = new RichTextBox();
            _logBox.Dock = DockStyle.Fill;
            _logBox.ReadOnly = true;
            _logBox.WordWrap = false;
            _logBox.BackColor = Color.FromArgb(28, 30, 36);
            _logBox.ForeColor = Color.FromArgb(220, 224, 230);
            _logBox.Font = new Font("Consolas", 9f);
            _logBox.BorderStyle = BorderStyle.None;
            p.Controls.Add(_logBox);

            var bar = new FlowLayoutPanel();
            bar.Dock = DockStyle.Top;
            bar.AutoSize = true;
            bar.AutoSizeMode = AutoSizeMode.GrowAndShrink;
            bar.WrapContents = false;
            bar.Padding = new Padding(0, 0, 0, 6);

            _logSource = new ComboBox();
            _logSource.DropDownStyle = ComboBoxStyle.DropDownList;
            _logSource.Width = 120;
            _logSource.Items.Add("全部");
            _logSource.Items.Add("后端");
            _logSource.Items.Add("AI服务");
            _logSource.Items.Add("系统");
            _logSource.SelectedIndex = 0;
            _logSource.SelectedIndexChanged += delegate { RenderLog(); };

            var lbSrc = Lbl("来源");
            lbSrc.Margin = new Padding(0, 5, 6, 0);
            _logSource.Margin = new Padding(0, 0, 12, 0);
            var bErr = MiniBtn("读取后端错误日志", delegate { ReadErrorLog(); }, 138);
            var bClear = MiniBtn("清空", delegate { _logs.Clear(); RenderLog(); }, 70);
            bar.Controls.Add(lbSrc);
            bar.Controls.Add(_logSource);
            bar.Controls.Add(bErr);
            bar.Controls.Add(bClear);
            p.Controls.Add(bar);
            return p;
        }

        private Panel BuildSettingsPage()
        {
            var p = NewPage("设置");
            var stack = Stack();

            TableLayoutPanel body;
            var gb = Group("运行环境", out body);
            _tbRoot = new TextBox();
            _tbRoot.Width = 430;
            _tbRoot.ReadOnly = true;
            _tbRoot.Text = _root;
            AddRow(body, Row(Lbl("项目根目录"), _tbRoot));

            _tbPython = new TextBox();
            _tbPython.Width = 430;
            AddRow(body, Row(Lbl("Python 路径"), _tbPython, MiniBtn("自动探测", delegate
            {
                var v = FindPython(_root);
                if (v == null) Msg("未找到 Python，可手动选择 python.exe");
                else _tbPython.Text = v;
            }, 88)));

            _tbNode = new TextBox();
            _tbNode.Width = 430;
            AddRow(body, Row(Lbl("Node 路径"), _tbNode, MiniBtn("自动探测", delegate
            {
                var v = FindNode();
                if (v == null) Msg("未找到 Node，可手动选择 node.exe");
                else _tbNode.Text = v;
            }, 88)));
            AddRow(stack, gb);

            TableLayoutPanel body2;
            var gb2 = Group("密钥配置（backend_fastapi\\.env）", out body2);
            _lbEnvState = Lbl("状态：-");
            AddRow(body2, Row(_lbEnvState, MiniBtn("打开 .env", delegate
            {
                var f = Path.Combine(_root, "backend_fastapi", ".env");
                if (!File.Exists(f))
                {
                    var ex = Path.Combine(_root, "backend_fastapi", ".env.example");
                    if (File.Exists(ex)) File.Copy(ex, f, false);
                    else File.WriteAllText(f, "LLM_BASE_URL=\r\nLLM_API_KEY=\r\nLLM_MODEL=\r\n", Encoding.UTF8);
                }
                OpenDir(f);
            }, 100)));
            AddRow(stack, gb2);

            TableLayoutPanel body3;
            var gb3 = Group("行为", out body3);
            _ckMinimize = Check("点窗口最小化按钮（—）时隐藏到托盘，而不是缩到任务栏", true);
            _ckRunOnBoot = Check("开机自动启动本程序", false);
            AddRow(body3, Row(_ckMinimize));
            AddRow(body3, Row(_ckRunOnBoot));
            var lbTheme = Lbl("浅色 / 深色在窗口顶栏右侧的「主题」按钮上切换，本窗口和网页页面会一起变。");
            Mute(lbTheme);
            AddRow(body3, Row(lbTheme));
            AddRow(body3, Row(MiniBtn("保存设置", delegate { SaveSettings(); Msg("已保存"); }, 100)));
            AddRow(stack, gb3);

            p.Controls.Add(stack);
            return p;
        }

        #endregion

        #region 控件小工具

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
            b.BackColor = Color.FromArgb(64, 120, 192);
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

        #endregion

        #region 路径与设置

        private string ResolveRoot()
        {
            var env = Environment.GetEnvironmentVariable("STOCK_POOL_ROOT");
            if (!string.IsNullOrEmpty(env) && Directory.Exists(env)) return env;
            var d = new DirectoryInfo(AppDomain.CurrentDomain.BaseDirectory);
            for (int i = 0; i < 6 && d != null; i++)
            {
                if (File.Exists(Path.Combine(d.FullName, "frontend", "index.html"))
                    && Directory.Exists(Path.Combine(d.FullName, "backend_fastapi")))
                    return d.FullName;
                d = d.Parent;
            }
            return AppDomain.CurrentDomain.BaseDirectory;
        }

        private static string FindPython(string root)
        {
            var cands = new List<string>();
            cands.Add(Path.Combine(root, "backend_fastapi", ".venv", "Scripts", "python.exe"));
            cands.Add(Path.Combine(root, "backend_fastapi", "venv", "Scripts", "python.exe"));
            var codex = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
                ".cache", "codex-runtimes", "codex-primary-runtime", "dependencies", "python", "python.exe");
            cands.Add(codex);
            var wb = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
                ".workbuddy", "binaries", "python");
            if (Directory.Exists(wb))
            {
                try
                {
                    foreach (var dir in Directory.GetDirectories(wb, "versions", SearchOption.AllDirectories))
                    {
                        foreach (var v in Directory.GetDirectories(dir))
                        {
                            cands.Add(Path.Combine(v, "python.exe"));
                        }
                    }
                }
                catch { }
            }
            foreach (var c in cands) if (File.Exists(c)) return c;
            return FindOnPath("python.exe");
        }

        private static string FindNode()
        {
            var envN = Environment.GetEnvironmentVariable("STOCK_POOL_NODE");
            if (!string.IsNullOrEmpty(envN) && File.Exists(envN)) return envN;
            var cands = new List<string>();
            cands.Add(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "nodejs", "node.exe"));
            var wb = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
                ".workbuddy", "binaries", "node");
            if (Directory.Exists(wb))
            {
                try
                {
                    foreach (var dir in Directory.GetDirectories(wb, "versions", SearchOption.AllDirectories))
                        foreach (var v in Directory.GetDirectories(dir))
                            cands.Add(Path.Combine(v, "node.exe"));
                }
                catch { }
            }
            foreach (var c in cands) if (File.Exists(c)) return c;
            return FindOnPath("node.exe");
        }

        private static string FindOnPath(string name)
        {
            var path = Environment.GetEnvironmentVariable("PATH") ?? "";
            foreach (var dir in path.Split(Path.PathSeparator))
            {
                if (string.IsNullOrEmpty(dir)) continue;
                try
                {
                    var f = Path.Combine(dir.Trim('"'), name);
                    if (File.Exists(f)) return f;
                }
                catch { }
            }
            return null;
        }

        private void LoadSettings()
        {
            string python = FindPython(_root);
            string node = FindNode();
            bool auto = true, mini = true, stop = true, boot = false;
            string theme = "dark";
            try
            {
                if (File.Exists(_settingsPath))
                {
                    foreach (var line in File.ReadAllLines(_settingsPath, Encoding.UTF8))
                    {
                        var kv = line.Split(new char[] { '=' }, 2);
                        if (kv.Length != 2) continue;
                        var k = kv[0].Trim();
                        var v = kv[1].Trim();
                        if (k == "python" && File.Exists(v)) python = v;
                        else if (k == "node" && File.Exists(v)) node = v;
                        else if (k == "autostart") auto = v == "1";
                        else if (k == "minimize") mini = v == "1";
                        else if (k == "stoponexit") stop = v == "1";
                        else if (k == "runonboot") boot = v == "1";
                        else if (k == "theme") theme = (v == "light" ? "light" : "dark");
                    }
                }
            }
            catch { }
            if (_tbPython != null) _tbPython.Text = python ?? "";
            if (_tbNode != null) _tbNode.Text = node ?? "";
            if (_ckAutoStart != null) _ckAutoStart.Checked = auto;
            if (_ckMinimize != null) _ckMinimize.Checked = mini;
            if (_ckStopOnExit != null) _ckStopOnExit.Checked = stop;
            if (_ckRunOnBoot != null) _ckRunOnBoot.Checked = boot;
            _light = (theme == "light");
            ApplyTheme();
        }

        private void SaveSettings()
        {
            try
            {
                var dir = Path.GetDirectoryName(_settingsPath);
                if (!Directory.Exists(dir)) Directory.CreateDirectory(dir);
                var sb = new StringBuilder();
                sb.AppendLine("python=" + (_tbPython.Text ?? ""));
                sb.AppendLine("node=" + (_tbNode.Text ?? ""));
                sb.AppendLine("autostart=" + (_ckAutoStart.Checked ? "1" : "0"));
                sb.AppendLine("minimize=" + (_ckMinimize.Checked ? "1" : "0"));
                sb.AppendLine("stoponexit=" + (_ckStopOnExit.Checked ? "1" : "0"));
                sb.AppendLine("runonboot=" + (_ckRunOnBoot.Checked ? "1" : "0"));
                sb.AppendLine("theme=" + (_light ? "light" : "dark"));
                File.WriteAllText(_settingsPath, sb.ToString(), Encoding.UTF8);
                ApplyRunOnBoot(_ckRunOnBoot.Checked);
            }
            catch (Exception ex)
            {
                Msg("保存失败：" + ex.Message);
            }
        }

        private static void ApplyRunOnBoot(bool on)
        {
            try
            {
                using (var rk = Registry.CurrentUser.OpenSubKey(RunKey, true))
                {
                    if (rk == null) return;
                    if (on) rk.SetValue("StockPoolLauncher", "\"" + Application.ExecutablePath + "\"");
                    else rk.DeleteValue("StockPoolLauncher", false);
                }
            }
            catch { }
        }

        private string DataDirGuess()
        {
            var d = Path.Combine(_root, "backend_fastapi", "data");
            if (Directory.Exists(d)) return d;
            return Path.Combine(_root, "backend_fastapi");
        }

        #endregion

        #region 服务管控

        private void InitServices()
        {
            var py = string.IsNullOrEmpty(_tbPython.Text) ? FindPython(_root) : _tbPython.Text;
            var node = string.IsNullOrEmpty(_tbNode.Text) ? FindNode() : _tbNode.Text;

            _backend = new ServiceItem();
            _backend.Name = "后端";
            _backend.Exe = py;
            _backend.Args = "-m uvicorn main:app --host 127.0.0.1 --port 8000";
            _backend.WorkDir = Path.Combine(_root, "backend_fastapi");
            _backend.HealthUrl = "http://127.0.0.1:8000/health";

            var dshBin = Path.Combine(_root, "agent_dsh", "node_modules", "@deepseek-ai", "dsh", "lib", "bin.js");
            _dsh = new ServiceItem();
            _dsh.Name = "AI服务";
            _dsh.Exe = node;
            _dsh.Args = "\"" + dshBin + "\" web --patch cordis.runtime.yml";
            _dsh.WorkDir = Path.Combine(_root, "agent_dsh");
            _dsh.HealthUrl = "http://127.0.0.1:3080/";
        }

        private void Boot()
        {
            RefreshStatus(true);
            if (!_ckAutoStart.Checked) return;
            var th = new Thread(delegate()
            {
                try
                {
                    if (!Probe(_backend.HealthUrl, 1500))
                    {
                        Ui(delegate { Log("系统", "正在启动后端…"); });
                        Ui(delegate { StartBackend(false); });
                        WaitHealth(_backend, 45);
                    }
                    else
                    {
                        Ui(delegate { Log("系统", "检测到后端已在运行（由其他进程启动，本程序不抢占）"); });
                    }

                    if (!Probe(_dsh.HealthUrl, 1500))
                    {
                        Ui(delegate { Log("系统", "正在启动 AI 服务…"); });
                        Ui(delegate { StartDsh(false); });
                        WaitHealth(_dsh, 30);
                    }
                    else
                    {
                        Ui(delegate { Log("系统", "检测到 AI 服务已在运行"); });
                    }
                    Ui(delegate { RefreshStatus(false); });
                }
                catch (Exception ex)
                {
                    Ui(delegate { Log("系统", "启动流程异常：" + ex.Message); });
                }
            });
            th.IsBackground = true;
            th.Start();
        }

        private static void WaitHealth(ServiceItem svc, int seconds)
        {
            for (int i = 0; i < seconds * 2; i++)
            {
                if (Probe(svc.HealthUrl, 1500)) return;
                Thread.Sleep(500);
            }
        }

        private void StartBackend(bool warn)
        {
            StartOne(_backend, warn);
        }

        private void StartDsh(bool warn)
        {
            StartOne(_dsh, warn);
        }

        private void StartOne(ServiceItem svc, bool warn)
        {
            try
            {
                if (svc.Alive || Probe(svc.HealthUrl, 1200))
                {
                    if (warn) Msg(svc.Name + " 已在运行");
                    return;
                }
                if (string.IsNullOrEmpty(svc.Exe) || !File.Exists(svc.Exe))
                {
                    Msg("未找到可执行文件：" + svc.Exe + "\n请在「设置」页指定正确路径。");
                    return;
                }
                svc.Start(delegate(string line) { Log(svc.Name, line); });
                svc.Starting = true;
                Log("系统", svc.Name + " 已发起启动（" + Path.GetFileName(svc.Exe) + "）");
                RefreshStatus(false);
            }
            catch (Exception ex)
            {
                Msg("启动 " + svc.Name + " 失败：" + ex.Message);
            }
        }

        private void StopBackend(bool warn)
        {
            StopOne(_backend, warn);
        }

        private void StopDsh(bool warn)
        {
            StopOne(_dsh, warn);
        }

        private void StopOne(ServiceItem svc, bool warn)
        {
            if (!svc.Alive)
            {
                svc.Owned = false;
                if (warn) Msg(svc.Name + " 未在运行");
                RefreshStatus(false);
                return;
            }
            svc.Stop(delegate(string line) { Log(svc.Name, line); });
            Log("系统", svc.Name + " 已停止");
            RefreshStatus(false);
        }

        private void Restart(ServiceItem svc)
        {
            var th = new Thread(delegate()
            {
                Ui(delegate { StopOne(svc, false); });
                Thread.Sleep(1200);
                Ui(delegate { StartOne(svc, false); });
                WaitHealth(svc, 45);
                Ui(delegate { RefreshStatus(false); });
            });
            th.IsBackground = true;
            th.Start();
        }

        private void RefreshStatus(bool initial)
        {
            bool bOk = Probe(_backend.HealthUrl, 1500);
            bool aOk = Probe(_dsh.HealthUrl, 1500);

            if (bOk)
            {
                string body;
                Probe(_backend.HealthUrl, 1500, out body);
                var ver = JsonValue(body, "api_version");
                SetDot(_lbBackendDot, Color.FromArgb(46, 204, 113), "后端 :8000 · 正常");
                if (_lbBackendState != null) _lbBackendState.Text = "状态：运行中" + (_backend.Owned ? "（本程序启动）" : "（其他进程启动）");
                if (_lbApiVer != null) _lbApiVer.Text = "接口版本 v" + (ver ?? "-");
            }
            else if (_backend != null && (_backend.Alive || _backend.Starting))
            {
                SetDot(_lbBackendDot, Color.Orange, "后端 :8000 · 启动中");
                if (_lbBackendState != null) _lbBackendState.Text = "状态：启动中…";
            }
            else
            {
                SetDot(_lbBackendDot, Color.Gray, "后端 :8000 · 未运行");
                if (_lbBackendState != null) _lbBackendState.Text = "状态：未运行";
                if (_lbApiVer != null) _lbApiVer.Text = "接口版本 -";
            }

            if (aOk)
            {
                SetDot(_lbAiDot, Color.FromArgb(46, 204, 113), "AI 服务 :3080 · 正常");
                if (_lbAiState != null) _lbAiState.Text = "状态：运行中";
            }
            else if (_dsh != null && (_dsh.Alive || _dsh.Starting))
            {
                SetDot(_lbAiDot, Color.Orange, "AI 服务 :3080 · 启动中");
                if (_lbAiState != null) _lbAiState.Text = "状态：启动中…";
            }
            else
            {
                SetDot(_lbAiDot, Color.Gray, "AI 服务 :3080 · 未运行");
                if (_lbAiState != null) _lbAiState.Text = "状态：未运行（未启动不影响股票池与估值功能）";
            }

            if (bOk && _backend != null) _backend.Starting = false;
            if (aOk && _dsh != null) _dsh.Starting = false;

            if (_lbEnvState != null)
            {
                var f = Path.Combine(_root, "backend_fastapi", ".env");
                _lbEnvState.Text = File.Exists(f) ? "状态：已配置 .env" : "状态：缺少 .env（LLM 相关功能不可用）";
                _lbEnvState.ForeColor = File.Exists(f) ? Color.ForestGreen : Color.IndianRed;
            }
            if (initial && _lbBackendAddr != null) _lbBackendAddr.Text = "地址：http://127.0.0.1:8000   （Python：" + Path.GetFileName(_backend.Exe ?? "-") + "）";
        }

        private static void SetDot(Label lb, Color color, string text)
        {
            if (lb == null) return;
            lb.ForeColor = color;
            lb.Text = "● " + text;
        }

        private static bool Probe(string url)
        {
            return Probe(url, 1500);
        }

        private static bool Probe(string url, int timeoutMs)
        {
            string body;
            return Probe(url, timeoutMs, out body);
        }

        private static bool Probe(string url, int timeoutMs, out string body)
        {
            body = "";
            HttpWebResponse resp = null;
            try
            {
                var req = (HttpWebRequest)WebRequest.Create(url);
                req.Method = "GET";
                req.Timeout = timeoutMs;
                req.ReadWriteTimeout = timeoutMs;
                req.KeepAlive = false;
                resp = (HttpWebResponse)req.GetResponse();
                using (var sr = new StreamReader(resp.GetResponseStream(), Encoding.UTF8))
                {
                    body = sr.ReadToEnd();
                }
                return (int)resp.StatusCode < 400;
            }
            catch
            {
                return false;
            }
            finally
            {
                if (resp != null)
                {
                    try { resp.Close(); } catch { }
                }
            }
        }

        private static string JsonValue(string json, string key)
        {
            if (string.IsNullOrEmpty(json)) return null;
            try
            {
                var js = new JavaScriptSerializer();
                var d = js.DeserializeObject(json) as Dictionary<string, object>;
                if (d == null || !d.ContainsKey(key)) return null;
                var v = d[key];
                if (v == null) return null;
                return Convert.ToString(v);
            }
            catch { return null; }
        }

        #endregion

        #region 日志 / 打开动作

        private void Log(string tag, string text)
        {
            if (text == null) return;
            var line = DateTime.Now.ToString("HH:mm:ss") + "  " + text;
            _logs.Add(new LogEntry { Tag = tag, Text = line });
            if (_logs.Count > 4000) _logs.RemoveRange(0, _logs.Count - 4000);
            if (_logBox == null || _logSource == null) return;
            var sel = Convert.ToString(_logSource.SelectedItem);
            if (sel == "全部" || sel == tag) _logBox.AppendText(line + "\r\n");
        }

        private void RenderLog()
        {
            if (_logBox == null || _logSource == null) return;
            var sel = Convert.ToString(_logSource.SelectedItem);
            _logBox.Clear();
            var sb = new StringBuilder();
            foreach (var e in _logs)
            {
                if (sel == "全部" || sel == e.Tag) sb.AppendLine(e.Text);
            }
            _logBox.Text = sb.ToString();
            _logBox.SelectionStart = _logBox.TextLength;
            _logBox.ScrollToCaret();
        }

        private void ReadErrorLog()
        {
            var f = Path.Combine(_root, "backend_fastapi", "uvicorn-error.log");
            if (!File.Exists(f)) { Msg("暂无 uvicorn-error.log"); return; }
            try
            {
                var lines = File.ReadAllLines(f, Encoding.UTF8);
                int from = Math.Max(0, lines.Length - 300);
                var sb = new StringBuilder();
                for (int i = from; i < lines.Length; i++) sb.AppendLine(lines[i]);
                _logSource.SelectedItem = "系统";
                Log("系统", "---- uvicorn-error.log 末尾 ----");
                _logBox.AppendText(sb.ToString());
            }
            catch (Exception ex) { Msg("读取失败：" + ex.Message); }
        }

        private void OpenWeb()
        {
            var f = Path.Combine(_root, "frontend", "index.html");
            if (!File.Exists(f)) { Msg("未找到 frontend\\index.html"); return; }
            WebWindow.Show("股票池追踪系统", UrlWithTheme(new Uri(f).AbsoluteUri));
        }

        private static void OpenDir(string path)
        {
            try
            {
                if (File.Exists(path)) Process.Start("explorer.exe", "/select,\"" + path + "\"");
                else if (Directory.Exists(path)) Process.Start("explorer.exe", "\"" + path + "\"");
                else MessageBox.Show("路径不存在：" + path);
            }
            catch (Exception ex) { MessageBox.Show("打开失败：" + ex.Message); }
        }

        #endregion

        #region 托盘与退出

        private void InitTray()
        {
            _tray = new NotifyIcon();
            try { _tray.Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); }
            catch { _tray.Icon = SystemIcons.Application; }
            _tray.Text = AppTitle;
            _tray.Visible = true;
            _tray.DoubleClick += delegate { ShowMe(); };

            var menu = new ContextMenu();
            menu.MenuItems.Add("显示主窗口", delegate { ShowMe(); });
            menu.MenuItems.Add("重启后端", delegate { Restart(_backend); });
            menu.MenuItems.Add("重启 AI 服务", delegate { Restart(_dsh); });
            menu.MenuItems.Add("打开网页版（独立窗口）", delegate { OpenWeb(); });
            menu.MenuItems.Add("关闭所有网页窗口", delegate { WebWindow.CloseAll(); });
            menu.MenuItems.Add("-");
            menu.MenuItems.Add("退出（停止服务）", delegate { RealExit(); });
            _tray.ContextMenu = menu;
        }

        private void ShowMe()
        {
            Show();
            WindowState = FormWindowState.Normal;
            BringToFront();
            Activate();
        }

        private void RealExit()
        {
            Close();
        }

        /// <summary>窗口最小化时按需隐藏到托盘（右上角 × 不在这里处理，它就是退出）。</summary>
        private void OnResize(object sender, EventArgs e)
        {
            if (WindowState != FormWindowState.Minimized) return;
            if (_ckMinimize == null || !_ckMinimize.Checked) return;
            Hide();
            if (_tray != null)
                _tray.ShowBalloonTip(2500, AppTitle, "已隐藏到托盘，双击图标可重新打开", ToolTipIcon.Info);
        }

        /// <summary>右上角 × / 托盘「退出」：保存设置 → 按设置停止服务 → 关掉网页窗口 → 退出进程。</summary>
        private void OnFormClosing(object sender, FormClosingEventArgs e)
        {
            SaveSettingsQuiet();
            if (_ckStopOnExit != null && _ckStopOnExit.Checked)
            {
                if (_backend != null) _backend.Stop(delegate(string s) { });
                if (_dsh != null) _dsh.Stop(delegate(string s) { });
            }
            if (_tray != null) { _tray.Visible = false; _tray.Dispose(); }
            WebWindow.CloseAll();
        }

        private void Msg(string text)
        {
            MessageBox.Show(this, text);
        }

        private void SaveSettingsQuiet()
        {
            try
            {
                var dir = Path.GetDirectoryName(_settingsPath);
                if (!Directory.Exists(dir)) Directory.CreateDirectory(dir);
                var sb = new StringBuilder();
                sb.AppendLine("python=" + (_tbPython.Text ?? ""));
                sb.AppendLine("node=" + (_tbNode.Text ?? ""));
                sb.AppendLine("autostart=" + (_ckAutoStart.Checked ? "1" : "0"));
                sb.AppendLine("minimize=" + (_ckMinimize.Checked ? "1" : "0"));
                sb.AppendLine("stoponexit=" + (_ckStopOnExit.Checked ? "1" : "0"));
                sb.AppendLine("runonboot=" + (_ckRunOnBoot.Checked ? "1" : "0"));
                sb.AppendLine("theme=" + (_light ? "light" : "dark"));
                File.WriteAllText(_settingsPath, sb.ToString(), Encoding.UTF8);
            }
            catch { }
        }

        #endregion

        private void Ui(Action a)
        {
            if (IsDisposed) return;
            try
            {
                if (InvokeRequired) BeginInvoke(a);
                else a();
            }
            catch { }
        }
    }
}
