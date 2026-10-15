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
        private void StockLoadSaolei(string code)
        {
            _stockSaoleiStatus.Text = "获取中…";
            _stockSaoleiStatus.Tag = "muted";
            _stockSaoleiStatus.ForeColor = C.Flat;
            _stockSaoleiList.Controls.Clear();
            System.Threading.Tasks.Task.Run(delegate
            {
                try
                {
                    string resp = VRequest("http://127.0.0.1:8000/api/stock/saolei/" + code, null);
                    var j = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(resp);
                    Invoke((Action)delegate
                    {
                        if (code == _stockCurrent) StockRenderSaolei(j);
                    });
                }
                catch (Exception ex)
                {
                    string msg = "获取失败：" + ex.Message;
                    try { Invoke((Action)delegate { if (code == _stockCurrent) StockSaoleiFail(msg); }); }
                    catch (Exception) { }
                }
            });
        }


        private void StockRenderSaolei(Dictionary<string, object> j)
        {
            _stockSaoleiList.Controls.Clear();

            int total = VIntOf(J.Get(j, "total"));
            int risk = VIntOf(J.Get(j, "risk"));
            int safe = VIntOf(J.Get(j, "safe"));
            string date = J.Str(J.Get(j, "date"));
            var cats = J.Arr(J.Get(j, "categories"));

            _stockSaoleiStatus.Text = "总检查 " + total + " 项 · 风险项 " + risk + " 项 · 安全项 " + safe + " 项"
                + (date != "" ? "（数据日期 " + date + "）" : "");
            _stockSaoleiStatus.Tag = "muted";
            _stockSaoleiStatus.ForeColor = risk > 0
                ? C.UpErr
                : C.Flat;

            // ---- 四大类风险清单（财务 / 市场 / 交易 / ST，并排展示）----
            if (cats != null && cats.Count > 0)
            {
                var grid = new TableLayoutPanel();
                grid.ColumnCount = cats.Count;
                grid.RowCount = 1;
                grid.AutoSize = true;
                grid.AutoSizeMode = AutoSizeMode.GrowAndShrink;
                grid.Dock = DockStyle.Top;
                grid.Margin = new Padding(0, 0, 0, 10);
                grid.Padding = new Padding(0);
                grid.RowStyles.Add(new RowStyle(SizeType.AutoSize));

                for (int c = 0; c < cats.Count; c++)
                {
                    grid.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
                    var cat = J.Map(cats[c]);
                    if (cat == null) continue;
                    var blk = SaoleiCategory(J.Str(J.Get(cat, "name")), J.Arr(J.Get(cat, "items")));
                    blk.Margin = new Padding(0, 0, 18, 0);
                    grid.Controls.Add(blk, c, 0);
                }
                _stockSaoleiList.Controls.Add(grid);
            }
            else
            {
                _stockSaoleiList.Controls.Add(Mute(Lbl("该股暂无风险清单数据")));
            }

            // ---- 个股亮点（辅）----
            var arr = J.Arr(J.Get(j, "highlights"));
            int n = arr == null ? 0 : arr.Count;
            var head = Lbl("个股亮点" + (n > 0 ? "（" + n + " 项）" : ""));
            head.Font = new Font("Microsoft YaHei UI", 9.5f, FontStyle.Bold);
            head.Margin = new Padding(0, 6, 0, 6);
            _stockSaoleiList.Controls.Add(head);

            if (n == 0)
            {
                _stockSaoleiList.Controls.Add(Mute(Lbl("该股暂无亮点记录")));
                return;
            }

            for (int i = 0; i < n; i++)
            {
                var ev = J.Map(arr[i]);
                if (ev == null) continue;
                string name = J.Str(J.Get(ev, "name"));
                string desc = J.Str(J.Get(ev, "desc"));

                var panel = new Panel();
                panel.AutoSize = true;
                panel.AutoSizeMode = AutoSizeMode.GrowAndShrink;
                panel.Dock = DockStyle.Top;
                panel.Margin = new Padding(0, 0, 0, 8);

                var t = Lbl("· " + name);
                t.Font = new Font("Microsoft YaHei UI", 10f, FontStyle.Bold);
                t.AutoSize = true;
                t.MaximumSize = new Size(760, 0);
                t.Dock = DockStyle.Top;
                panel.Controls.Add(t);

                if (desc != "")
                {
                    var d = Mute(Lbl(desc));
                    d.AutoSize = true;
                    d.MaximumSize = new Size(760, 0);
                    d.Dock = DockStyle.Top;
                    d.Margin = new Padding(0, 2, 0, 0);
                    panel.Controls.Add(d);
                }
                _stockSaoleiList.Controls.Add(panel);
            }
        }

        private static TableLayoutPanel SaoleiCategory(string title, System.Collections.ArrayList items)
        {
            var ok = new List<Dictionary<string, object>>();
            if (items != null)
            {
                foreach (object o in items)
                {
                    var m = J.Map(o);
                    if (m != null) ok.Add(m);
                }
            }

            var t = new TableLayoutPanel();
            t.ColumnCount = 1;
            t.AutoSize = true;
            t.AutoSizeMode = AutoSizeMode.GrowAndShrink;
            t.Margin = new Padding(0);
            t.Padding = new Padding(0);
            t.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));

            var head = Lbl(title);
            head.Font = new Font("Microsoft YaHei UI", 9.5f, FontStyle.Bold);
            head.Margin = new Padding(0, 0, 0, 6);
            t.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            t.Controls.Add(head, 0, 0);

            var body = new TableLayoutPanel();
            body.ColumnCount = 2;
            body.RowCount = Math.Max(1, ok.Count);
            body.AutoSize = true;
            body.AutoSizeMode = AutoSizeMode.GrowAndShrink;
            body.Margin = new Padding(0);
            body.Padding = new Padding(0);
            body.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
            body.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
            for (int i = 0; i < body.RowCount; i++) body.RowStyles.Add(new RowStyle(SizeType.AutoSize));

            if (ok.Count == 0)
            {
                body.Controls.Add(Mute(Lbl("—")), 0, 0);
            }
            else
            {
                for (int i = 0; i < ok.Count; i++)
                {
                    bool trig = J.Str(J.Get(ok[i], "trig")) == "1";
                    var nm = Lbl(J.Str(J.Get(ok[i], "name")));
                    nm.Margin = new Padding(0, 0, 12, 3);
                    var st = Lbl(trig ? "有" : "无");
                    st.Margin = new Padding(0, 0, 0, 3);
                    if (trig)
                    {
                        st.ForeColor = C.UpErr;
                        st.Font = new Font("Microsoft YaHei UI", 9f, FontStyle.Bold);
                    }
                    else
                    {
                        st.Tag = "muted";
                    }
                    body.Controls.Add(nm, 0, i);
                    body.Controls.Add(st, 1, i);
                }
            }

            t.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            t.Controls.Add(body, 0, 1);
            return t;
        }

        private static int VIntOf(object o)
        {
            int v;
            return int.TryParse(J.Str(o), out v) ? v : 0;
        }


        private void StockSaoleiFail(string msg)
        {
            _stockSaoleiList.Controls.Clear();
            _stockSaoleiStatus.Text = msg;
            _stockSaoleiStatus.Tag = "bad";
            _stockSaoleiStatus.ForeColor = C.UpErr;
        }
    }
}
