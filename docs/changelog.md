# 变更日志

所有重要变更记录于此，格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [v0.2.0] - 2026-09-02

### 新增与修复

- 新增无未来函数的策略评分引擎与 1/3/5/10 数据日复盘。
- 新增分析快照持久化、备份恢复和 Agent 结构化分析快照。
- 个股调研升级为带证据 ID、来源链接、覆盖状态和事件标签的可审计响应。
- Skill 升级至 v2，修复编辑正文丢失、重复 ID、启动加载竞争和删除绑定残留。
- Markdown 改用 DOMPurify 清洗；FastAPI 增加输入校验与标准状态码。
- 启动脚本移除 `D:\ai` 硬编码，新增后端环境初始化脚本与自动化测试。

## [v0.1.0] - 2026-08-15

### 新增

- **frontend**：股票池追踪单页（Excel 导入、变动分析、连续在榜、ECharts 图表、数据管理、个股详情、个股 AI 调研弹窗）。
- **backend_fastapi**：FastAPI 个股调研后端（巨潮公告 + 机构调研纪要 + 东财新闻 → LLM → markdown），`/api/stock/research` + `/health`，12h 缓存。
- **agent_dsh**：红色小牛问答 Agent（基于 deepseek-harness），两个自定义 Tool（`getStockPoolSnapshot`、`fetchStockResearch`）、Skill 角色规则库、`/api/bull/*` REST 网关。
- 前端红色小牛 UI：悬浮按钮、聊天窗、Skill 管理（新建/编辑/删除/导入/导出）。
- 工程化：目录重构（frontend / backend_fastapi / agent_dsh / docs / examples）、统一 `.gitignore`、Git 规范与文档。
