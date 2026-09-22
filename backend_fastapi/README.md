# 个股时效性调研服务（FastAPI 后端）

纯本地后端：采集「巨潮公告 + 机构调研纪要 + 东财财经新闻」，把最新公开资料喂给大模型，
返回结构化的 markdown 调研文本。密钥放 `.env`，不硬编码。

## 一、环境要求

- Python 3.10+（本机可用 `py -3.11`）
- 项目虚拟环境使用 `.venv/`，依赖见 `requirements.txt`（fastapi / uvicorn / akshare / requests / beautifulsoup4 / pydantic / python-dotenv）

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
.venv/Scripts/python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
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
POST /api/stock/valuation → 股票估值（简化 DCF：前 5 年 + 永续，乐观/中性/悲观三情景）
                            请求体 {"code":"600519","net_profit_forecast":1200}；只传 code 会自动抓取
                            股价（新浪）、总股本（腾讯）、期初净利润（东财，TTM 归母 → 年报扣非 → 年报归母）
                            net_profit_forecast 为可选，不传则三档按兜底 25% / 10% / 5% 计算
GET  /api/stock/quote     → 按代码查名称与当前股价（供输入代码后即时确认，不拉财务数据）
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

## 七、股票池历史、K线与消息面

**模块分工**（股价已独立拆分，便于页面 / 估值 / 回测复用）：

| 模块 | 职责 |
|---|---|
| `db.py` | 共享 SQLite 基础设施：连接、库路径、建表调度、幂等加列 |
| `price_store.py` | **行情存储与复权计算**，纯数据、不发网络请求；对外入口 `load_bars(code, adjust, as_of)` |
| `price_service.py` | 行情采集：日 K 三口径、复权因子、除权明细，含数据源降级 |
| `history_store.py` / `history_service.py` | 股票池快照、消息面与后台同步任务编排 |

**数据落盘口径**：库里只存 **不复权原始价** 与 **后复权因子 `hfq_factor`**，复权价一律现算：

```text
hfq(t)        = raw(t) × hfq_factor(t)
qfq(t, as_of) = raw(t) × hfq_factor(t) ÷ hfq_factor(as_of)
```

`as_of` 是复权基准日，缺省为区间内最后交易日（等价于常规前复权）；**回测应传回测起点**，
使历史价格不含未来信息，且同一段历史每次回测结果一致。不存 `qfq_factor` 的原因正是它
以「最新日」为基准、每天都在变。实测 `raw × hfq_factor` 与数据源 hfq 价格偏差 **0.0000%**。

**同步与访问**：股票池页面确认导入后，把当天快照写入本地 SQLite，并在后台只为池内股票增量补齐
最近两年的日 K（三口径）。东方财富行情不可用时自动降级腾讯行情；复权因子与除权明细来自新浪，
且默认只在首次采集（仅在发生除权时才需刷新）。单只股票失败不会阻塞整批导入。

点击股票可查看日 K、MA5/10/20/60、成交量、入池/出池标记、公告及新闻时间轴，以及历次在池区间。
行情在导入后后台更新，消息面在首次打开个股时按需抓取并缓存 12 小时。

**数据存放位置**：所有运行期数据集中在一个数据根目录，便于备份与迁移。
**默认为「程序上一级的 `stockanaly-data`」**，即与仓库并列：

```text
<上一级>/
├── stockanaly-main/     ← 程序
└── stockanaly-data/     ← 默认数据目录
    ├── stock_history.db   股票池快照 / 消息面 / 同步任务（体积小）
    ├── mentor_lab.db      大佬策略实验室
    ├── bars/
    │   ├── bars_YYYY.db   日K 原始价，按年分片（5000 只约 250 MB/年）
    │   └── factors.db     复权因子 + 除权除息明细（全量，行数少）
    └── chip/              SCR 选股数据（raw 原始文件、processed 计算结果、meta.json）
```

**日K 按年分片**：单文件体积可控、便于备份与归档（老年份可只读），跨年查询由 `price_store`
按年份逐库读取后合并排序。表使用 `WITHOUT ROWID`（主键即聚簇数据，省一份索引），
连接启用 `mmap_size` / `cache_size` 调优。库里**只存不复权原始价**，
复权价由 `hfq_factor` 现算——这比存三套复权价节省约 2/3 空间。

解析顺序为：`STOCK_DATA_DIR` > `.env` 的 `DATA_DIR` > 默认（程序上一级的 `stockanaly-data`）。
**启动时会自动把项目内旧位置的数据复制到新目录**（只复制、不删除源文件）。这些数据不提交到 Git。

```text
GET  /api/system/storage            查看当前数据目录与各项占用
POST /api/system/storage            修改数据目录（写入 .env，重启后生效）
GET  /api/system/integrity          重新校验数据完整性（签名 / 写入时间 / 结构版本）
```

**数据可信原则**：**丢数据可重拉，错误数据不可接受**——可用性可以让步，正确性不能让步。
完整说明见 `docs/database.md` 第八节。

**完整性校验**：每个库都有一张 `_meta` 表，记录结构版本与「程序最后写入时间」并做 HMAC 签名；
写事务提交时自动刷新（各 store 无需关心）。校验为**只读**操作，可发现有人绕过程序直接改库：
签名不匹配、或文件 mtime 明显晚于记录时间（容差 300 秒）都会报出来。
日 K 还额外有**按股指纹**（`_digests`，覆盖全部行情字段），能定位到具体哪只股票被改动。

发现日 K 不可信时**直接全量重抓该股并整体替换**（先抓取、后写入，失败则保留原数据），
绝不展示可疑数据；其余库只报告、不自动删除。密钥首次运行自动生成并写入 `.env`，
Windows 下由 DPAPI 密封（绑定本机 + 当前用户，不提供恢复码）。

```text
POST /api/history/pool/import       保存每日股票池并可启动行情同步
GET  /api/history/sync/{job_id}     查询后台同步进度
GET  /api/history/stock/{code}      查询个股 K线、消息和入池轨迹
```

## 八、说明

- 数据源均为公开接口，反爬/改版可能导致个别来源失败，已做优雅降级，不会导致整体报错。
- 「东方财富股吧高赞帖子」为可选原型逆向，尚未接入（akshare 1.18 已移除股吧函数），后续可单独扩展。

## 九、盘面及板块分析（`market_service.py`）

启动器原生「盘面及板块」标签页（`launcher/MarketPage.cs`；原前端页 `frontend/market-sector.html` 已删除）由 `GET /api/market/*` 五个接口驱动：
外围环境、大盘资金、板块β、连板梯队、大面股。

- 数据源：新浪行情（外围指数 / 大宗商品）、新浪指数（两市成交额）、乐咕乐股（涨跌家数）、
  同花顺（行业 / 概念 / 个股资金流、大单追踪）、申万（一级行业实时行情）、东方财富（大盘资金流、涨停 / 炸板 / 跌停池）。
- 降级策略：任一来源失败只写入响应的 `errors` 字段，其余模块照常返回；
  大盘资金在东财接口不可用时自动切换为同花顺汇总口径，并在 `main_flow.source` 中说明。
- 缓存：进程内 60–120 秒，`?force=1` 强制刷新；`limit-up` / `big-loss` 的 `date` 参数可指定 `YYYYMMDD`，
  留空时自动回溯到最近一个有涨停数据的交易日。
