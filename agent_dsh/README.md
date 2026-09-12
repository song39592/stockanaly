# 红色小牛问答助手（基于 deepseek-harness / dsh）

右下角悬浮红色小牛，点击弹出聊天窗。红色小牛是一个跑在 **dsh**（DeepSeek 开源 agent 框架）上的 Agent：
读取页面股票池快照、按当前选中的 **Skill 角色规则库** 回答，并能调用个股调研接口做多工具链式调用；
完整 Agent 轨迹（工具入参/出参、Skill 注入、LLM 原始 IO）可在 dsh 自带 webui 回看调试。

## 一、架构与数据流

```
frontend/index.html（前端 UI）
   │  ①创建会话  ②上传快照  ③发送问题
   ▼
dsh 服务（Node，127.0.0.1:3080，webui 同端口）
   ├─ Tool1 getStockPoolSnapshot  读前端上传的股票池快照（内存，按会话绑定）
   ├─ Tool2 fetchStockResearch    回连 FastAPI /api/stock/research 拉个股调研
   ├─ Skill 规则引擎              加载 agent_dsh/skills_library/*.skill，按会话注入 systemPrompt
   └─ agent loop → DeepSeek LLM
   ▲
   │  ④markdown 回答 + traceId
frontend 用 marked 渲染
```

前端不直接调大模型，所有 LLM / 工具调用 / 会话轨迹都由 dsh 托管。

## 二、前置条件

- Node.js 22+
- 已执行 `cd D:\ai\agent_dsh && npm install`（安装 `@deepseek-ai/dsh`，走 npmmirror 镜像）
- `D:\ai\agent_dsh\.env` 已填 `DEEPSEEK_API_KEY`（凭据，勿提交；模板见 `.env.example`；baseURL 默认 `https://api.deepseek.com`）
  > 这是**红色小牛专用**的密钥。FastAPI 后端（个股 AI 调研 / 盘面分析 / 策略实验室）读的是另一套：
  > `backend_fastapi/.env` 中的 `LLM_API_KEY`，两处需分别配置，互不影响。

## 三、启动

1. **FastAPI 后端**：双击根目录 `启动系统.bat`（会同时打开 `frontend/index.html`），
   或手动 `D:/ai/backend_fastapi/venv/Scripts/python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000`。
2. **dsh 服务**：双击 `D:\ai\agent_dsh\start-dsh.bat`（等于 `cd D:\ai\agent_dsh && npx dsh web --patch cordis.patch.yml`）。
   看到 `dsh web: http://127.0.0.1:3080` 即成功。
3. 打开 `frontend/index.html`，点右下角 🐂 开始对话。

## 四、调试（看 Agent 轨迹）

1. 浏览器打开 `http://127.0.0.1:3080`（dsh webui）。
2. 在聊天窗发问后，每条回答下方有 `traceId`（= 会话 id），在 webui 找到对应会话即可看到：
   - Agent 拿到了什么股票池快照；
   - 调用了哪个 Tool、入参出参是什么；
   - 当前注入的 Skill 规则完整内容；
   - LLM 原始输入输出。
3. 对比调试：同一个股票池，切换两套 Skill，看输出差异。

## 五、Skill 规则（多角色）

Skill v2 增加 `strategyType`、`parameters` 和 `riskRules`。编辑器通过详情接口读取完整正文；重复 ID 返回 409，导入会在写盘前整体校验，并且至少保留一个 Skill。启动时自动生成当前目录对应的 `cordis.runtime.yml`，项目可移动目录。

- 位置：`agent_dsh/skills_library/*.skill`（JSON 文件，自带 `general.skill` 即最简格式范例）。
- 在聊天窗顶部下拉切换 Skill（绑定到当前会话，切换后下一轮生效）。
- 聊天窗「⚙」打开 Skill 管理：新建 / 编辑 / 删除 / 导入 .skill / 导出 .skill，全部通过 dsh `/api/bull/skill/*` 接口。
- 内置三套角色：默认「通用分析」（`general.skill`）、「短线猎手」（`short-trader-01.skill`）、
  「巴菲特价值投资」（`bft.skill`）。
- Skill 文件在 dsh 重启时自动加载；运行中也可在 UI 里导入。

## 六、自定义插件（位于 `agent_dsh/plugins/`）

| 文件 | 作用 |
|---|---|
| `tool-stockpool-snapshot.mjs` | Tool1 `getStockPoolSnapshot`：读会话绑定的快照（无参，从 `exec.agent.session` 推导） |
| `tool-stock-research.mjs` | Tool2 `fetchStockResearch`：回连 FastAPI 调研接口，失败降级为错误文本 |
| `role-skills.mjs` | Skill 加载/播种 + 全局角色规则段兜底 |
| `bull-http.mjs` | `/api/bull/*` REST 网关 + 会话/Agent 生命周期 + 快照存储 |
| `bull-store.mjs` | 共享内存状态 + Skill 文件读写（非插件） |

对外接口（前端调用）：

```
POST /api/bull/session/create             → {sessionId}
POST /api/bull/session/{id}/snapshot      → 上传快照
POST /api/bull/session/{id}/chat          → {query} → {answer, traceId}
POST /api/bull/session/{id}/bind-skill    → {skillId}
GET  /api/bull/skill/list                 → skill 列表
POST /api/bull/skill/create|update|delete → 增改删
POST /api/bull/skill/import               → 上传 .skill
GET  /api/bull/skill/export?skillId=      → 下载 .skill
```

## 七、关键约束与原型局限

- deepseek-harness 为预览版，接口可能变，**仅限本地原型，禁止公网暴露**。
- 快照存 dsh 进程内存，重启丢失（前端每次提问都会重新上传，无感）。
- 会话→Skill 绑定存内存，重启后需重新选择（Skill 文件本身落盘持久化）。
- 未开启 shell / 文件执行能力；自定义工具只做 HTTP（回连 FastAPI）与内存读取。

## 八、换模型

默认用 DeepSeek V4（`deepseek-v4-flash`）。若要换，编辑 `cordis.patch.yml` 加一条覆盖：

```yaml
- id: agent-default-model
  config:
    provider: deepseek-official
    model: deepseek-chat
```
