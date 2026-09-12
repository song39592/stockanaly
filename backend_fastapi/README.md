# 个股时效性调研服务（FastAPI 后端）

纯本地后端：采集「巨潮公告 + 机构调研纪要 + 东财财经新闻」，把最新公开资料喂给大模型，
返回结构化的 markdown 调研文本。密钥放 `.env`，不硬编码。

## 一、环境要求

- Python 3.10+（本机可用 `py -3.11`）
- 已内置虚拟环境 `venv/`，依赖见 `requirements.txt`（fastapi / uvicorn / akshare / requests / beautifulsoup4 / pydantic / python-dotenv）

## 二、配置密钥

> 注意：这里（`backend_fastapi/.env`）是**后端专用**的一套配置，键名 `LLM_API_KEY`。
> 「红色小牛」用的是另一套（`agent_dsh/.env` 的 `DEEPSEEK_API_KEY`），两者互不影响——
> 只填了 dsh 那套时，本后端会因读到占位符而调用失败（`GET /health` 的 `llm_problem` 会说明原因）。

1. 复制 `.env.example` 为 `.env`；
2. 填写真实值（密钥只存本机，不要提交）：
   ```
   LLM_BASE_URL=https://api.deepseek.com/v1
   LLM_API_KEY=sk-你的密钥
   LLM_MODEL=deepseek-chat
   PORT=8000
   ```

## 三、启动

推荐一键启动：双击项目根目录的 `启动系统.bat`，它会自动启动后端（最小化窗口）并打开 `frontend/index.html`，
且后端已在运行时不会重复启动。

手动启动（调试用）：

```bash
cd D:/ai/backend_fastapi
venv/Scripts/python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
```

## 四、接口

```
GET  /health              → {"ok":true,"llm_ready":true/false}
POST /api/stock/research  → 请求体 {"code":"300209","name":"行云科技"}
                            返回 markdown + evidence + coverage + data_as_of
GET  /api/market/global   → 外围环境（美股 / 亚太 / 港股 / 大宗商品 / 费城半导体）
GET  /api/market/capital  → 大盘资金（主力净流向 / 特大单 / 两市成交 / 涨跌家数）
GET  /api/market/sectors  → 板块β（行业与概念资金流 Top10、申万一级行业）
GET  /api/market/limit-up → 连板梯队（连板结构 + 晋级率，date 可选）
GET  /api/market/big-loss → 大面股（炸板池 + 跌停池，date 可选）
                            以上盘面接口均支持 ?force=1 跳过进程内缓存
```

测试：
```bash
curl "http://127.0.0.1:8000/health"
curl -X POST "http://127.0.0.1:8000/api/stock/research" \
  -H "Content-Type: application/json" \
  -d "{\"code\":\"300209\",\"name\":\"行云科技\"}"
```

## 五、内部流程

1. 内存缓存（单股 12 小时）命中直接返回；
2. 采集（每个来源失败只降级、不崩溃）：
   - 公司基础信息（akshare，失败静默跳过）
   - 巨潮公告近 2 个月（akshare 拉列表 → 过滤程序性公告 → 重点公告抓正文做摘要压缩）
   - 机构调研纪要（从公告「调研活动」抓正文问答）
   - 东财财经新闻近 30 天（去重）
   - 请求间强制 sleep，防反爬
3. 拼 prompt（证据按不可信输入隔离 + 四板块结构 + 禁预测/禁荐股）→ 调 LLM → 追加固定风险声明。
4. 返回 markdown 以及可审计证据清单：证据 ID、来源链接、发布时间、采集时间、事件标签和覆盖状态。

首次使用或虚拟环境失效时运行 `setup_backend.bat`；启动脚本使用当前目录，不依赖固定盘符。

## 六、大佬策略实验室

从股票池页面顶部进入“大佬策略实验室”。它支持导入 PDF、TXT、Markdown 教学资料，按页保留 PDF 证据位置，并按以下流程管理策略知识：

1. AI 提炼资料或每日观点；
2. 人工批准、驳回或直接修订结构化内容；
3. 仅使用已批准内容生成候选 Skill；
4. 用股票池历史数据回测，并人工审批评估结果；
5. 审批通过后晋级为活动版本，再发布到红色小牛。

扫描版 PDF 如果没有文字层会标记为需要 OCR，不会伪造提炼结果。当前迭代方式是“知识库 + 规则版本 + 人工反馈”的可审计闭环，不会直接修改大模型权重。

## 七、说明

- 数据源均为公开接口，反爬/改版可能导致个别来源失败，已做优雅降级，不会导致整体报错。
- 「东方财富股吧高赞帖子」为可选原型逆向，尚未接入（akshare 1.18 已移除股吧函数），后续可单独扩展。

## 八、盘面及板块分析（`market_service.py`）

前端「盘面及板块分析」页（`frontend/market-sector.html`）由 `GET /api/market/*` 五个接口驱动：
外围环境、大盘资金、板块β、连板梯队、大面股。

- 数据源：新浪行情（外围指数 / 大宗商品）、新浪指数（两市成交额）、乐咕乐股（涨跌家数）、
  同花顺（行业 / 概念 / 个股资金流、大单追踪）、申万（一级行业实时行情）、东方财富（大盘资金流、涨停 / 炸板 / 跌停池）。
- 降级策略：任一来源失败只写入响应的 `errors` 字段，其余模块照常返回；
  大盘资金在东财接口不可用时自动切换为同花顺汇总口径，并在 `main_flow.source` 中说明。
- 缓存：进程内 60–120 秒，`?force=1` 强制刷新；`limit-up` / `big-loss` 的 `date` 参数可指定 `YYYYMMDD`，
  留空时自动回溯到最近一个有涨停数据的交易日。
