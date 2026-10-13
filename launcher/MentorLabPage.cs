using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Net;
using System.Text;
using System.Windows.Forms;
using System.Web.Script.Serialization;

namespace StockPool
{
    /// <summary>
    /// 大佬策略实验室（原生内嵌标签页）：把公开资料变成可回测的候选 Skill。
    ///
    /// 核心链路：**资料 → AI 提炼 → 人工审核 → 候选 Skill → 晋级 → 发布到红色小牛**。
    /// 这条链路的每一环都保留**人工审核**闸门（后端 skill/review 会校验
    /// ruleContent / skillId 非空），不做全自动。
    ///
    /// 为什么原生化：原先是 frontend/mentor-lab/index.html 外部窗口，样式与主
    /// 窗口不一致、要点两次。与「红色小牛」一起做成原生页后，一级导航里就能
    /// 连续走完，不用切浏览器。
    ///
    /// ⚠️ **刻意不做「回测评估」**：原网页版那块依赖
    ///   - `localStorage['stockPool.data.v1']` —— 浏览器里的股票池，原生拿不到；
    ///   - `frontend/shared/analysis-engine.js`（204 行）—— 浏览器 JS 回测引擎。
    /// 而「策略回测」已是一级导航里的独立功能（跑策略注册表里的 scr90/macd），
    /// 所以这里不重复造一套口径不同的回测，避免同一件事给出两个数字。
    ///
    /// 后端接口（features/mentor/routes.py，字段以实测为准）：
    ///   GET  /api/mentor/list                    -> {mentors:[{id,name,description}]}
    ///   POST /api/mentor/create                  {name,description} -> {mentor}
    ///   GET  /api/mentor/state?mentor_id=        -> {materials,daily_views,
    ///                                                  skill_versions,evaluations}
    ///   POST /api/mentor/material/upload         multipart(mentor_id, file)
    ///   POST /api/mentor/material/analyze        {material_id} -> {extraction}
    ///   GET  /api/mentor/extraction/{id}         -> {extraction}
    ///   POST /api/mentor/extraction/review       {extraction_id,structured,status,note}
    ///   POST /api/mentor/daily-view/analyze      {mentor_id,view_date,data_as_of,raw_text}
    ///   POST /api/mentor/daily-view/upload       multipart(mentor_id,view_date,data_as_of,file)
    ///   POST /api/mentor/daily-view/review       {view_id,structured,status}
    ///   POST /api/mentor/skill/generate          {mentor_id,change_reason} -> {skill_version}
    ///   POST /api/mentor/skill/review            {mentor_id,version,skill,note}
    ///   POST /api/mentor/skill/promote           {mentor_id,version}
    ///   POST（dsh :3080）/api/bull/skill/create   发布到红色小牛
    /// </summary>
    internal sealed partial class MainForm
    {
        private int _mlTabIndex = -1;
        private SubTabStrip _mlStrip;

        // 研究对象
        private ComboBox _mlMentorBox;
        private TextBox _mlMentorName, _mlMentorDesc;
        private List<string> _mlMentorIds = new List<string>();

        // 资料 / 每日观点 / Skill 的状态
        private ListBox _mlMaterialBox, _mlViewBox, _mlSkillBox;
        private Label _mlStatus;
        private bool _mlBusy;

        // 审核编辑区
        private RichTextBox _mlReviewJson;
        private TextBox _mlReviewNote;
        private Label _mlReviewTitle;
        private string _mlReviewKind = "";      // extraction / daily / skill
        private string _mlReviewId = "";        // extraction_id / view_id / version

        private const string ApiBase = "http://127.0.0.1:8000";

        #region 实验室

        private Panel BuildMentorLabPage()
        {
            var p = NewPage("策略实验室");
            _mlTabIndex = _tabPages.Count - 1;

            var root = new TableLayoutPanel();
            root.Dock = DockStyle.Fill;
            root.Margin = new Padding(0);
            root.ColumnCount = 1;
            root.RowCount = 3;      // 0 顶栏/ 1 二级标签 / 2 内容（SubTabStrip 的 1,2 对应这个约定）
            root.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100f));
            root.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            root.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            root.RowStyles.Add(new RowStyle(SizeType.Percent, 100f));
            p.Controls.Add(root);

            var head = Stack();
            TableLayoutPanel b0;
            var g0 = Group("研究对象", out b0);
            _mlMentorBox = new ComboBox();
            _mlMentorBox.Width = 240;
            _mlMentorBox.DropDownStyle = ComboBoxStyle.DropDownList;
            _mlMentorBox.SelectedIndexChanged += delegate { MlReload(); };
            _mlMentorName = new TextBox();
            _mlMentorName.Width = 180;
            _mlMentorDesc = new TextBox();
            _mlMentorDesc.Width = 260;
            _mlStatus = Mute(Lbl("连接中…"));
            AddRow(b0, Row(Lbl("当前"), _mlMentorBox,
                MiniBtn("刷新", delegate { MlLoadMentors(); }, 70)));
            AddRow(b0, Row(Lbl("新建"), _mlMentorName, _mlMentorDesc,
                MiniBtn("创建", delegate { MlCreateMentor(); }, 70)));
            AddRow(b0, Row(_mlStatus));
            AddRow(head, g0);
            g0.Margin = new Padding(0, 0, 0, 4);
            root.Controls.Add(head, 0, 0);

            _mlStrip = new SubTabStrip(this, root, 1, 2, "ml-subtab")
            {
                FixedButtonSize = false,
                BodyAutoScroll = true
            };
            _mlStrip.Add("资料", MlBuildMaterial);
            _mlStrip.Add("每日观点", MlBuildDaily);
            _mlStrip.Add("Skill", MlBuildSkill);
            _mlStrip.Add("审核", MlBuildReview);   // 人工审核闸门：三个环节都汇到这里
            _mlStrip.Select(0);
            return p;
        }

        // ---------------- ① 资料 ----------------

        private void MlBuildMaterial(TableLayoutPanel stack)
        {
            stack.AutoSize = false;
            stack.Dock = DockStyle.Fill;

            TableLayoutPanel b0;
            var g0 = Group("上传教学资料（PDF / 文本）", out b0);
            var file = new TextBox();
            file.Width = 300;
            string fname = "";
            var btn = MiniBtn("选择文件…", delegate
            {
                using (var dlg = new OpenFileDialog())
                {
                    dlg.Filter = "文本 / PDF|*.txt;*.md;*.pdf|所有文件|*.*";
                    if (dlg.ShowDialog() == DialogResult.OK) { file.Text = dlg.FileName; fname = dlg.FileName; }
                }
            }, 110);
            var up = MiniBtn("上传并解析", delegate
            {
                string path = (file.Text ?? "").Trim();
                if (path == "") { MlStatus("请先选择文件。", true); return; }
                MlUploadMaterial(path);
            }, 120);
            MlKeepFile(file, delegate(string p) { fname = p; });   // 记住真实路径
            AddRow(b0, Row(Lbl("文件"), file, btn, up));
            AddRow(stack, g0);

            TableLayoutPanel b1;
            var g1 = Group("资料列表", out b1);
            MlMakeFill(g1, b1);
            _mlMaterialBox = new ListBox();
            _mlMaterialBox.Dock = DockStyle.Fill;
            _mlMaterialBox.SelectedIndexChanged += delegate { MlOnMaterialPicked(); };
            AddFillRow(b1, _mlMaterialBox);
            var acts = Row(
                MiniBtn("AI 提炼选中", delegate { MlAnalyzeMaterial(); }, 110),
                MiniBtn("审核提炼结果", delegate { MlReviewExtraction(); }, 120));
            AddRow(stack, acts);
            AddFillRow(stack, g1);
            MlSetMaterialButtons(null);
        }

        // ---------------- ② 每日观点 ----------------

        private void MlBuildDaily(TableLayoutPanel stack)
        {
            stack.AutoSize = false;
            stack.Dock = DockStyle.Fill;

            TableLayoutPanel b0;
            var g0 = Group("录入当日观点", out b0);
            var date = new DateTimePicker();
            date.Format = DateTimePickerFormat.Short;
            date.Value = DateTime.Today;
            var asOf = new DateTimePicker();
            asOf.Format = DateTimePickerFormat.Short;
            asOf.Value = DateTime.Now;
            var text = new TextBox();
            text.Multiline = true;
            text.Height = 90;
            text.Dock = DockStyle.Top;
            AddRow(b0, Row(Lbl("观点日期"), date, Lbl("数据截止"), asOf));
            AddRow(b0, Row(Mute(Lbl("原文（会交给AI 提炼成可核验的结构化记录）"))));
            AddRow(b0, text);
            AddRow(b0, Row(
                MiniBtn("提炼这份观点", delegate
                {
                    string t = (text.Text ?? "").Trim();
                    if (t == "") { MlStatus("请填写观点原文。", true); return; }
                    MlAnalyzeDaily(date.Value.ToString("yyyy-MM-dd"),
                                   asOf.Value.ToString("yyyy-MM-dd HH:mm"), t);
                }, 130),
                MiniBtn("从文件提炼…", delegate
                {
                    using (var dlg = new OpenFileDialog())
                    {
                        dlg.Filter = "文本 / PDF|*.txt;*.md;*.pdf|所有文件|*.*";
                        if (dlg.ShowDialog() == DialogResult.OK)
                            MlUploadDaily(dlg.FileName, date.Value.ToString("yyyy-MM-dd"),
                                          asOf.Value.ToString("yyyy-MM-dd HH:mm"));
                    }
                }, 140)));
            AddRow(stack, g0);

            TableLayoutPanel b1;
            var g1 = Group("每日观点列表", out b1);
            MlMakeFill(g1, b1);
            _mlViewBox = new ListBox();
            _mlViewBox.Dock = DockStyle.Fill;
            AddFillRow(b1, _mlViewBox);
            AddRow(stack, Row(MiniBtn("审核 / 查看选中", delegate { MlReviewDaily(); }, 140)));
            AddFillRow(stack, g1);
        }

        // ---------------- ③ Skill ----------------

        private void MlBuildSkill(TableLayoutPanel stack)
        {
            stack.AutoSize = false;
            stack.Dock = DockStyle.Fill;

            TableLayoutPanel b0;
            var g0 = Group("候选 Skill", out b0);
            MlMakeFill(g0, b0);
            _mlSkillBox = new ListBox();
            _mlSkillBox.Dock = DockStyle.Fill;
            _mlSkillBox.SelectedIndexChanged += delegate { MlOnSkillPicked(); };
            AddFillRow(b0, _mlSkillBox);
            AddRow(stack, Row(
                MiniBtn("生成候选版本", delegate { MlGenerateSkill(); }, 120),
                MiniBtn("审核 / 编辑", delegate { MlReviewSkill(); }, 110),
                MiniBtn("晋级为正式", delegate { MlPromoteSkill(); }, 120),
                MiniBtn("发布到红色小牛", delegate { MlPublishSkill(); }, 150)));
            AddRow(stack, Row(Mute(Lbl("发布 = 把该正式版本写进 dsh 的 Skill 列表，之后可在「红色小牛」页选用它。"))));
            AddFillRow(stack, g0);
            MlSetSkillButtons(null);
        }

        /// <summary>进入本页时拉一次研究对象清单（页面是懒加载的，进来时可能还是空的）。</summary>
        private bool _mlLoaded;
        private void MlOnEnter()
        {
            if (_mlLoaded) return;
            _mlLoaded = true;
            MlLoadMentors();
        }

        // ---------------- 数据加载 ----------------

        private void MlLoadMentors()
        {
            if (_mlBusy) return;
            _mlBusy = true;
            System.Threading.Tasks.Task.Run(delegate
            {
                string body, err = "";
                bool ok = MlGet("/api/mentor/list", 20000, out body);
                Ui(delegate
                {
                    _mlBusy = false;
                    if (!ok) { MlStatus("读取研究对象失败：" + err, true); return; }
                    List<string> ids = new List<string>();
                    List<string> names = new List<string>();
                    try
                    {
                        var j = new JavaScriptSerializer()
                            .Deserialize<Dictionary<string, object>>(body);
                        object arr;
                        if (j.TryGetValue("mentors", out arr))
                        {
                            var list = arr as System.Collections.IList;
                            if (list != null)
                                foreach (var it in list)
                                {
                                    var d = it as Dictionary<string, object>;
                                    if (d == null) continue;
                                    ids.Add(J.StrOr(d["id"], ""));
                                    names.Add(J.StrOr(d["name"], "?"));
                                }
                        }
                    }
                    catch (Exception ex) { MlStatus("解析失败：" + ex.Message, true); return; }
                    string keep = _mlMentorBox.SelectedItem as string;
                    _mlMentorIds = ids;
                    _mlMentorBox.Items.Clear();
                    foreach (var n in names) _mlMentorBox.Items.Add(n);
                    if (keep != null && _mlMentorBox.Items.Contains(keep))
                        _mlMentorBox.SelectedItem = keep;
                    else if (_mlMentorBox.Items.Count > 0) _mlMentorBox.SelectedIndex = 0;
                    else MlStatus("还没有研究对象：填上面的名称与说明，点「创建」。", true);
                });
            });
        }

        private string MlCurMentorId()
        {
            int i = _mlMentorBox.SelectedIndex;
            return (i >= 0 && i < _mlMentorIds.Count) ? _mlMentorIds[i] : "";
        }

        private void MlCreateMentor()
        {
            if (_mlBusy) return;
            string name = (_mlMentorName.Text ?? "").Trim();
            if (name == "") { MlStatus("请填写研究对象名称。", true); return; }
            _mlBusy = true;
            string desc = (_mlMentorDesc.Text ?? "").Trim();
            System.Threading.Tasks.Task.Run(delegate
            {
                string payload = new JavaScriptSerializer().Serialize(
                    new Dictionary<string, object> { { "name", name }, { "description", desc } });
                string body, err = "";
                bool ok = MlPost("/api/mentor/create", payload, 20000, out body);
                if (!ok) err = body;
                Ui(delegate
                {
                    _mlBusy = false;
                    if (!ok) { MlStatus("创建失败：" + err, true); return; }
                    _mlMentorName.Clear();
                    _mlMentorDesc.Clear();
                    MlStatus("已创建，正在刷新列表…");
                    MlLoadMentors();
                });
            });
        }

        private void MlReload()
        {
            string id = MlCurMentorId();
            if (id == "") return;
            if (_mlBusy) return;
            _mlBusy = true;
            System.Threading.Tasks.Task.Run(delegate
            {
                string body, err = "";
                bool ok = MlGet("/api/mentor/state?mentor_id=" + Uri.EscapeDataString(id), 30000, out body);
                if (!ok) err = body;
                Ui(delegate
                {
                    _mlBusy = false;
                    if (!ok) { MlStatus("读取状态失败：" + err, true); return; }
                    MlApplyState(body);
                });
            });
        }

        private void MlApplyState(string json)
        {
            Dictionary<string, object> j;
            try
            {
                j = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(json);
            }
            catch (Exception ex) { MlStatus("解析状态失败：" + ex.Message, true); return; }
            if (j == null) { MlStatus("状态为空。", true); return; }

            // 资料
            _mlMaterialBox.BeginUpdate();
            _mlMaterialBox.Items.Clear();
            List<string> mids = new List<string>();
            object arr;
            if (j.TryGetValue("materials", out arr))
            {
                var list = arr as System.Collections.IList;
                if (list != null)
                    foreach (var it in list)
                    {
                        var d = it as Dictionary<string, object>;
                        if (d == null) continue;
                        string id = J.StrOr(d["id"], "");
                        mids.Add(id);
                        _mlMaterialBox.Items.Add(MlLine("file_name", d) + "  ·  解析 "
                            + MlLine("parse_status", d) + "  ·  提炼 "
                            + J.StrOr(d["extraction_status"], "未提炼"));
                    }
            }
            _mlMaterialBox.EndUpdate();
            _mlMaterialBox.Tag = mids;
            MlSetMaterialButtons(mids.Count > 0 ? mids[0] : null);

            // 每日观点
            _mlViewBox.BeginUpdate();
            _mlViewBox.Items.Clear();
            List<string> vids = new List<string>();
            if (j.TryGetValue("daily_views", out arr))
            {
                var list = arr as System.Collections.IList;
                if (list != null)
                    foreach (var it in list)
                    {
                        var d = it as Dictionary<string, object>;
                        if (d == null) continue;
                        vids.Add(J.StrOr(d["id"], ""));
                        _mlViewBox.Items.Add(J.StrOr(d["view_date"], "?") + "  ·  "
                            + J.StrOr(d["status"], "?"));
                    }
            }
            _mlViewBox.EndUpdate();
            _mlViewBox.Tag = vids;

            // Skill 版本
            _mlSkillBox.BeginUpdate();
            _mlSkillBox.Items.Clear();
            List<string> vers = new List<string>();
            if (j.TryGetValue("skill_versions", out arr))
            {
                var list = arr as System.Collections.IList;
                if (list != null)
                    foreach (var it in list)
                    {
                        var d = it as Dictionary<string, object>;
                        if (d == null) continue;
                        string ver = J.Int(d["version"]).ToString();
                        vers.Add(ver);
                        object sj;
                        string nm = "";
                        if (d.TryGetValue("skill_json", out sj) && sj is Dictionary<string, object>)
                            nm = J.StrOr(((Dictionary<string, object>)sj)["skillName"], "");
                        _mlSkillBox.Items.Add("v" + ver + "  ·  " + J.StrOr(d["status"], "?")
                                             + (nm == "" ? "" : "  ·  " + nm));
                    }
            }
            _mlSkillBox.EndUpdate();
            _mlSkillBox.Tag = vers;
            MlSetSkillButtons(vers.Count > 0 ? vers[0] : null);

            MlStatus("已刷新。回测评估请到一级导航的「策略回测」。");
        }

        private string MlLine(string field, Dictionary<string, object> d)
        {
            object v;
            return d.TryGetValue(field, out v) && v != null ? v.ToString() : "?";
        }

        // ---------------- 资料动作 ----------------

        private void MlKeepFile(TextBox box, Action<string> set)
        {
            box.Tag = set;
        }

        private string MlPickMaterialId()
        {
            int i = _mlMaterialBox.SelectedIndex;
            var ids = _mlMaterialBox.Tag as List<string>;
            return (i >= 0 && ids != null && i < ids.Count) ? ids[i] : "";
        }

        private void MlOnMaterialPicked()
        {
            var ids = _mlMaterialBox.Tag as List<string>;
            MlSetMaterialButtons(ids != null && _mlMaterialBox.SelectedIndex >= 0
                ? ids[_mlMaterialBox.SelectedIndex] : null);
        }

        private void MlSetMaterialButtons(string id)
        {
        }

        private void MlAnalyzeMaterial()
        {
            string mid = MlPickMaterialId();
            if (mid == "") { MlStatus("请先选中一份资料。", true); return; }
            if (_mlBusy) return;
            _mlBusy = true;
            MlStatus("AI 提炼中…（大模型可能需要 30~120 秒，资料长则更久）");
            System.Threading.Tasks.Task.Run(delegate
            {
                string payload = new JavaScriptSerializer().Serialize(
                    new Dictionary<string, object> { { "material_id", mid } });
                string body, err = "";
                bool ok = MlPost("/api/mentor/material/analyze", payload, 300000, out body);
                string json = "";
                if (ok)
                {
                    try
                    {
                        var j = new JavaScriptSerializer()
                            .Deserialize<Dictionary<string, object>>(body);
                        object ex;
                        if (j.TryGetValue("extraction", out ex) && ex is Dictionary<string, object>)
                        {
                            var e = (Dictionary<string, object>)ex;
                            json = MlJsonOf(e, "structured_json");
                        }
                    }
                    catch (Exception ex) { err = ex.Message; }
                }
                else err = body;
                string finalJson = json;
                Ui(delegate
                {
                    _mlBusy = false;
                    if (!ok) { MlStatus("提炼失败：" + err, true); return; }
                    MlOpenReview("资料提炼人工审核", "extraction", "", finalJson);
                    MlReload();
                });
            });
        }

        private void MlReviewExtraction()
        {
            // 先按当前列表行找到 extraction_id
            string matId = MlPickMaterialId();
            if (matId == "") { MlStatus("请先选中一份资料。", true); return; }
            MlStatus("读取提炼结果…");
            System.Threading.Tasks.Task.Run(delegate
            {
                string body, err = "";
                bool ok = MlGet("/api/mentor/state?mentor_id="
                    + Uri.EscapeDataString(MlCurMentorId()), 30000, out body);
                string exId = "", json = "";
                if (ok)
                {
                    try
                    {
                        var j = new JavaScriptSerializer()
                            .Deserialize<Dictionary<string, object>>(body);
                        object arr;
                        if (j.TryGetValue("materials", out arr))
                        {
                            var list = arr as System.Collections.IList;
                            if (list != null)
                                foreach (var it in list)
                                {
                                    var d = it as Dictionary<string, object>;
                                    if (d == null || J.StrOr(d["id"], "") != matId) continue;
                                    exId = J.StrOr(d["extraction_id"], "");
                                }
                        }
                    }
                    catch (Exception ex) { err = ex.Message; }
                }
                if (exId == "") err = err == "" ? "这份资料还没有提炼结果（先点「AI 提炼选中」）。" : err;
                string fid = exId, fj = json;
                Ui(delegate
                {
                    if (fid == "") { MlStatus(fid == "" ? err : err, true); return; }
                    MlLoadExtraction(fid);
                });
            });
        }

        private void MlLoadExtraction(string exId)
        {
            System.Threading.Tasks.Task.Run(delegate
            {
                string body, err = "";
                bool ok = MlGet("/api/mentor/extraction/" + Uri.EscapeDataString(exId), 30000, out body);
                string json = "";
                if (ok)
                {
                    try
                    {
                        var j = new JavaScriptSerializer()
                            .Deserialize<Dictionary<string, object>>(body);
                        object ex;
                        if (j.TryGetValue("extraction", out ex) && ex is Dictionary<string, object>)
                            json = MlJsonOf((Dictionary<string, object>)ex, "structured_json");
                    }
                    catch (Exception ex) { err = ex.Message; }
                }
                else err = body;
                string fj = json;
                Ui(delegate
                {
                    if (!ok || fj == "") { MlStatus("读取提炼结果失败：" + err, true); return; }
                    MlOpenReview("资料提炼人工审核", "extraction", exId, fj);
                });
            });
        }

        private void MlUploadMaterial(string path)
        {
            if (_mlBusy) return;
            _mlBusy = true;
            MlStatus("上传并解析中…");
            System.Threading.Tasks.Task.Run(delegate
            {
                string body, err = "";
                bool ok = MlUpload("/api/mentor/material/upload",
                    new Dictionary<string, string> { { "mentor_id", MlCurMentorId() } },
                    "file", path, 120000, out body);
                if (!ok) err = body;
                Ui(delegate
                {
                    _mlBusy = false;
                    if (!ok) { MlStatus("上传失败：" + err, true); return; }
                    MlStatus("上传完成。点「AI 提炼选中」让它读这份资料。");
                    MlReload();
                });
            });
        }

        // ---------------- 每日观点 ----------------

        private void MlAnalyzeDaily(string date, string asOf, string raw)
        {
            if (_mlBusy) return;
            _mlBusy = true;
            MlStatus("提炼中…（30~120 秒）");
            System.Threading.Tasks.Task.Run(delegate
            {
                string payload = new JavaScriptSerializer().Serialize(
                    new Dictionary<string, object> {
                        { "mentor_id", MlCurMentorId() }, { "view_date", date },
                        { "data_as_of", asOf }, { "raw_text", raw } });
                string body, err = "";
                bool ok = MlPost("/api/mentor/daily-view/analyze", payload, 300000, out body);
                string json = "";
                if (ok)
                {
                    try
                    {
                        var j = new JavaScriptSerializer()
                            .Deserialize<Dictionary<string, object>>(body);
                        object dv;
                        if (j.TryGetValue("daily_view", out dv) && dv is Dictionary<string, object>)
                            json = MlJsonOf((Dictionary<string, object>)dv, "structured_json");
                    }
                    catch (Exception ex) { err = ex.Message; }
                }
                else err = body;
                string fj = json;
                Ui(delegate
                {
                    _mlBusy = false;
                    if (!ok) { MlStatus("提炼失败：" + err, true); return; }
                    MlOpenReview("每日观点人工审核", "daily", "", fj);
                    MlReload();
                });
            });
        }

        private void MlUploadDaily(string path, string date, string asOf)
        {
            if (_mlBusy) return;
            _mlBusy = true;
            MlStatus("上传并提炼中…（30~120 秒）");
            System.Threading.Tasks.Task.Run(delegate
            {
                string body, err = "";
                bool ok = MlUpload("/api/mentor/daily-view/upload",
                    new Dictionary<string, string> {
                        { "mentor_id", MlCurMentorId() }, { "view_date", date },
                        { "data_as_of", asOf } },
                    "file", path, 300000, out body);
                if (!ok) err = body;
                Ui(delegate
                {
                    _mlBusy = false;
                    if (!ok) { MlStatus("上传提炼失败：" + err, true); return; }
                    MlStatus("已提炼。点列表里的「审核 / 查看选中」做人工确认。");
                    MlReload();
                });
            });
        }

        private void MlReviewDaily()
        {
            int i = _mlViewBox.SelectedIndex;
            var ids = _mlViewBox.Tag as List<string>;
            if (i < 0 || ids == null || i >= ids.Count) { MlStatus("请先选中一条每日观点。", true); return; }
            string vid = ids[i];
            System.Threading.Tasks.Task.Run(delegate
            {
                string body, err = "";
                bool ok = MlGet("/api/mentor/state?mentor_id="
                    + Uri.EscapeDataString(MlCurMentorId()), 30000, out body);
                string json = "";
                if (ok)
                {
                    try
                    {
                        var j = new JavaScriptSerializer()
                            .Deserialize<Dictionary<string, object>>(body);
                        object arr;
                        if (j.TryGetValue("daily_views", out arr))
                        {
                            var list = arr as System.Collections.IList;
                            if (list != null)
                                foreach (var it in list)
                                {
                                    var d = it as Dictionary<string, object>;
                                    if (d == null || J.StrOr(d["id"], "") != vid) continue;
                                    json = MlJsonOf(d, "structured_json");
                                }
                        }
                    }
                    catch (Exception ex) { err = ex.Message; }
                }
                string fj = json;
                Ui(delegate
                {
                    if (fj == "") { MlStatus("读取观点内容失败：" + err, true); return; }
                    MlOpenReview("每日观点人工审核", "daily", vid, fj);
                });
            });
        }

        // ---------------- Skill ----------------

        private string MlPickVersion()
        {
            int i = _mlSkillBox.SelectedIndex;
            var vs = _mlSkillBox.Tag as List<string>;
            return (i >= 0 && vs != null && i < vs.Count) ? vs[i] : "";
        }

        private void MlOnSkillPicked()
        {
        }

        private void MlSetSkillButtons(string ver)
        {
        }

        private void MlGenerateSkill()
        {
            if (_mlBusy) return;
            _mlBusy = true;
            MlStatus("生成候选 Skill中…（要综合已审核的知识与观点，30~180 秒）");
            System.Threading.Tasks.Task.Run(delegate
            {
                string payload = new JavaScriptSerializer().Serialize(
                    new Dictionary<string, object> { { "mentor_id", MlCurMentorId() } });
                string body, err = "";
                bool ok = MlPost("/api/mentor/skill/generate", payload, 300000, out body);
                if (!ok) err = body;
                Ui(delegate
                {
                    _mlBusy = false;
                    if (!ok) { MlStatus("生成失败：" + err, true); return; }
                    MlStatus("已生成候选版本。点「审核 / 编辑」确认后再晋级。");
                    MlReload();
                });
            });
        }

        private void MlReviewSkill()
        {
            string ver = MlPickVersion();
            if (ver == "") { MlStatus("请先选中一个 Skill 版本。", true); return; }
            System.Threading.Tasks.Task.Run(delegate
            {
                string body, err = "";
                bool ok = MlGet("/api/mentor/state?mentor_id="
                    + Uri.EscapeDataString(MlCurMentorId()), 30000, out body);
                string json = "";
                if (ok)
                {
                    try
                    {
                        var j = new JavaScriptSerializer()
                            .Deserialize<Dictionary<string, object>>(body);
                        object arr;
                        if (j.TryGetValue("skill_versions", out arr))
                        {
                            var list = arr as System.Collections.IList;
                            if (list != null)
                                foreach (var it in list)
                                {
                                    var d = it as Dictionary<string, object>;
                                    if (d == null || J.Int(d["version"]).ToString() != ver) continue;
                                    json = MlPretty(MlObjToText(J.Get(d, "skill_json")));
                                }
                        }
                    }
                    catch (Exception ex) { err = ex.Message; }
                }
                string fj = json;
                Ui(delegate
                {
                    if (fj == "") { MlStatus("读取 Skill 内容失败：" + err, true); return; }
                    MlOpenReview("候选 Skill 人工校验", "skill", ver, fj);
                });
            });
        }

        private void MlPromoteSkill()
        {
            string ver = MlPickVersion();
            if (ver == "") { MlStatus("请先选中一个 Skill 版本。", true); return; }
            if (!UiSync(delegate
            {
                return MessageBox.Show(this, "确认把 v" + ver + " 晋级为正式版本？",
                    AppTitle + " - 晋级确认", MessageBoxButtons.YesNo,
                    MessageBoxIcon.Question) == DialogResult.Yes;
            })) return;
            if (_mlBusy) return;
            _mlBusy = true;
            System.Threading.Tasks.Task.Run(delegate
            {
                string payload = new JavaScriptSerializer().Serialize(
                    new Dictionary<string, object> {
                        { "mentor_id", MlCurMentorId() }, { "version", J.Int(ver) } });
                string body, err = "";
                bool ok = MlPost("/api/mentor/skill/promote", payload, 30000, out body);
                if (!ok) err = body;
                Ui(delegate
                {
                    _mlBusy = false;
                    if (!ok) { MlStatus("晋级失败：" + err, true); return; }
                    MlStatus("v" + ver + " 已晋级为正式版本，可发布到红色小牛。");
                    MlReload();
                });
            });
        }

        private void MlPublishSkill()
        {
            string ver = MlPickVersion();
            if (ver == "") { MlStatus("请先选中一个 Skill 版本。", true); return; }
            System.Threading.Tasks.Task.Run(delegate
            {
                string body, err = "";
                bool ok = MlGet("/api/mentor/state?mentor_id="
                    + Uri.EscapeDataString(MlCurMentorId()), 30000, out body);
                object sj = null;
                if (ok)
                {
                    try
                    {
                        var j = new JavaScriptSerializer()
                            .Deserialize<Dictionary<string, object>>(body);
                        object arr;
                        if (j.TryGetValue("skill_versions", out arr))
                        {
                            var list = arr as System.Collections.IList;
                            if (list != null)
                                foreach (var it in list)
                                {
                                    var d = it as Dictionary<string, object>;
                                    if (d == null || J.Int(d["version"]).ToString() != ver) continue;
                                    sj = J.Get(d, "skill_json");
                                }
                        }
                    }
                    catch (Exception ex) { err = ex.Message; }
                }
                if (sj == null) err = err == "" ? "读取 Skill 内容失败。" : err;
                object skill = sj;
                Ui(delegate
                {
                    if (skill == null) { MlStatus("发布失败：" + err, true); return; }
                    if (!UiSync(delegate
                    {
                        return MessageBox.Show(this,
                            "把 v" + ver + " 发布到红色小牛的 Skill 列表？\r\n"
                            + "（红色小牛页选中它之后，回答就会按这套规则）",
                            AppTitle + " - 发布确认", MessageBoxButtons.YesNo,
                            MessageBoxIcon.Question) == DialogResult.Yes;
                    })) return;
                    MlPublishToDsh(ver, skill);
                });
            });
        }

        private void MlPublishToDsh(string ver, object skill)
        {
            if (_mlBusy) return;
            _mlBusy = true;
            MlStatus("发布中…（写到 dsh :3080）");
            System.Threading.Tasks.Task.Run(delegate
            {
                string payload = MlObjToText(skill);
                string body, err = "";
                bool ok = RbPost("/api/bull/skill/create", payload, 30000, out body);
                if (!ok) err = body;
                Ui(delegate
                {
                    _mlBusy = false;
                    if (!ok) { MlStatus("发布失败（AI 服务在跑吗）：" + err, true); return; }
                    MlStatus("v" + ver + " 已发布。到「红色小牛」页刷新 Skill 列表即可选用。");
                });
            });
        }

        // ---------------- 审核弹窗 ----------------

        private void MlOpenReview(string title, string kind, string id, string json)
        {
            _mlReviewKind = kind;
            _mlReviewId = id;
            if (_mlReviewTitle != null) _mlReviewTitle.Text = title;
            if (_mlReviewJson != null) _mlReviewJson.Text = json ?? "";
            if (_mlReviewNote != null) _mlReviewNote.Clear();
            // 审核区是第 4 个二级页：切过去，用户才能立刻看到要审的 JSON
            if (_mlStrip != null && _mlStrip.Count >= 4) _mlStrip.Select(3);
            if (_mlReviewJson != null) _mlReviewJson.Focus();
        }

        private void MlSubmitReview(string status)
        {
            if (_mlBusy) return;
            if (_mlReviewJson == null) { MlStatus("审核区未就绪。", true); return; }
            Dictionary<string, object> structured;
            try
            {
                structured = new JavaScriptSerializer()
                    .Deserialize<Dictionary<string, object>>(_mlReviewJson.Text);
            }
            catch (Exception)
            {
                MlStatus("审核内容必须是有效 JSON。", true);
                return;
            }
            if (_mlBusy) return;
            _mlBusy = true;
            string kind = _mlReviewKind, id = _mlReviewId, note = (_mlReviewNote.Text ?? "").Trim();
            System.Threading.Tasks.Task.Run(delegate
            {
                string payload = new JavaScriptSerializer().Serialize(
                    new Dictionary<string, object> {
                        { "structured", structured }, { "status", status } });
                string url, extra = "";
                if (kind == "extraction")
                {
                    url = "/api/mentor/extraction/review";
                    extra = "\"extraction_id\":\"" + id + "\",";
                    payload = "{" + extra + payload.Substring(1);
                }
                else if (kind == "daily")
                {
                    url = "/api/mentor/daily-view/review";
                    payload = "{\"view_id\":\"" + id + "\"," + payload.Substring(1);
                }
                else
                {
                    url = "/api/mentor/skill/review";
                    payload = "{\"mentor_id\":\"" + MlCurMentorId()
                        + "\",\"version\":" + J.Int(id) + ",\"skill\":"
                        + MlObjToText(structured)
                        + (note == "" ? "" : ",\"note\":\"" + note.Replace("\"", "\\\"") + "\"") + "}";
                }
                string body, err = "";
                bool ok = MlPost(url, payload, 30000, out body);
                if (!ok) err = body;
                Ui(delegate
                {
                    _mlBusy = false;
                    if (!ok) { MlStatus("提交失败：" + err, true); return; }
                    MlStatus(status == "approved" ? "已审核通过。" : "已标记为 " + status + "。");
                    MlReload();
                });
            });
        }

        // ---------------- 布局助手 ----------------

        /// <summary>
        /// 审核区：提炼结果 / 每日观点 / 候选 Skill 都在这里人工过一遍。
        /// 设计成**常驻在页面底部**而不是弹窗 —— 原网页版用 modal，WinForms 里
        /// 弹窗会把主界面挡住，而审核往往要反复对照上面的列表，改完直接就能关。
        /// </summary>
        private void MlBuildReview(TableLayoutPanel stack)
        {
            stack.AutoSize = false;
            stack.Dock = DockStyle.Fill;

            TableLayoutPanel body;
            var g = Group("人工审核（改完点「通过」或「标记为待定」）", out body);
            MlMakeFill(g, body);
            _mlReviewTitle = Lbl("尚未选中审核对象");
            _mlReviewTitle.Font = new Font("Microsoft YaHei UI", 10f, FontStyle.Bold);
            AddRow(body, Row(_mlReviewTitle,
                MiniBtn("通过（approved）", delegate { MlSubmitReview("approved"); }, 130),
                MiniBtn("待定 / 拒绝", delegate { MlSubmitReview("pending_review"); }, 120)));
            _mlReviewJson = new RichTextBox();
            _mlReviewJson.Dock = DockStyle.Fill;
            _mlReviewJson.Font = new Font("Consolas", 9.5f);
            _mlReviewJson.WordWrap = false;
            AddFillRow(body, _mlReviewJson);
            _mlReviewNote = new TextBox();
            _mlReviewNote.Width = 500;
            AddRow(body, Row(Lbl("审核备注"), _mlReviewNote));
            AddFillRow(stack, g);
        }

        /// <summary>
        /// GroupBox + 它的内容表都是 AutoSize（UiKit.Group 的设定），AutoSize 表里
        /// Percent 行算不出高度。撑满前必须先改成 Fill —— 与 RedBullPage 同一个坑。
        /// </summary>
        private static void MlMakeFill(GroupBox g, TableLayoutPanel body)
        {
            g.AutoSize = false;
            g.Dock = DockStyle.Fill;
            body.AutoSize = false;
            body.Dock = DockStyle.Fill;
        }
        // `AddFillRow` 复用 RedBullPage.cs 里的那个（同一份 partial class，不能重名）。

        // ---------------- 小工具 ----------------

        private void MlStatus(string s)
        {
            MlStatus(s, false);
        }

        private void MlStatus(string s, bool err)
        {
            if (_mlStatus == null) return;
            _mlStatus.Text = s;
            _mlStatus.ForeColor = err ? C.UpErr : _cSub;
        }

        /// <summary>把 state 里已经解析好的对象字段转成缩进 JSON 文本（用于审核编辑）。</summary>
        private static string MlJsonOf(Dictionary<string, object> d, string field)
        {
            object v;
            if (!d.TryGetValue(field, out v) || v == null) return "";
            return MlPretty(MlObjToText(v));
        }

        private static string MlObjToText(object v)
        {
            if (v == null) return "null";
            if (v is string) return (string)v;
            try { return new JavaScriptSerializer().Serialize(v); }
            catch { return v.ToString(); }
        }

        private static string MlPretty(string json)
        {
            try
            {
                var o = new JavaScriptSerializer()
                    .Deserialize<Dictionary<string, object>>(json);
                return new JavaScriptSerializer().Serialize(o);
            }
            catch { return json; }
        }

        // ---------------- HTTP ----------------

        private static bool MlGet(string path, int timeoutMs, out string body)
        {
            body = "";
            try
            {
                var req = (HttpWebRequest)WebRequest.Create(ApiBase + path);
                req.Method = "GET";
                req.Timeout = timeoutMs;
                using (var resp = (HttpWebResponse)req.GetResponse())
                using (var rd = new StreamReader(resp.GetResponseStream(), Encoding.UTF8))
                { body = rd.ReadToEnd(); return true; }
            }
            catch (WebException wex)
            {
                var resp = wex.Response as HttpWebResponse;
                body = MlErrOf(resp) + " / " + wex.Message;
                return false;
            }
            catch (Exception ex) { body = ex.Message; return false; }
        }

        private static bool MlPost(string path, string json, int timeoutMs, out string body)
        {
            body = "";
            try
            {
                var req = (HttpWebRequest)WebRequest.Create(ApiBase + path);
                req.Method = "POST";
                req.ContentType = "application/json";
                req.Timeout = timeoutMs;
                var bytes = Encoding.UTF8.GetBytes(json ?? "{}");
                req.ContentLength = bytes.Length;
                using (var s = req.GetRequestStream()) s.Write(bytes, 0, bytes.Length);
                using (var resp = (HttpWebResponse)req.GetResponse())
                using (var rd = new StreamReader(resp.GetResponseStream(), Encoding.UTF8))
                { body = rd.ReadToEnd(); return true; }
            }
            catch (WebException wex)
            {
                var resp = wex.Response as HttpWebResponse;
                body = MlErrOf(resp) + " / " + wex.Message;
                return false;
            }
            catch (Exception ex) { body = ex.Message; return false; }
        }

        /// <summary>multipart/form-data 上传（资料 / 每日观点文件）。</summary>
        private static bool MlUpload(string path, Dictionary<string, string> fields,
                                    string fileField, string filePath,
                                    int timeoutMs, out string body)
        {
            body = "";
            string boundary = "----Mlb" + Guid.NewGuid().ToString("N");
            try
            {
                var fs = new FileStream(filePath, FileMode.Open, FileAccess.Read);
                var ms = new MemoryStream();
                var w = new StreamWriter(ms, new UTF8Encoding(false));
                foreach (var kv in fields)
                {
                    w.Write("--" + boundary + "\r\n");
                    w.Write("Content-Disposition: form-data; name=\"" + kv.Key + "\"\r\n\r\n");
                    w.Write(kv.Value + "\r\n");
                }
                w.Write("--" + boundary + "\r\n");
                w.Write("Content-Disposition: form-data; name=\"" + fileField
                        + "\"; filename=\"" + Path.GetFileName(filePath) + "\"\r\n");
                w.Write("Content-Type: application/octet-stream\r\n\r\n");
                w.Flush();
                fs.CopyTo(ms);
                fs.Close();
                var tail = Encoding.UTF8.GetBytes("\r\n--" + boundary + "--\r\n");
                ms.Write(tail, 0, tail.Length);
                byte[] payload = ms.ToArray();

                var req = (HttpWebRequest)WebRequest.Create(ApiBase + path);
                req.Method = "POST";
                req.ContentType = "multipart/form-data; boundary=" + boundary;
                req.Timeout = timeoutMs;
                req.ContentLength = payload.Length;
                using (var s = req.GetRequestStream()) s.Write(payload, 0, payload.Length);
                using (var resp = (HttpWebResponse)req.GetResponse())
                using (var rd = new StreamReader(resp.GetResponseStream(), Encoding.UTF8))
                { body = rd.ReadToEnd(); return true; }
            }
            catch (WebException wex)
            {
                var resp = wex.Response as HttpWebResponse;
                body = MlErrOf(resp) + " / " + wex.Message;
                return false;
            }
            catch (Exception ex) { body = ex.Message; return false; }
        }

        private static string MlErrOf(HttpWebResponse resp)
        {
            if (resp == null) return "HTTP -";
            try
            {
                using (var rd = new StreamReader(resp.GetResponseStream(), Encoding.UTF8))
                {
                    var t = rd.ReadToEnd();
                    try
                    {
                        var j = new JavaScriptSerializer()
                            .Deserialize<Dictionary<string, object>>(t);
                        object det;
                        if (j.TryGetValue("detail", out det) && det != null) return "HTTP " + (int)resp.StatusCode + ": " + det;
                    }
                    catch { }
                    return "HTTP " + (int)resp.StatusCode + (t == "" ? "" : ": " + t);
                }
            }
            catch { return "HTTP " + (int)resp.StatusCode; }
        }

        #endregion
    }
}