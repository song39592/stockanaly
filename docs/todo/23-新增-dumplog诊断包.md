# 23 · 新增：dumplog 诊断包（一键导出，供排障）

> 状态：**已完成**（2026-10-05）　|　优先级：高　|　收益 ★★★★☆ / 风险 ★☆☆☆☆　|　依赖 21、22（均已完成）

> **回填实际改动**
> - 新增 `backend_fastapi/dumplog.py`：收集 8 项 → `<data>/logs/dump-<YYYYMMDD-HHMMSS>.zip`
>   —— 两类日志末尾各 500 行、`system.json`（数据目录 + 完整性）、`health.json`（模块挂载错误）、
>   **脱敏** `config.json`、`env.txt`（Python + `pip freeze`）、`chip_rank_status.json`、`meta.json`。
> - `system_routes.py` 新增 **`POST /api/system/dump`**：用 POST 而非 GET ——
>   它会**创建文件**，不该由 GET 触发。命中密钥时返回 500 并说明命中位置。
> - 密钥扫描是**兜底**（不能只靠「记得脱敏」，日志内容是别的模块写的、本模块控制不了）：
>   对每份待写入文本扫 `sk-` / `LLM_API_KEY=` / `LLM_API_KEY_SEALED=` / `DATA_SECRET` / `api_key=`，
>   命中即**拒绝生成整个包**。
>   ⚠️ `sk-` 必须带左边界 `(?<![A-Za-z0-9])` 且长度 ≥16，否则 `task-` / `disk-` / `risk-`
>   这类普通词会被误判，导致正常排障时**永远导不出包**。
> - **启动器「导出诊断包」按钮未做**（按本项建议，等第 26/35 项重新编译 exe 时一起加）。

> **过程中发现并修掉的自身缺陷**
> 初版在拒绝生成时 `logger.error(...)` 把**命中的密钥样本**一并写进了日志 ——
> 于是真实 `backend.log` 被污染，之后每次导出都被拒绝，等于把密钥从一个地方抄到另一个地方。
> 已改为**只记录「哪个文件命中哪类模式」，绝不回显值**（日志与错误返回同样处理）。

## 问题
排障时需要在聊天里来回问「日志发我看看」「数据目录在哪」「装了什么版本」。
现在这些信息分散在：启动器窗口（关掉即失）、数据盘、venv、`.env`。

## 现状可参考的现成接口（都能直接当 dumplog 的数据源）
- `GET /api/system/storage` —— 数据目录信息（启动器 :1745 就在用它）
- `GET /api/system/integrity` —— 启动期算过一次并缓存的数据完整性校验结果（`main.py` 的 `_integrity_state`）
- `/health` —— 模块挂载错误 `_module_errors`（哪个路由模块挂了、为什么）
- `config.llm_key_state()` —— **已设计好**的密钥状态（只返回「已密封/明文/可用」，**不返回密钥本身**）
- `<data>/logs/backend.log`、`uvicorn-error.log` —— 第 22 项产出

## 建议做法
新增 `backend_fastapi/dumplog.py`（或 `tools/dumplog.py`），导出一个 zip 到 `<data>/logs/dump-<YYYYMMDD-HHMMSS>.zip`，包含：
1. `logs/backend.log`（最近 N 行）、`uvicorn-error.log`（最近 N 行）
2. `system.json`：`/api/system/storage` 与 `/api/system/integrity` 的结果
3. `health.json`：模块挂载错误列表
4. `config.json`：**脱敏**后的配置 —— 数据目录路径、LLM 的 `llm_key_state()`（不含密钥）、端口、是否配置 DATA_DIR
5. `env.txt`：`pip freeze` 结果 + Python 版本（即第 20 项锁文件的实际状态）
6. `chip_rank_status.json`：周榜状态（第 00 项修好后，这是最常需要问的东西）

对外提供两种触发方式：
- `GET /api/system/dump`（或 POST）→ 返回生成的文件名
- 启动器加一个「导出诊断包」按钮（需要重新编译 exe，可等第 26/35 项一起做）

## 注意（踩坑点）—— 安全是本项目第一位的
1. **密钥绝对不能进包**：`.env` 原文、`LLM_API_KEY`、DPAPI 密封值一律不写。
   只允许出现 `llm_key_state()` 返回的「是否配置 / 是否密封 / 是否可解开」。
2. **用户数据路径可以写**（本来就是本地诊断），但**不要**打包 DB 文件本身（`stock_history.db` / `bars/` 总共 2.1 GB）。
3. 打包前对文本做一次**扫描校验**：若任一文件命中 `sk-`、`LLM_API_KEY=` 等模式 → **拒绝生成**并报错。这是兜底，不能只靠「记得脱敏」。
4. zip 里不要包含绝对路径里的用户名以外的敏感信息（路径里通常带用户名，可接受）。

## 验收（2026-10-05 实测，经真实 HTTP 调用 `POST /api/system/dump`）
- [x] `<data>/logs/dump-*.zip` 生成 ✅ `dump-20261005-161737.zip`，8 项
- [x] `config.json` 只有密钥状态 ✅ 只有 `key_state{sealed, plain, usable, note}`
      与 `integrity_key{sealed, plain, usable}`，**没有任何密钥值**
      （`crypto.seal_state()` 的 `error` 详情刻意不带 —— DPAPI 异常信息可能含密文片段）
- [x] 全包检索 0 命中 ✅ 用同一套 `_scan` 对包内每个文件复扫，`sk-` /
      `LLM_API_KEY=` / `LLM_API_KEY_SEALED=` / `DATA_SECRET` / `api_key=` 均无命中
- [x] 故意写密钥 → **拒绝生成并报错** ✅ 真实调用返回 HTTP 500，说明命中的文件与模式；
      **未生成任何 zip**
- [x] 包体积合理 ✅ 2.8 KB，无 `.db`、无 `bars`
- [x] `meta.json` 带 git commit ✅ `002f31b`

> 注：验证时被污染的 `backend.log` / `uvicorn-error.log` 已删除重建，
> 临时诊断包与脚本均已清理，未留下垃圾。
