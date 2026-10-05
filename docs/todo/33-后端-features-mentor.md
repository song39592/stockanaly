# 33 · 后端：`features/mentor/`（大佬策略实验室）

> 状态：待办　|　优先级：低-中　|　收益 ★★☆☆☆ / 风险 ★★☆☆☆　|　**依赖第 27 项**

## 问题
大佬策略实验室（LLM 相关）模块混在扁平层。

## 建议目标
```
backend_fastapi/features/mentor/
├─ __init__.py
├─ routes.py      ← 原 mentor_routes.py（含 AI 结果 TTL 缓存 _ai_cache，AI_CACHE_TTL=600）
├─ collectors.py  ← 原 collectors.py（东财摘要等数据采集）
└─ llm_client.py  ← 原 llm_client.py（LLM 调用）
```

## 注意（踩坑点）
1. **`mentor_routes` 的 `_ai_cache`（:36-38）与 `board_service` 的 TTL 是不同实现**，
   且支持 `req.force` 绕过缓存 —— 搬运时不要顺手统一（统一缓存属审计「不建议做」的范畴）。
2. `llm_client` 走 LLM 服务，**超时语义与行情完全不同**（可能分钟级）—— 若第 15 项（httpclient 统一）已完成，
   接入公共 http 时**不要共用默认超时**。
3. LLM 配置依赖 `config.LLM_BASE_URL / LLM_API_KEY / LLM_MODEL`，密钥通过 DPAPI 密封（`config.read_secret`）——
   搬运时**不要在日志里打印密钥**（第 22 项会统一处理日志，本项只保证不新增打印）。
4. `agent_dsh/` 是与 dsh 服务配合的独立 agent（含 `node_modules` 32843 文件）—— **不在本项范围内，不移动**。

## 建议做法
同第 27 项的**兼容转发**模式。

## 验收
- [ ] `features/mentor/` 建立，三个模块迁入
- [ ] 实验室页：AI 分析能发起与返回（无密钥时也能看到明确的「未配置 LLM」提示）
- [ ] `force` 参数能绕过 AI 缓存
- [ ] `/health` 的 `_module_errors` 为空
