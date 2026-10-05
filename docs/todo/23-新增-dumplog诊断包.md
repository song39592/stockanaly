# 23 · 新增：dumplog 诊断包（一键导出，供排障）

> 状态：待办　|　优先级：高　|　收益 ★★★★☆ / 风险 ★☆☆☆☆　|　**依赖 21（logs/ 目录）与 22（日志落盘）**

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

## 验收
- [ ] 触发后 `<data>/logs/dump-*.zip` 生成
- [ ] 解开检查：`config.json` 里**只有**密钥状态、**没有**密钥值
- [ ] 在全包内检索 `sk-`、`LLM_API_KEY=`、`LLM_API_KEY_SEALED=` 均为 0 命中
- [ ] 故意把一个密钥写进日志 → 触发导出应**失败并报错**（脱敏扫描生效）
- [ ] 包体积合理（不含 DB / bars）
