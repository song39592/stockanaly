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
        private void StockLoadAi(string code, bool force = false)
        {
            if (_stockAiStatus != null)
            {
                _stockAiStatus.Text = force ? "AI 强制重新分析中…" : "AI 分析中…";
                _stockAiStatus.Tag = "muted";
                _stockAiStatus.ForeColor = C.Flat;
            }
            _stockAiMarkdown = "";
            if (_stockAiBox != null) FillAiDoc(_stockAiBox, _stockAiMarkdown);
            System.Threading.Tasks.Task.Run(delegate
            {
                try
                {
                    string body = new JavaScriptSerializer().Serialize(new Dictionary<string, object> {
                        { "code", code }, { "force", force },
                        { "depth", _aiDepth }, { "horizon", _aiHorizon }, { "focus", _aiFocus }
                    });
                    string resp = VRequest("http://127.0.0.1:8000/api/stock/research", body);
                    var j = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(resp);
                    Invoke((Action)delegate { StockRenderAi(j); });
                }
                catch (Exception)
                {
                    try
                    {
                        Invoke((Action)delegate
                        {
                            if (_stockAiStatus != null)
                            {
                                SetErr(_stockAiStatus, "分析失败（网络/超时，确认后端已启动且配置了 LLM）");
                            }
                        });
                    }
                    catch (Exception) { }
                }
            });
        }

        private static string AiPrefsPath()
        {
            var dir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "StockPoolLauncher");
            try { if (!Directory.Exists(dir)) Directory.CreateDirectory(dir); } catch { }
            return Path.Combine(dir, "ai_prefs.json");
        }


        private void StockRenderAi(Dictionary<string, object> j)
        {
            if (_stockAiStatus == null || _stockAiBox == null) return;
bool ok = J.IsOk(j);
            if (!ok)
            {
                string emsg = J.ErrMsg(j);
                if (string.IsNullOrEmpty(emsg)) emsg = "分析失败";
                SetErr(_stockAiStatus, emsg);
                _stockAiMarkdown = "";
                FillAiDoc(_stockAiBox, _stockAiMarkdown);
                return;
            }
            string md = J.Str(J.Get(j, "markdown"));
            _stockAiMarkdown = md;
            object cached;
            bool isCached = (j.TryGetValue("cached", out cached) && cached is bool && (bool)cached);
            string asOf = J.Str(J.Get(j, "data_as_of"));
            string d = J.Str(J.Get(j, "depth"));
            string h = J.Str(J.Get(j, "horizon"));
            var fobj = J.Get(j, "focus") as System.Collections.ArrayList;
            string f = "";
            if (fobj != null)
            {
                var tmp = new List<string>();
                foreach (var x in fobj) { var s = x as string; if (s != null) tmp.Add(s); }
                f = string.Join("、", tmp.ToArray());
            }
            string prefs = "";
            if (d != "" || h != "" || f != "")
                prefs = "  [偏好：" + (d != "" ? d : "?") + "/" + (h != "" ? h : "?") + (f != "" ? "/" + f : "") + "]";
            _stockAiStatus.Text = (isCached ? "（缓存）" : "已生成") + (asOf != "" ? " 数据截至 " + asOf : "") + prefs;
            _stockAiStatus.Tag = "muted";
            _stockAiStatus.ForeColor = C.DownDeep;
            FillAiDoc(_stockAiBox, _stockAiMarkdown);
        }

        private void FillAiDoc(RichTextBox rt, string md)
        {
            if (_docFont == null) _docFont = new Font("Microsoft YaHei UI", 9.5f);
            if (_docBold == null) _docBold = new Font(_docFont, FontStyle.Bold);
            var normal = _docFont;

            rt.Clear();
            rt.BackColor = _cPanel;
            rt.ForeColor = _cText;
            rt.SelectionFont = normal;
            rt.SelectionColor = _cText;

            if (string.IsNullOrEmpty(md) || md.Trim() == "")
            {
                rt.SelectionColor = _cSub;
                rt.AppendText("（暂无 AI 分析。配置好 LLM_API_KEY 并启动后端后，点击「生成分析」即可生成。）");
                rt.SelectionStart = 0; rt.SelectionLength = 0;
                return;
            }

            var lines = md.Replace("\r\n", "\n").Split('\n');
            bool first = true;
            foreach (string rawLine in lines)
            {
                string line = rawLine.TrimEnd();
                if (line.Trim() == "") continue;

                var hm = Regex.Match(line, @"^(#{1,6}\s+|[0-9]+[、.)]\s+)(.*)$");
                if (hm.Success)
                {
                    if (!first) rt.AppendText(Environment.NewLine);
                    rt.SelectionFont = _docBold;
                    rt.SelectionColor = _cText;
                    rt.AppendText(hm.Groups[2].Value.Trim() + Environment.NewLine + Environment.NewLine);
                    first = false;
                    continue;
                }
                if (Regex.IsMatch(line, @"^[-*_]{3,}$"))
                {
                    if (!first) rt.AppendText(Environment.NewLine);
                    rt.SelectionFont = normal;
                    rt.SelectionColor = _cSub;
                    rt.AppendText("────────────────────────────" + Environment.NewLine + Environment.NewLine);
                    first = false;
                    continue;
                }
                var qm = Regex.Match(line, @"^>\s?(.*)$");
                if (qm.Success)
                {
                    rt.SelectionFont = normal;
                    rt.SelectionColor = _cSub;
                    rt.AppendText("　　" + qm.Groups[1].Value + Environment.NewLine);
                    first = false;
                    continue;
                }
                var lm = Regex.Match(line, @"^[-*]\s+(.*)$");
                if (lm.Success)
                {
                    rt.SelectionFont = normal;
                    rt.SelectionColor = _cText;
                    rt.AppendText("· ");
                    AppendInline(rt, lm.Groups[1].Value, normal, _docBold);
                    rt.AppendText(Environment.NewLine);
                    first = false;
                    continue;
                }
                rt.SelectionFont = normal;
                rt.SelectionColor = _cText;
                AppendInline(rt, line, normal, _docBold);
                rt.AppendText(Environment.NewLine + Environment.NewLine);
                first = false;
            }
            rt.SelectionStart = 0;
            rt.SelectionLength = 0;
        }

        private static void AppendInline(RichTextBox rt, string text, Font normal, Font bold)
        {
            var re = new Regex(@"\*\*(.+?)\*\*");
            int last = 0;
            foreach (Match m in re.Matches(text))
            {
                if (m.Index > last)
                {
                    rt.SelectionFont = normal;
                    rt.AppendText(text.Substring(last, m.Index - last));
                }
                rt.SelectionFont = bold;
                rt.AppendText(m.Groups[1].Value);
                last = m.Index + m.Length;
            }
            if (last < text.Length)
            {
                rt.SelectionFont = normal;
                rt.AppendText(text.Substring(last));
            }
        }
    }
}
