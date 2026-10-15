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
        private void StockLoadValuation(string code)
        {
            _stockValStatus.Text = "计算中…";
            _stockValStatus.Tag = "muted";
            _stockValStatus.ForeColor = C.Flat;
            System.Threading.Tasks.Task.Run(delegate
            {
                try
                {
                    var o = new Dictionary<string, object>();
                    o["code"] = code;
                    string body = new JavaScriptSerializer().Serialize(o);
                    string resp = VRequest("http://127.0.0.1:8000/api/stock/valuation", body);
                    var j = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(resp);
                    Invoke((Action)delegate { StockRenderValuation(j); });
                }
                catch (Exception)
                {
                    try
                    {
                        Invoke((Action)delegate
                        {
                            SetErr(_stockValStatus, "计算失败");
                        });
                    }
                    catch (Exception) { }
                }
            });
        }


        private void StockRenderValuation(Dictionary<string, object> j)
        {
            _stockValKpi.Controls.Clear();
            _stockValGrid.Rows.Clear();

bool ok = J.IsOk(j);
            if (!ok)
            {
                string emsg = J.ErrMsg(j);
                if (string.IsNullOrEmpty(emsg)) emsg = "计算失败";
                SetErr(_stockValStatus, emsg);
                return;
            }

            var m = J.Map(J.Get(j, "market"));
            var f = J.Map(J.Get(j, "finance"));
            var g = J.Map(J.Get(j, "growth"));
            var a = J.Map(J.Get(j, "assumptions"));

            AddKpi(_stockValKpi, "股票", J.Str(J.Get(j, "name")), J.Str(J.Get(j, "code")));
            AddKpi(_stockValKpi, "当前股价", J.Fmt(J.NumOrNull(J.Get(m, "price"))) + " 元", J.Str(J.Get(m, "price_source")));
            AddKpi(_stockValKpi, "期初净利润", J.Fmt(J.NumOrNull(J.Get(f, "net_profit_base"))) + " 亿", J.Str(J.Get(f, "indicator")));
            double? cagr = J.NumOrNull(J.Get(g, "cagr"));
            object cons;
            bool fromC = (g != null && g.TryGetValue("from_consensus", out cons) && cons is bool && (bool)cons);
            AddKpi(_stockValKpi, "复合增长率", cagr != null ? J.Pct100(cagr) : "走兜底",
                fromC ? "机构预测" : "按模板兜底");
            AddKpi(_stockValKpi, "贴现率", J.Pct100Plain(J.NumOrNull(J.Get(a, "discount_rate"))), "两段法 DCF");

            var scenarios = J.Arr(J.Get(j, "scenarios"));
            if (scenarios != null)
            {
                foreach (Dictionary<string, object> s in scenarios)
                {
                    string verdict = J.Str(J.Get(s, "verdict"));
                    string cls = (verdict == "低估" || verdict == "高估") ? verdict : "合理";
                    double? rr = J.NumOrNull(J.Get(s, "return_rate"));
                    int row = _stockValGrid.Rows.Add(
                        J.Str(J.Get(s, "label")),
                        J.Pct100(J.NumOrNull(J.Get(s, "growth"))),
                        J.Fmt(J.NumOrNull(J.Get(s, "value_per_share"))) + " 元",
                        J.Fmt(J.NumOrNull(J.Get(s, "undervalued_ratio")), 4),
                        cls,
                        J.Fmt(J.NumOrNull(J.Get(s, "target_price"))) + " 元",
                        J.Pct100(rr));
                    _stockValGrid.Rows[row].Cells[4].Style.ForeColor = verdict == "低估" ? C.Down
                        : (verdict == "高估" ? C.Up : C.Flat);
                    if (rr != null)
                        _stockValGrid.Rows[row].Cells[6].Style.ForeColor = rr.Value > 0 ? C.Up : (rr.Value < 0 ? C.Down : C.Flat);
                }
            }
            _stockValGrid.Fit(150, 620);

            _stockValStatus.Text = J.Str(J.Get(j, "name")) + "（" + J.Str(J.Get(j, "code")) + "）已计算";
            _stockValStatus.Tag = "muted";
            _stockValStatus.ForeColor = C.DownDeep;
        }

        private void StockJumpValuation(string code)
        {
            _vCode.Text = code;
            ValuationCalc();
            SelectTab(_valTabIndex);
        }
    }
}
