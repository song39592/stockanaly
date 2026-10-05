# 22 · 后端：日志落盘到 `<data>/logs/`（现在没有日志目录，全靠 print 到 stderr）

> 状态：待办　|　优先级：高　|　收益 ★★★★☆ / 风险 ★★☆☆☆　|　**依赖第 21 项（先有 logs/ 目录）**

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

## 验收
- [ ] 后端启动后 `<data>/logs/backend.log` 产生，含时间戳与级别
- [ ] 启动器日志窗口**仍能**实时看到输出（双写生效）
- [ ] 启动器「读取错误日志」按钮仍能工作（走兼容路径）
- [ ] 日志中检索不到任何 `sk-` 开头的密钥或 DPAPI 密文
- [ ] `/health` 正常；故意触发一次异常能在日志文件里找到堆栈
