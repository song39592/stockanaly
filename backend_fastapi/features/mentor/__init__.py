# -*- coding: utf-8 -*-
"""大佬策略实验室（第 33 项）。

    routes.py       <- 原 mentor_routes.py  HTTP 出口（15 条路由，前缀 /api/mentor）
    store.py        <- 原 mentor_store.py   本地库（人物 / 素材 / 技能 / 评估）
    collectors.py   <- 原 collectors.py     东财摘要等数据采集
    llm_client.py   <- 原 llm_client.py     LLM 调用封装

## 文档漏列了一个模块：store.py

原待办的建议目标只列了 `routes` / `collectors` / `llm_client` 三个，
**漏了 `mentor_store.py`**（321 行）。它显然属于本功能：
- `main.py:30` 模块级 import，`:175` 在 try 里调 `mentor_store.init_db()`，
  失败时以「大佬策略实验室（本地库初始化）」为 label 记入 `_module_errors`；
- `mentor_routes.py:14` 与 `test_mentor_store.py:8` 都 import 它。

本项按四模块搬（`store.py`），否则会在顶层留下一个孤立文件。

## 踩坑点四条的处置

1. ⭐ **原文档这条写错了位置**：`mentor_routes` 里**没有** `_ai_cache`，
   全仓也搜不到 `AI_CACHE_TTL`（第 33 项写文档时它还在别的模块里）。
   真正的 `_ai_cache` / `AI_CACHE_TTL = 600` 在 **`features/market/routes.py:37-39`**，
   是**盘面页**的 AI 缓存，与 `board_service._TTL_HOURS`（DB 持久化）是两套不同实现 ——
   **两者在第 28 项（features/market）就已按"不统一"的原则原样保留**，本项无需处理。
   本模块（实验室页）**根本没有 AI 缓存**，`call_llm` 每次都真调。
2. **`llm_client` 的超时语义保持独立**：`call_llm(prompt, timeout=180)` **显式传 timeout**
   给 `core.httpclient.post`（后者的 timeout 是必填参数，正是为了防止误用行情口径）。
   本项只改 `import config` 的指向，**不动 timeout**，仍与第 15 项统一后的公共 http 正确协作。
3. **不新增任何密钥打印**：`config.LLM_API_KEY` 只出现在 Authorization 请求头里。
   搬运后全程只验证「是否已配置」，不输出明文。
4. **`agent_dsh/` 不存在**（原文档提到它含 node_modules 32843 文件）——
   本项无对象、也未移动任何目录。
"""
