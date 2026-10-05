# 22 · 后端：日志落盘到 `<data>/logs/`（现在没有日志目录，全靠 print 到 stderr）

> 状态：**已完成**（2026-10-05）　|　优先级：高　|　收益 ★★★★☆ / 风险 ★★☆☆☆　|　依赖第 21 项（已完成，`logs/` 已就位）

> **回填实际改动**
> - 新增 `backend_fastapi/logutil.py`：具名 logger `stockpool`，**双写** ——
>   `StreamHandler(sys.stderr)`（保持 `[info]`/`[warn]`/`[error]`/`[fatal]` 前缀不变，
>   启动器按流打标签的行为不受影响）+ 两个 `TimedRotatingFileHandler`
>   （按天轮转、保留 7 天（logutil.KEEP_DAYS，与 cleanup.DEFAULT_RETAIN_DAYS 同口径）、UTF-8）：`<data>/logs/backend.log`（INFO+）
>   与 `<data>/logs/uvicorn-error.log`（WARNING+，与 uvicorn 错误日志同名便于对照）。
>   日志文件不可写时**降级为只写 stderr**，绝不让日志拖垮启动。
> - `main.py`：11 处 `print(..., file=sys.stderr)` 全部换成 `logger.info/warning/critical`；
>   `_record_error` 改用 `logger.warning(..., exc_info=True)`，**堆栈随之进文件**
>   （原来只是 `traceback.print_exc()` 打到 stderr，窗口一关就没了）。
>   `sys` / `traceback` 两个导入已随之移除。
> - **启动器零改动**：走注意 3 的**方案 A** —— 旧路径 `backend_fastapi/uvicorn-error.log`
>   保持不变（仍由 uvicorn 的重定向写入），新位置只是副本。

> **为什么用具名 logger 而不是 root（关键，别改回去）**
> uvicorn 启动时用 `--log-config` 调 `logging.config.dictConfig()`。本项目的
> `uvicorn_logging.json` 带 `disable_existing_loggers: false`，且只声明
> `uvicorn` / `uvicorn.error` / `uvicorn.access` 三个 logger、**不声明 root**；
> 该配置又发生在导入应用**之前**。所以这里自建具名 logger 并设 `propagate=False`
> 不会被它清掉。若哪天改成直接配 root，就会被 uvicorn 的 dictConfig 冲掉。

## 问题
全仓**没有使用 `logging` 模块**，也没有日志目录。后端靠 `print(..., file=sys.stderr)` 输出，
启动器把 stderr 流**实时捕获到窗口**——关掉窗口日志就没了，事后无法回溯。

## 现状
| 位置 | 说明 |
|---|---|
| `main.py:91,112,125,134,142,145,152,156,175` | 11 处 `print(..., file=sys.stderr)`，前缀 `[info]` / `[warn]` / `[fatal]`，**无时间戳、无级别过滤** |
| `backend_fastapi/uvicorn_logging.json` | 通过 `--log-config` 传给 uvicorn，把启动横幅从 stderr 改到 stdout（见 StockPoolLauncher.cs:2187-2189） |
| `backend_fastapi/uvicorn-error.log` | 启动器「读取错误日志」按钮直接读这个文件（:2634），**路径写死在 `backend_fastapi/`** |
| `E:\stockanaly-data` | **没有任何 logs 目录** |

## 建议做法
1. 接入 `logging`：在 `main.py` 或新建 `logutil.py` 里配置，输出**同时**到 stderr（保持启动器实时显示）
   和 `<data>/logs/backend.log`（按天轮转，保留 N 天）。
2. 把 uvicorn 的错误输出也落到 `<data>/logs/uvicorn-error.log`（保留**旧路径同名文件**做兼容，见注意 3）。
3. 把 `main.py` 里那 11 处 `print(file=sys.stderr)` 换成 `logger.info/warning/error`。

## 注意（踩坑点）
1. **别动 stderr 输出** —— 启动器依赖流来给日志打 `[err]` 标签（:2183-2186 有明确注释：真正的告警/异常写 stderr 是对的）。
   做法是「**双写**」，不是「改成只写文件」。
2. 日志文件**必须**做轮转与保留天数上限，否则 `<data>` 会被撑满 —— 清理交给第 24 项（定时清理）。
3. 启动器 `ReadErrorLog()`（:2634）硬编码读 `backend_fastapi/uvicorn-error.log`：
   - 方案 A：让 uvicorn 继续写旧路径，新路径作为副本 → 启动器不用改
   - 方案 B：改启动器路径 → 需要重新编译 exe
   **建议先走 A**（零改动），等第 26/35 项重新编译 exe 时再顺手切到 B。
4. 日志里**绝不能写密钥**：`config.py` 有 `LLM_API_KEY` 与 DPAPI 密封值，打日志时只输出「已配置/未配置」。
5. 数据盘在仓库外、且可能被用户改到别的盘 → 日志路径必须走 `storage` 的常量，不能硬编码。

## 验收（2026-10-05 实测）
- [x] `<data>/logs/backend.log` 产生，含时间戳与级别 ✅
      `2026-10-05 16:04:44 [fatal] 启动被拒绝：程序已在运行，同一数据目录只能有一个实例。`
- [x] 启动器日志窗口**仍能**实时看到输出 ✅ 双写生效，stderr 形态与改造前完全一致
      （`[info]` / `[warn]` 前缀 + 堆栈），启动器按流打 `[err]` 的行为未受影响
- [x] 启动器「读取错误日志」按钮仍能工作 ✅ 方案 A 兼容，旧路径文件未动
- [x] 日志中检索不到密钥 ✅ 对 `sk-` / `LLM_API_KEY` / `DATA_SECRET` 检索，**全部未命中**
- [x] `/health` 正常 ✅ `ok=True`、`_module_errors` 为空
- [x] 异常堆栈能落盘 ✅ 模拟故障后 `backend.log` 里能看到完整
      `Traceback (most recent call last)` 及其调用链
- [x] 分级生效 ✅ `uvicorn-error.log` 只收 WARNING+，检索 `[info]` **无命中**

> 注：验证时产生的日志内容已清理（那次干净启动本就没有可输出的日志，
> 文件是空的后被我主动删除），`logs/` 目录保留为空，下次启动自动重建。
