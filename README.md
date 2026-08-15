# 股票池智能分析平台

个人本地原型项目：Excel 导入股票池 → 每日变动/连续在榜分析 → 个股 AI 调研 → 「红色小牛」多角色问答 Agent。

## 模块组成

| 目录 | 说明 | 技术栈 |
|---|---|---|
| `frontend/` | 前端单页（Excel 导入、ECharts 图表、个股调研弹窗、红色小牛 UI） | 原生 HTML/JS |
| `backend_fastapi/` | FastAPI 调研后端（个股公告/新闻/调研采集 + LLM 调研接口） | Python 3.10+ / FastAPI |
| `agent_dsh/` | 红色小牛 Agent（自定义 Tool 插件、Skill 角色规则库） | Node 22+ / deepseek-harness |
| `docs/` | 需求、变更日志、开发规范 | — |
| `examples/` | 示例 .skill 规则、示例股票池 Excel | — |

## 目录结构

```
stock-pool-agent/
├── frontend/index.html        # 前端页面
├── backend_fastapi/           # FastAPI 后端
├── agent_dsh/                 # dsh Agent（plugins/ + skills_library/）
├── docs/                      # 文档
├── examples/                  # 示例文件
├── .gitignore
├── README.md
└── LICENSE
```

## 快速启动

1. **FastAPI 后端**：双击根目录 `启动系统.bat`（会同时打开 `frontend/index.html`）。
2. **dsh Agent 服务**：双击 `agent_dsh/start-dsh.bat`（看到 `dsh web: http://127.0.0.1:3080` 即成功）。
3. 打开 `frontend/index.html`，右下角 🐂 开始对话；调试看 `http://127.0.0.1:3080` webui。

> 首次使用前：`backend_fastapi/.env` 与 `agent_dsh/.env` 填好 DeepSeek 密钥（模板见各自的 `.env.example`）。

## 文档

- [agent_dsh/README.md](agent_dsh/README.md) — 红色小牛 Agent 架构与接口
- [backend_fastapi/README.md](backend_fastapi/README.md) — FastAPI 后端接口
- [docs/requirement.md](docs/requirement.md) — 需求文档
- [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) — 提交规范 / 分支策略 / 版本回退
- [docs/changelog.md](docs/changelog.md) — 版本变更日志

## 版本管理

见 [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md)。分支：`main`（稳定 + tag）→ `dev`（日常开发）→ `feature/xxx`（功能分支）。
