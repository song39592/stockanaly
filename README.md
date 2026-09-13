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
- **盘面及板块分析**（`frontend/market-sector.html`，从股票池页顶部「📊 盘面及板块分析」按钮进入）：
  ① 外围环境（美股 / 亚太韩日 / 港股 / 大宗商品 / 费城半导体）、② 大盘资金（主力净流向 / 特大单方向 / 两市成交 / 涨跌家数）、
  ③ 板块β（行业与概念资金流 Top10 + 申万一级行业涨跌）、④ 连板梯队（连板结构 + 晋级率）、⑤ 大面股（炸板 / 跌停）；
  顶部可选择交易日：①③ 支持历史交易日（部分口径有历史源）、④⑤ 按所选交易日回溯、② 暂无历史源恒为实时快照；
  对应后端 `GET /api/market/*`，多源公开接口 + 单源失败自动降级 + 进程内缓存。
- **股票估值计算**（`frontend/valuation.html`，从股票池页顶部「📈 股票估值计算」按钮进入）：
  输入 6 位代码即自动抓取股价 / 总股本 / 期初净利润（TTM 归母优先，缺失时退回年报扣非 / 年报归母），
  输入后即时显示股票名称与股价用于确认（`GET /api/stock/quote`）；
  用两段法（前 5 年净利润贴现 + 永续增长，贴现率 10%）给出乐观 / 中性 / 悲观三档每股价值、
  股价/价值（低估程度）、预测 N 年价与收益率，并逐步展示计算过程；对应 `POST /api/stock/valuation`。
  机构预测第 N 年净利润为可选：提供时三档为乐观 ×1.5 / 中性 ×1 / 悲观固定 5%，未提供则兜底 25% / 10% / 5%。
- **SCR 选股（筹码体系）**（`frontend/chip-scr.html`，从股票池页「筹码体系 · SCR 选股」进入）：
  导入行情软件导出的多期「临时条件股YYYYMMDD.xls」（文件名日期自动解析、可在页面手改），
  跨期合并做周级三档分类：磨主峰（连续 N 期全勤 ∩ 流通市值 100–800 亿 ∩ PE>0）/ 向下破位离榜 / 启动型离榜；
  阈值可调，点「🔄 刷新计算」手动触发；计算需**最近 5 周**数据（按周归组，周中执行时最新一期为上一周，
  缺失任一期的数据会直接报错并列出缺口，不做静默跳过）；对应 `POST /api/chip/scr/analyze` 等接口，
  原始文件与计算结果分别存放于 `backend_fastapi/chip_data/raw` 与 `chip_data/processed`。

## 系统架构

```
frontend/index.html（原生 HTML/JS 单页，浏览器本地 localStorage 存数据）
   │  Excel 导入 / 图表 / 个股调研弹窗 / 红色小牛聊天窗
   ├──────────────► backend_fastapi（FastAPI，:8000）
   │                  GET  /health
   │                  POST /api/stock/research  → 采集 + LLM → markdown
   │                  GET  /api/market/*        → 盘面及板块（外围/资金/板块β/连板/大面）
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
├── frontend/                     # index.html（股票池）/ market-sector.html（盘面及板块分析）/ mentor-lab.html（大佬策略实验室）/ valuation.html（股票估值计算）/ chip-scr.html（SCR 选股）
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

## 🔑 API Key 配置位置

项目有**两套互相独立**的 LLM 配置，改一处不影响另一处（这是最常踩的坑）：

| 用途 | 配置文件（相对项目根目录） | 键名 | 模板 |
|---|---|---|---|
| 个股 AI 调研 · 盘面及板块分析 · 大佬策略实验室（FastAPI 后端 :8000） | `backend_fastapi/.env` | `LLM_API_KEY`（另需 `LLM_BASE_URL`、`LLM_MODEL`） | `backend_fastapi/.env.example` |
| 红色小牛问答 Agent（dsh 服务 :3080） | `agent_dsh/.env` | `DEEPSEEK_API_KEY` | `agent_dsh/.env.example` |

本机绝对路径：

```
C:\Users\Admin\Desktop\stockanaly-main\backend_fastapi\.env   →  LLM_API_KEY
C:\Users\Admin\Desktop\stockanaly-main\agent_dsh\.env         →  DEEPSEEK_API_KEY
```

要点：

- 后端只要**兼容 OpenAI 协议**的平台都能接入：改 `LLM_BASE_URL` + `LLM_API_KEY` + `LLM_MODEL` 三项即可。
  **三项必须来自同一平台**，混用会返回 401（典型坑：Key 取自腾讯云 TokenHub，地址却填成混元老平台）。
- 两边填的 Key 可以不同；**只填一边时只有对应功能可用**（「个股调研报错、红色小牛正常」通常就是后端这处没填）。
- `.env` 已被 `.gitignore` 忽略；**`.env.example` 会被提交，只能放占位符，切勿写入真实密钥**。
- 修改 `.env` 后必须**重启对应服务**才生效（后端：关掉后端窗口后重新运行 `启动系统.bat`）。
- 自查：`http://127.0.0.1:8000/health` 的 `llm_ready` 为 `true` 且 `llm_problem` 为空；
  调用报错时错误信息会附带上游原始说明，便于判断是哪家平台的 Key 有问题。
- 常用平台配置对照：

  | 平台 | `LLM_BASE_URL` | `LLM_MODEL` 示例 |
  |---|---|---|
  | DeepSeek 官方 | `https://api.deepseek.com/v1` | `deepseek-chat` |
  | 腾讯云 TokenHub（广州） | `https://tokenhub.tencentmaas.com/v1` | `deepseek-v4-flash` / `deepseek-v4-pro` / `hy3` / `glm-5.3` / `kimi-k3` |
  | 腾讯云 TokenHub（新加坡） | `https://tokenhub-intl.tencentmaas.com/v1` | 同上（需为对应地域开通） |
  | 腾讯混元（老平台） | `https://api.hunyuan.cloud.tencent.com/v1` | `hunyuan-turbos-latest` |

  > TokenHub 与混元是两个平台，**Key 不可通用**；TokenHub 还分地域，广州 Key 调新加坡域名会 401。
  > 红色小牛（dsh）走 provider 机制，默认固定 DeepSeek 官方，不支持任意 OpenAI 兼容地址。
- Key 申请：DeepSeek https://platform.deepseek.com/api_keys ｜ 腾讯云 TokenHub https://console.cloud.tencent.com/tokenhub/apikey

## 端口与接口速览

| 服务 | 端口 | 主要接口 |
|---|---|---|
| backend_fastapi | 8000 | `GET /health`；`POST /api/stock/research`（`{"code","name"}` → markdown）；`POST /api/stock/valuation`（估值计算）；`GET /api/market/*`（盘面及板块五类数据） |
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
