---
description: 写 PowerShell 脚本（.ps1）的硬性约定：内置只读变量名、外部命令与退出码、破坏性操作的判据
alwaysApply: true
enabled: true
---

# PowerShell 脚本写法约定（launcher\install_env.ps1 等）

## 不要用内置只读 / 自动变量名
- PowerShell 有一批**只读**变量：`$HOME`、`$PID`、`$ERROR` 等，赋值会直接报
  “Cannot overwrite variable … because it is read-only or constant”。
- 踩过的坑：脚本里用 `$home` 存「venv 底座 Python 路径」→ 赋值失败 → 可移植性判定恒为「来自别的电脑」
  → **误删了正在使用的虚拟环境**（要重建 venv + 重装依赖才能恢复）。
- 规矩：局部变量用业务化名字（`$basePy`、`$venvRoot`、`$reqFile`、`$dshDir`），
  不要用 `$home` / `$pid` / `$error` / `$args` 这类名字。

## 外部命令：只看退出码就把输出丢掉
```powershell
function Test-Cmd {
    param([string]$exe, [string[]]$argv)
    & $exe @argv *> $null          # 丢弃全部输出流
    return ($LASTEXITCODE -eq 0)
}
```
- 数组参数要**显式命名传**（`-argv @('-m','pip')`）；位置参数传数组会被当成 splatting（`@(...)` 里的元素会散成多个参数）。
- 找可执行文件用 `Get-Command xxx -CommandType Application`，不要用 `where`
  （`where` 会命中 Windows Store 的空壳别名 `WindowsApps\python.exe`，能查到但跑不了）。

## 破坏性操作（删目录 / 删虚拟环境）必须有结论性判据
- **判据不确定 → 一律不动。** 例：venv 可移植性只有在
  「pyvenv.cfg 存在 **且** 有 home 字段 **且** 底座 python.exe 不存在」时才判为不可用并删除重建；
  缺 pyvenv.cfg / 缺 home 字段 → 视为可用，**不删**。
- 删除前先把原因打出来（用户要知道为什么被重建）。

## 退出码是对外契约
- 安装脚本退出码：`0` 成功 / `1` 根目录不对 / `2` 没有 Python / `3` 建虚拟环境失败 /
  `4` 装依赖失败 / `5` 依赖校验失败。
- 启动器侧有对应映射（`StockPoolLauncher.ExitReason`），
  改退出码语义要同步改映射与弹窗文案，否则用户只会看到「未知错误」。

## 脚本要自带完整性标记
- 脚本头部写一行 `# INTEGRITY: <标记>`；启动器执行前校验（详见 `cross-device-portability.md`）。
