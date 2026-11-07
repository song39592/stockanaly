using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Diagnostics;
using System.Text;
using System.Text.RegularExpressions;
using System.Windows.Forms;
using System.Web.Script.Serialization;
using System.IO;

namespace StockPool
{
    internal sealed partial class MainForm
    {
        /// <summary>二级页 ① K线：图表撑满、不滚动（滚轮只缩放，不与翻页冲突）。</summary>
        private Panel BuildStockKlinePanel()
        {
            var klinePage = new Panel();
            klinePage.AutoScroll = false;
            var kLayout = new TableLayoutPanel();
            kLayout.Dock = DockStyle.Fill;
            kLayout.Margin = new Padding(0);
            kLayout.ColumnCount = 2;
            kLayout.RowCount = 2;
            kLayout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100f));   // K 线
            kLayout.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 178f));  // 右侧筹码分布窗口
            kLayout.RowStyles.Add(new RowStyle(SizeType.AutoSize));       // 指标勾选 / 筹码公式下拉
            kLayout.RowStyles.Add(new RowStyle(SizeType.Percent, 100f));  // 图表撑满
            klinePage.Controls.Add(kLayout);
            var kTop = Stack();
            // ⚠️ 这里的列宽必须给**具体值**，不能沿用 Stack() 的 Percent 100。
            // 在 AutoSize 表里 Percent 会绕成「列宽 ← 表宽 ← 子项首选宽」的循环，
            // 指标勾选那行 FlowLayoutPanel（WrapContents=true）会被用 ~98px 去测量 →
            // 8 个控件各占一行 → 首选高度 208px，而 AutoSize 行取的正是它，
            // 于是 K 线上方空出一大块（实测：Percent 时 kTop=208/图表=517，
            // Absolute 时 kTop=36/图表=689，且 520~1400 宽度下空余均为 0）。
            kTop.ColumnStyles.Clear();
            kTop.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 300f));

            // 主图指标勾选行已移除：主图指标与副图一样由右下角输入框键入指标名 / id 切换。
            StockRefreshIndicatorList();   // 异步拉取指标清单：主图 / 副图候选灌进右下角输入框的联想下拉
            StockLoadChipFormulas();       // 同步筹码公式清单：索引名 → 切换右侧筹码窗口（与指标同一入口）

            // 「更新至 …」与「个股概况（在榜统计 KPI）」已按需求移除
            // （副图改用顶部代码框输入）；省下的高度全部归 K 线——row1 是 Percent 100，会自动吃满。

            _stockKline = new KLineChart();
            _stockKline.Dock = DockStyle.Fill;
            _stockKline.Tag = "kline";
            _stockKline.TabStop = true;
            _stockKline.OnRangeChanged = delegate (int r)
            {
                _stockRange = r;
                _stockRangeIdx = -1;                  // 滚轮自定义根数：范围按钮取消高亮
                StockSetRangeActive();
                ScheduleChipReload();     // 滚轮缩放：防抖后重算（帧窗口 = 新的可见根数）
            };
            _stockKline.BackColorChanged += delegate { _stockKline.Invalidate(); };

            // 筹码分布窗口：与 K 线等高并列在右侧，价格轴与 K 线主图严格对齐
            _stockChip = new ChipPanel();
            _stockChip.Dock = DockStyle.Fill;
            _stockChip.Owner = _stockKline;
            // 光标跟随：光标在 K 线上左右移动 → 筹码窗口切到那一天的分布
            _stockKline.OnHoverDate = delegate (string d) { if (_stockChip != null) _stockChip.SetCursorDate(d); };
            // 窗口平移（键盘光标到边缘 auto-pan / 拖动）→ 筹码按新的可见窗口重算，
            // 否则平移到更早日期后，光标日期会落到筹码快照之外（对不上 / 显示边界帧）。
            _stockKline.OnViewChanged = delegate { ScheduleChipReload(); };
            _stockKline.PriceAxisChanged += delegate { if (_stockChip != null) _stockChip.Invalidate(); };
            _stockChip.BackColorChanged += delegate { _stockChip.Invalidate(); };

            // （原「筹码公式下拉框」已移除：筹码模型改由右下角输入框键入索引名切换，
            //   与主图 / 副图指标同一入口，见 QuickSearch.cs 与 StockOpen。）
            kLayout.Controls.Add(kTop, 0, 0);                  // 指标勾选
            kLayout.Controls.Add(_stockKline, 0, 1);       // 图表占满剩余高度
            kLayout.Controls.Add(_stockChip, 1, 1);        // 右侧筹码分布窗口
            return klinePage;
        }
    }
}
