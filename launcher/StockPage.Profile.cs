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
        private Label BoardLine(Color color)
        {
            var l = new Label();
            l.AutoSize = true;
            l.MaximumSize = new Size(960, 0);       // 超宽自动换行
            l.ForeColor = color;
            l.Margin = new Padding(0, 1, 0, 3);
            l.TabStop = false;
            return l;
        }


        private void StockLoadBoards(string code)
        {
            StockLoadBoards(code, false);
        }


        private void StockLoadBoards(string code, bool refresh)
        {
            if (string.IsNullOrEmpty(code) || _stockBlocks == null) return;
            int round = _stockRound;
            string url = "http://127.0.0.1:8000/api/stock/boards?code=" + code
                       + (refresh ? "&refresh=1" : "");
            System.Threading.Tasks.Task.Run(delegate
            {
                try
                {
                    string resp = VRequest(url, null);
                    var j = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(resp);
                    Invoke((Action)delegate { if (round == _stockRound) StockRenderBoards(j); });
                }
                catch (Exception)
                {
                    try
                    {
                        Invoke((Action)delegate
                        {
                            if (round != _stockRound || _stockBlocks == null) return;
                            _stockBlocks.Text = "板块标注不可用";
                            _stockBlocks.ForeColor = C.Flat;
                        });
                    }
                    catch (Exception) { }
                }
            });
        }


        private void StockRenderBoards(Dictionary<string, object> j)
        {
            if (_stockBlocks == null) return;
            Color muted = C.Flat;
            _stockCptBlocks.Text = "";
            _stockRgnBlocks.Text = "";
bool ok = J.IsOk(j);
            if (!ok)
            {
                string emsg = J.ErrMsg(j);
                _stockBlocks.Text = string.IsNullOrEmpty(emsg) ? "板块标注不可用" : emsg;
                _stockBlocks.ForeColor = muted;
                return;
            }

            // 索引还没建好：提示 + 定时重查（后台重建约 1 分钟，重建完成前先不强求）
            bool syncing = false;
            object syv;
            if (j.TryGetValue("syncing", out syv) && syv is bool) syncing = (bool)syv;
            var boardsObj = J.Get(j, "boards") as Dictionary<string, object>;
            bool empty = (boardsObj == null
                          || J.Arr(J.Get(boardsObj, "industry")) == null
                          || J.Arr(J.Get(boardsObj, "industry")).Count == 0);
            if (empty)
            {
                if (syncing)
                {
                    _stockBlocks.Text = "板块索引建立中（约 1 分钟）…";
                    _stockBlocks.ForeColor = C.LineGold;
                    ScheduleBoardsRetry();
                    return;
                }
                _stockBlocks.Text = "板块索引未建立，点此 ↻ 重建索引";
                _stockBlocks.ForeColor = C.LineCyan;
                _stockBlocks.Cursor = Cursors.Hand;
                _stockBlocks.Click -= StockRebuildBoards;      // 防重复挂接
                _stockBlocks.Click += StockRebuildBoards;
                return;
            }
            _stockBlocks.Cursor = Cursors.Default;

            // 通达信配色：行业黄 / 概念绿 / 地域青；最相关（成分股最少）加 ★
            _stockBlocks.Text = BoardLineText("行业：", J.Arr(J.Get(boardsObj, "industry")), 6);
            _stockBlocks.ForeColor = C.RpsYellowLight;
            _stockCptBlocks.Text = BoardLineText("概念：", J.Arr(J.Get(boardsObj, "concept")), 12);
            _stockCptBlocks.ForeColor = C.UpSoft;
            _stockRgnBlocks.Text = BoardLineText("地域：", J.Arr(J.Get(boardsObj, "region")), 3);
            _stockRgnBlocks.ForeColor = C.LineCyan;
        }

        private static string BoardLineText(string title, System.Collections.ArrayList items, int maxShow)
        {
            if (items == null || items.Count == 0) return "";
            var names = new List<string>();
            int n = Math.Min(items.Count, maxShow);
            for (int i = 0; i < n; i++)
            {
                var d = items[i] as Dictionary<string, object>;
                if (d == null) continue;
                string name = Convert.ToString(J.Get(d, "board"));
                if (string.IsNullOrEmpty(name)) continue;
                names.Add((i == 0 && items.Count > 1 ? "★" : "") + name);
            }
            if (names.Count == 0) return "";
            string extra = items.Count > n ? ("  …共" + items.Count) : "";
            return title + string.Join("  ", names.ToArray()) + extra;
        }

        private void StockLoadBasic(string code)
        {
            if (string.IsNullOrEmpty(code) || _stockBasicKpi == null) return;
            int round = ++_stockBasicRound;
            _stockBasicStatus.Text = "正在加载基本信息…";
            _stockBasicStatus.Tag = "muted";
            _stockBasicStatus.ForeColor = C.Flat;
            System.Threading.Tasks.Task.Run(delegate
            {
                try
                {
                    string resp = VRequest("http://127.0.0.1:8000/api/stock/profile?code=" + code, null);
                    var j = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(resp);
                    Invoke((Action)delegate { if (round == _stockBasicRound) StockRenderBasic(j); });
                }
                catch (Exception ex)
                {
                    try
                    {
                        Invoke((Action)delegate
                        {
                            if (round == _stockBasicRound)
                            {
                                _stockBasicStatus.Text = "加载失败：" + ex.Message;
                                _stockBasicStatus.Tag = "bad";
                                _stockBasicStatus.ForeColor = C.UpErr;
                            }
                        });
                    }
                    catch (Exception) { }
                }
            });
        }


        private static string FmtShares(object o)
        {
            double? v = J.NumOrNull(o);
            if (v == null) return "—";
            if (v >= 1e8) return (v.Value / 1e8).ToString("F2") + " 亿股";
            if (v >= 1e4) return (v.Value / 1e4).ToString("F2") + " 万股";
            return v.Value.ToString("F0") + " 股";
        }


        private void StockRenderBasic(Dictionary<string, object> j)
        {
            if (_stockBasicKpi == null) return;
            _stockBasicKpi.Controls.Clear();
            _stockBasicLimitKpi.Controls.Clear();
            _stockBasicHolders.Rows.Clear();

bool ok = J.IsOk(j);
            if (!ok)
            {
                string emsg = J.Str(J.Get(j, "detail"));
                if (string.IsNullOrEmpty(emsg)) emsg = "加载失败";
                _stockBasicStatus.Text = emsg;
                _stockBasicStatus.Tag = "bad";
                _stockBasicStatus.ForeColor = C.UpErr;
                return;
            }

            string industry = J.Str(J.Get(j, "industry"));
            AddKpi(_stockBasicKpi, "所属行业", string.IsNullOrEmpty(industry) ? "—" : industry,
                "上市 " + (string.IsNullOrEmpty(J.Str(J.Get(j, "listing_date"))) ? "—" : J.Str(J.Get(j, "listing_date"))));
            AddKpi(_stockBasicKpi, "总市值", J.Yi(J.Get(j, "total_mv_yi")),
                "总股本 " + FmtShares(J.Get(j, "total_shares")));
            AddKpi(_stockBasicKpi, "流通市值", J.Yi(J.Get(j, "float_mv_yi")),
                "流通股本 " + FmtShares(J.Get(j, "float_shares")));

            string bus = J.Str(J.Get(j, "main_business"));
            _stockBasicBus.Text = string.IsNullOrEmpty(bus) ? "" : ("主营业务：" + bus);

            var lu = J.Get(j, "limit_up") as Dictionary<string, object>;
            if (lu != null)
            {
                double? lpct = J.NumOrNull(J.Get(j, "limit_pct"));
                string last = J.Str(J.Get(lu, "last_date"));
                string range = "";
                var rArr = J.Arr(J.Get(lu, "max_range"));
                if (rArr != null && rArr.Count >= 2) range = J.Str(rArr[0]) + " ~ " + J.Str(rArr[1]);
                AddKpi(_stockBasicLimitKpi, "上次涨停", string.IsNullOrEmpty(last) ? "—" : last,
                    lpct != null ? ("涨跌幅限制 " + lpct.Value + "%") : "");
                AddKpi(_stockBasicLimitKpi, "历史最大连板",
                    (J.NumOrNull(J.Get(lu, "max_streak")) ?? 0) + " 板",
                    string.IsNullOrEmpty(range) ? "" : range);
                AddKpi(_stockBasicLimitKpi, "涨停次数",
                    (J.NumOrNull(J.Get(lu, "total")) ?? 0).ToString(), "历史累计");
            }

            var holders = J.Arr(J.Get(j, "holders"));
            if (holders != null)
            {
                foreach (Dictionary<string, object> h in holders)
                {
                    double? pct = J.NumOrNull(J.Get(h, "pct"));
                    _stockBasicHolders.Rows.Add(
                        J.Str(J.Get(h, "rank")),
                        J.Str(J.Get(h, "name")),
                        J.Str(J.Get(h, "share_type")),
                        FmtShares(J.Get(h, "shares")),
                        pct == null ? "—" : pct.Value.ToString("F2") + "%",
                        J.Str(J.Get(h, "change")),
                        J.Str(J.Get(h, "change_ratio")));
                }
                _stockBasicHolders.Fit(200, 620);
            }

            var errs = J.Arr(J.Get(j, "errors"));
            bool hasErr = errs != null && errs.Count > 0;
            _stockBasicStatus.Text = hasErr ? ("部分数据不可用：" + JoinErrs(errs)) : "加载完成";
            _stockBasicStatus.Tag = "muted";
            _stockBasicStatus.ForeColor = hasErr ? C.Warn : C.Flat;
        }

        /// <summary>同步筹码公式清单 → `_stockChipOpts`，供右下角输入框按**索引名**切换筹码模型
        /// （原独立下拉框已移除；与主图 / 副图指标共用同一个输入入口）。
        /// Id = 纯字母索引名（EMALOCK / TRIDECAY，键入用），Raw = 后端公式 id（拼 &amp;formula= 用）。</summary>
        private void StockLoadChipFormulas()
        {
            System.Threading.Tasks.Task.Run(delegate
            {
                try
                {
                    string body;
                    if (!GetText("http://127.0.0.1:8000/api/chip/dist/formulas", 15000, out body)
                        || string.IsNullOrEmpty(body))
                        return;
                    var j = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(body);
                    var items = J.Arr(J.Get(j, "items"));
                    var list = new List<IndOpt>();
                    if (items != null)
                    {
                        foreach (Dictionary<string, object> it in items)
                        {
                            string fid = J.Str(J.Get(it, "id"));
                            if (string.IsNullOrEmpty(fid)) continue;
                            string idx = J.Str(J.Get(it, "index"));
                            list.Add(new IndOpt
                            {
                                Id = string.IsNullOrEmpty(idx) ? fid : idx,   // 纯字母索引名（键入用）
                                Name = J.Str(J.Get(it, "name")) ?? fid,
                                Raw = fid,                                    // 后端公式 id
                            });
                        }
                    }
                    Invoke((Action)delegate { StockApplyChipFormulas(list); });
                }
                catch { }
            });
        }


        private void StockApplyChipFormulas(List<IndOpt> list)
        {
            _stockChipOpts.Clear();
            if (list != null) _stockChipOpts.AddRange(list);
        }

        private void ScheduleChipReload()
        {
            Debounce(ref _chipTimer, 400, delegate
            {
                if (_stockCurrent != null) StockLoadChip(_stockCurrent);
            });
        }


        private void StockLoadChip(string code)
        {
            if (string.IsNullOrEmpty(code) || _stockChip == null) return;
            int round = _stockRound;
            _stockChip.SetStatus("筹码计算中…", false);
            string adjust = string.IsNullOrEmpty(_stockAdjust) ? "qfq" : _stockAdjust;
            string url = "http://127.0.0.1:8000/api/chip/dist?code=" + code
                       + "&adjust=" + adjust
                       + "&days=" + (_stockRange > 0 ? _stockRange.ToString() : "0")
                       + "&bins=80";
            if (!string.IsNullOrEmpty(_stockChipFormulaId))
                url += "&formula=" + Uri.EscapeDataString(_stockChipFormulaId);
            url += "&period=" + (string.IsNullOrEmpty(_stockPeriod) ? "day" : _stockPeriod);
            System.Threading.Tasks.Task.Run(delegate
            {
                try
                {
                    string body;
                    if (!GetText(url, 30000, out body) || string.IsNullOrEmpty(body))
                    {
                        Invoke((Action)delegate
                        {
                            if (round == _stockRound) _stockChip.SetStatus("筹码接口无响应", true);
                        });
                        return;
                    }
                    var j = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(body);
bool ok = J.IsOk(j);
                    if (!ok)
                    {
                        string emsg = J.ErrMsg(j);
                        if (string.IsNullOrEmpty(emsg)) emsg = "筹码数据不可用";
                        string cap = emsg;
                        Invoke((Action)delegate
                        {
                            if (round == _stockRound) _stockChip.SetStatus(cap, true);
                        });
                        return;
                    }
                    // 解析放工作线程：逐日快照可达上千帧 × 80 个分箱，不能卡 UI
                    var bins = StockParseChipBins(j);
                    var dates = new List<string>();
                    var frames = new List<double[]>();
                    var grayFrames = new List<double[]>();
                    var fstats = new List<ChipStats>();
                    StockParseChipFrames(j, bins, dates, frames, fstats, grayFrames);
                    // 口径提示：锁仓修正 = 前十大流通股东「占流通股比例 > 5%」合计 r，
                    // 换手率按 1/(1-r) 放大（对齐通达信 / 东财口径，见 chip_formulas/tri_decay.py）。
                    object lockObj;
                    bool lockup = (j.TryGetValue("lockup_applied", out lockObj) && lockObj is bool && (bool)lockObj);
                    string note = "未做锁仓修正";
                    if (lockup)
                    {
                        double lr = J.NumAt(j, "lockup_ratio");
                        double lf = J.NumAt(j, "lockup_factor");
                        note = string.Format("已做锁仓修正（占流通股 {0:F1}%，系数 {1:F3}）", lr * 100.0, lf);
                    }
                    Invoke((Action)delegate
                    {
                        if (round != _stockRound) return;
                        _stockChip.SetData(bins, dates, frames, fstats, note, grayFrames);
                        // 重算后若光标仍停在 K 线上，立刻回到光标那一天
                        if (_stockKline != null) _stockChip.SetCursorDate(_stockKline.CursorDate);
                    });
                }
                catch (Exception ex)
                {
                    try
                    {
                        Invoke((Action)delegate
                        {
                            if (round == _stockRound) _stockChip.SetStatus("筹码加载失败：" + ex.Message, true);
                        });
                    }
                    catch (Exception) { }
                }
            });
        }


        private static List<ChipBin> StockParseChipBins(Dictionary<string, object> j)
        {
            var bins = new List<ChipBin>();
            var arr = J.Arr(J.Get(j, "bins"));
            if (arr == null) return bins;
            foreach (Dictionary<string, object> d in arr)
            {
                var b = new ChipBin();
                b.Lo = J.NumOrNull(J.Get(d, "lo")) ?? 0;
                b.Hi = J.NumOrNull(J.Get(d, "hi")) ?? 0;
                b.Price = J.NumOrNull(J.Get(d, "price")) ?? 0;
                b.Pct = J.NumOrNull(J.Get(d, "pct")) ?? 0;
                bins.Add(b);
            }
            return bins;
        }

        private static void StockParseChipFrames(Dictionary<string, object> j, List<ChipBin> bins,
            List<string> dates, List<double[]> frames, List<ChipStats> fstats, List<double[]> grayFrames)
        {
            var frObj = J.Get(j, "frames") as Dictionary<string, object>;
            if (frObj != null)
            {
                var dArr = J.Arr(J.Get(frObj, "dates"));
                if (dArr != null)
                    foreach (object o in dArr) dates.Add(Convert.ToString(o));

                var pArr = J.Arr(J.Get(frObj, "pct"));
                if (pArr != null)
                {
                    foreach (object row in pArr)
                    {
                        var rl = row as System.Collections.ArrayList;
                        if (rl == null) continue;
                        var vals = new double[rl.Count];
                        for (int k = 0; k < rl.Count; k++) vals[k] = J.NumOrNull(rl[k]) ?? 0;
                        frames.Add(vals);
                    }
                }

                var gArr = J.Arr(J.Get(frObj, "gray"));
                if (gArr != null)
                {
                    foreach (object row in gArr)
                    {
                        var rl = row as System.Collections.ArrayList;
                        if (rl == null) continue;
                        var vals = new double[rl.Count];
                        for (int k = 0; k < rl.Count; k++) vals[k] = J.NumOrNull(rl[k]) ?? 0;
                        grayFrames.Add(vals);
                    }
                }

                var sArr = J.Arr(J.Get(frObj, "stats"));
                if (sArr != null)
                {
                    foreach (object row in sArr)
                    {
                        var d = row as Dictionary<string, object>;
                        var s = new ChipStats();
                        if (d != null)
                        {
                            s.Close = J.NumOrNull(J.Get(d, "close")) ?? 0;
                            s.AvgCost = J.NumOrNull(J.Get(d, "avg_cost"));
                            s.PeakPrice = J.NumOrNull(J.Get(d, "peak_price"));
                            s.ProfitRatio = J.NumOrNull(J.Get(d, "profit_ratio"));
                            s.Scr90 = J.NumOrNull(J.Get(d, "scr90"));
                        }
                        fstats.Add(s);
                    }
                }
            }
            if (frames.Count == 0 && bins.Count > 0)
            {   // 兜底：单帧（最新一天）
                var vals = new double[bins.Count];
                for (int i = 0; i < bins.Count; i++) vals[i] = bins[i].Pct;
                frames.Add(vals);
                dates.Add(J.Str(J.Get(j, "as_of")));
                var s = new ChipStats();
                var sObj = J.Get(j, "stats") as Dictionary<string, object>;
                if (sObj != null)
                {
                    s.Close = J.NumOrNull(J.Get(j, "last_close")) ?? 0;
                    s.AvgCost = J.NumOrNull(J.Get(sObj, "avg_cost"));
                    s.PeakPrice = J.NumOrNull(J.Get(sObj, "peak_price"));
                    s.ProfitRatio = J.NumOrNull(J.Get(sObj, "profit_ratio"));
                    s.Scr90 = J.NumOrNull(J.Get(sObj, "scr90"));
                }
                fstats.Add(s);
            }
        }

        private static List<double> AlignSeries(List<string> dates, object datesObj, System.Collections.ArrayList dataArr)
        {
            var data = new List<double>();
            for (int i = 0; i < dates.Count; i++) data.Add(double.NaN);
            if (dataArr == null) return data;
            var srcDates = new List<string>();
            var dl = datesObj as System.Collections.ArrayList;
            if (dl != null)
                foreach (object d in dl) srcDates.Add(Convert.ToString(d));
            if (srcDates.Count == dataArr.Count)
            {
                var map = new Dictionary<string, double>();
                for (int k = 0; k < srcDates.Count; k++)
                {
                    object v = dataArr[k];
                    map[srcDates[k]] = (v == null) ? double.NaN : Convert.ToDouble(v);
                }
                for (int i = 0; i < dates.Count; i++)
                {
                    double val;
                    if (map.TryGetValue(dates[i], out val)) data[i] = val;
                }
            }
            else
            {
                for (int i = 0; i < dates.Count && i < dataArr.Count; i++)
                {
                    object v = dataArr[i];
                    data[i] = (v == null) ? double.NaN : Convert.ToDouble(v);
                }
            }
            return data;
        }

        private static void ApplySeriesStyle(ChartSeries s)
        {
            switch (s.Name)
            {
                case "RPS120": s.Color = C.Down;   break;   // COLORGREEN
                case "RPS250": s.Color = C.RpsWhite; break;   // COLORWHITE
                case "RPS50": s.Color = C.RpsYellow;  break;    // COLORYELLOW
                case "RPS20": s.Color = C.Flat; break;    // COLORGRAY
                case "RPS10": s.Color = C.RpsCyan; break;    // COLORLICYAN
                default: s.Color = LineColor(s.Name); return;
            }
            s.Width = 2f;                       // LINETHICK2
            s.HasHi = true;
            s.HiThreshold = RpsHiThreshold;
            s.HiColor = RpsHiRed;
        }


        private static Color LineColor(string name)
        {
            if (name != null)
            {
                if (name.Contains("DIF")) return C.LineDif;
                if (name.Contains("DEA")) return C.LineDea;
            }
            return C.LineViolet;
        }
    }
}
