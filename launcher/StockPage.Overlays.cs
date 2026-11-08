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
        private int _stockIndRound;              // 指标请求轮次：切股后旧结果作废

        private static string _stamp()
        {
            return DateTime.Now.ToString("HH:mm:ss.fff");
        }

        private string StockIndicatorNameOf(string id)
        {
            if (string.IsNullOrEmpty(id)) return null;
            foreach (var o in _stockMainOpts) if (o.Id == id) return o.Name;
            foreach (var o in _stockSubOpts) if (o.Id == id) return o.Name;
            return null;
        }

        private void StockRefreshIndicatorList()
        {
            System.Threading.Tasks.Task.Run(delegate
            {
                try
                {
                    string body;
                    if (!GetText("http://127.0.0.1:8000/api/indicators", 15000, out body) || string.IsNullOrEmpty(body))
                        return;
                    var list = new JavaScriptSerializer().Deserialize<System.Collections.ArrayList>(body);
                    var mains = new List<Dictionary<string, object>>();
                    var subs = new List<Dictionary<string, object>>();
                    if (list != null)
                    {
                        foreach (Dictionary<string, object> it in list)
                        {
                            string panel = J.Str(J.Get(it, "panel"));
                            if (panel == "none") continue;                   // 不显示类型：不进任何列表
                            if (panel == "main") mains.Add(it); else subs.Add(it);   // lower / right → 副图候选
                        }
                    }
                    Invoke((Action)delegate { BuildIndicatorToggles(mains, subs); });
                }
                catch { }
            });
        }

        private void BuildIndicatorToggles(List<Dictionary<string, object>> mains,
                                           List<Dictionary<string, object>> subs)
        {
            _stockMainOpts.Clear();
            foreach (var it in mains)
                _stockMainOpts.Add(new IndOpt { Id = J.Str(J.Get(it, "id")), Name = J.Str(J.Get(it, "name")) });
            // 后端清单里若恰好没有 "ma"，这里补一个，保证输入 ma / 均线 也能作为主图指标解析
            bool hasMa = false;
            foreach (var o in _stockMainOpts) if (o.Id == "MA") { hasMa = true; break; }
            if (!hasMa) _stockMainOpts.Add(new IndOpt { Id = "MA", Name = "均线 MA" });
            _stockSubOpts.Clear();
            foreach (var it in subs)
                _stockSubOpts.Add(new IndOpt { Id = J.Str(J.Get(it, "id")), Name = J.Str(J.Get(it, "name")) });
        }


        private void StockLoadIndicators(string code)
        {
            int round = ++_stockIndRound;   // 轮次守卫：切股 / 刷新后旧指标结果作废
            Log("指标", string.Format("{0} [IND] 发起 code={1} round={2} cur={3}", _stamp(), code, round, _stockCurrent));
            // 主图（单选）+ 副图（固定 1 个）合并成一次批量请求；「ma」与主图其它指标走同一通道
            var ids = new List<string>();
            if (!string.IsNullOrEmpty(_stockMainIndicator))
                ids.Add(_stockMainIndicator);
            if (!string.IsNullOrEmpty(_stockSubIndicator) && !ids.Contains(_stockSubIndicator))
                ids.Add(_stockSubIndicator);

            if (ids.Count == 0)
            {
                if (_stockIndStatus != null) _stockIndStatus.Text = "";
                if (_stockKline != null)
                {
                    _stockKline.SetIndicators(new List<LowerPanel>());
                    _stockKline.SetMainIndicators(new List<ChartSeries>());
                }
                return;
            }
            // 计算前先给个提示：RPS 这类横截面指标首次要预热「全市场日 K 面板」，可能要十几秒；
            // 没有这行的话，勾选后界面毫无动静，用户会以为指标没出来。
            if (_stockIndStatus != null)
            {
                bool slow = ids.Contains("RPS");
                _stockIndStatus.Text = slow
                    ? "指标计算中…（RPS 首次约 15 秒预热全市场）"
                    : "指标计算中…";
                _stockIndStatus.Tag = "muted";
            }
            System.Threading.Tasks.Task.Run(delegate
            {
                try
                {
                    var items = new System.Collections.ArrayList();
                    foreach (var id in ids)
                    {
                        var it = new Dictionary<string, object>();
                        it["id"] = id;
                        it["params"] = new Dictionary<string, object>();
                        items.Add(it);
                    }
                    var req = new Dictionary<string, object>
                    {
                        { "code", code },
                        { "items", items },
                        { "period", string.IsNullOrEmpty(_stockPeriod) ? "day" : _stockPeriod },
                    };
                    string json = new JavaScriptSerializer().Serialize(req);
                    string body;
                    if (!PostJson("http://127.0.0.1:8000/api/indicators/batch", json, 120000, out body) || string.IsNullOrEmpty(body))
                        return;
                    var j = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(body);
                    var itemsResp = J.Arr(J.Get(j, "items"));
                    Log("指标", string.Format("{0} [IND] HTTP返回 round={1} items={2}", _stamp(), round, itemsResp == null ? 0 : itemsResp.Count));
                    var dates = (_stockKline != null) ? _stockKline.GetDates() : new List<string>();
                    var panels = new List<LowerPanel>();
                    var mainSeries = new List<ChartSeries>();
                    if (itemsResp != null)
                    {
                        foreach (Dictionary<string, object> it in itemsResp)
                        {
                            string id = J.Str(J.Get(it, "id"));
                            string name = J.Str(J.Get(it, "name"));
                            string panel = J.Str(J.Get(it, "panel"));
                            if (J.Get(it, "error") != null) continue;
                            var sArr = J.Arr(J.Get(it, "series"));
                            if (sArr == null) continue;
                            if (panel == "main")
                            {
                                // 主图叠加：序列直接叠加到价格轴
                                int maIdx = 0;
                                foreach (Dictionary<string, object> s in sArr)
                                {
                                    string sname = J.Str(J.Get(s, "name"));
                                    string kind = J.Str(J.Get(s, "kind"));
                                    var dArr = J.Arr(J.Get(s, "data"));
                                    var data = AlignSeries(dates, J.Get(s, "dates"), dArr);
                                    var cs = new ChartSeries { Name = sname, Kind = kind, Data = data };
                                    ApplySeriesStyle(cs);
                                    // MA5/10/20/60 等多条均线按出现顺序依次取色，避免按周期取模撞色（如 20 与 60 同色）
                                    if (sname != null && sname.StartsWith("MA"))
                                    {
                                        Color[] mac = { C.LineDif, C.LineDea, C.LinePink, C.LineViolet };
                                        cs.Color = mac[maIdx % mac.Length];
                                        maIdx++;
                                    }
                                    // 后端下发的样式（颜色 / 线宽）：新增指标在此生效，无需改前端；
                                    // 用户覆盖过的调色板语义色仍以用户为准。
                                    ApplyBackendStyle(cs, s);
                                    mainSeries.Add(cs);
                                }
                            }
                            else
                            {
                                // lower / right：作为下方副图面板
                                var lp = new LowerPanel { Id = id, Title = name, Weight = 1, Series = new List<ChartSeries>() };
                                foreach (Dictionary<string, object> s in sArr)
                                {
                                    string sname = J.Str(J.Get(s, "name"));
                                    string kind = J.Str(J.Get(s, "kind"));
                                    var dArr = J.Arr(J.Get(s, "data"));
                                    var data = AlignSeries(dates, J.Get(s, "dates"), dArr);
                                    var cs = new ChartSeries { Name = sname, Kind = kind, Data = data };
                                    ApplySeriesStyle(cs);
                                    ApplyBackendStyle(cs, s);
                                    lp.Series.Add(cs);
                                }
                                panels.Add(lp);
                            }
                        }
                    }
                    Invoke((Action)delegate
                    {
                        // 期间又切了股票 / 又发起了一轮指标请求 → 旧结果作废，避免 MA 用旧日期对新 K 线
                        if (round != _stockIndRound || _stockCurrent != code)
                        {
                            Log("指标", string.Format("{0} [IND] 丢弃 round={1} ind={2} code={3} cur={4}", _stamp(), round, _stockIndRound, code, _stockCurrent));
                            return;
                        }
                        Log("指标", string.Format("{0} [IND] 应用 round={1} main={2} sub={3}", _stamp(), round, mainSeries.Count, panels.Count));
                        if (_stockKline != null)
                        {
                            _stockKline.SetIndicators(panels);
                            _stockKline.SetMainIndicators(mainSeries);
                        }
                        if (_stockIndStatus != null) _stockIndStatus.Text = "";
                    });
                }
                catch { if (_stockIndStatus != null) _stockIndStatus.Text = ""; }
            });
        }
    }
}
