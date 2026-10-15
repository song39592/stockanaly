using System;
using System.Collections.Generic;
using System.Drawing;
using System.Text.RegularExpressions;
using System.Windows.Forms;

namespace StockPool
{
    /// <summary>
    /// 右下角隐藏输入框（键盘输入即唤出）：全局统一的「代码 / 指标」入口。
    /// 敲数字 → 打开个股；敲字母 → 输指标英文 id（macd / rsi / rps …）切换副图。
    /// 附带输入联想下拉（主窗体内不可聚焦 ListBox，不抢焦点、不闪烁）。
    ///
    /// 原先这份逻辑散落在 StockPoolLauncher.cs（唤起 + 构建）与 StockPage.cs（联想弹层），
    /// 现收敛到本文件，与主窗口其余逻辑解耦；顶部「代码/指标」框已移除，只保留此入口。
    /// </summary>
    internal sealed partial class MainForm
    {
        // 右下角输入框
        private TextBox _quickSearch;          // 隐藏搜索 / 指标输入框
        private Control _quickPrevFocus;       // 唤出前拥有焦点的控件，取消时还原
        // 当前激活的代码 / 指标名：与 _quickSearch.Text 解耦，专门用于异步竞态守卫。
        // 这样回车 / 选中后即可安全清空输入框文本，而不影响后台名称加载的守卫判断。
        private string _quickActiveCode = "";

        // 输入联想下拉（主窗体内的子控件，不抢焦点）
        private SuggestionList _stockSugList;
        private readonly List<IndOpt> _stockSugMatches = new List<IndOpt>();   // 当前联想命中项（与列表行一一对应）

        /// <summary>构建右下角输入框与联想下拉（在 MainForm 构造函数里调用）。</summary>
        private void BuildQuickSearch()
        {
            _quickSearch = new TextBox();
            _quickSearch.Width = 160;
            _quickSearch.MaxLength = 24;                 // 既装 6 位代码，也装指标英文 id（macd / chip_scr）
            // 禁用输入法：保证字母直达（否则中文输入法会把字母拿去组词，指标英文代码输不进来）。
            _quickSearch.ImeMode = ImeMode.Disable;
            _quickSearch.Visible = false;
            _quickSearch.Anchor = AnchorStyles.Bottom | AnchorStyles.Right;
            _quickSearch.Location = new Point(ClientSize.Width - _quickSearch.Width - 18,
                                              ClientSize.Height - _quickSearch.Height - 18);
            _quickSearch.KeyDown += delegate(object s, KeyEventArgs e)
            {
                if (e.KeyCode == Keys.Escape) { e.Handled = true; HideQuickSearch(); return; }
                if (StockSugVisible())                       // 联想下拉展开时，方向键 / 回车先归它管
                {
                    if (e.KeyCode == Keys.Down)  { StockSugMove(1);  e.Handled = true; return; }
                    if (e.KeyCode == Keys.Up)    { StockSugMove(-1); e.Handled = true; return; }
                    if (e.KeyCode == Keys.Enter)
                    {
                        e.Handled = true; e.SuppressKeyPress = true;
                        if (_stockSugList.SelectedIndex >= 0) StockSugPick();
                        else StockSugHide();
                        return;
                    }
                    if (e.KeyCode == Keys.Tab) { StockSugHide(); return; }
                }
                if (e.KeyCode == Keys.Enter)
                {
                    e.Handled = true;
                    string code = _quickSearch.Text.Trim();
                    HideQuickSearch();                       // 先收起（会清空文本，故先取 code）
                    if (code.Length == 0) return;
                    SelectTab(_stockTabIndex);
                    _quickSearch.Text = code;                // 交给 StockOpen：6 位数字=个股；否则按指标名/id 切换副图
                    StockOpen();
                    _stockStrip.Select(0);
                }
            };
            _quickSearch.TextChanged += delegate { StockSugUpdate(); };
            // 焦点一离开就整框收起（不还原焦点，尊重用户刚点去的位置）。
            // 否则框会「挂着但没焦点」：后续按键既进不了框、也唤不出框，表现为无法输入。
            // 联想下拉是不可聚焦子控件，点击它不会触发 Leave，不影响点选。
            _quickSearch.Leave += delegate { HideQuickSearch(false); };
            Controls.Add(_quickSearch);
            _quickSearch.BringToFront();

            // 联想下拉（不可聚焦，点击不抢输入框焦点）
            _stockSugList = new SuggestionList();
            _stockSugList.Font = new Font("Microsoft YaHei UI", 9.5f);
            _stockSugList.BorderStyle = BorderStyle.FixedSingle;
            _stockSugList.Margin = new Padding(0);
            _stockSugList.BackColor = Color.FromArgb(40, 44, 52);
            _stockSugList.ForeColor = Color.FromArgb(220, 224, 230);
            _stockSugList.Visible = false;
            _stockSugList.Click += delegate { StockSugPick(); };
            Controls.Add(_stockSugList);                     // 挂到主窗体，绝对定位，显示时 BringToFront
        }

        /// <summary>由 ProcessCmdKey 调用：焦点不在文本类控件、且本框未显示时，数字/字母键可唤出本框。</summary>
        private bool QuickSearchSummonOnKey(Keys key, Keys keyData)
        {
            if (_quickSearch == null) return false;
            if (ActiveControl is TextBox || ActiveControl is ComboBox || ActiveControl is RichTextBox) return false;
            if (_quickSearch.Visible && ActiveControl == _quickSearch) return false;   // 正在本框输入，按键照常上屏
            Keys k = key;
            bool digit = ((k >= Keys.D0 && k <= Keys.D9) || (k >= Keys.NumPad0 && k <= Keys.NumPad9));
            bool letter = (k >= Keys.A && k <= Keys.Z);
            if ((digit || letter) && (keyData & (Keys.Control | Keys.Alt)) == 0)
            {
                char ch;
                if (k >= Keys.D0 && k <= Keys.D9) ch = (char)('0' + ((int)k - (int)Keys.D0));
                else if (k >= Keys.NumPad0 && k <= Keys.NumPad9) ch = (char)('0' + ((int)k - (int)Keys.NumPad0));
                else ch = (char)('a' + ((int)k - (int)Keys.A));   // 字母 → 小写，匹配指标 id（macd/rsi…）
                if (_quickSearch.Visible) _quickSearch.Focus();   // 兜底：框还挂着但焦点跑了 → 拉回来继续输
                else ShowQuickSearch();
                _quickSearch.AppendText(ch.ToString());
                return true;
            }
            return false;
        }

        private void ShowQuickSearch()
        {
            if (_quickSearch == null) return;
            _quickPrevFocus = ActiveControl;
            _quickSearch.Text = "";                          // 每次唤出都是新输入
            _quickSearch.Visible = true;
            _quickSearch.BringToFront();
            _quickSearch.Focus();
        }

        private void HideQuickSearch()
        {
            HideQuickSearch(true);
        }

        /// <summary>收起输入框。restoreFocus=true 时把焦点还给唤出前的控件（回车/ESC 用）；false 用于 Leave。</summary>
        private void HideQuickSearch(bool restoreFocus)
        {
            if (_quickSearch == null || !_quickSearch.Visible) return;
            _quickSearch.Visible = false;
            _quickSearch.Text = "";
            Control prev = _quickPrevFocus;
            _quickPrevFocus = null;
            if (restoreFocus && prev != null && !prev.IsDisposed && prev.Visible) prev.Focus();
        }

        // ===== 输入联想下拉 =====
        private bool StockSugVisible()
        {
            return _stockSugList != null && _stockSugList.Visible;
        }

        private void StockSugHide()
        {
            if (_stockSugList != null) _stockSugList.Visible = false;
        }

        /// <summary>随输入内容筛选主图 / 副图指标名，刷新下拉；无匹配则隐藏。仅在本框可见时生效。</summary>
        private void StockSugUpdate()
        {
            if (_stockSugList == null) return;
            if (_quickSearch == null || !_quickSearch.Visible) { StockSugHide(); return; }
            string t = _quickSearch.Text.Trim();
            _stockSugMatches.Clear();
            if ((_stockSubOpts.Count == 0 && _stockMainOpts.Count == 0) || t.Length == 0)
            {
                StockSugHide();
                return;
            }
            foreach (var o in _stockSubOpts)
                if (o.Name.IndexOf(t, StringComparison.OrdinalIgnoreCase) >= 0
                 || o.Id.IndexOf(t, StringComparison.OrdinalIgnoreCase) >= 0) _stockSugMatches.Add(o);
            foreach (var o in _stockMainOpts)
                if (o.Name.IndexOf(t, StringComparison.OrdinalIgnoreCase) >= 0
                 || o.Id.IndexOf(t, StringComparison.OrdinalIgnoreCase) >= 0) _stockSugMatches.Add(o);
            if (_stockSugMatches.Count == 0) { StockSugHide(); return; }

            _stockSugList.Items.Clear();
            foreach (var o in _stockSugMatches)
            {
                bool isMain = false;
                foreach (var m in _stockMainOpts) if (m.Id == o.Id) { isMain = true; break; }
                _stockSugList.Items.Add(isMain ? o.Name + "（主图）" : o.Name);
            }
            _stockSugList.SelectedIndex = 0;
            // 框在右下角：下拉显示在输入框「上方」，避免超出屏幕底边。
            int h = Math.Min(_stockSugList.ItemHeight * _stockSugMatches.Count + 4, 220);
            Point sp = _quickSearch.PointToScreen(new Point(0, -h));
            _stockSugList.Location = this.PointToClient(sp);
            _stockSugList.Width = Math.Max(_quickSearch.Width, 200);
            _stockSugList.Height = h;
            _stockSugList.BringToFront();
            _stockSugList.Visible = true;
        }

        private void StockSugMove(int d)
        {
            if (_stockSugList == null || _stockSugList.Items.Count == 0) return;
            int i = _stockSugList.SelectedIndex + d;
            if (i < 0) i = 0;
            if (i >= _stockSugList.Items.Count) i = _stockSugList.Items.Count - 1;
            _stockSugList.SelectedIndex = i;
        }

        /// <summary>从联想下拉选中一项 → 写入输入框并切换副图。</summary>
        private void StockSugPick()
        {
            if (_stockSugList == null || _stockSugList.SelectedIndex < 0) return;
            if (_stockSugList.SelectedIndex >= _stockSugMatches.Count) return;
            IndOpt m = _stockSugMatches[_stockSugList.SelectedIndex];
            _quickSearch.Text = m.Name;
            StockSugHide();
            StockOpen();                                // StockOpen 内按指标名解析并替换副图
            HideQuickSearch();                          // 选完即收起：否则输入框仍可见且带旧文字，下次输入会叠加
        }

        /// <summary>不可聚焦的 ListBox：鼠标点击它也不会把焦点从输入框挪走，故不抢焦点、不闪烁。</summary>
        private sealed class SuggestionList : ListBox
        {
            public SuggestionList() { SetStyle(ControlStyles.Selectable, false); TabStop = false; IntegralHeight = false; }
            protected override bool ShowFocusCues { get { return false; } }
        }
    }
}
