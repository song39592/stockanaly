using System;
using System.Collections.Generic;
using System.Drawing;
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
            private readonly List<int> _jumpCols = new List<int>();

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
            }

            /// <summary>设置列（会清空现有列）。</summary>
            public void SetColumns(IList<GridColumn> cols)
            {
                Columns.Clear();
                _codeCol = -1;
                _jumpCols.Clear();
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
                    if (c.IsCode) _codeCol = i;
                    if (c.Jump) _jumpCols.Add(i);
                }
            }

            /// <summary>整表可排序与否（默认否；排序会打乱依赖行序的主/明细结构）。</summary>
            public void SetSortable(bool sortable)
            {
                foreach (DataGridViewColumn col in Columns)
                    col.SortMode = sortable ? DataGridViewColumnSortMode.Automatic
                                            : DataGridViewColumnSortMode.NotSortable;
            }

            /// <summary>清空并按二维数组重填（值按列序）。</summary>
            public void SetRows(IList<string[]> rows)
            {
                Rows.Clear();
                if (rows == null) return;
                for (int i = 0; i < rows.Count; i++) Rows.Add(rows[i]);
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
        /// </summary>
        private void OpenStock(string code)
        {
            if (string.IsNullOrEmpty(code)) return;
            code = code.Trim();
            if (!Regex.IsMatch(code, "^[0-9]{6}$")) return;   // 非 6 位数字（如名称列）不跳
            _stockCode.Text = code;
            SelectTab(_stockTabIndex);
            StockOpen();
        }
    }
}
