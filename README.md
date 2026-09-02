# 股票池智能分析平台

个人本地原型项目：Excel 导入每日股票池 → 变动/连续在榜分析 → 个股 AI 调研 → 「红色小牛」多角色问答 Agent。
仅本地运行，不公网部署；所有 LLM 调用走 DeepSeek，密钥只放本机 `.env`。

## 功能特性

- **股票池追踪**（前端单页，页面标题「股票池追踪系统」）：Excel（.xlsx / .xls / GBK 文本）导入、按日期保存；
  概览卡片（总数 / 新增入池 / 出池 / 覆盖天数）、日期选择器联动、新增/出池/持续在池变动分析、
  连续在榜天数（≥7 红、3–6 橙、1–2 灰）、TOP 20 排行榜、ECharts 图表（行业分布 / 规模趋势 / 入池出池）、
  数据管理（导出备份 / 恢复 / 按天删除 / 清空）、个股详情时间轴。
- **个股 AI 调研**：输入股票代码/名称，后端采集巨潮公告 + 机构调研纪要 + 东财财经新闻，
  喂给大模型生成结构化 markdown 调研报告（含 12 小时缓存与固定风险声明）。
- **红色小牛问答 Agent**：右下角悬浮 🐂 聊天窗，基于 dsh（deepseek-harness）运行，
  读取页面股票池快照、按当前 Skill 角色规则回答，可链式调用个股调研工具；
  支持多套 Skill 角色（通用分析 / 短线猎手 / 巴菲特价值投资），完整 Agent 轨迹可在 dsh webui 回看调试。

## 系统架构

```
frontend/index.html（原生 HTML/JS 单页，浏览器本地 localStorage 存数据）
   │  Excel 导入 / 图表 / 个股调研弹窗 / 红色小牛聊天窗
   ├──────────────► backend_fastapi（FastAPI，:8000）
   │                  GET  /health
   │                  POST /api/stock/research  → 采集 + LLM → markdown
   │
   └──────────────► agent_dsh（dsh 服务，Node，:3080，webui 同端口）
                      自定义 Tool：getStockPoolSnapshot（读快照）
                                  fetchStockResearch（回连 :8000 调研）
                      Skill 规则引擎：skills_library/*.skill 按会话注入
                      REST 网关：/api/bull/*（会话/快照/对话/Skill CRUD）
```

前端不直接调大模型：所有 LLM / 工具调用 / 会话轨迹均由 dsh 托管；个股调研由 FastAPI 后端托管。

## 模块组成

| 目录 | 说明 | 技术栈 |
|---|---|---|
| `frontend/` | 前端单页（Excel 导入、ECharts 图表、个股调研弹窗、红色小牛 UI） | 原生 HTML/JS（CDN 引入 SheetJS / ECharts / marked） |
| `backend_fastapi/` | FastAPI 调研后端（个股公告/新闻/调研采集 + LLM 调研接口） | Python 3.10+ / FastAPI / akshare |
| `agent_dsh/` | 红色小牛 Agent（自定义 Tool 插件、Skill 角色规则库） | Node 22+ / @deepseek-ai/dsh |
| `docs/` | 需求、变更日志、开发规范 | — |
| `examples/` | 示例股票池 Excel（.xls） | — |

## 目录结构

```
stock-pool-agent/                 # 项目根目录（本机为 D:\ai）
├── 启动系统.bat                  # 一键启动：后端 + dsh + 打开前端
├── frontend/index.html           # 前端页面（唯一文件）
├── backend_fastapi/              # FastAPI 后端（main.py / collectors.py / config.py）
│   ├── start_backend.bat         # 单独启动后端
│   └── .env.example              # 密钥模板（复制为 .env）
├── agent_dsh/                    # dsh Agent（plugins/ + skills_library/）
│   ├── start-dsh.bat             # 单独启动 dsh 服务
│   ├── cordis.patch.yml          # 插件挂载 + 角色 persona 配置
│   └── .env.example              # DEEPSEEK_API_KEY 模板
├── docs/                         # requirement / changelog / CONTRIBUTING
├── examples/                     # 示例股票池 Excel
├── .gitignore
├── README.md
└── LICENSE                       # MIT
```

## 快速启动

1. **首次准备**：
   - 后端：`backend_fastapi/.env` 填 `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL`（模板见 `.env.example`）；
   - dsh：`agent_dsh/.env` 填 `DEEPSEEK_API_KEY`（模板见 `.env.example`），并已执行
     `cd D:\ai\agent_dsh && npm install`（依赖 `@deepseek-ai/dsh`）。
2. **一键启动**：双击根目录 `启动系统.bat` —— 自动拉起 FastAPI 后端（:8000）与 dsh 服务（:3080，
   已运行则跳过），随后打开 `frontend/index.html`。
3. **单独启动 / 调试**：
   - 后端：`backend_fastapi\start_backend.bat`，或手动
     `D:/ai/backend_fastapi/venv/Scripts/python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000`；
   - dsh：`agent_dsh\start-dsh.bat`，看到 `dsh web: http://127.0.0.1:3080` 即成功。
4. 打开 `frontend/index.html`，右下角 🐂 开始对话；调试 Agent 轨迹看 `http://127.0.0.1:3080` webui。

> 启动脚本与 `cordis.patch.yml` 中硬编码了 `D:\ai` 绝对路径，若移动项目目录需同步修改这些文件。

## 端口与接口速览

| 服务 | 端口 | 主要接口 |
|---|---|---|
| backend_fastapi | 8000 | `GET /health`；`POST /api/stock/research`（`{"code","name"}` → markdown） |
| agent_dsh（dsh webui + REST） | 3080 | `GET /`（webui）；`/api/bull/session/*`、`/api/bull/skill/*`（会话/快照/对话/Skill CRUD） |

## 内置 Skill 角色

| skillId | 名称 | 说明 |
|---|---|---|
| `general` | 通用分析 | 默认；客观归纳股票池数据，禁编造、禁荐股 |
| `short-trader-01` | 短线猎手 | 强势板块跟踪，只筛选连续入池 ≥3 天标的 |
| `bft`（`warren_buffett_01`） | 巴菲特价值投资 | 能力圈 / 护城河 / 安全边际，拒绝题材炒作 |

## 文档

- [agent_dsh/README.md](agent_dsh/README.md) — 红色小牛 Agent 架构与接口
- [backend_fastapi/README.md](backend_fastapi/README.md) — FastAPI 后端接口
- [docs/requirement.md](docs/requirement.md) — 需求文档
- [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) — 提交规范 / 分支策略 / 版本回退
- [docs/changelog.md](docs/changelog.md) — 版本变更日志

## 版本管理

见 [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md)。分支：`main`（稳定 + tag）→ `dev`（日常开发）→ `feature/xxx`（功能分支）。

## 注意事项

- **仅限本地原型**：deepseek-harness（dsh）为预览版，接口可能变动，禁止公网暴露服务。
- 快照与会话→Skill 绑定存 dsh 进程内存，重启丢失（Skill 文件本身落盘持久化）。
- 未开启 shell / 文件执行能力；自定义工具只做 HTTP（回连 FastAPI）与内存读取。
- 数据源均为公开接口，反爬/改版可能导致个别来源失败，后端已做优雅降级，不影响整体功能。

## 策略评分与复盘（v2）

- `frontend/analysis-engine.js` 计算市场温度、候选评分、正反证据与数据质量；生成信号时只读取目标日及之前的数据。
- 历史复盘按后续第 1/3/5/10 个已导入数据日的同股价格计算，缺价、出池和样本不足均不填补；不包含交易成本、复权和停牌处理。
- 每日分析快照保存在 `stockPool.analysis.v1`，导出备份时会与股票池数据一并导出。
- Skill v2 支持 `strategyType`、`parameters`、`riskRules` 和 `ruleContent`，仍兼容旧版 Skill。
- 个股调研同时返回证据 ID、来源链接、发布时间、采集时间、事件标签和数据源覆盖状态。

测试：`cd agent_dsh && npm test`；后端：`cd backend_fastapi && venv\Scripts\python.exe -m unittest -v`。

## License

[MIT](LICENSE)
