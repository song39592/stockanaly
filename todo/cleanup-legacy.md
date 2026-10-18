# 后期待办：清理历史残留

## 背景
项目早期靠一批 `.bat` 脚本启动（后端 / dsh / 打开网页），并散落了一些临时/示例文件。
现在 **C# 原生 launcher（`股票池追踪系统.exe`，由 `launcher/StockPoolLauncher.cs` 编译）
已完整接管启动职责**——`StockPoolLauncher.cs:5` 注释原话：「内嵌拉起 FastAPI 后端（:8000）
与 dsh AI 服务（:3080），不再依赖 .bat」。launcher 自己负责：
- 后端：`_backend.Args = "-m uvicorn main:app --host 127.0.0.1 --port 8000"`（`:1935`）+ 健康检查 `WaitHealth`；
- dsh：`StartDsh()`（`:2012`），`_dsh.Args = "...dsh web --patch cordis.runtime.yml"`（`:1945`），`HealthUrl :3080`；
- 密钥配置 UI（`:1150` 起的「密钥配置」组，直接编辑 `backend_fastapi/.env` 与 `agent_dsh/.env`）。

因此早期脚本大概率是**被取代的历史残留**，但删除前必须确认没有别的调用点、且 launcher
的行为确实等价（尤其「旧后端占用 8000 端口时清理旧进程」这一段）。

## 范围与清单
| 路径 | 类型 | 判断 | 风险 |
|------|------|------|------|
| `启动系统.bat` | 旧总启动器 | launcher 已取代（含后端/dsh/网页/健康检查/密钥） | **中**：bat 里有一段「kill 占用 8000 的旧 python 进程」逻辑，需先确认 launcher 是否也处理端口冲突，否则删后可能出现「端口被旧进程占住、launcher 起不来」 |
| `examples/临时条件股20260813.xls`、`examples/临时条件股20260814.xls` | 用户临时导出的条件选股结果 | 非源码/非示例；全仓仅 `README.md`、`docs/changelog.md` 文本提及，**无代码 import** | **低**：删前确认无 import、并同步更新文档里的引用 |
| `backend_fastapi/run_backend.bat`、`start_backend.bat`、`_restart_backend.bat` | 旧后端启动/重启脚本 | `启动系统.bat` 曾调用 `run_backend.bat`；launcher 现直接用 uvicorn 命令行，疑似冗余 | **中**：确认全仓无其他处调用 |
| `agent_dsh/start-dsh.bat` | 旧 dsh 启动脚本 | launcher 现直接命令行启 dsh，疑似冗余 | **低-中** |
| `backend_fastapi/清理日志与缓存.bat` | 维护脚本（清日志/缓存） | 功能独立、可能仍有人手动用 | **待确认**（保留优先，除非确认无用） |

## 明确不在本次范围（避免误删在用物）
- `launcher/build_exe.bat`（编译 EXE，**刚用过**）、`launcher/install_env.bat`（装环境，**在用**）；
- `股票池追踪系统.exe`（launcher 编译产物，在用）；
- `__pycache__/`、`*.pyc`、`*.pyi`、`.env`/`.env.bak`、`.venv`/`venv` 等（常规 gitignore/依赖，单独处理，不在「历史残留」清理内）。

## 步骤
1. **等价性核实**：在 launcher 代码里搜「8000 端口占用 / 旧进程清理 / 端口冲突」处理，
   确认 launcher 起后端前会清理或避开占用 8000 的旧进程；若无，先给 launcher 补上再删 bat。
2. **调用点核实**：全仓 grep 上述每个 `.bat` 的文件名，确认除 `启动系统.bat` 外没有被别处引用
   （CI / 文档 / 其他脚本）。
3. **删 `examples/`**：删除两个临时 `.xls`；检查 `README.md`、`docs/changelog.md` 中对 `examples`
   的引用并同步更新（若无实质引用则说明是临时文件、可忽略）。
4. **删确认无用的旧脚本**：`backend_fastapi/run_backend.bat`、`start_backend.bat`、`_restart_backend.bat`、
   `agent_dsh/start-dsh.bat`（以第 2 步核实结果为准；`清理日志与缓存.bat` 确认无用再删）。
5. **删 `启动系统.bat`**：放在最后，确保前面等价性已确认。
6. **提交**：按分支策略，新分支（如 `chore/cleanup-legacy`）提交，不直接进 `main`。

## 验证
- 双击 `股票池追踪系统.exe`：后端 :8000 + dsh :3080 正常起来、网页能开（确认 launcher 行为未因删脚本而变）。
- 若之前有「旧进程占 8000」的习惯场景，复现一次确认 launcher 能正常接管。
- 仓库文件数明显减少，功能零回归。

## 风险 / 注意
- **删 `启动系统.bat` 前必须确认 launcher 行为等价**：否则一部分用户仍依赖双击这个 bat，
  删了会断他们的启动方式（尤其是端口冲突清理那段）。
- **别误删 `install_env.bat` / `build_exe.bat`**：它们是新流程的必需品，不是残留。
- 文档引用要同步，避免留下「指向已删文件」的死链。
