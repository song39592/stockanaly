using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Windows.Forms;
using System.Web.Script.Serialization;

namespace StockPool
{
    /// <summary>
    /// 指标索引（个股分析二级页）：列出 indicators 目录下全部指标文件并做自检。
    ///   · 指标名称：@indicator(name=...) 的中文名（无则用文件名）
    ///   · 索引名称：@indicator(id=...) 的纯字母 id（右下角输入框切换指标用的 key，
    ///               如 换手率=VOL、集中度=SCR）
    ///   · 指标文件名：该行对应的 .py 文件（指标落在 indicators/、筹码公式落在 chipformulas/）
    ///   · 指标检查：索引是否重复 + 输入输出数据是否正确（合成数据跑通、输出结构合法）
    /// 仅 ok 通过的指标才可经快捷键显示。数据来自后端 GET /api/indicators/diagnose。
    /// 页面另提供一个「打开指标目录」链接，打开**所选行文件所在**的目录——指标落在
    /// backend_fastapi/indicators/，筹码公式落在 backend_fastapi/chipformulas/。
    /// </summary>
    internal sealed partial class MainForm
    {
        private int _stockIndSubIndex = -1;
        private Label _indStatus;
        private DataGridView _indGrid;
        private Label _indOpenDir;                 // 「打开指标目录」链接
        private readonly List<string> _indRowDirs = new List<string>(); // 每行文件所在目录（打开目录用）
        private string _indSelDir;                  // 当前所选行文件所在目录（未选时用 _indDir）
        private readonly List<string> _indMsgs = new List<string>();   // 每行检查详情（tooltip 用）
        private readonly ToolTip _indTip = new ToolTip();
        private bool _indBusy;
        private bool _indLoaded;
        private int _indPass;                       // 通过数
        private int _indHelper;                     // 辅助模块数（无指标输出，不算通过也不算失败）
        private int _indFail;                       // 未通过数

        /// <summary>构建「指标索引」二级页内容面板（由 Stock 页挂到二级导航）。</summary>
        internal Panel BuildStockIndicatorIndexPage()
        {
            var p = new Panel();
            p.Dock = DockStyle.Fill;
            p.AutoScroll = true;

            var root = new FlowLayoutPanel();
            root.Dock = DockStyle.Top;
            root.AutoSize = true;
            root.AutoSizeMode = AutoSizeMode.GrowAndShrink;
            root.FlowDirection = FlowDirection.TopDown;
            root.WrapContents = false;
            root.Margin = new Padding(0, 0, 0, 10);
            root.Padding = new Padding(0);
            p.Controls.Add(root);

            var head = new FlowLayoutPanel();
            head.AutoSize = true;
            head.WrapContents = false;
            head.Margin = new Padding(0, 0, 0, 6);
            head.Controls.Add(Lbl("指标索引（索引名称＝右下角输入框切换指标用的纯字母 id，如 换手率=VOL、集中度=SCR）"));
            head.Controls.Add(MiniBtn("刷新", delegate { _indLoaded = false; IndLoad(); }, 92));
            _indStatus = Mute(Lbl("尚未加载"));
            head.Controls.Add(_indStatus);
            root.Controls.Add(head);

            _indGrid = IndGrid();
            _indGrid.Width = 860;
            root.Controls.Add(_indGrid);

            // 「打开指标目录」链接 + 目录路径：打开**所选行文件所在**目录
            // （指标在 indicators/、筹码公式在 chipformulas/），未选时回退到默认目录。
            _indOpenDir = new Label();
            _indOpenDir.AutoSize = true;
            _indOpenDir.Text = "📂 打开指标文件目录";
            _indOpenDir.ForeColor = Color.FromArgb(80, 140, 220);
            _indOpenDir.Font = new Font("Microsoft YaHei UI", 9f, FontStyle.Underline);
            _indOpenDir.Cursor = Cursors.Hand;
            _indOpenDir.Margin = new Padding(0, 8, 0, 2);
            _indOpenDir.Click += delegate
            {
                string dir = string.IsNullOrEmpty(_indSelDir) ? _indDir : _indSelDir;
                if (string.IsNullOrEmpty(dir) || !Directory.Exists(dir)) return;
                try { Process.Start(new ProcessStartInfo(dir) { UseShellExecute = true }); }
                catch (Exception ex) { IndSetStatus("打开目录失败：" + ex.Message, true); }
            };
            root.Controls.Add(_indOpenDir);

            _indDirLabel = Mute(Lbl(""));
            _indDirLabel.MaximumSize = new Size(740, 0);
            root.Controls.Add(_indDirLabel);

            return p;
        }

        private Label _indDirLabel;
        private string _indDir;

        /// <summary>与 StockGrid 同款只读外观的表格（4 列：名称 / 索引 / 文件名 / 检查）。</summary>
        private DataGridView IndGrid()
        {
            var g = new DataGridView();
            g.AllowUserToAddRows = false;
            g.AllowUserToDeleteRows = false;
            g.ReadOnly = true;
            g.SelectionMode = DataGridViewSelectionMode.FullRowSelect;
            g.MultiSelect = false;
            g.RowHeadersVisible = false;
            g.BorderStyle = BorderStyle.None;
            g.EnableHeadersVisualStyles = false;
            g.AutoSizeColumnsMode = DataGridViewAutoSizeColumnsMode.None;
            g.AllowUserToResizeColumns = false;
            g.AllowUserToResizeRows = false;
            g.ColumnHeadersHeightSizeMode = DataGridViewColumnHeadersHeightSizeMode.DisableResizing;
            g.RowTemplate.Height = 24;
            g.ScrollBars = ScrollBars.Vertical;
            g.TabStop = false;
            g.Tag = "grid";

            var cName = new DataGridViewTextBoxColumn
            {
                Name = "name",
                HeaderText = "指标名称",
                AutoSizeMode = DataGridViewAutoSizeColumnMode.AllCells,
            };
            cName.MinimumWidth = 200;
            var cId = new DataGridViewTextBoxColumn
            {
                Name = "id",
                HeaderText = "索引名称",
                AutoSizeMode = DataGridViewAutoSizeColumnMode.AllCells,
            };
            cId.MinimumWidth = 90;
            var cFile = new DataGridViewTextBoxColumn
            {
                Name = "file",
                HeaderText = "指标文件名",
                AutoSizeMode = DataGridViewAutoSizeColumnMode.AllCells,
            };
            cFile.MinimumWidth = 130;
            var cCheck = new DataGridViewTextBoxColumn
            {
                Name = "check",
                HeaderText = "指标检查",
                AutoSizeMode = DataGridViewAutoSizeColumnMode.Fill,
            };
            g.Columns.Add(cName);
            g.Columns.Add(cId);
            g.Columns.Add(cFile);
            g.Columns.Add(cCheck);

            g.CellMouseEnter += delegate(object s, DataGridViewCellEventArgs e)
            {
                if (e.RowIndex < 0 || e.ColumnIndex != 3) { _indTip.SetToolTip(g, ""); return; }
                string m = (e.RowIndex < _indMsgs.Count) ? _indMsgs[e.RowIndex] : "";
                _indTip.SetToolTip(g, string.IsNullOrEmpty(m) ? "通过：索引唯一且输入输出数据校验正常" : m);
            };
            g.SelectionChanged += delegate { UpdateIndDirFromSelection(); };
            return g;
        }

        /// <summary>切到该二级页时自动加载（已加载则不重复拉）。</summary>
        internal void StockIndOnEnter()
        {
            if (_indGrid == null) return;
            if (_indLoaded) return;
            IndLoad();
        }

        // ---------------- 数据加载 ----------------

        private void IndLoad()
        {
            if (_indBusy) return;
            _indBusy = true;
            IndSetStatus("加载中…", false);
            RunUi<Dictionary<string, object>>(
                delegate
                {
                    string body;
                    if (!GetText("http://127.0.0.1:8000/api/indicators/diagnose", 60000, out body)
                        || string.IsNullOrEmpty(body))
                        return null;
                    try
                    {
                        return new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(body);
                    }
                    catch { return null; }
                },
                delegate(Dictionary<string, object> j)
                {
                    _indBusy = false;
                    if (j == null) { IndSetStatus("读取失败：后端未响应", true); return; }
                    if (!J.IsOk(j)) { IndSetStatus("读取失败：" + J.ErrMsg(j), true); return; }
                    _indDir = J.Str(J.Get(j, "dir"));
                    if (!string.IsNullOrEmpty(_indDir))
                    {
                        _indDirLabel.Text = _indDir;
                        _indOpenDir.Visible = Directory.Exists(_indDir);
                    }
                    var arr = J.Arr(J.Get(j, "items"));
                    IndFill(arr);
                    UpdateIndDirFromSelection();
                    int ndup = J.Arr(J.Get(j, "dup_ids")) == null ? 0 : J.Arr(J.Get(j, "dup_ids")).Count;
                    var sp = new List<string>();
                    sp.Add(string.Format("共 {0} 项", _indMsgs.Count));
                    sp.Add("通过 " + _indPass);
                    if (_indHelper > 0) sp.Add("辅助 " + _indHelper);
                    if (_indFail > 0) sp.Add("失败 " + _indFail);
                    sp.Add("重复索引 " + ndup);
                    IndSetStatus(string.Join(" · ", sp), _indFail > 0);
                    _indLoaded = true;
                },
                delegate(Exception ex)
                {
                    _indBusy = false;
                    IndSetStatus("读取失败：" + ex.Message, true);
                },
                null);
        }

        private void IndFill(ArrayList items)
        {
            _indMsgs.Clear();
            _indRowDirs.Clear();
            var data = new List<string[]>();
            var helperFlags = new List<bool>();
            _indPass = _indHelper = _indFail = 0;
            if (items != null)
            {
                foreach (Dictionary<string, object> d in items)
                {
                    string name = J.Str(J.Get(d, "name"));
                    if (name.Length == 0) name = J.Str(J.Get(d, "file"));   // 退化：用文件名
                    string id = J.Str(J.Get(d, "id"));
                    if (id.Length == 0) id = "—";
                    string file = J.Str(J.Get(d, "file"));
                    string rawPath = J.Str(J.Get(d, "path"));
                    string dir = rawPath.Length > 0 ? Path.GetDirectoryName(rawPath) : _indDir;
                    if (string.IsNullOrEmpty(dir)) dir = _indDir;   // 路径缺失时回退到默认目录
                    bool ok = J.Get(d, "ok") is bool && (bool)J.Get(d, "ok");
                    bool helper = J.Get(d, "helper") is bool && (bool)J.Get(d, "helper");
                    string note = J.Str(J.Get(d, "note"));
                    var msgs = J.Arr(J.Get(d, "messages"));
                    string detail = "";
                    if (msgs != null && msgs.Count > 0)
                    {
                        var parts = new List<string>();
                        foreach (var m in msgs) parts.Add(m == null ? "" : m.ToString());
                        detail = string.Join("；", parts);
                    }
                    string check;
                    if (helper)
                    {
                        // 辅助模块（仅提供中间变量、无指标输出）：中性标注，不算失败
                        check = "— 辅助模块（无指标输出，不参与检查）";
                        if (detail.Length == 0)
                            detail = "辅助模块：仅提供中间变量，不注册为指标，无需检查";
                        _indHelper++;
                    }
                    else if (ok)
                    {
                        // ok 但未真正校验数据（本机没下载行情）时如实标注，避免误以为跑通
                        check = (note == "OK") ? "✅ 通过" : ("✅ " + note);
                        _indPass++;
                    }
                    else
                    {
                        check = "❌ 未通过";
                        _indFail++;
                    }
                    _indMsgs.Add(detail);
                    helperFlags.Add(helper);
                    _indRowDirs.Add(dir);
                    data.Add(new[] { name, id, file, check });
                }
            }
            _indGrid.Rows.Clear();
            for (int i = 0; i < data.Count; i++)
            {
                _indGrid.Rows.Add(data[i]);
                // 辅助模块弱化显示（灰字），与真正的指标行区分开
                if (helperFlags[i]) _indGrid.Rows[i].DefaultCellStyle.ForeColor = C.Flat;
            }
            // 高度：按内容撑开，超过上限才出竖向滚动条（与 StockGrid.Fit 一致）
            int rh = _indGrid.RowTemplate.Height > 0 ? _indGrid.RowTemplate.Height : 24;
            int want = _indGrid.ColumnHeadersHeight + 8 + _indGrid.Rows.Count * rh;
            _indGrid.Height = Math.Min(Math.Max(want, 120), 620);
        }

        /// <summary>把「打开目录」目标切到当前所选行文件所在目录（未选时回退到 _indDir）。</summary>
        private void UpdateIndDirFromSelection()
        {
            string dir = _indDir;
            if (_indGrid != null && _indGrid.SelectedRows.Count > 0)
            {
                int i = _indGrid.SelectedRows[0].Index;
                if (i >= 0 && i < _indRowDirs.Count && !string.IsNullOrEmpty(_indRowDirs[i]))
                    dir = _indRowDirs[i];
            }
            _indSelDir = dir;
            if (_indDirLabel != null) _indDirLabel.Text = dir ?? "";
            if (_indOpenDir != null) _indOpenDir.Visible = !string.IsNullOrEmpty(dir) && Directory.Exists(dir);
        }

        private void IndSetStatus(string text, bool failed)
        {
            if (_indStatus == null) return;
            _indStatus.Text = text;
            _indStatus.Tag = "muted";
            _indStatus.ForeColor = failed ? C.Warn : C.Flat;
        }
    }
}
