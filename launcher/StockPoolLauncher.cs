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

    internal sealed partial class MainForm : Form
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
        private readonly Label _lbBackendDetail;


        private RichTextBox _logBox;
        private ComboBox _logSource;
        private Label _lbBackendState;
        private Label _lbAiState;
        private Label _lbBackendAddr;
        private Label _lbAiAddr;
        private TextBox _tbPython;
        private TextBox _tbNode;
        private TextBox _tbRoot;
        private TextBox _tbDataDir;
        private TextBox _tbTdx;
        private Label _lbTdx;
        private Dictionary<string, object> _lastTdxStatus;
        private CheckBox _ckAutoStart;
        private CheckBox _ckMinimize;
        private CheckBox _ckStopOnExit;
        private CheckBox _ckRunOnBoot;
        private Label _lbEnvState;
        private Button _btnTheme;
        private bool _light;   // true = 浅色主题（网页 + 本窗口都跟着这个按钮走）
        private bool _envChecked;   // 本次运行是否已做过环境检查（自动只做一次，装失败也不重试）
        private static Font _docFont;   // RPS 页说明框的正文 / 加粗字体（缓存，换肤时复用）
        private static Font _docBold;

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

            _lbBackendDetail = new Label();
            _lbBackendDetail.Text = "后端 -";
            _lbBackendDetail.ForeColor = Color.FromArgb(170, 178, 190);
            _lbBackendDetail.Tag = "head-muted";
            _lbBackendDetail.AutoSize = true;
            _lbBackendDetail.Margin = new Padding(0, 4, 0, 0);
            headFlow.Controls.Add(_lbBackendDetail);

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
            BuildValuationPage();
            BuildMarketPage();
            BuildStockPage();
            BuildDownloadPage();
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
            if (index == _mktTabIndex) MktOnEnter();   // 进入盘面页自动拉最新数据
            if (index == _stockTabIndex) StockOnEnter();
            if (index == _dlTabIndex) DlOnEnter();     // 进入下载页接着上次的任务刷新进度
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
                MiniBtn("接口文档", delegate { WebWindow.Show("接口文档", "http://127.0.0.1:8000/docs"); })));
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
            if (_mktSubBtns != null && _mktSubBtns.Count > 0) SkinMktSubTabs();
            if (_stockSubBtns != null && _stockSubBtns.Count > 0) SkinStockSubTabs();
            if (_stockHistoryList != null) StockRenderHistoryNav();
            if (_stockRangeMap != null && _stockRangeMap.Count > 0) StockSetRangeActive();
            if (_stockAdjustMap != null && _stockAdjustMap.Count > 0) StockSetAdjustActive();
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
            else if (tag == "mkt-dir")
            {
                // 盘面页的涨跌色（红涨 / 绿跌）由页面自己设置，不参与换肤
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
            else if (tag == "kpi")
            {
                c.BackColor = _cPanel;
                c.ForeColor = _cText;
            }
            else if (tag == "stock-range")
            {
                // 范围按钮的配色由 StockSetRangeActive 控制，跳过默认按钮上色
            }
            else if (tag == "stock-adjust")
            {
                // 复权按钮（前复权 / 不复权）的配色由 StockSetAdjustActive 控制，跳过默认按钮上色
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
                // 说明框里已插入的文字不会跟着 ForeColor 走，用新配色重写一遍
                if (tag == "doc-rps") FillRpsDoc((RichTextBox)c);
                else if (tag == "doc-chip") FillChipDoc((RichTextBox)c);
                else if (tag == "doc-ai") FillAiDoc((RichTextBox)c);
            }
            else if (c is DataGridView)
            {
                var dgv = (DataGridView)c;
                dgv.EnableHeadersVisualStyles = false;
                dgv.BackgroundColor = _cBg;
                dgv.GridColor = _light ? Color.FromArgb(222, 224, 228) : Color.FromArgb(58, 62, 72);
                Color sel = Color.FromArgb(64, 120, 192);
                // 单元格样式优先级：Cell > Row > RowsDefault > AlternatingRows > Column > DefaultCellStyle
                // 只设 DefaultCellStyle 会被上层压制，这里逐层显式设置，保证行底色跟随主题
                dgv.DefaultCellStyle.BackColor = _cPanel;
                dgv.DefaultCellStyle.ForeColor = _cText;
                dgv.DefaultCellStyle.SelectionBackColor = sel;
                dgv.DefaultCellStyle.SelectionForeColor = Color.White;
                dgv.RowsDefaultCellStyle.BackColor = _cPanel;
                dgv.RowsDefaultCellStyle.ForeColor = _cText;
                dgv.RowsDefaultCellStyle.SelectionBackColor = sel;
                dgv.RowsDefaultCellStyle.SelectionForeColor = Color.White;
                dgv.AlternatingRowsDefaultCellStyle.BackColor = _cPanel;
                dgv.AlternatingRowsDefaultCellStyle.ForeColor = _cText;
                dgv.AlternatingRowsDefaultCellStyle.SelectionBackColor = sel;
                dgv.AlternatingRowsDefaultCellStyle.SelectionForeColor = Color.White;
                dgv.RowTemplate.DefaultCellStyle.BackColor = _cPanel;
                dgv.RowTemplate.DefaultCellStyle.ForeColor = _cText;
                dgv.ColumnHeadersDefaultCellStyle.BackColor = _cBg;
                dgv.ColumnHeadersDefaultCellStyle.ForeColor = _cSub;
                dgv.ColumnHeadersDefaultCellStyle.SelectionBackColor = _cBg;
                dgv.ColumnHeadersDefaultCellStyle.SelectionForeColor = _cSub;
                foreach (DataGridViewColumn col in dgv.Columns)
                {
                    col.DefaultCellStyle.BackColor = _cPanel;
                    col.DefaultCellStyle.ForeColor = _cText;
                    col.DefaultCellStyle.SelectionBackColor = sel;
                    col.DefaultCellStyle.SelectionForeColor = Color.White;
                }
                dgv.Invalidate();
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

        /// <summary>RPS 体系：整页放使用说明（说明区自己滚动），底部固定一个「进入系统」按钮。</summary>
        private Panel BuildRpsPage()
        {
            var p = NewPage("RPS 体系");
            p.AutoScroll = false;   // 说明区内部滚动，页面本身不滚，按钮始终可见

            var root = new TableLayoutPanel();
            root.Dock = DockStyle.Fill;
            root.Margin = new Padding(0);
            root.Padding = new Padding(0);
            root.ColumnCount = 1;
            root.RowCount = 3;
            root.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100f));
            root.RowStyles.Add(new RowStyle(SizeType.AutoSize));      // 顶头一句话
            root.RowStyles.Add(new RowStyle(SizeType.Percent, 100f)); // 使用说明（自己滚动）
            root.RowStyles.Add(new RowStyle(SizeType.AutoSize));      // 底部按钮
            p.Controls.Add(root);

            var head = Lbl("RPS（股价相对强度）体系在网页版「股票池追踪系统」里，下面是这个页面的使用说明。");
            Mute(head);
            head.Margin = new Padding(0, 0, 0, 6);
            root.Controls.Add(head, 0, 0);

            var doc = new RichTextBox();
            doc.Tag = "doc-rps";
            doc.ReadOnly = true;
            doc.BorderStyle = BorderStyle.None;
            doc.Dock = DockStyle.Fill;
            doc.ScrollBars = RichTextBoxScrollBars.Vertical;
            doc.Font = new Font("Microsoft YaHei UI", 9.5f);
            doc.Margin = new Padding(0, 0, 0, 10);
            FillRpsDoc(doc);
            root.Controls.Add(doc, 0, 1);

            var enter = MiniBtn("进入系统", delegate { OpenWeb(); }, 180);
            enter.Height = 34;
            enter.Font = new Font("Microsoft YaHei UI", 10.5f);
            var hint = Lbl("点按钮打开独立窗口（没有地址栏、没有标签页），重复点只会切到已开着的窗口。");
            Mute(hint);
            root.Controls.Add(Row(enter, hint), 0, 2);

            return p;
        }

        /// <summary>把 RPS 体系的使用说明写进只读说明框；换肤时会被再调一次，用新配色重排一遍。</summary>
        private static void FillRpsDoc(RichTextBox rt)
        {
            var heads = new string[] {
                "1. 导入数据", "2. 查看历史", "3. 连续在榜天数", "4. 表格排序",
                "5. 数据备份与恢复", "6. 字段识别", "7. 个股研究详情"
            };
            var bodies = new string[] {
                "：点击「导入今日股票池」，选择 .xlsx / .xls 文件（也支持通达信等软件导出的 GBK 文本表格）。"
                    + "解析后预览确认，即以当天日期保存。",
                "：顶部日期选择器切换到任意有数据的一天，概览卡片、变动分析、表格和图表都会联动。",
                "：从选中日期往前数，只要连续出现就累加，中断则重新计算（≥7 天红色、3–6 天橙色、1–2 天灰色）。",
                "：点击表头切换排序；点击「细分行业」按行业分组，组内按连续天数降序。",
                "：「导出备份」下载 JSON 文件，「恢复数据」从 JSON 导入，「数据管理」可按天删除或清空。",
                "：自动识别表头中的「代码 / 名称 / 细分行业 / 地区」列；行业为空归为「未分类」；"
                    + "股票代码自动补零到 6 位，并自动去重。",
                "：点击表格 / 排行榜里的股票，查看真实日 K、均线、成交量、进出池标记和公告新闻时间轴；"
                    + "详情页的「AI 调研」按钮可继续生成主营业务、近期事项和多空观点报告。行情与调研依赖本地后端，本启动器会自动拉起。"
            };
            if (_docFont == null) _docFont = new Font("Microsoft YaHei UI", 9.5f);
            if (_docBold == null) _docBold = new Font(_docFont, FontStyle.Bold);
            var normal = _docFont;

            rt.Clear();
            rt.SelectionFont = normal;
            rt.SelectionColor = rt.ForeColor;
            rt.AppendText("纯本地工具，数据只保存在浏览器本地，不上传任何服务器。页面顶部的工具栏负责导入、导出与数据管理。"
                        + Environment.NewLine + Environment.NewLine);
            for (int i = 0; i < heads.Length; i++)
            {
                rt.SelectionFont = _docBold;
                rt.SelectionColor = rt.ForeColor;
                rt.AppendText(heads[i]);
                rt.SelectionFont = normal;
                rt.SelectionColor = rt.ForeColor;
                rt.AppendText(bodies[i] + Environment.NewLine + Environment.NewLine);
            }
            rt.SelectionStart = 0;
            rt.SelectionLength = 0;
        }

        /// <summary>筹码体系：整页放使用说明（说明区自己滚动），底部固定一个「进入系统」按钮。</summary>
        private Panel BuildChipPage()
        {
            var p = NewPage("筹码体系");
            p.AutoScroll = false;

            var root = new TableLayoutPanel();
            root.Dock = DockStyle.Fill;
            root.Margin = new Padding(0);
            root.Padding = new Padding(0);
            root.ColumnCount = 1;
            root.RowCount = 3;
            root.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100f));
            root.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            root.RowStyles.Add(new RowStyle(SizeType.Percent, 100f));
            root.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            p.Controls.Add(root);

            var head = Lbl("筹码体系在网页版「SCR 选股 · 筹码」里，下面是这个页面的使用说明。");
            Mute(head);
            head.Margin = new Padding(0, 0, 0, 6);
            root.Controls.Add(head, 0, 0);

            var doc = new RichTextBox();
            doc.Tag = "doc-chip";
            doc.ReadOnly = true;
            doc.BorderStyle = BorderStyle.None;
            doc.Dock = DockStyle.Fill;
            doc.ScrollBars = RichTextBoxScrollBars.Vertical;
            doc.Font = new Font("Microsoft YaHei UI", 9.5f);
            doc.Margin = new Padding(0, 0, 0, 10);
            FillChipDoc(doc);
            root.Controls.Add(doc, 0, 1);

            var enter = MiniBtn("进入系统", delegate { OpenWebPage("chip-scr.html", "SCR 选股 · 筹码体系"); }, 180);
            enter.Height = 34;
            enter.Font = new Font("Microsoft YaHei UI", 10.5f);
            var hint = Lbl("点按钮打开独立窗口（没有地址栏、没有标签页），重复点只会切到已开着的窗口。");
            Mute(hint);
            root.Controls.Add(Row(enter, hint), 0, 2);

            return p;
        }

        /// <summary>把筹码体系的使用说明写进说明框；换肤时会被再调一次，用新配色重排一遍。</summary>
        private static void FillChipDoc(RichTextBox rt)
        {
            // "#" 开头 = 小节标题，"-" 开头 = 列点，其余为正文段落
            var lines = new string[] {
                "#一、数据来源",
                "-通达信 → 用指标 SCR 做条件选股（SCR 后 100）→ 导出「临时条件股YYYYMMDD.xls」→ 按周导入本系统。",
                "-每周一份、需覆盖最近 5 周：周一~周四及周五收盘前（15:00）最新一期为上一周，周五收盘后及周末为本周；缺少任一期会直接报错并列出缺口。",
                "-导出文件多为 GBK 文本表格（扩展名虽是 .xls），本系统已兼容 GBK 文本 / Excel / CSV 三种格式。",
                "#二、选股逻辑",
                "SCR 后 100 表示筹码集中度极低：换手低迷、流动性匮乏、股价窄幅小幅波动。该形态通常说明筹码已高度集中在少数账户手中，高控盘特征明显，多处于吸筹磨底期——主力在低位缓慢收集筹码、尚未拉动股价。",
                "#三、需人工排除的情形（形态相似，性质相反）",
                "-高位平台出货：股价已在相对高位横盘派发，同样表现为低波动，但方向与吸筹相反。",
                "-大规模回购：回购注销或库存股导致筹码数据失真，并非主力主动吸筹。",
                "-指数配置：因指数成分股调整而被动持有，缺少主动控盘意图。",
                "#四、三档含义",
                "-第一档·磨主峰：最新一期在榜 ∩ 连续 N 期全勤 ∩ 流通市值区间内 ∩ PE > 0。持续留在低集中度池中，最贴合「吸筹磨底」特征。",
                "-第二档·向下破位离榜：曾入选但最新一期已离榜，区间涨幅未达启动阈值。该形态存在两个相反的演化方向，本系统公式目前无法识别：向上——筹码最高峰锁定、价格企稳，为启动前的深度洗盘，第一预期为回拉主峰价，若后续继续强势突破，则大概率进入主升；向下——下方筹码持续堆积、支撑失守，则为真正的破位下行。风险提示：下方筹码堆积时千万不能过早介入，须等价格企稳、回拉主峰价确认后再评估；形态相似但方向相反，务必结合筹码结构与量能人工判断。",
                "-第三档·启动型离榜：离榜且区间涨幅达到阈值（默认 30 日涨幅 ≥ 10%）。疑似吸筹完成并启动，可跟踪后续回踩机会。",
                "#五、使用建议",
                "-本页输出的是形态候选池，不等于买入信号，请结合基本面、行业景气与大盘环境二次判断。",
                "-阈值（连续全勤期数 / 最少在榜期数 / 流通市值区间 / 启动阈值）可在「展开阈值」中调整，改完点「刷新计算」生效。",
                "-三档表格支持点档位标题折叠、点表头排序，便于按市值 / PE / 涨幅快速筛查。",
                "-本页结果由公开数据与固定规则推演，仅供研究参考，不构成任何投资建议。"
            };
            if (_docFont == null) _docFont = new Font("Microsoft YaHei UI", 9.5f);
            if (_docBold == null) _docBold = new Font(_docFont, FontStyle.Bold);
            var normal = _docFont;

            rt.Clear();
            rt.SelectionFont = normal;
            rt.SelectionColor = rt.ForeColor;
            var first = true;
            foreach (var raw in lines)
            {
                bool isHead = raw.Length > 0 && raw[0] == '#';
                rt.SelectionFont = isHead ? _docBold : normal;
                rt.SelectionColor = rt.ForeColor;
                if (isHead)
                {
                    if (!first) rt.AppendText(Environment.NewLine);
                    rt.AppendText(raw.Substring(1) + Environment.NewLine);
                }
                else if (raw.Length > 0 && raw[0] == '-')
                {
                    rt.AppendText("· " + raw.Substring(1) + Environment.NewLine);
                }
                else
                {
                    rt.AppendText(raw + Environment.NewLine + Environment.NewLine);
                }
                first = false;
            }
            rt.SelectionStart = 0;
            rt.SelectionLength = 0;
        }

        /// <summary>分析工具：三个工具各做成一张卡片（图标 + 标题 + 简介 + 进入按钮）。</summary>
        private Panel BuildToolPage()
        {
            var p = NewPage("分析工具");
            var stack = Stack();

            var tip = Lbl("盘面及板块分析、大佬策略实验室各开一个自己的窗口（没有地址栏、没有标签页）；"
                        + "股票估值计算已做成原生页面，就在本窗口的「估值计算」标签页里。都需要后端 / AI 服务在跑。");
            Mute(tip);
            tip.AutoSize = false;
            tip.Width = 720;
            tip.Height = 40;
            AddRow(stack, Row(tip));

            AddRow(stack, ToolCard("📊", "盘面及板块分析",
                "外围市场、大盘资金、行业 / 概念板块强弱与个股联动，一屏看清当日盘面结构。",
                null, "盘面及板块分析", onEnter: delegate { SelectTab(_mktTabIndex); }));
            AddRow(stack, ToolCard("🧠", "大佬策略实验室",
                "把大佬公开资料交给 AI 提炼，人工审核后生成每日观点，沉淀为可回测的候选策略。",
                "mentor-lab.html", "大佬策略实验室"));
            AddRow(stack, ToolCard("📈", "股票估值计算",
                "输入标的与假设，按多种估值模型测算内在价值区间，辅助判断高估 / 低估。",
                null, "股票估值计算", onEnter: delegate { SelectTab(_valTabIndex); }));
            AddRow(stack, ToolCard("🔍", "个股分析",
                "前复权日K + 入池出池轨迹 + 消息面时间轴，一图看清个股历史与异动。",
                null, "个股分析", onEnter: delegate { SelectTab(_stockTabIndex); }));

            var tip2 = Lbl("重复点同一个「进入」按钮，网页版只会把已开着的窗口切到最前；「股票估值计算」则切到本窗口的估值标签页。");
            Mute(tip2);
            AddRow(stack, Row(tip2));

            p.Controls.Add(stack);
            return p;
        }


        /// <summary>一张工具卡片：图标 + 标题（GroupBox 标题）、简介、进入按钮；跟随主题换肤。</summary>
        private GroupBox ToolCard(string icon, string title, string desc, string file, string pageTitle, Action onEnter = null)
        {
            TableLayoutPanel body;
            var gb = Group(icon + "  " + title, out body);
            body.BackColor = Color.Transparent;   // 跟随 GroupBox 卡片底色，不露两层底色
            var d = Lbl(desc);
            d.AutoSize = true;
            d.MaximumSize = new Size(780, 0);
            AddRow(body, Row(d));
            var enter = onEnter != null
                ? MiniBtn("进入", delegate { onEnter(); }, 96)
                : MiniBtn("进入", delegate { OpenWebPage(file, pageTitle); }, 96);
            AddRow(body, Row(enter));
            return gb;
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
            _logSource.Items.Add("环境");
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

            _tbDataDir = new TextBox();
            _tbDataDir.Width = 430;
            AddRow(body, Row(Lbl("数据目录（stockanaly-data）"), _tbDataDir));
            AddRow(body, Row(MiniBtn("浏览…", delegate
            {
                using (var dlg = new FolderBrowserDialog())
                {
                    dlg.Description = "选择 stockanaly-data 数据目录";
                    if (!string.IsNullOrEmpty(_tbDataDir.Text) && Directory.Exists(_tbDataDir.Text))
                        dlg.SelectedPath = _tbDataDir.Text;
                    if (dlg.ShowDialog(this) == DialogResult.OK)
                        _tbDataDir.Text = dlg.SelectedPath;
                }
            }, 88), MiniBtn("保存并重载", delegate { SaveDataDir(); }, 110),
                MiniBtn("刷新", delegate { RefreshDataDir(); }, 80),
                Mute(Lbl("修改后重启后端生效"))));

            _tbDataDir.Text = ResolveDataDirSetting();
            RefreshDataDir();

            _tbTdx = new TextBox();
            _tbTdx.Width = 430;
            AddRow(body, Row(Lbl("通达信目录（new_tdx64）"), _tbTdx));
            _lbTdx = Mute(Lbl("未检测"));
            AddRow(body, Row(MiniBtn("浏览…", delegate
            {
                using (var dlg = new FolderBrowserDialog())
                {
                    dlg.Description = "选择通达信安装目录（里面有 vipdoc 的那个）";
                    if (!string.IsNullOrEmpty(_tbTdx.Text) && Directory.Exists(_tbTdx.Text))
                        dlg.SelectedPath = _tbTdx.Text;
                    if (dlg.ShowDialog(this) == DialogResult.OK)
                        _tbTdx.Text = dlg.SelectedPath;
                }
            }, 88), MiniBtn("保存", delegate { SaveTdxPath(); }, 80),
                MiniBtn("自动检测", delegate { DetectTdx(); }, 96),
                MiniBtn("刷新", delegate { RefreshTdx(); }, 80), _lbTdx));

            RefreshTdx();

            var lbEnvTip = Lbl("启动时自动检查环境：Python + 后端依赖（requirements.txt）是硬要求，缺了会拉起 launcher\\install_env.bat 装一次，装不上就退出并给出错误日志。");
            Mute(lbEnvTip);
            AddRow(body, Row(lbEnvTip, MiniBtn("检查 / 修复环境", delegate
            {
                var th = new Thread(delegate()
                {
                    try
                    {
                        if (PrepareEnv(true)) Ui(delegate { Msg("环境检查通过。"); });
                    }
                    catch (Exception ex)
                    {
                        Ui(delegate { Msg("环境检查出错：" + ex.Message); });
                    }
                });
                th.IsBackground = true;
                th.Start();
            }, 120)));
            AddRow(stack, gb);

            TableLayoutPanel body2;
            var gb2 = Group("密钥配置（backend_fastapi\\.env · agent_dsh\\.env）", out body2);
            _lbEnvState = Lbl("状态：-");
            AddRow(body2, Row(MiniBtn("打开后端 .env", delegate
            {
                var f = Path.Combine(_root, "backend_fastapi", ".env");
                if (!File.Exists(f))
                {
                    var ex = Path.Combine(_root, "backend_fastapi", ".env.example");
                    if (File.Exists(ex)) File.Copy(ex, f, false);
                    else File.WriteAllText(f, "LLM_BASE_URL=\r\nLLM_API_KEY=\r\nLLM_MODEL=\r\n", Encoding.UTF8);
                }
                OpenDir(f);
            }, 130), _lbEnvState));
            AddRow(body2, Row(MiniBtn("打开小牛 .env", delegate
            {
                var f = Path.Combine(_root, "agent_dsh", ".env");
                if (!File.Exists(f))
                {
                    var ex = Path.Combine(_root, "agent_dsh", ".env.example");
                    if (File.Exists(ex)) File.Copy(ex, f, false);
                    else File.WriteAllText(f, "DEEPSEEK_API_KEY=\r\n", Encoding.UTF8);
                }
                OpenDir(f);
            }, 130), Mute(Lbl("红色小牛问答专用"))));
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

        /// <summary>本地推算 stockanaly-data 数据目录：优先读 backend_fastapi/.env 的
        /// DATA_DIR / STOCK_DATA_DIR，否则用默认 &lt;仓库上级&gt;/stockanaly-data。</summary>
        private string ResolveDataDirSetting()
        {
            try
            {
                var env = Path.Combine(_root, "backend_fastapi", ".env");
                if (File.Exists(env))
                {
                    foreach (var line in File.ReadAllLines(env, Encoding.UTF8))
                    {
                        var t = line.Trim();
                        if (t.StartsWith("DATA_DIR=", StringComparison.OrdinalIgnoreCase))
                            return t.Substring(10).Trim().Trim('"');
                        if (t.StartsWith("STOCK_DATA_DIR=", StringComparison.OrdinalIgnoreCase))
                            return t.Substring(16).Trim().Trim('"');
                    }
                }
            }
            catch { }
            var parent = Path.GetDirectoryName(_root);
            return Path.Combine(parent ?? _root, "stockanaly-data");
        }

        /// <summary>从后端 /api/system/storage 取当前数据目录并刷新文本框（后端未运行时静默跳过）。</summary>
        private void RefreshDataDir()
        {
            var th = new Thread(delegate()
            {
                string body;
                if (!Probe("http://127.0.0.1:8000/api/system/storage", 5000, out body)) return;
                var root = JsonValue(body, "root");
                if (!string.IsNullOrEmpty(root))
                    Ui(delegate { if (_tbDataDir != null) _tbDataDir.Text = root; });
            });
            th.IsBackground = true;
            th.Start();
        }

        /// <summary>把文本框里的路径 POST 给后端 /api/system/storage，写入 .env（重启后端生效）。</summary>
        private void SaveDataDir()
        {
            var path = (_tbDataDir.Text ?? "").Trim();
            if (string.IsNullOrEmpty(path)) { Msg("请先选择或填写数据目录"); return; }
            var th = new Thread(delegate()
            {
                try
                {
                    string body;
                    if (!Probe("http://127.0.0.1:8000/api/system/storage", 5000, out body))
                    {
                        Ui(delegate { Msg("后端未运行，无法保存数据目录（可在后端启动后重试）"); });
                        return;
                    }
                    var js = new JavaScriptSerializer();
                    var json = js.Serialize(new Dictionary<string, object> { { "data_dir", path } });
                    if (!PostJson("http://127.0.0.1:8000/api/system/storage", json, 8000, out body))
                    {
                        var err = JsonValue(body, "detail") ?? body;
                        Ui(delegate { Msg("保存失败：" + (err ?? "未知错误")); });
                        return;
                    }
                    Ui(delegate
                    {
                        Msg("已保存数据目录，重启后端后生效（旧数据会在启动时自动补到新目录）");
                        RefreshDataDir();
                    });
                }
                catch (Exception ex) { Ui(delegate { Msg("保存出错：" + ex.Message); }); }
            });
            th.IsBackground = true;
            th.Start();
        }

        // ---- 通达信目录（配置后，下载页才会出现「同步通达信历史数据」）----

        private const string TdxStatusUrl = "http://127.0.0.1:8000/api/history/download/tdx/status";

        /// <summary>把通达信目录写到后端设置里（后端会校验该目录是否真有日线数据）。</summary>
        private void SaveTdxPath()
        {
            var path = (_tbTdx.Text ?? "").Trim();
            var th = new Thread(delegate()
            {
                try
                {
                    string resp;
                    if (!Probe(TdxStatusUrl, 5000, out resp))
                    {
                        Ui(delegate { Msg("后端未运行，无法保存通达信目录"); });
                        return;
                    }
                    var json = new JavaScriptSerializer().Serialize(
                        new Dictionary<string, object> { { "tdx_path", path } });
                    if (!PostJson("http://127.0.0.1:8000/api/history/download/settings",
                                  json, 15000, out resp))
                    {
                        var err = JsonValue(resp, "detail");
                        Ui(delegate { Msg("保存失败：" + (string.IsNullOrEmpty(err) ? resp : err)); });
                        return;
                    }
                    Ui(delegate { Msg("已保存通达信目录"); RefreshTdx(); });
                }
                catch (Exception ex) { Ui(delegate { Msg("保存出错：" + ex.Message); }); }
            });
            th.IsBackground = true;
            th.Start();
        }

        /// <summary>拉一次后端的通达信检测结果，填入路径框并给出「可用 / 未配置」提示。</summary>
        private void RefreshTdx()
        {
            var th = new Thread(delegate()
            {
                string resp;
                try
                {
                    if (!Probe(TdxStatusUrl, 10000, out resp)) return;
                    var root = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(resp);
                    Ui(delegate
                    {
                        if (_tbTdx == null || _lbTdx == null) return;
                        _lastTdxStatus = root;
                        var configured = Convert.ToString(DictVal(root, "configured") ?? "");
                        var auto = Convert.ToString(DictVal(root, "auto_detected") ?? "");
                        bool valid = false;
                        try { valid = Convert.ToBoolean(DictVal(root, "valid")); }
                        catch { }
                        int codes = 0;
                        try { codes = Convert.ToInt32(DictVal(root, "codes")); }
                        catch { }
                        var sample = Convert.ToString(DictVal(root, "sample") ?? "");
                        if (_tbTdx.Text.Length == 0) _tbTdx.Text = configured.Length > 0 ? configured : auto;
                        if (valid) _lbTdx.Text = string.Format("可用：{0} 只，{1}", codes, sample);
                        else if (auto.Length > 0) _lbTdx.Text = "未配置（检测到 " + auto + "）";
                        else _lbTdx.Text = "未配置，也没检测到通达信";
                        DlRefreshTdx();          // 让下载页同步显示 / 隐藏本地同步入口
                    });
                }
                catch { }
            });
            th.IsBackground = true;
            th.Start();
        }

        /// <summary>用后端自动检测到的路径填入并保存。</summary>
        private void DetectTdx()
        {
            if (_lastTdxStatus == null) { RefreshTdx(); return; }
            var auto = Convert.ToString(DictVal(_lastTdxStatus, "auto_detected") ?? "");
            if (string.IsNullOrEmpty(auto)) { Msg("本机没有检测到通达信目录，请手动浏览选择"); return; }
            _tbTdx.Text = auto;
            SaveTdxPath();
        }

        private static object DictVal(Dictionary<string, object> dict, string key)
        {
            object value;
            return (dict != null && dict.TryGetValue(key, out value)) ? value : null;
        }

        #endregion

        #region 环境与依赖检查

        /// <summary>一次环境检查的结果：Problems 是硬伤（后端起不来），Warnings 只影响可选功能。</summary>
        private sealed class EnvCheck
        {
            public string Python;
            public string Version;
            public List<string> Problems = new List<string>();
            public List<string> Warnings = new List<string>();

            public bool Ok { get { return Problems.Count == 0; } }

            public string ProblemText { get { return string.Join("；", Problems.ToArray()); } }
        }

        /// <summary>
        /// 启动前检查环境：Python + 后端依赖是硬要求，缺了就拉起 launcher\install_env.bat 装一次；
        /// 装完复检，还不行就把安装日志显示出来并退出。整个过程只尝试一次，不反复重试。
        /// </summary>
        /// <param name="manual">true = 设置页手动点的「检查 / 修复环境」，允许再跑一次。</param>
        private bool PrepareEnv(bool manual)
        {
            if (_envChecked && !manual) return true;
            _envChecked = true;

            var r = CheckEnv();
            if (r.Ok)
            {
                Ui(delegate { Log("环境", "环境检查通过：Python " + (r.Version ?? "-")); });
                return true;
            }
            Ui(delegate
            {
                foreach (var p in r.Problems) Log("环境", "缺失：" + p);
                foreach (var w in r.Warnings) Log("环境", "提示：" + w);
            });

            bool go = UiSync<bool>(delegate
            {
                return MessageBox.Show(this,
                    "环境检查未通过：\n  · " + r.ProblemText +
                    "\n\n是否现在自动安装？\n（创建 backend_fastapi\\.venv 并安装 requirements.txt，只尝试一次）",
                    AppTitle + " - 环境检查", MessageBoxButtons.YesNo, MessageBoxIcon.Warning) == DialogResult.Yes;
            });
            if (!go) { EnvFail("环境不完整，且未执行安装（在提示框里选了「否」）", null); return false; }

            var bat = Path.Combine(_root, "launcher", "install_env.bat");
            if (!File.Exists(bat)) { EnvFail("找不到安装脚本：" + bat, null); return false; }

            Ui(delegate { Log("环境", "开始安装环境（launcher\\install_env.bat）…"); });
            int code;
            var output = RunInstaller(bat, out code);
            if (code != 0) { EnvFail("安装脚本退出码 " + code + "：" + ExitReason(code), output); return false; }

            var r2 = CheckEnv();
            if (!r2.Ok) { EnvFail("安装完成，但复检仍未通过：" + r2.ProblemText, output); return false; }

            Ui(delegate
            {
                Log("环境", "环境安装完成，复检通过：Python " + (r2.Version ?? "-"));
                if (!string.IsNullOrEmpty(r2.Python) && _tbPython != null)
                {
                    _tbPython.Text = r2.Python;
                    // 服务还没拉起时才改指向，避免丢掉已启动进程的句柄
                    if (_backend != null && _backend.Proc == null) _backend.Exe = r2.Python;
                    SaveSettingsQuiet();
                }
            });
            return true;
        }

        /// <summary>检查 Python / 后端依赖（硬要求），以及 Node、node_modules（只提示）。</summary>
        private EnvCheck CheckEnv()
        {
            var r = new EnvCheck();

            string py = UiSync<string>(delegate
            {
                return !string.IsNullOrEmpty(_tbPython.Text) ? _tbPython.Text : FindPython(_root);
            });
            r.Python = py;

            if (string.IsNullOrEmpty(py) || !File.Exists(py))
            {
                r.Problems.Add("未找到 Python 解释器（后端依赖它启动）");
            }
            else
            {
                int code;
                var ver = RunCapture(py, "-c \"import sys; print('%d.%d.%d' % sys.version_info[:3])\"", 30000, out code);
                if (code != 0 || string.IsNullOrWhiteSpace(ver))
                {
                    r.Problems.Add("Python 无法执行：" + py);
                }
                else
                {
                    r.Version = ver.Trim();
                    if (!VersionAtLeast(r.Version, 3, 9))
                        r.Problems.Add("Python 版本过低（" + r.Version + "，需要 3.9 及以上）");
                }

                int depCode;
                var depOut = RunCapture(py, "-c \"import uvicorn, fastapi, akshare, pypdf, multipart\"", 180000, out depCode);
                if (depCode != 0)
                    r.Problems.Add("后端依赖不完整（requirements.txt 没装齐）" + Brief(depOut));
            }

            string node = UiSync<string>(delegate
            {
                return !string.IsNullOrEmpty(_tbNode.Text) ? _tbNode.Text : FindNode();
            });
            if (string.IsNullOrEmpty(node) || !File.Exists(node))
                r.Warnings.Add("未找到 Node.js：AI 服务（:3080）起不来，后端和网页不受影响");
            else if (!Directory.Exists(Path.Combine(_root, "agent_dsh", "node_modules")))
                r.Warnings.Add("agent_dsh\\node_modules 缺失：AI 服务（:3080）起不来，后端和网页不受影响");

            return r;
        }

        /// <summary>跑一次环境安装脚本，输出实时进日志框，同时收集全文（失败时落盘 + 弹窗展示）。</summary>
        private string RunInstaller(string bat, out int exitCode)
        {
            var sb = new StringBuilder();
            var psi = new ProcessStartInfo();
            psi.FileName = "cmd.exe";
            psi.Arguments = "/c chcp 65001 >nul && \"" + bat + "\" \"" + _root + "\" silent";
            psi.WorkingDirectory = _root;
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.RedirectStandardOutput = true;
            psi.RedirectStandardError = true;
            psi.StandardOutputEncoding = Encoding.UTF8;
            psi.StandardErrorEncoding = Encoding.UTF8;
            psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            psi.EnvironmentVariables["PYTHONUTF8"] = "1";

            // 安装可能很久（首次装 akshare / 拉 npm），给 20 分钟上限
            exitCode = RunAndCollect(psi, 20 * 60 * 1000, sb,
                delegate(string line) { Ui(delegate { Log("环境", line); }); });
            return sb.ToString();
        }

        /// <summary>跑一条命令并拿到输出（用于探测 Python 版本、依赖是否齐全）。</summary>
        private static string RunCapture(string exe, string args, int timeoutMs, out int exitCode)
        {
            var sb = new StringBuilder();
            var psi = new ProcessStartInfo();
            psi.FileName = exe;
            psi.Arguments = args;
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.RedirectStandardOutput = true;
            psi.RedirectStandardError = true;
            psi.StandardOutputEncoding = Encoding.UTF8;
            psi.StandardErrorEncoding = Encoding.UTF8;
            psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            psi.EnvironmentVariables["PYTHONUTF8"] = "1";
            exitCode = RunAndCollect(psi, timeoutMs, sb, null);
            return sb.ToString();
        }

        /// <summary>启动进程：异步收集 stdout/stderr，超时直接终止，返回退出码（-1 = 超时或异常）。</summary>
        private static int RunAndCollect(ProcessStartInfo psi, int timeoutMs, StringBuilder sink, Action<string> onLine)
        {
            try
            {
                using (var p = new Process())
                {
                    p.StartInfo = psi;
                    p.OutputDataReceived += delegate(object s, DataReceivedEventArgs e)
                    {
                        if (e.Data == null) return;
                        if (sink != null) lock (sink) sink.AppendLine(e.Data);
                        if (onLine != null) onLine(e.Data);
                    };
                    p.ErrorDataReceived += delegate(object s, DataReceivedEventArgs e)
                    {
                        if (e.Data == null) return;
                        if (sink != null) lock (sink) sink.AppendLine("[err] " + e.Data);
                        if (onLine != null) onLine("[err] " + e.Data);
                    };
                    p.Start();
                    p.BeginOutputReadLine();
                    p.BeginErrorReadLine();

                    if (!p.WaitForExit(timeoutMs))
                    {
                        try { p.Kill(); } catch { }
                        p.WaitForExit(5000);
                        if (sink != null) sink.AppendLine("[timeout] 超过 " + (timeoutMs / 1000) + " 秒仍未结束，已终止。");
                        return -1;
                    }
                    p.WaitForExit();   // 等异步读取把剩下的输出收完
                    return p.ExitCode;
                }
            }
            catch (Exception ex)
            {
                if (sink != null) sink.AppendLine("[error] " + ex.Message);
                return -1;
            }
        }

        /// <summary>环境装不上：日志框切到「环境」+ 弹窗显示原因和日志末尾，然后退出程序。</summary>
        private void EnvFail(string reason, string installLog)
        {
            string path = "";
            try
            {
                var dir = Path.GetDirectoryName(_settingsPath);
                if (!Directory.Exists(dir)) Directory.CreateDirectory(dir);
                path = Path.Combine(dir, "env-install.log");
                File.WriteAllText(path, installLog ?? "(没有安装输出)", Encoding.UTF8);
            }
            catch { path = ""; }

            Ui(delegate
            {
                Log("环境", "==== 环境检查 / 安装失败：" + reason + " ====");
                if (!string.IsNullOrEmpty(path)) Log("环境", "完整安装日志：" + path);
                if (_logSource != null)
                {
                    _logSource.SelectedItem = "环境";
                    RenderLog();
                }
                SelectTab(3);   // 运行日志页：让日志留在屏幕上

                var sb = new StringBuilder();
                sb.AppendLine("环境检查 / 安装未通过，程序将退出。");
                sb.AppendLine();
                sb.AppendLine("原因：" + reason);
                var tail = Tail(installLog, 20);
                if (!string.IsNullOrEmpty(tail))
                {
                    sb.AppendLine();
                    sb.AppendLine("---- 安装日志末尾 ----");
                    sb.AppendLine(tail);
                }
                if (!string.IsNullOrEmpty(path))
                {
                    sb.AppendLine();
                    sb.AppendLine("完整日志：" + path);
                }
                sb.AppendLine();
                sb.AppendLine("也可以手动运行 launcher\\install_env.bat 排查。");
                MessageBox.Show(this, sb.ToString(), AppTitle + " - 环境错误",
                    MessageBoxButtons.OK, MessageBoxIcon.Error);
                Application.Exit();
            });
        }

        private static string ExitReason(int code)
        {
            switch (code)
            {
                case 1: return "项目根目录不对，找不到 backend_fastapi\\requirements.txt";
                case 2: return "本机没有可用的 Python 3，请先安装 3.9 及以上版本并勾选 Add to PATH";
                case 3: return "创建虚拟环境 backend_fastapi\\.venv 失败，或里面没有 pip";
                case 4: return "pip 安装依赖失败（默认源与清华镜像都失败，多为断网 / 代理 / 杀软拦截）";
                case 5: return "依赖装完仍无法导入";
                case -1: return "安装超时或无法启动安装进程";
                default: return "未知错误";
            }
        }

        private static bool VersionAtLeast(string v, int major, int minor)
        {
            try
            {
                var parts = v.Split('.');
                int ma = int.Parse(parts[0]);
                int mi = parts.Length > 1 ? int.Parse(parts[1]) : 0;
                return ma > major || (ma == major && mi >= minor);
            }
            catch { return true; }   // 解析不出版本就当够用，避免误报
        }

        /// <summary>取输出的头一行（去掉空行），用来在提示里说明缺了什么。</summary>
        private static string Brief(string text)
        {
            if (string.IsNullOrEmpty(text)) return "";
            foreach (var line in text.Replace("\r", "").Split('\n'))
            {
                var t = line.Trim();
                if (t.Length == 0) continue;
                if (t.Length > 160) t = t.Substring(0, 160) + "…";
                return "（" + t + "）";
            }
            return "";
        }

        private static string Tail(string text, int lines)
        {
            if (string.IsNullOrEmpty(text)) return "";
            var all = text.Replace("\r", "").Split('\n');
            int from = Math.Max(0, all.Length - lines);
            var sb = new StringBuilder();
            for (int i = from; i < all.Length; i++)
            {
                var t = all[i];
                if (t.Length > 200) t = t.Substring(0, 200) + "…";
                sb.AppendLine(t);
            }
            return sb.ToString();
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
            var th = new Thread(delegate()
            {
                try
                {
                    // 先把环境跑通：缺依赖就装一次，装不上直接退出（不做第二次尝试）
                    if (!PrepareEnv(false)) return;

                    if (!UiSync<bool>(delegate { return _ckAutoStart != null && _ckAutoStart.Checked; }))
                    {
                        Ui(delegate { RefreshStatus(false); });
                        return;
                    }

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

        private void StartOne(ServiceItem svc, bool warn, bool force = false)
        {
            try
            {
                if (!force && (svc.Alive || Probe(svc.HealthUrl, 1200)))
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
                // 后端可能由外部进程启动（本程序没持有句柄，Owned=false，Proc==null）：
                // 这种进程 svc.Alive 为 false，原来直接 return 会导致「停止/重启」无效
                // （重启时旧进程没被停掉，新进程又撞实例锁失败）。故从 /health 取 pid 结束它。
                if (KillByHealth(svc))
                {
                    Log("系统", svc.Name + " 已停止（结束外部启动的进程）");
                    RefreshStatus(false);
                    return;
                }
                svc.Owned = false;
                if (warn) Msg(svc.Name + " 未在运行");
                RefreshStatus(false);
                return;
            }
            svc.Stop(delegate(string line) { Log(svc.Name, line); });
            Log("系统", svc.Name + " 已停止");
            RefreshStatus(false);
        }

        /// <summary>结束「非本程序启动」的后端：从 /health 取 pid 后 Kill。返回是否真的结束了进程。</summary>
        private static bool KillByHealth(ServiceItem svc)
        {
            string body;
            if (!Probe(svc.HealthUrl, 1200, out body)) return false;
            var pidStr = JsonValue(body, "pid");
            int pid;
            if (int.TryParse(pidStr, out pid) && pid > 0)
            {
                try
                {
                    Process.GetProcessById(pid).Kill();
                    return true;
                }
                catch { return false; }
            }
            return false;
        }

        private static void WaitStop(ServiceItem svc, int seconds)
        {
            // 轮询直到后端端口不再响应（旧进程真正退出、端口释放），避免新进程因端口占用/Probe 误判而启动失败
            for (int i = 0; i < seconds * 4; i++)
            {
                if (!Probe(svc.HealthUrl, 800)) return;
                Thread.Sleep(250);
            }
        }

        private void Restart(ServiceItem svc)
        {
            var th = new Thread(delegate()
            {
                Ui(delegate { StopOne(svc, false); });
                WaitStop(svc, 20);                            // 等旧进程真正退出、端口释放
                Ui(delegate { StartOne(svc, false, true); }); // 强制启动，跳过"已在运行"误判
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
                var detail = JsonValue(body, "backend_detail");
                SetDot(_lbBackendDot, Color.FromArgb(46, 204, 113), "后端 :8000 · 正常");
                if (_lbBackendState != null) _lbBackendState.Text = "状态：运行中" + (_backend.Owned ? "（本程序启动）" : "（其他进程启动）");
                if (_lbApiVer != null) _lbApiVer.Text = "接口版本 v" + (ver ?? "-");
                if (_lbBackendDetail != null) _lbBackendDetail.Text = "后端 " + (detail ?? "-");
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

        private static bool PostJson(string url, string json, int timeoutMs, out string body)
        {
            body = "";
            HttpWebResponse resp = null;
            try
            {
                var req = (HttpWebRequest)WebRequest.Create(url);
                req.Method = "POST";
                req.ContentType = "application/json; charset=utf-8";
                req.Timeout = timeoutMs;
                req.ReadWriteTimeout = timeoutMs;
                req.KeepAlive = false;
                var bytes = Encoding.UTF8.GetBytes(json);
                req.ContentLength = bytes.Length;
                using (var s = req.GetRequestStream())
                    s.Write(bytes, 0, bytes.Length);
                resp = (HttpWebResponse)req.GetResponse();
                using (var sr = new StreamReader(resp.GetResponseStream(), Encoding.UTF8))
                    body = sr.ReadToEnd();
                return (int)resp.StatusCode < 400;
            }
            catch (WebException ex)
            {
                try
                {
                    if (ex.Response != null)
                        using (var sr = new StreamReader(ex.Response.GetResponseStream(), Encoding.UTF8))
                            body = sr.ReadToEnd();
                }
                catch { }
                return false;
            }
            catch { return false; }
            finally
            {
                if (resp != null)
                {
                    try { resp.Close(); } catch { }
                }
            }
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

        /// <summary>在工作线程里同步跑一段 UI 代码并拿返回值（弹确认框、读控件值）。</summary>
        private T UiSync<T>(Func<T> f)
        {
            if (IsDisposed) return default(T);
            try
            {
                if (InvokeRequired) return (T)Invoke(f);
                return f();
            }
            catch { return default(T); }
        }
    }
}
