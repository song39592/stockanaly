using System;
using System.Collections.Generic;
using System.Drawing;
using System.Globalization;
using System.Text.RegularExpressions;
using System.Windows.Forms;

namespace StockPool
{
    /// <summary>
    /// 个股分析（原生内嵌标签页）：MainForm 的拆分文件 · **通用表格**。
    ///
    /// 背景：表格在多个页面重复出现，「只读 / 禁排序 / 禁选中高亮 / 自适应列宽列高 /
    /// 点击代码跳个股页」这套组合被复制了五遍（MktGrid / StGrid / ScrGrid 及两处内联）。
    /// 这里收敛成一个自定义控件 `StockGrid`：样式与行为**内聚在控件里**，各页只声明
    /// 「有哪些列 + 每行是什么值」，不再各自抄一遍外观配置。
    ///
    /// 设计要点：
    ///   1. 自定义控件而非静态工厂：外观/行为属于控件自身，调用方拿到的就是「已经配好的表」；
    ///      同时仍然是一个 DataGridView，换肤的 `c is DataGridView` 分支照旧命中。
    ///   2. **列必须有稳定标识**（`GridColumn.Name`）：此前有的表用中文表头当列标识、
    ///      有的叫 c0..cn、有的压根没设 Name，导致 `Cells["代码"]` 这类写法换个表就炸。
    ///   3. 列宽**逐列控制**（AllCells / Fill / 固定宽），不由整表二选一：
    ///      Fill 会把「名称」这类短列撑得很宽，AllCells 更适合窄列，两者各有场景。
    ///   4. 「点击代码跳详情页」由控件内置：声明哪一列是代码列即可，无需各页重复挂事件。
    ///      跳到个股页统一走 `OpenStock(code)`（原先各处是「写 _stockCode + SelectTab」两步式）。
    ///   5. 现价沿用各表**自己数据里**的价格（策略取 recommend 的 price、周榜取 close …），
    ///      这一层不代为取数，保持「表格只负责显示」。
    ///   6. **排序自己实现**（第 20 项）：点表头降序 → 再点升序 → 第三次取消。
    ///      不用 `SortMode.Automatic` —— `SetRows` 是手工 `Rows.Add`，没有数据源绑定，
    ///      Automatic 点了不会有任何反应（盘面 3 张表与个股时间线此前就一直是坏的）。
    ///      两个必须注意的点：
    ///      · **数值列按数值比**：单元格存的是格式化后的字符串（"8.37" / "10.35"），
    ///        按字符串比会得出 "10.35" &lt; "8.37" 的错误结果。右对齐列即数值列。
    ///      · **缺值恒排最后**：显示「—」的行在升序降序下都沉底，不去中间插队。
    ///      序号列用 `GridColumn.Ordinal` 标记，排序后自动重排 —— 否则会出现
    ///      「#3」出现在第 5 行这种看起来像坏了的情况。
    /// </summary>
    internal sealed partial class MainForm
    {
        /// <summary>表格的列声明。</summary>
        private sealed class GridColumn
        {
            /// <summary>稳定标识；为空则取 <see cref="Header"/>（兼容既有的 Cells["代码"] 写法）。</summary>
            public string Name;
            public string Header;
            /// <summary>列宽方式：AllCells（按内容+表头自适应）/ Fill（撑满剩余）/ None（固定 <see cref="Width"/>）。</summary>
            public DataGridViewAutoSizeColumnMode Size = DataGridViewAutoSizeColumnMode.AllCells;
            public int Width = 60;                  // 仅 Size = None 时生效
            /// <summary>列宽下限（0 = 不设）。用于「名称」这类自适应会被压得过窄的列：
            /// 名称可能带 `*ST` / `XD` / `XR` / `DR` / `N` 前缀，且空值会显示「—」，
            /// 只按内容自适应在极端情况下会窄到看不全。</summary>
            public int MinWidth;
            public bool Right;                      // 数字列右对齐
            public bool IsCode;                     // 该列的值是股票代码（跳转时读这一列）
            public bool Jump;                       // 点击该列可跳转个股页（代码列 / 名称列）
            /// <summary>该列是「序号」列（值为 1..N）：排序后**自动重排**，
            /// 否则排完序序号会乱（如第 5 行显示「#3」），看起来像坏了。
            /// 刻意不自动推断第 0 列 —— 有些表第 0 列是代码而不是序号。</summary>
            public bool Ordinal;

            public GridColumn(string header) : this(header, header, false) { }

            public GridColumn(string header, string name) : this(header, name, false) { }

            public GridColumn(string header, string name, bool right)
            {
                Header = header;
                Name = string.IsNullOrEmpty(name) ? header : name;
                Right = right;
            }
        }

        /// <summary>
        /// 统一外观与行为的只读表格：只读 / 禁增删 / 默认禁排序 / 禁选中高亮 /
        /// 行高 24 / 无行头无边框 / 逐列自适应 / 高度按内容撑开（超出上限才出滚动条）。
        /// </summary>
        private sealed class StockGrid : DataGridView
        {
            public MainForm Owner;
            private int _codeCol = -1;
            private int _ordinalCol = -1;               // 序号列（排序后重排）
            private readonly List<int> _jumpCols = new List<int>();
            private readonly List<string> _origHeader = new List<string>();
            // ---- 排序（第 20 项）----
            private readonly List<string[]> _src = new List<string[]>();  // 原始行（未排序）
            private bool _sortable;
            private int _sortCol = -1;
            private bool _sortAsc = true;
            /// <summary>排序记在**列名**上而不是列索引（第 20 项加）。
            /// 原因：盘面页每次进入都 `MktLoadAll()` 重建表格，若某些页还会重设列，
            /// 索引就会指到**别的列**上去（排的是A 列、箭头却画在 B 列）。
            /// 列名稳定，`SetColumns` 之后按名字重新解析即可。</summary>
            private string _sortByName;

            public StockGrid()
            {
                AllowUserToAddRows = false;
                AllowUserToDeleteRows = false;
                ReadOnly = true;
                SelectionMode = DataGridViewSelectionMode.FullRowSelect;
                MultiSelect = false;
                RowHeadersVisible = false;
                BorderStyle = BorderStyle.None;
                EnableHeadersVisualStyles = false;
                ColumnHeadersHeightSizeMode = DataGridViewColumnHeadersHeightSizeMode.DisableResizing;
                RowTemplate.Height = 24;
                // 整表 None：由**每列**自己的 AutoSizeMode 决定宽度（见类注释第 3 点）
                AutoSizeColumnsMode = DataGridViewAutoSizeColumnsMode.None;
                AllowUserToResizeColumns = false;
                AllowUserToResizeRows = false;
                ScrollBars = ScrollBars.None;
                TabStop = false;
                Dock = DockStyle.Top;
                Tag = "grid";
                // 背景色**不要设 Transparent**：DataGridView 不支持透明背景，运行时会抛
                // ArgumentException（且建表发生在窗体构造期，直接就是「打不开」）。
                // 底色统一交给换肤（Skin 里 `c is DataGridView` 分支会设 BackgroundColor）。
                // 选中即清空实现「不可选中高亮」；必须带判断，否则 ClearSelection 会再触发
                // 一次 SelectionChanged，存在递归风险。
                SelectionChanged += delegate
                {
                    if (SelectedRows.Count > 0 || SelectedCells.Count > 0) ClearSelection();
                };
                CellClick += OnCellClick;
                CellMouseMove += OnCellMouseMove;
                ColumnHeaderMouseClick += OnHeaderClick;   // 排序（第 20 项）
            }

            /// <summary>设置列（会清空现有列）。</summary>
            public void SetColumns(IList<GridColumn> cols)
            {
                Columns.Clear();
                _codeCol = -1;
                _ordinalCol = -1;
                _jumpCols.Clear();
                _origHeader.Clear();
                for (int i = 0; i < cols.Count; i++)
                {
                    GridColumn c = cols[i];
                    var col = new DataGridViewTextBoxColumn();
                    col.Name = c.Name;
                    col.HeaderText = c.Header;
                    col.AutoSizeMode = c.Size;
                    col.SortMode = DataGridViewColumnSortMode.NotSortable;
                    col.DefaultCellStyle.Alignment = c.Right
                        ? DataGridViewContentAlignment.MiddleRight
                        : DataGridViewContentAlignment.MiddleLeft;
                    if (c.Size == DataGridViewAutoSizeColumnMode.None) col.Width = c.Width;
                    if (c.MinWidth > 0) col.MinimumWidth = c.MinWidth;
                    Columns.Add(col);
                    _origHeader.Add(c.Header);      // 排序加 ▲▼ 后要能还原，故先存原名
                    if (c.IsCode) _codeCol = i;
                    if (c.Jump) _jumpCols.Add(i);
                    if (c.Ordinal) _ordinalCol = i;
                }
            }

            /// <summary>
            /// 整表可排序与否（默认否）。开启后**点表头即排序**，同一列再点切换升降序。
            ///
            /// ⚠️ 这里**不能**用 `SortMode.Automatic` —— 本控件的 `SetRows` 是逐行
            /// `Rows.Add(string[])` 手工填充的，**没有 BindingSource**，
            /// DataGridView 找不到底层数据源，点击表头什么都不会发生
            /// （盘面涨停/炸板/跌停与个股时间线四张表此前就一直是这个状态：
            /// 代码调了 SetSortable(true)，但点了没反应）。故自己排。
            /// </summary>
            public void SetSortable(bool sortable)
            {
                _sortable = sortable;
                if (!sortable)
                {
                    _sortCol = -1;
                    _sortByName = null;
                    for (int i = 0; i < Columns.Count; i++)
                    {
                        Columns[i].SortMode = DataGridViewColumnSortMode.NotSortable;
                        if (i < _origHeader.Count) Columns[i].HeaderText = _origHeader[i];
                    }
                }
            }

            /// <summary>清空并按二维数组重填（值按列序）；同时留存原始行供排序使用。</summary>
            public void SetRows(IList<string[]> rows)
            {
                _src.Clear();
                if (rows != null)
                    for (int i = 0; i < rows.Count; i++) _src.Add(rows[i]);
                ResolveSortCol();
                ShowSorted();
            }

            /// <summary>按列名重新解析排序列（列被重建后索引会失效）。</summary>
            private void ResolveSortCol()
            {
                _sortCol = -1;
                if (string.IsNullOrEmpty(_sortByName)) return;
                for (int i = 0; i < Columns.Count; i++)
                    if (Columns[i].Name == _sortByName) { _sortCol = i; break; }
                if (_sortCol < 0) _sortByName = null;      // 那列已经没了，放弃排序
            }

            private void ShowSorted()
            {
                Rows.Clear();
                List<string[]> view = _src;
                if (_sortCol >= 0 && _sortCol < Columns.Count)
                {
                    int col = _sortCol;
                    bool numeric = Columns[col].DefaultCellStyle.Alignment
                                   == DataGridViewContentAlignment.MiddleRight;
                    var order = new int[_src.Count];
                    for (int i = 0; i < order.Length; i++) order[i] = i;
                    // 稳定排序：相等时按原下标，升降序切换也不会让同值行乱跳
                    Array.Sort(order, delegate(int a, int b)
                    {
                        // ⚠️ 缺值必须在**取反之前**判定：CompareCell 里「缺值返回 1
                        // （排后）」本身是对的，但下面降序要对结果取反，一取反缺值
                        // 就被翻到**最前面**了 —— 实测「—」会跑到榜首。
                        // 语义要求：缺值恒沉底，不随升降序变化。
                        if (numeric)
                        {
                            bool ma = !NumericOf(CellOf(_src[a], col)).HasValue;
                            bool mb = !NumericOf(CellOf(_src[b], col)).HasValue;
                            if (ma || mb)
                            {
                                if (ma && mb) return a.CompareTo(b);
                                return ma ? 1 : -1;
                            }
                        }
                        int c = CompareCell(a, b, col, numeric);
                        if (c != 0) return _sortAsc ? c : -c;
                        return a.CompareTo(b);
                    });
                    view = new List<string[]>(_src.Count);
                    foreach (int i in order) view.Add(_src[i]);
                }
                for (int i = 0; i < view.Count; i++)
                {
                    // 序号列重排：排序后 # 必须跟着走，否则会出现「#3」出现在第 5 行
                    if (_ordinalCol >= 0 && view[i].Length > _ordinalCol)
                    {
                        string[] copy = (string[])view[i].Clone();
                        copy[_ordinalCol] = (i + 1).ToString(CultureInfo.InvariantCulture);
                        view[i] = copy;
                    }
                    Rows.Add(view[i]);
                }
            }

            /// <summary>
            /// 比较两行在某列的大小。<paramref name="numeric"/> 为真时按**数值**比，
            /// 否则按字符串。
            ///
            /// ⚠️ 数值列必须走数值比较：本控件的单元格存的是**已格式化的字符串**
            /// （如 "8.37" / "10.35"），按字符串比会得出 "10.35" &lt; "8.37" 的错误结果。
            /// 右对齐（`GridColumn.Right`）即代表数值列。
            /// </summary>
            private int CompareCell(int a, int b, int col, bool numeric)
            {
                string sa = CellOf(_src[a], col);
                string sb = CellOf(_src[b], col);
                if (numeric)
                {
                    double? va = NumericOf(sa);
                    double? vb = NumericOf(sb);
                    if (!va.HasValue && !vb.HasValue) return 0;
                    if (!va.HasValue) return 1;
                    if (!vb.HasValue) return -1;
                    return va.Value.CompareTo(vb.Value);
                }
                return string.Compare(sa ?? "", sb ?? "", StringComparison.Ordinal);
            }

            private static string CellOf(string[] row, int col)
            {
                return (row != null && col >= 0 && col < row.Length) ? row[col] : null;
            }

            /// <summary>
            /// 把单元格显示值解析成数值；解析不出来返回 null（视作缺值）。
            /// 容忍百分号与千分位逗号 —— 周榜的「涨幅%」列是 "12.34%"。
            /// </summary>
            private static double? NumericOf(string text)
            {
                if (string.IsNullOrEmpty(text)) return null;
                string s = text.Trim().TrimEnd('%', '‰').Replace(",", "").Replace(" ", "");
                if (s.Length == 0 || s == "—") return null;
                double v;
                return double.TryParse(s, NumberStyles.Float, CultureInfo.InvariantCulture, out v)
                    ? v : (double?)null;
            }

            private void OnHeaderClick(object sender, DataGridViewCellMouseEventArgs e)
            {
                if (!_sortable || e.ColumnIndex < 0) return;
                if (e.Button != MouseButtons.Left) return;
                int col = e.ColumnIndex;
                // 三态循环：降序 → 升序 → 取消排序（回到原始顺序）
                if (_sortCol != col) { _sortCol = col; _sortAsc = false; }
                else if (_sortAsc) { _sortAsc = false; }
                else { _sortCol = -1; _sortAsc = true; }
                _sortByName = _sortCol >= 0 ? Columns[_sortCol].Name : null;
                PaintHeaders();
                ShowSorted();
            }

            /// <summary>表头重绘：排序列加 ▲ / ▼，其余还原。</summary>
            private void PaintHeaders()
            {
                for (int i = 0; i < Columns.Count && i < _origHeader.Count; i++)
                    Columns[i].HeaderText = _origHeader[i];
                if (_sortCol >= 0 && _sortCol < _origHeader.Count)
                    Columns[_sortCol].HeaderText =
                        _origHeader[_sortCol] + (_sortAsc ? " ▲" : " ▼");
            }

            /// <summary>按内容撑高度；超过 maxH 才启用竖向滚动条。**填完行后再调**。</summary>
            public void Fit(int minH = 120, int maxH = 620)
            {
                int rh = RowTemplate.Height > 0 ? RowTemplate.Height : 24;
                int want = ColumnHeadersHeight + 8 + Rows.Count * rh;
                if (want < minH) want = minH;
                if (want > maxH)
                {
                    want = maxH;
                    ScrollBars = ScrollBars.Vertical;
                }
                else
                {
                    ScrollBars = ScrollBars.None;
                }
                Height = want;
                // 显式重新量一次：逐行 Add 的中间态会把列算窄（如 70.06 被截成 70…）
                for (int i = 0; i < Columns.Count; i++)
                {
                    if (Columns[i].AutoSizeMode == DataGridViewAutoSizeColumnMode.None) continue;
                    if (Columns[i].AutoSizeMode == DataGridViewAutoSizeColumnMode.Fill) continue;
                    AutoResizeColumn(i, DataGridViewAutoSizeColumnMode.AllCells);
                }
            }

            private void OnCellClick(object sender, DataGridViewCellEventArgs e)
            {
                if (Owner == null || e.RowIndex < 0) return;
                if (!_jumpCols.Contains(e.ColumnIndex)) return;
                string code = CodeOf(e.RowIndex);
                if (!string.IsNullOrEmpty(code)) Owner.OpenStock(code);
            }

            private void OnCellMouseMove(object sender, DataGridViewCellMouseEventArgs e)
            {
                if (e.RowIndex < 0 || e.ColumnIndex < 0) return;
                Cursor = _jumpCols.Contains(e.ColumnIndex) &&
                         !string.IsNullOrEmpty(CodeOf(e.RowIndex)) ? Cursors.Hand : Cursors.Default;
            }

            /// <summary>取该行的代码（6 位数字才算有效，避免把名称列当代码跳走）。</summary>
            private string CodeOf(int rowIndex)
            {
                if (_codeCol < 0 || rowIndex >= Rows.Count) return null;
                object v = Rows[rowIndex].Cells[_codeCol].Value;
                string text = v == null ? "" : Convert.ToString(v).Trim();
                return Regex.IsMatch(text, "^[0-9]{6}$") ? text : null;
            }
        }

        /// <summary>
        /// 建一张统一外观的表格并声明列。**统一走这里**才能保证 Owner 被赋值（否则点击不跳转）。
        /// </summary>
        private StockGrid NewGrid(IList<GridColumn> cols)
        {
            var g = new StockGrid();
            g.Owner = this;
            g.SetColumns(cols);
            return g;
        }

        /// <summary>
        /// 股票「名称」列：点击可跳转，并给一个宽度下限 —— 名称会带 `*ST` / `XD` / `XR` /
        /// `DR` / `N` 这类前缀，纯按内容自适应在名字都很短（或全为空显示「—」）时会窄到看不全。
        /// </summary>
        private static GridColumn NameColumn()
        {
            return new GridColumn("名称", "name") { Jump = true, MinWidth = 92 };
        }

        /// <summary>
        /// 跳到个股详情页并打开该股。原先这个「两步式」在盘面页、快捷搜索、历史导航各抄了一遍。
        ///
        /// 第 20 项：从表格跳来时**记下来源页**，供 ESC 原路返回（见 `ProcessCmdKey`）。
        /// 只在「来源不是个股页自己」时记 —— 个股页内部换股票（点股东表的另一只票）
        /// 不算跳转，否则 ESC 会想把用户送回个股页自己。
        /// </summary>
        private void OpenStock(string code)
        {
            if (string.IsNullOrEmpty(code)) return;
            code = code.Trim();
            if (!Regex.IsMatch(code, "^[0-9]{6}$")) return;   // 非 6 位数字（如名称列）不跳
            if (_tabIndex != _stockTabIndex) _returnTabIndex = _tabIndex;
            _stockCode.Text = code;
            SelectTab(_stockTabIndex);
            StockOpen();
        }

        /// <summary>ESC 返回用：上一个页签（-1 = 无）。由 OpenStock 写、ESC 消费后清空。</summary>
        private int _returnTabIndex = -1;

        /// <summary>ESC 返回：回到来源页。**返回点一次性**，用完即清，
        /// 免得用户手动切走之后 ESC 又把他拽回去。</summary>
        private bool EscReturnFromStock()
        {
            if (_returnTabIndex < 0 || _returnTabIndex >= _tabPages.Count) return false;
            if (_tabIndex != _stockTabIndex) return false;      // 只在个股页里才响应
            int back = _returnTabIndex;
            _returnTabIndex = -1;
            SelectTab(back);
            return true;
        }
    }
}
