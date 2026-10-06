# 33 · 后端：`features/mentor/`（大佬策略实验室）

> 状态：**已完成**（2026-10-06）　|　优先级：低-中　|　收益 ★★☆☆☆ / 风险 ★★☆☆☆
> 依赖第 27 项（已完成）

## 回填实际改动

```
backend_fastapi/features/mentor/
├─ __init__.py
├─ routes.py       ← 原 mentor_routes.py  HTTP 出口（312 行，15 条路由，前缀 /api/mentor）
├─ store.py        ← 原 mentor_store.py   本地库：人物/素材/技能/评估（321 行）
├─ collectors.py   ← 原 collectors.py     东财摘要等数据采集（229 行）
└─ llm_client.py   ← 原 llm_client.py     LLM 调用封装（51 行）
```

顶层保留 4 个**别名转发**（`sys.modules[__name__] = _impl`）：
`mentor_routes.py` / `mentor_store.py` / `collectors.py` / `llm_client.py`。

### ⚠️ 文档漏列了一个模块：`store.py`

原待办的建议目标只列了 `routes` / `collectors` / `llm_client` 三个，
**漏了 `mentor_store.py`**（321 行）。它显然属于本功能：

- `main.py:30` 模块级 import，`:175` 在 try 里调 `mentor_store.init_db()`，
  失败时以「大佬策略实验室（本地库初始化）」为 label 记入 `_module_errors`；
- `mentor_routes.py:14` 与 `test_mentor_store.py:8` 都 import 它。

本项按**四模块**搬，否则会在顶层留下一个孤立文件。

### 函数体零改动 —— difflib 逐行核对过

**7 个文件**共 20 行差异，**全部是 import 行**（0 行非 import）：

| 文件 | 改动 |
|---|---|
| `routes.py` | `config` → `core`；`mentor_store` → 同包 `store`；`llm_client` → 同包 |
| `collectors.py` | `from market_service import _ak` → `from features.market.service import _ak`（第 28 项已搬） |
| `llm_client.py` | `config` → `core`（`from core import httpclient` 已正确） |
| `store.py` | `config` → `core` |
| **`features/history/service.py`** | `from collectors import ...` → `from features.mentor.collectors import ...` |
| **`features/market/routes.py`** | `from llm_client import ...` → `from features.mentor.llm_client import ...` |
| **`features/stock/routes.py`** | `from collectors import (` / `from llm_client import ...` → 改指 `features.mentor.*` |

**后 3 个是已搬文件**（第 28/29/30 项）。它们本来引用的是顶层转发；本项把它们一起改到
规范位置，理由是：这些文件**已经在 `features/` 里**，再指向顶层转发等于在新架构内部
留了个**反向依赖**。改动只有 3 行，趁本项一起清掉比留到后面便宜。

## 踩坑点四条的处置

1. ⚠️ **原文档这条写错了位置，已确认并处置**：
   `mentor_routes` 里**没有** `_ai_cache`，全仓也搜不到 `AI_CACHE_TTL`。
   真正的 `_ai_cache` / `_ai_cache_lock` / `AI_CACHE_TTL = 600` 在
   **`features/market/routes.py:37-39`** —— 是**盘面页**的 AI 缓存，
   与 `board_service._TTL_HOURS`（DB 持久化、跨进程有效）是**两套不同实现**。
   两者在**第 28 项（features/market）**就已按「不统一」的原则原样保留，本项无需处理。
   **本模块（实验室页）根本没有 AI 缓存**，`call_llm` 每次都真调 —— 所以原验收里
   「`force` 参数能绕过 AI 缓存」这条**不属于本项**，改为对第 28 项做回归（见下）。
2. **`llm_client` 的超时语义保持独立** ✅ `call_llm(prompt, timeout=180)` **显式传 timeout**
   给 `core.httpclient.post`（后者的 timeout 是必填参数，正是为了防止误用行情口径）。
   本项只改 `import config` 的指向、**一个字没动 timeout**，仍与第 15 项统一后的公共 http
   正确协作。实测签名搬家前后都是 `(prompt: str, timeout: int = 180) -> str`。
3. **不新增任何密钥打印** ✅ `config.LLM_API_KEY` 只出现在 Authorization 请求头里。
   验证手段：把真实密钥取出来在启动日志里全文检索 —— **0 处命中**。
   搬运全程只验证「是否已配置」，不输出明文。
4. **`agent_dsh/` 不存在** —— 原文档提到它含 `node_modules` 32843 文件；
   实测该目录**在本仓库里不存在**，本项无对象、也未移动任何目录。

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
- [x] `features/mentor/` 建立，**四个**模块迁入 ✅（文档只列了 3 个，`store.py` 是补上的）
- [x] 实验室页：AI 分析能发起与返回 ✅ **真实调了一次 LLM**：`call_llm('只回复两个字：收到')`
      **2.5 秒**返回 `'收到'`（端到端通了，不只是接口 200）。
      另外 `GET /api/mentor/list` `ok=true`（1 个人物）、
      `GET /api/mentor/state?mentor_id=...` `ok=true`
      （返回 `materials` / `daily_views` / `skill_versions` / `evaluations`）
- [x] ~~`force` 参数能绕过 AI 缓存~~ → **本项无此功能**（缓存属盘面页，见踩坑点 1），
      改为**对第 28 项做回归**，结果如下
- [x] `/health` 的 `_module_errors` 为空 ✅ `ok=True`、**11/11** 路由挂载、stderr **无任何告警**

## 附：对第 28 项「盘面 AI 缓存 + force」的回归（本项改了它的 import）

| 请求 | 结果 | 耗时 |
|---|---|---|
| `force=false` 第 1 次 | `ok=true` `cached=False` | **52.9s**（真调 LLM） |
| `force=false` 第 2 次 | `ok=true` `cached=True` | **0.0s**（缓存命中，瞬时） |
| `force=true` | `ok=true` `cached=False` | **54.8s**（绕过缓存，真调） |

三连结果把缓存语义钉死了：**缓存还在、`force` 还能绕过、且 LLM 链路在搬家后仍然通**
（本项改了 `features/market/routes.py` 的 `llm_client` 指向，这一项是它的回归保护）。
顺带确认 `features/market/routes.py` 的 `_ai_cache`（进程内、600s）与
`board_service._TTL_HOURS`（DB 持久化、跨进程）仍是**两套独立实现**，未被统一。

## 验收（其余项，2026-10-06 实测）

- [x] **函数体零改动** ✅ difflib 对比 HEAD：7 个文件 20 行差异全是 import，0 行非 import
- [x] 基线逐项对照 **零差异** ✅ `STATE_DIR` / `router prefix` / **15 条路由清单** /
      `mentor_store` 44 个公开名 / `collectors` 26 个公开名 / `llm_client` 5 个公开名 /
      `call_llm` 签名 / LLM 配置状态（只比「是否已配」）
- [x] 4 个转发与实现**是同一对象** ✅ 全部 `is` 为 True；
      `collectors` 的私有名 / 模块级状态（`ak` / `httpclient`）照常可见
- [x] 13 个模块全部导入成功 ✅ 含 4 个转发、3 个已搬文件、`main`、`test_mentor_store`
- [x] 踩坑点 2（超时语义）✅ `call_llm` 签名 `timeout: int = 180` 搬家前后一致，
      `llm_client` 只改了 `import config`
- [x] 踩坑点 3（不打印密钥）✅ 真实密钥在启动日志里全文检索 **0 处命中**
- [x] 全仓 .py 三引号成对 + `ast.parse` **全部通过**；52 个单元测试全绿；`.env` 未被改动
- [x] HTTP 路径零变化 ✅ prefix 仍是 `/api/mentor`

## 记录

- 2026-10-06 执行，位置为阶段 C 第 22 步（排在 32 之后）。
- 本项是「按功能搬家」这一轮的**倒数第二项**。流程沿用第 29~32 项的做法：
  所有 Python 文件生成与替换都走脚本 + 脚本内双校验（三引号成对 + `ast.parse`）。
  转发生成器这次**预检了实现文件存在性**（第 32 项踩过「转发名当实现名」），一次通过。
- **本项的额外工作量来自"被搬模块早已被别的已搬文件引用"**：
  `collectors` 与 `llm_client` 是跨功能共享的（个股页、股票池历史、盘面页都用），
  3 个已搬文件共 4 处引用要一并改指向。**这类"反向依赖"最好在搬家当期清掉**，
  拖到后面会让转发文件长期挂着。
