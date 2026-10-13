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
    /// 红色小牛（原生内嵌标签页）：多角色问答 Agent 的原生入口。
    ///
    /// 为什么从网页搬成原生：原先只在 `frontend/home` 里以「右下角悬浮聊天窗」
    /// 存在，要另开浏览器、样式与主窗口不一致，且每次都得重新登录会话。现在作为
    /// 一级导航里的独立标签页，与估值计算 / 个股分析同级。
    ///
    /// 后端是 `agent_dsh`（Node，127.0.0.1:3080）的 `/api/bull/*` 网关：
    ///     POST   /api/bull/session/create              -> {sessionId}
    ///     POST   /api/bull/session/{id}/bind-skill      {skillId}
    ///     POST   /api/bull/session/{id}/snapshot        {…}
    ///     POST   /api/bull/session/{id}/chat{query}     -> {answer, traceId}
    ///     GET    /api/bull/skill/list                   -> skill 列表
    ///     GET    /api/bull/skill/detail?skillId=       -> 单个 skill
    ///     POST   /api/bull/skill/create | update | delete
    ///     POST   /api/bull/skill/import     GET /api/bull/skill/export?skillId=
    ///
    /// 二级页：**问答** 与 **Skill 管理**（对等网页版的两块能力）。
    /// 原生调用不受 CORS 限制 —— 网页版为此要靠 dsh 放开跨域，这是原生化顺带的好处。
    /// </summary>
    internal sealed partial class MainForm
    {
        // ---- 红色小牛页（原生内嵌）----
        private int _rbTabIndex = -1;
        private SubTabStrip _rbStrip;

        // 问答页
        private ComboBox _rbSkillBox;
        private Label _rbSessionLabel, _rbChatStatus;
        private RichTextBox _rbChat;
        private TextBox _rbInput;
        private Button _rbSend, _rbNewSession;
        private bool _rbBusy;

        // Skill 管理页
        private ListBox _rbSkillList;
        private TextBox _rbSkillName, _rbSkillId, _rbSkillDesc;
        private RichTextBox _rbSkillBody;
        private Label _rbSkillStatus;
        private Button _rbSkillSave, _rbSkillNew, _rbSkillDelete, _rbSkillReload;

        private const string DshBase = "http://127.0.0.1:3080";
        private string _rbSessionId = "";

        /// <summary>
        /// 把一个 GroupBox 改成「随父容器撑满」，并让它的内容表也撑满。
        ///
        /// `UiKit.Group()` 为了让普通页面省心，返回的是 `AutoSize = true` 的
        /// GroupBox + AutoSize 内容表（见 UiKit.cs:113）。但在**需要占满剩余空间**
        /// 的场景（聊天区 / 列表 / 编辑区）里，AutoSize 表中 Percent 行算不出高度，
        /// 控件会退回默认高（实测 RichTextBox 只剩 25px），空白就留在中间。
        ///
        /// 所以凡是「要撑满」的 GroupBox，都过一遍这个方法。
        /// </summary>
        private static void MakeFillGroup(GroupBox g, TableLayoutPanel body)
        {
            g.AutoSize = false;
            g.AutoSizeMode = AutoSizeMode.GrowAndShrink;
            g.Dock = DockStyle.Fill;
            body.AutoSize = false;
            body.Dock = DockStyle.Fill;
        }

        /// <summary>
        /// 加一行并让它**按比例撑满**剩余空间。
        ///
        /// 为什么不能直接用 AddRow：它固定用 `SizeType.AutoSize`，而二级页给的
        /// `stack` 来自 `Stack()`（`AutoSize = true` + `Dock = Top`）—— 在
        /// AutoSize 表里，`Dock = Fill` 与 Percent 行都拿不到高度，控件会被压成
        /// 内容高度，空白就留在了中间（实测：Skill 列表被压成一小块，其余全空）。
        /// 同一个坑项目里已踩过一次，见 StockPage.cs 关于「AutoSize 表里 Percent
        /// 会绕成循环」的注释。
        ///
        /// 用法：调用的那个 stack / body 需要先 `AutoSize = false; Dock = Fill;`。
        /// </summary>
        private static void AddFillRow(TableLayoutPanel t, Control row)
        {
            row.Margin = new Padding(0, 0, 0, 8);
            t.RowStyles.Add(new RowStyle(SizeType.Percent, 100f));
            t.Controls.Add(row);
        }

        #region 红色小牛页

        /// <summary>红色小牛：① 问答（会话 + Skill 选择 + 对话）→ ② Skill 管理（增删改查 / 导入导出）。</summary>
        private Panel BuildRedBullPage()
        {
            var p = NewPage("红色小牛");
            _rbTabIndex = _tabPages.Count - 1;

            var root = new TableLayoutPanel();
            root.Dock = DockStyle.Fill;
            root.Margin = new Padding(0);
            root.ColumnCount = 1;
            // ⚠️ 必须是 **3 行**，与下面 SubTabStrip(this, root, 1, 2, ...) 的
            // barRow=1 / bodyRow=2 对齐（与 StrategyPage 同一约定）。
            // 原来只建了 2 行：标签栏占了 Percent 行，内容容器落到第 3 行
            // （不存在 → 自动追加成 AutoSize），于是内容区只拿到内容高度、
            // 中间留一大片空白 —— 用户反馈的「空间太大」就是这个。
            root.RowCount = 3;
            root.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100f));
            root.RowStyles.Add(new RowStyle(SizeType.AutoSize));       // 行0 状态区
            root.RowStyles.Add(new RowStyle(SizeType.AutoSize));       // 行1 二级标签栏
            root.RowStyles.Add(new RowStyle(SizeType.Percent, 100f));  // 行2 二级页内容
            p.Controls.Add(root);

            var head = Stack();
            TableLayoutPanel b0;
            var g0 = Group("状态", out b0);
            _rbSessionLabel = Lbl("未建立会话");
            _rbSessionLabel.Font = new Font("Microsoft YaHei UI", 10f, FontStyle.Bold);
            AddRow(b0, Row(Lbl("会话"), _rbSessionLabel,
                _rbNewSession = MiniBtn("新建会话", delegate { RbNewSession(); }, 100)));
            AddRow(head, g0);
            g0.Margin = new Padding(0, 0, 0, 4);
            root.Controls.Add(head, 0, 0);

            _rbStrip = new SubTabStrip(this, root, 1, 2, "rb-subtab")
            {
                FixedButtonSize = false,
                BodyAutoScroll = true
            };
            _rbStrip.Add("问答", RbBuildChat);
            _rbStrip.Add("Skill 管理", RbBuildSkills);
            _rbStrip.Select(0);
            return p;
        }

        // ---------------- ① 问答 ----------------

        private void RbBuildChat(TableLayoutPanel stack)
        {
            stack.AutoSize = false;
            stack.Dock = DockStyle.Fill;

            TableLayoutPanel b0;
            var g0 = Group("Skill（决定它以什么角色/规则回答）", out b0);
            _rbSkillBox = new ComboBox();
            _rbSkillBox.Width = 320;
            _rbSkillBox.DropDownStyle = ComboBoxStyle.DropDownList;
            _rbSkillBox.SelectedIndexChanged += delegate { RbBindSkill(); };
            _rbChatStatus = Mute(Lbl("尚未连接 AI 服务"));
            AddRow(b0, Row(Lbl("Skill"), _rbSkillBox,
                MiniBtn("刷新", delegate { RbLoadSkills(); }, 70), _rbChatStatus));
            AddRow(stack, g0);

            // 聊天记录：吃掉中间所有剩余空间（原来塞在 AutoSize 行里被压扁，
            // 空白全留在中间 —— 用户反馈「上面的空间太大」就是这个）
            TableLayoutPanel chatBox;
            var gChat = Group("对话", out chatBox);
            MakeFillGroup(gChat, chatBox);
            _rbChat = new RichTextBox();
            _rbChat.Dock = DockStyle.Fill;
            _rbChat.ReadOnly = true;
            _rbChat.Font = new Font("Consolas", 10f);
            _rbChat.DetectUrls = true;
            _rbChat.BorderStyle = BorderStyle.FixedSingle;
            _rbChat.WordWrap = true;
            AddFillRow(chatBox, _rbChat);
            AddFillRow(stack, gChat);

            TableLayoutPanel b1;
            var g1 = Group("提问", out b1);
            _rbInput = new TextBox();
            _rbInput.Multiline = true;
            _rbInput.Height = 54;
            _rbInput.Dock = DockStyle.Top;
            _rbInput.KeyDown += delegate(object s, KeyEventArgs e)
            {
                if (e.KeyCode == Keys.Enter && e.Control) { e.SuppressKeyPress = true; RbSend(); }
            };
            AddRow(b1, _rbInput);
            AddRow(b1, Row(_rbSend = MiniBtn("发送（Ctrl+Enter）", delegate { RbSend(); }, 150),
                Mute(Lbl("会话每次打开需先「新建会话」；AI 服务在「服务控制台」启动。"))));
            AddRow(stack, g1);      // 输入区贴着底部，高度固定
        }

        // ---------------- ② Skill 管理 ----------------

        private void RbBuildSkills(TableLayoutPanel stack)
        {
            stack.AutoSize = false;
            stack.Dock = DockStyle.Fill;

            var split = new TableLayoutPanel();
            split.Dock = DockStyle.Fill;
            split.ColumnCount = 2;
            // 左列给**具体值**而非 Percent —— AutoSize 表里 Percent 会绕成
            // 「列宽←表宽←子项首选宽」的循环（StockPage.cs 里记过这个坑）。
            split.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 300f));
            split.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100f));
            split.RowStyles.Add(new RowStyle(SizeType.Percent, 100f));
            split.Margin = new Padding(0);

            TableLayoutPanel b1;
            var g1 = Group("Skill 列表", out b1);
            MakeFillGroup(g1, b1);           // 列表要吃掉左列的剩余高度
            _rbSkillList = new ListBox();
            _rbSkillList.Dock = DockStyle.Fill;
            _rbSkillList.SelectedIndexChanged += delegate { RbSelectSkill(); };
            _rbSkillReload = MiniBtn("刷新列表", delegate { RbLoadSkills(); }, 90);
            _rbSkillNew = MiniBtn("新建", delegate { RbNewSkill(); }, 70);
            AddRow(b1, Row(_rbSkillReload, _rbSkillNew));
            AddFillRow(b1, _rbSkillList);      // 列表吃掉剩余高度（原先这里有 bug：加进了一个从未挂载的 lrow）
            split.Controls.Add(g1, 0, 0);

            TableLayoutPanel b2;
            var g2 = Group("内容", out b2);
            MakeFillGroup(g2, b2);           // 同上：规则正文要吃掉右列剩余高度
            _rbSkillName = new TextBox();
            _rbSkillName.Width = 220;
            _rbSkillId = new TextBox();
            _rbSkillId.ReadOnly = true;
            _rbSkillId.Width = 220;
            _rbSkillDesc = new TextBox();
            _rbSkillDesc.Width = 420;
            AddRow(b2, Row(Lbl("名称"), _rbSkillName));
            AddRow(b2, Row(Lbl("说明"), _rbSkillDesc, Mute(Lbl("≤500 字，会显示在列表里"))));
            AddRow(b2, Row(Lbl("SkillId"), _rbSkillId,
                Mute(Lbl("点「新建」后可填；仅字母 / 数字 / 下划线 / 连字符"))));
            _rbSkillBody = new RichTextBox();
            _rbSkillBody.Dock = DockStyle.Fill;
            _rbSkillBody.Font = new Font("Consolas", 9.5f);
            _rbSkillBody.WordWrap = false;
            AddFillRow(b2, _rbSkillBody);     // 规则正文吃掉剩余高度
            _rbSkillStatus = Mute(Lbl("选中列表项后可编辑并保存。"));
            AddRow(b2, Row(_rbSkillSave = MiniBtn("保存", delegate { RbSaveSkill(); }, 80),
                _rbSkillDelete = MiniBtn("删除", delegate { RbDeleteSkill(); }, 80), _rbSkillStatus));
            split.Controls.Add(g2, 1, 0);

            g1.Margin = new Padding(0, 0, 6, 0);
            AddFillRow(stack, split);          // 整块吃掉二级页剩余空间
        }

        // ---------------- 会话 ----------------

        private void RbNewSession()
        {
            if (_rbBusy) return;
            _rbBusy = true;
            SetRbChatStatus("正在建立会话…");
            System.Threading.Tasks.Task.Run(delegate
            {
                string body, err = "";
                bool ok = RbPost("/api/bull/session/create", "{}", 20000, out body);
                string sid = "";
                if (ok)
                {
                    try
                    {
                        var j = new JavaScriptSerializer()
                            .Deserialize<Dictionary<string, object>>(body);
                        sid = J.StrOr(j["sessionId"], "");
                    }
                    catch (Exception ex) { err = ex.Message; }
                }
                else err = body;
                Ui(delegate
                {
                    _rbBusy = false;
                    if (sid == "")
                    {
                        SetRbChatStatus("无法建立会话（AI 服务是否在跑）：" + err);
                        return;
                    }
                    _rbSessionId = sid;
                    _rbSessionLabel.Text = sid.Substring(0, Math.Min(18, sid.Length)) + "…";
                    SetRbChatStatus("会话已建立，请选择 Skill 后提问。");
                    RbChatAppend("系统", "会话已建立：" + sid);
                });
            });
        }

        private void RbBindSkill()
        {
            if (_rbSessionId == "" || _rbSkillBox.SelectedItem == null) return;
            string skillId = RbSelectedSkillId();
            if (skillId == "") return;
            System.Threading.Tasks.Task.Run(delegate
            {
                string body;
                RbPost("/api/bull/session/" + _rbSessionId + "/bind-skill",
                       new JavaScriptSerializer().Serialize(
                    new Dictionary<string, object> { { "skillId", skillId } }), 20000, out body);
                Ui(delegate { SetRbChatStatus("已绑定 Skill：" + _rbSkillBox.SelectedItem); });
            });
        }

        // ---------------- 问答 ----------------

        private void RbSend()
        {
            if (_rbBusy) return;
            string q = (_rbInput.Text ?? "").Trim();
            if (q == "") return;
            if (_rbSessionId == "")
            {
                SetRbChatStatus("请先点「新建会话」。");
                return;
            }
            _rbBusy = true;
            _rbInput.Clear();
            RbChatAppend("我", q);
            SetRbChatStatus("回答中…（大模型可能需要 10~60 秒）");
            System.Threading.Tasks.Task.Run(delegate
            {
                string body, err = "";
                string payload = new JavaScriptSerializer().Serialize(
                    new Dictionary<string, object> { { "query", q } });
                bool ok = RbPost("/api/bull/session/" + _rbSessionId + "/chat", payload, 180000, out body);
                string ans = "";
                if (ok)
                {
                    try
                    {
                        var j = new JavaScriptSerializer()
                            .Deserialize<Dictionary<string, object>>(body);
                        ans = J.StrOr(j["answer"], "");
                    }
                    catch (Exception ex) { err = ex.Message; }
                }
                else err = body;
                Ui(delegate
                {
                    _rbBusy = false;
                    if (ok && ans != "") { RbChatAppend("小牛", ans); SetRbChatStatus("完成。"); }
                    else
                    {
                        RbChatAppend("系统", "【失败】" + err);
                        SetRbChatStatus("请求失败：" + err);
                    }
                });
            });
        }

        // ---------------- Skill 管理 ----------------

        private void RbLoadSkills()
        {
            if (_rbBusy) return;
            _rbBusy = true;
            System.Threading.Tasks.Task.Run(delegate
            {
                string body, err = "";
                bool ok = RbGet("/api/bull/skill/list", 20000, out body);
                Ui(delegate
                {
                    _rbBusy = false;
                    if (!ok) { SetRbSkillStatus("读取 Skill 列表失败：" + err); return; }
                    RbFillSkillList(body);
                });
            });
        }

        private void RbFillSkillList(string json)
        {
            List<string> ids = new List<string>();
            List<string> names = new List<string>();
            try
            {
                var j = new JavaScriptSerializer()
                    .Deserialize<Dictionary<string, object>>(json);
                object arr;
                if (j.TryGetValue("skills", out arr) || j.TryGetValue("data", out arr))
                {
                    var list = arr as System.Collections.IList;
                    if (list != null)
                    {
                        foreach (var it in list)
                        {
                            var d = it as Dictionary<string, object>;
                            if (d == null) continue;
                            // 字段名以 dsh 实际返回为准（实测 /skill/list）：
                            //   skillId / skillName / description / version / strategyType
                            string sid = J.StrOr(d["skillId"], J.StrOr(d["id"], ""));
                            string nm = J.StrOr(d["skillName"], J.StrOr(d["name"], sid));
                            if (sid == "") continue;
                            ids.Add(sid);
                            names.Add(nm + "  [" + sid + "]");
                        }
                    }
                }
            }
            catch (Exception ex) { SetRbSkillStatus("解析列表失败：" + ex.Message); return; }

            string keep = _rbSkillBox.SelectedItem as string;
            _rbSkillList.BeginUpdate();
            _rbSkillList.Items.Clear();
            _rbSkillBox.Items.Clear();
            for (int i = 0; i < ids.Count; i++)
            {
                _rbSkillList.Items.Add(names[i]);          // index 与 ids 一一对应
                _rbSkillBox.Items.Add(names[i]);
            }
            _rbSkillList.EndUpdate();
            if (keep != null && _rbSkillList.Items.Contains(keep))
                _rbSkillList.SelectedItem = keep;
            else if (_rbSkillList.Items.Count > 0)
                _rbSkillList.SelectedIndex = 0;
            SetRbSkillStatus("共 " + ids.Count + " 个 Skill。");
        }

        private string RbSelectedSkillId()
        {
            int i = _rbSkillList.SelectedIndex;
            if (i < 0) return "";
            // 与 RbFillSkillList 的显示格式一致：名称  [skillId]
            string line = _rbSkillList.Items[i] as string;
            if (line == null) return "";
            int a = line.LastIndexOf('['), b = line.LastIndexOf(']');
            return (a >= 0 && b > a) ? line.Substring(a + 1, b - a - 1) : "";
        }

        private void RbSelectSkill()
        {
            string sid = RbSelectedSkillId();
            _rbSkillId.Text = sid;
            _rbSkillId.ReadOnly = true;      // 已有 skill 的 id 不可改（它是文件名）
            if (sid == "") return;
            System.Threading.Tasks.Task.Run(delegate
            {
                string body, err = "";
                bool ok = RbGet("/api/bull/skill/detail?skillId=" + Uri.EscapeDataString(sid), 20000, out body);
                Ui(delegate
                {
                    if (!ok) { SetRbSkillStatus("读取详情失败：" + err); return; }
                    try
                    {
                        var j = new JavaScriptSerializer()
                            .Deserialize<Dictionary<string, object>>(body);
                        object sk;
                        if (j.TryGetValue("skill", out sk) && sk is Dictionary<string, object>)
                        {
                            var sd = (Dictionary<string, object>)sk;
                            // 实测 detail 返回的是 skillName / description / ruleContent
                            //（不是 name / content）—— 写错会让编辑框空白
                            _rbSkillName.Text = J.StrOr(sd["skillName"], J.StrOr(sd["name"], ""));
                            _rbSkillDesc.Text = J.StrOr(sd["description"], "");
                            object content;
                            if (!sd.TryGetValue("ruleContent", out content))
                                content = sd.ContainsKey("content") ? sd["content"] : "";
                            _rbSkillBody.Text = content == null ? "" : content.ToString();
                        }
                        else
                        {
                            _rbSkillName.Text = J.StrOr(j["skillName"], J.StrOr(j["name"], ""));
                            _rbSkillDesc.Text = J.StrOr(j["description"], "");
                            _rbSkillBody.Text = body;
                        }
                        SetRbSkillStatus("已载入 " + sid);
                    }
                    catch (Exception ex) { SetRbSkillStatus("解析失败：" + ex.Message); }
                });
            });
        }

        private void RbNewSkill()
        {
            _rbSkillId.Text = "";
            _rbSkillId.ReadOnly = false;     // 新建时要能填 skillId（validateSkill 要求）
            _rbSkillName.Text = "";
            _rbSkillDesc.Text = "";
            _rbSkillBody.Text = "";
            _rbSkillList.SelectedIndex = -1;
            SetRbSkillStatus("新建模式：填好 SkillId / 名称 / 说明 / 规则正文后点「保存」。");
        }

        private void RbSaveSkill()
        {
            if (_rbBusy) return;
            string sid = (_rbSkillId.Text ?? "").Trim();
            string name = (_rbSkillName.Text ?? "").Trim();
            string content = _rbSkillBody.Text;
            if (name == "") { SetRbSkillStatus("请填写 Skill 名称。"); return; }
            if (sid == "") { SetRbSkillStatus("请填写 SkillId（新建时必填）。"); return; }
            if (content.Trim() == "") { SetRbSkillStatus("规则正文不能为空（validateSkill 要求）。"); return; }
            if (_rbBusy) return;
            _rbBusy = true;
            System.Threading.Tasks.Task.Run(delegate
            {
                // ⭐ 字段名以 dsh 的 validateSkill 为准（bull-store.mjs:52）：
                //   skillId（新建必填，仅字母数字下划线连字符 ≤64）/ skillName（非空 ≤80）
                //   description（≤500）/ ruleContent（**必填**，≤100000）
                // 之前我按 name / content 传，保存会被 validateSkill 拒掉。
                // 同时用字典序列化而不是手拼JSON —— Skill 正文里只要有引号 /
                // 反斜杠 / 换行，手拼出来的就是非法 JSON。
                var arg = new Dictionary<string, object>
                {
                    { "skillName", name },
                    { "ruleContent", content },
                    { "description", _rbSkillDesc.Text },
                };
                string body, err = "";
                bool ok;
                if (sid == "")
                    ok = RbPost("/api/bull/skill/create",
                                new JavaScriptSerializer().Serialize(arg), 20000, out body);
                else
                {
                    arg["skillId"] = sid;     // update 用它定位已有 skill
                    ok = RbPost("/api/bull/skill/update",
                                new JavaScriptSerializer().Serialize(arg), 20000, out body);
                }
                if (!ok) err = body;
                Ui(delegate
                {
                    _rbBusy = false;
                    if (!ok) { SetRbSkillStatus("保存失败：" + err); return; }
                    SetRbSkillStatus("已保存「" + name + "」。");
                    RbLoadSkills();
                });
            });
        }

        private void RbDeleteSkill()
        {
            string sid = (_rbSkillId.Text ?? "").Trim();
            if (sid == "") { SetRbSkillStatus("请先选中要删除的 Skill。"); return; }
            if (UiSync(delegate
            {
                return MessageBox.Show(this, "确认删除 Skill「" + sid + "」？",
                    AppTitle + " - 删除确认", MessageBoxButtons.YesNo,
                    MessageBoxIcon.Warning) == DialogResult.Yes;
            })) RbDeleteSkillInner(sid);
        }

        private void RbDeleteSkillInner(string sid)
        {
            if (_rbBusy) return;
            _rbBusy = true;
            System.Threading.Tasks.Task.Run(delegate
            {
                string body, err = "";
                bool ok = RbPost("/api/bull/skill/delete",
                                new JavaScriptSerializer().Serialize(
                                    new Dictionary<string, object> { { "skillId", sid } }),
                                20000, out body);
                if (!ok) err = body;
                Ui(delegate
                {
                    _rbBusy = false;
                    if (!ok) { SetRbSkillStatus("删除失败：" + err); return; }
                    SetRbSkillStatus("已删除 " + sid);
                    RbLoadSkills();
                });
            });
        }

        // ---------------- 小工具 ----------------

        private void RbChatAppend(string who, string text)
        {
            if (_rbChat == null) return;
            _rbChat.SelectionStart = _rbChat.TextLength;
            _rbChat.SelectionLength = 0;
            if (who == "我") _rbChat.SelectionColor = _cText;
            else if (who == "小牛") _rbChat.SelectionColor = C.DownSoft;
            else _rbChat.SelectionColor = C.UpErr;
            _rbChat.AppendText("[" + DateTime.Now.ToString("HH:mm:ss") + "] "
                               + who + "：" + text + Environment.NewLine + Environment.NewLine);
            _rbChat.SelectionStart = _rbChat.TextLength;
            _rbChat.ScrollToCaret();
        }

        private void SetRbChatStatus(string s)
        {
            if (_rbChatStatus == null) return;
            _rbChatStatus.Text = s;
            _rbChatStatus.ForeColor = s != null && s.StartsWith("完成") ? C.DownSoft : _cSub;
        }

        private void SetRbSkillStatus(string s)
        {
            if (_rbSkillStatus == null) return;
            _rbSkillStatus.Text = s;
        }

        /// <summary>dsh 的 POST（JSON）。失败时把响应当错误信息返回，便于直接展示。</summary>
        private static bool RbPost(string path, string json, int timeoutMs, out string body)
        {
            body = "";
            try
            {
                var req = (HttpWebRequest)WebRequest.Create(DshBase + path);
                req.Method = "POST";
                req.ContentType = "application/json";
                req.Timeout = timeoutMs;
                var bytes = Encoding.UTF8.GetBytes(json ?? "{}");
                req.ContentLength = bytes.Length;
                using (var s = req.GetRequestStream()) s.Write(bytes, 0, bytes.Length);
                using (var resp = (HttpWebResponse)req.GetResponse())
                using (var rd = new StreamReader(resp.GetResponseStream(), Encoding.UTF8))
                {
                    body = rd.ReadToEnd();
                    return true;
                }
            }
            catch (WebException wex)
            {
                var resp = wex.Response as HttpWebResponse;
                body = "HTTP " + (resp == null ? 0 : (int)resp.StatusCode)
                       + " / " + wex.Message;
                return false;
            }
            catch (Exception ex) { body = ex.Message; return false; }
        }

        private static bool RbGet(string path, int timeoutMs, out string body)
        {
            body = "";
            try
            {
                var req = (HttpWebRequest)WebRequest.Create(DshBase + path);
                req.Method = "GET";
                req.Timeout = timeoutMs;
                using (var resp = (HttpWebResponse)req.GetResponse())
                using (var rd = new StreamReader(resp.GetResponseStream(), Encoding.UTF8))
                {
                    body = rd.ReadToEnd();
                    return true;
                }
            }
            catch (WebException wex)
            {
                var resp = wex.Response as HttpWebResponse;
                body = "HTTP " + (resp == null ? 0 : (int)resp.StatusCode)
                       + " / " + wex.Message;
                return false;
            }
            catch (Exception ex) { body = ex.Message; return false; }
        }

        #endregion
    }
}