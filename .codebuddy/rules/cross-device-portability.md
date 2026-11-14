---
description: 跨设备可移植性：产物要拷到别的电脑跑 —— 脚本选型与编码、.ps1 写法、子进程环境、完整性校验、退出码契约、部署纪律、失败反馈
alwaysApply: true
enabled: true
---

# 跨设备可移植性（本项目产物要整目录拷到别的电脑跑）

本仓库的产物会被拷到其他电脑（行尾被转换、编码被转换、目录没拷全是常见坑）。
凡是**要跟 exe 一起分发**的文件，都必须满足下面的要求。

## 一、脚本选型：不要新增 .bat，用 PowerShell

cmd.exe 解析 .bat 有三个「换个设备就炸」的坑：
1. **行尾**：被拷贝 / 压缩 / 网盘传成 LF-only 后，`if (...)` 括号块整体错位，
   报「xxx 不是内部或外部命令」「was unexpected at this time」；
2. **编码**：cmd 按控制台代码页读脚本，UTF-8 中文在 936 机器上字节对齐被打乱，
   同样把语法拆坏；
3. **括号块**：块内任何一行带特殊字符都可能让整块解析失败。

- 要在别的机器上跑的脚本一律 `.ps1`（行尾无关、语法自解释）。
- 仅开发机用的构建脚本（如 `launcher\build_exe.bat`）可以留 `.bat`，
  由 `.gitattributes` 的 `*.bat text eol=crlf` 保证 CRLF。

## 二、.ps1 必须带 UTF-8 BOM

- Windows PowerShell 5.1 没有 BOM 就按**系统 ANSI 代码页**读脚本 → 中文全是乱码，
  甚至因乱码把语法拆坏。
- 保存 `.ps1` 一律 **UTF-8 with BOM**；启动器执行前也会统一补 BOM（`PrepareEnvScript`）。

## 三、.ps1 写法约定

### 不要用内置只读 / 自动变量名
PowerShell 有一批**只读**变量：`$HOME`、`$PID`、`$ERROR` 等，赋值会直接报
“Cannot overwrite variable … because it is read-only or constant”。

- 踩过的坑：脚本里用 `$home` 存「venv 底座 Python 路径」→ 赋值失败 →
  可移植性判定恒为「来自别的电脑」→ **误删了正在使用的虚拟环境**（要重建 venv +
  重装依赖才能恢复）。
- 规矩：局部变量用业务化名字（`$basePy`、`$venvRoot`、`$reqFile`、`$dshDir`），
  不要用 `$home` / `$pid` / `$error` / `$args` 这类名字。

### 外部命令：只看退出码就把输出丢掉
```powershell
function Test-Cmd {
    param([string]$exe, [string[]]$argv)
    & $exe @argv *> $null          # 丢弃全部输出流
    return ($LASTEXITCODE -eq 0)
}
```
- 数组参数要**显式命名传**（`-argv @('-m','pip')`）；位置参数传数组会被当成 spletning
  （`@(...)` 里的元素会散成多个参数）。
- 找可执行文件用 `Get-Command xxx -CommandType Application`，不要用 `where`
  （`where` 会命中 Windows Store 的空壳别名 `WindowsApps\python.exe`，能查到但跑不了）。

### 破坏性操作（删目录 / 删虚拟环境）必须有结论性判据
- **判据不确定 → 一律不动。** 例：venv 可移植性只有在
  「pyvenv.cfg 存在 **且** 有 home 字段 **且** 底座 python.exe 不存在」时才判为不可用并删除重建；
  缺 pyvenv.cfg / 缺 home 字段 → 视为可用，**不删**。
- 删除前先把原因打出来（用户要知道为什么被重建）。

### 退出码是对外契约
- 安装脚本退出码：`0` 成功 / `1` 根目录不对 / `2` 没有 Python / `3` 建虚拟环境失败 /
  `4` 装依赖失败 / `5` 依赖校验失败。
- 启动器侧有对应映射（`StockPoolLauncher.ExitReason`），改退出码语义要同步改映射与
  弹窗文案，否则用户只会看到「未知错误」。

### 脚本要自带完整性标记
脚本头部写一行 `# INTEGRITY: <标记>`；启动器执行前校验（见第六节）。

## 四、子进程一律 UTF-8

- 启动器拉起的 python / pip 子进程必须带 `PYTHONUTF8=1` + `PYTHONIOENCODING=utf-8`。
- 原因：pip 读 `-r requirements.txt` 用的是 **locale 编码**，中文 Windows（GBK）上直接
  `UnicodeDecodeError` 崩掉 —— 装依赖必失败。

## 五、传给外部程序的路径要去掉结尾反斜杠

`powershell -File x.ps1 -Root "C:\path\"` 里的反斜杠引号会被当成转义、吃掉引号 →
必须传不带尾反斜杠的路径。

## 六、完整性校验：文件坏了要明说

- 需要随 exe 分发的脚本 / 配置，文件里放一个**完整性标记**
  （如 `INTEGRITY: STOCKPOOL_ENV_INSTALL_V2`），执行前先校验。
- 校验不过 → 明确报「该文件已损坏或被改写，请重新拷贝整个项目目录」，
  **不要**让用户去猜一堆看不懂的解析报错。

## 七、部署纪律：只换 exe 等于没换

- exe 之外还有必须同步更新的文件。**分发单元**（目标设备要能直接跑，缺一不可）：
  - `stockpool.exe`（发布版）
  - `launcher\install_env.ps1`（环境安装脚本；exe 之外，必须同步更新）
  - `backend_fastapi\`（含 `requirements.txt`）
  - `agent_dsh\`（AI 服务）
- 改了 `launcher\*` 下的脚本或后端入口，**必须让目标设备重新拷贝整个目录**，
  并在交付说明里写清这一点 —— **只换 exe 不换脚本等于没换**。
- **换设备不用人工跑命令**：启动时依赖检查通过后，启动器会补跑一次
  `install_env.ps1 -ConfigOnly`，自愈运行期产物里残留的旧机器绝对路径
  （`.env` 的 `DATA_DIR` / `DATA_SECRET_SEALED`、embeddable Python 的 `._pth`、dsh 插件路径）。
  判据与动作都在脚本里（幂等、只改有结论性判据的项）—— **启动器不要再复制第二份判据逻辑**。
- **Node.js 也自动装**：本机没有 node 时，`install_env.ps1 -RuntimeOnly` 会下官方 Windows
  zip 便携版（约 30MB，免安装器与管理员权限、不写系统 PATH）到 agent_dsh 目录下的 `.node`
  子目录；装不上只警告、不阻断 —— AI 服务（:3080）是可选功能，后端与网页不受影响。
- **路径不要写死在配置里**：dsh 的插件路径原先硬编码成开发机的绝对目录 → 拷到别的电脑就
  静默失效（dsh 照样「启动成功」，只是插件加载不到）。现在由
  `agent_dsh\generate-patch.mjs` 按**当前目录**生成 `cordis.runtime.yml`，启动器拉起 dsh 前
  会自动跑一次。同理，任何新增的「按目录算出来」的配置都要走这个套路，不要把路径写进模板。

## 八、失败反馈要给「可执行的下一步」

- 报错弹窗**不能只给日志尾部**：根因常在最前面（脚本一启动就炸时，尾部只剩善后信息）→
  给「日志开头 + 关键错误行 + 末尾」三段。
- 退出码要映射成人类可读的原因，不要只丢「未知错误」。
- 区分可重试与不可重试：目录 / 文件缺失 → 明确告诉用户重新拷贝；网络 / 杀软拦截 →
  给具体处置步骤。