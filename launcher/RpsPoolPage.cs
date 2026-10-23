using System;
using System.Collections;
using System.Collections.Generic;
using System.Drawing;
using System.Threading.Tasks;
using System.Windows.Forms;
using System.Web.Script.Serialization;

namespace StockPool
{
    /// <summary>
    /// RPS 强势池（每日全市场 RPS≥阈值，按交易日落库，滚动窗口保留最近 N 天）：
    ///   MainForm 的拆分文件，表格复用 GridKit 的 StockGrid（NewGrid / GridColumn）。
    ///
    /// 数据来自本机后端：
    ///   GET  /api/history/rps-pool        —— 读取已落库的每日强势池，并对比最新与上一交易日
    ///                                         得出 在池 / 入池 / 出池；
    ///   POST /api/history/rps-pool/refresh —— 触发后台计算（全市场 RPS≥阈值落库 + 截断），
    ///                                         返回 job_id；
    ///   GET  /api/history/rps-pool/job/{id}—— 轮询进度。
    /// 口径：在池 = 最新交易日命中的全部强股（按 RPS120 降序）；
    ///       入池 = 较上一交易日新增；出池 = 较上一交易日移除。
    /// RPS 见 backend_fastapi/indicators/rps_data.py（全市场 N 日涨幅百分比排名 ×100）。
    /// </summary>
    internal sealed partial class MainForm
    {
        private int _rpsPoolTabIndex = -1;
        private Label _rpsPoolStatus;
        private Label _rpsPoolInfo;
        private Label _rpsPoolInCap, _rpsPoolEnterCap, _rpsPoolExitCap;
        private StockGrid _rpsPoolIn, _rpsPoolEnter, _rpsPoolExit;
        private bool _rpsPoolBusy;
        private ComboBox _rpsPoolDate;        // 交易日选择（顶部「最新」+ 已落库交易日）
        private bool _rpsPoolSyncing;         // 填充下拉项时忽略 SelectedIndexChanged

        private Panel BuildRpsPoolPage()
        {
            var p = NewPage("RPS 池");
            _rpsPoolTabIndex = _tabPages.Count - 1;
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

            // 第一行：标题 + 刷新按钮 + 状态
            var head = new FlowLayoutPanel();
            head.AutoSize = true;
            head.WrapContents = false;
            head.Margin = new Padding(0, 0, 0, 6);
            head.Controls.Add(Lbl("RPS 强势池（全市场 RPS≥90 的强势股，按交易日增量更新）"));
            head.Controls.Add(MiniBtn("刷新", delegate { RpsPoolRefresh(); }, 92));
            _rpsPoolStatus = Mute(Lbl("尚未加载"));
            head.Controls.Add(_rpsPoolStatus);
            root.Controls.Add(head);

            // 日期选择行：选历史交易日查看（顶部「最新」+ 已落库交易日）
            var dateRow = new FlowLayoutPanel();
            dateRow.AutoSize = true;
            dateRow.WrapContents = false;
            dateRow.Margin = new Padding(0, 0, 0, 6);
            dateRow.Controls.Add(Mute(Lbl("查看日期")));
            _rpsPoolDate = new ComboBox();
            _rpsPoolDate.DropDownStyle = ComboBoxStyle.DropDownList;
            _rpsPoolDate.Width = 130;
            _rpsPoolDate.Items.Add("最新");
            _rpsPoolDate.SelectedIndex = 0;
            _rpsPoolDate.SelectedIndexChanged += delegate
            {
                if (_rpsPoolSyncing) return;
                string sel = _rpsPoolDate.SelectedItem as string;
                RpsPoolLoad(sel == "最新" ? null : sel);
            };
            dateRow.Controls.Add(_rpsPoolDate);
            root.Controls.Add(dateRow);

            // 汇总行：在池 / 入池 / 出池 计数 + RPS 口径说明
            _rpsPoolInfo = Mute(Lbl(""));
            _rpsPoolInfo.MaximumSize = new Size(1180, 0);
            _rpsPoolInfo.Margin = new Padding(0, 0, 0, 8);
            root.Controls.Add(_rpsPoolInfo);

            _rpsPoolInCap = Mute(Lbl("在池"));
            root.Controls.Add(_rpsPoolInCap);
            _rpsPoolIn = RpsPoolGrid();
            root.Controls.Add(_rpsPoolIn);

            _rpsPoolEnterCap = Mute(Lbl("入池（较上一快照新增）"));
            root.Controls.Add(_rpsPoolEnterCap);
            _rpsPoolEnter = RpsPoolGrid();
            root.Controls.Add(_rpsPoolEnter);

            _rpsPoolExitCap = Mute(Lbl("出池（较上一快照移除）"));
            root.Controls.Add(_rpsPoolExitCap);
            _rpsPoolExit = RpsPoolGrid();
            root.Controls.Add(_rpsPoolExit);

            return p;
        }

        /// <summary>三张表共用的列声明：序号 / 代码(可跳) / 名称(可跳) / 行业 / RPS50·120·250。</summary>
        private StockGrid RpsPoolGrid()
        {
            var g = NewGrid(new List<GridColumn> {
                new GridColumn("#", "rank", true) { Ordinal = true },
                new GridColumn("代码", "code") { IsCode = true, Jump = true },
                NameColumn(),
                new GridColumn("行业", "industry"),
                new GridColumn("RPS50", "rps50", true),
                new GridColumn("RPS120", "rps120", true),
                new GridColumn("RPS250", "rps250", true),
            });
            g.Dock = DockStyle.None;         // 本页是流式布局，宽度显式给
            g.Width = 760;
            g.SetSortable(true);             // 任意列可排序（第 20 项）
            return g;
        }

        /// <summary>进入本页时自动加载（已有数据则不重复拉）。</summary>
        private void RpsPoolOnEnter()
        {
            if (_rpsPoolIn == null) return;
            if (_rpsPoolIn.Rows.Count > 0) return;
            RpsPoolLoad();
        }

        // ---------------- 数据加载 ----------------

        private void RpsPoolLoad(string date = null)
        {
            if (_rpsPoolBusy) return;
            _rpsPoolBusy = true;
            RpsPoolSetStatus("加载中…", false);
            RunUi<Dictionary<string, object>>(
                delegate
                {
                    string url = "http://127.0.0.1:8000/api/history/rps-pool";
                    if (!string.IsNullOrEmpty(date)) url += "?date=" + date;
                    string body;
                    if (!GetText(url, 60000, out body))
                        return null;
                    try
                    {
                        return new JavaScriptSerializer()
                            .Deserialize<Dictionary<string, object>>(body);
                    }
                    catch { return null; }
                },
                delegate(Dictionary<string, object> j)
                {
                    _rpsPoolBusy = false;
                    if (j == null) { RpsPoolSetStatus("读取失败：后端未响应", true); return; }
                    if (!J.IsOk(j)) { RpsPoolSetStatus("读取失败：" + J.ErrMsg(j), true); return; }

                    var inArr = J.Arr(J.Get(j, "in_pool"));
                    var enArr = J.Arr(J.Get(j, "entered"));
                    var exArr = J.Arr(J.Get(j, "exited"));
                    RpsPoolFill(_rpsPoolIn, inArr);
                    RpsPoolFill(_rpsPoolEnter, enArr);
                    RpsPoolFill(_rpsPoolExit, exArr);

                    // 同步交易日下拉框，并定位到当前查看日
                    RpsPoolSyncDates(J.Arr(J.Get(j, "dates")), J.Str(J.Get(j, "latest")));

                    string note = J.Str(J.Get(j, "note"));
                    int nin = inArr == null ? 0 : inArr.Count;
                    int nen = enArr == null ? 0 : enArr.Count;
                    int nex = exArr == null ? 0 : exArr.Count;
                    RpsPoolSetStatus(note.Length > 0 ? note : "已更新", false);
                    _rpsPoolInfo.Text = string.Format(
                        "在池 {0} 只 · 入池 {1} 只 · 出池 {2} 只"
                        + "；RPS 为全市场 N 日涨幅百分比排名 ×100（≥90 即前 10% 强势）",
                        nin, nen, nex);
                    _rpsPoolInCap.Text = "在池（" + nin + " 只）";
                    _rpsPoolEnterCap.Text = "入池（较上一交易日新增 " + nen + " 只）";
                    _rpsPoolExitCap.Text = "出池（较上一交易日移除 " + nex + " 只）";
                },
                delegate(Exception ex)
                {
                    _rpsPoolBusy = false;
                    RpsPoolSetStatus("读取失败：" + ex.Message, true);
                },
                null);
        }

        /// <summary>用后端返回的可用交易日重建下拉框（顶部「最新」+ 降序日期），
        /// 并把选中项定位到 viewed（后端实际展示的日期）。填充期间忽略 SelectedIndexChanged。</summary>
        private void RpsPoolSyncDates(ArrayList datesArr, string viewed)
        {
            if (_rpsPoolDate == null) return;
            _rpsPoolSyncing = true;
            _rpsPoolDate.Items.Clear();
            _rpsPoolDate.Items.Add("最新");
            if (datesArr != null)
            {
                foreach (object d in datesArr)
                {
                    string s = J.Str(d);
                    if (s.Length > 0) _rpsPoolDate.Items.Add(s);
                }
            }
            string viewedKey = string.IsNullOrEmpty(viewed) ? "最新" : viewed;
            int sel = (viewedKey == "最新") ? 0 : _rpsPoolDate.Items.IndexOf(viewedKey);
            _rpsPoolDate.SelectedIndex = sel >= 0 ? sel : 0;
            _rpsPoolSyncing = false;
        }

        // ---------------- 刷新（触发后台每日计算 + 轮询进度） ----------------

        private void RpsPoolRefresh()
        {
            if (_rpsPoolBusy) return;
            _rpsPoolBusy = true;
            RpsPoolSetStatus("正在启动计算…", false);
            RunUi<Dictionary<string, object>>(
                delegate
                {
                    // 1) 触发后台刷新
                    string body;
                    string req = new JavaScriptSerializer().Serialize(new Dictionary<string, object> {
                        { "threshold", 90 }, { "window", 20 }, { "max_days", 20 }
                    });
                    if (!PostJson("http://127.0.0.1:8000/api/history/rps-pool/refresh", req, 20000, out body))
                    {
                        Invoke((Action)delegate
                        {
                            if (!IsDisposed && !Disposing)
                                RpsPoolSetStatus("启动失败：后端未响应", true);
                        });
                        return null;
                    }
                    Dictionary<string, object> jr;
                    try { jr = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(body); }
                    catch
                    {
                        Invoke((Action)delegate
                        {
                            if (!IsDisposed && !Disposing)
                                RpsPoolSetStatus("启动失败：响应解析错误", true);
                        });
                        return null;
                    }
                    if (!J.IsOk(jr))
                    {
                        string msg = J.ErrMsg(jr);
                        Invoke((Action)delegate
                        {
                            if (!IsDisposed && !Disposing)
                                RpsPoolSetStatus("启动失败：" + msg, true);
                        });
                        return null;
                    }
                    string jobId = J.Str(J.Get(jr, "job_id"));
                    if (jobId.Length == 0)
                    {
                        Invoke((Action)delegate
                        {
                            if (!IsDisposed && !Disposing)
                                RpsPoolSetStatus("启动失败：未返回任务ID", true);
                        });
                        return null;
                    }

                    // 2) 轮询进度（最多约 10 分钟）
                    for (int i = 0; i < 600; i++)
                    {
                        string jb;
                        if (!GetText("http://127.0.0.1:8000/api/history/rps-pool/job/" + jobId, 30000, out jb))
                        {
                            Invoke((Action)delegate
                            {
                                if (!IsDisposed && !Disposing)
                                    RpsPoolSetStatus("进度查询失败：后端未响应", true);
                            });
                            return null;
                        }
                        Dictionary<string, object> jj;
                        try { jj = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(jb); }
                        catch
                        {
                            Invoke((Action)delegate
                            {
                                if (!IsDisposed && !Disposing)
                                    RpsPoolSetStatus("进度解析失败", true);
                            });
                            return null;
                        }
                        if (!J.IsOk(jj))
                        {
                            string msg = J.ErrMsg(jj);
                            Invoke((Action)delegate
                            {
                                if (!IsDisposed && !Disposing)
                                    RpsPoolSetStatus("进度查询失败：" + msg, true);
                            });
                            return null;
                        }
                        var job = J.Get(jj, "job") as Dictionary<string, object>;
                        if (job == null)
                        {
                            Invoke((Action)delegate
                            {
                                if (!IsDisposed && !Disposing)
                                    RpsPoolSetStatus("任务不存在", true);
                            });
                            return null;
                        }
                        string status = J.Str(J.Get(job, "status"));
                        int progress = 0;
                        object pv = J.Get(job, "progress");
                        if (pv != null) progress = Convert.ToInt32(pv);
                        string note = J.Str(J.Get(job, "note"));
                        Invoke((Action)delegate
                        {
                            if (!IsDisposed && !Disposing)
                                RpsPoolSetStatus(
                                    (note.Length > 0 ? note : status) + "（" + progress + "%）",
                                    status == "failed");
                        });
                        if (status == "done" || status == "failed")
                            return jj;
                        System.Threading.Thread.Sleep(1000);
                    }
                    Invoke((Action)delegate
                    {
                        if (!IsDisposed && !Disposing)
                            RpsPoolSetStatus("计算超时（超过 10 分钟）", true);
                    });
                    return null;
                },
                delegate(Dictionary<string, object> j)
                {
                    _rpsPoolBusy = false;
                    if (j != null) RpsPoolLoad();   // 计算完成后拉取最新数据
                },
                delegate(Exception ex)
                {
                    _rpsPoolBusy = false;
                    RpsPoolSetStatus("刷新失败：" + ex.Message, true);
                },
                null);
        }

        private static void RpsPoolFill(StockGrid g, ArrayList items)
        {
            var data = new List<string[]>();
            int no = 0;
            if (items != null)
            {
                foreach (Dictionary<string, object> d in items)
                {
                    no++;
                    string name = J.Str(J.Get(d, "name"));
                    if (name.Length == 0) name = "—";
                    string ind = J.Str(J.Get(d, "industry"));
                    if (ind.Length == 0) ind = "—";
                    data.Add(new[] {
                        no.ToString(),
                        J.Str(J.Get(d, "code")),
                        name, ind,
                        RpsCell(J.Get(d, "rps50")),
                        RpsCell(J.Get(d, "rps120")),
                        RpsCell(J.Get(d, "rps250")),
                    });
                }
            }
            g.SetRows(data);
            g.Fit(120, 620);
        }

        private static string RpsCell(object v)
        {
            double? d = J.NumOrNull(v);
            return d == null ? "—" : d.Value.ToString("F1");
        }

        private void RpsPoolSetStatus(string text, bool failed)
        {
            if (_rpsPoolStatus == null) return;
            _rpsPoolStatus.Text = text;
            _rpsPoolStatus.Tag = "muted";
            _rpsPoolStatus.ForeColor = failed ? C.Warn : C.Flat;
        }
    }
}
