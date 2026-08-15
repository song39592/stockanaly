# 个股时效性调研服务（FastAPI 后端）

纯本地后端：采集「巨潮公告 + 机构调研纪要 + 东财财经新闻」，把最新公开资料喂给大模型，
返回结构化的 markdown 调研文本。密钥放 `.env`，不硬编码。

## 一、环境要求

- Python 3.10+（本机可用 `py -3.11`）
- 已内置虚拟环境 `venv/`，依赖见 `requirements.txt`（fastapi / uvicorn / akshare / requests / beautifulsoup4 / pydantic / python-dotenv）

## 二、配置密钥

1. 复制 `.env.example` 为 `.env`；
2. 填写真实值（密钥只存本机，不要提交）：
   ```
   LLM_BASE_URL=https://api.deepseek.com/v1
   LLM_API_KEY=sk-你的密钥
   LLM_MODEL=deepseek-chat
   PORT=8000
   ```

## 三、启动

推荐一键启动：双击项目根目录的 `启动系统.bat`，它会自动启动后端（最小化窗口）并打开 `stock-pool.html`，
且后端已在运行时不会重复启动。

手动启动（调试用）：

```bash
cd D:/ai/backend
venv/Scripts/python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
```

## 四、接口

```
GET  /health              → {"ok":true,"llm_ready":true/false}
POST /api/stock/research  → 请求体 {"code":"300209","name":"行云科技"}
                            返回 {"ok":true,"markdown":"...","cached":false}
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
3. 拼 prompt（含原始资料 + 四板块结构 + 禁预测/禁荐股）→ 调 LLM → 追加固定风险声明 → 返回 markdown。

## 六、说明

- 数据源均为公开接口，反爬/改版可能导致个别来源失败，已做优雅降级，不会导致整体报错。
- 「东方财富股吧高赞帖子」为可选原型逆向，尚未接入（akshare 1.18 已移除股吧函数），后续可单独扩展。
