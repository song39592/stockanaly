# 跨设备运行修复（launcher / install_env）—— 待办与落地盘点

## 背景
目标设备跑不起来，根因两个（2026-10-15 已定位并实测）：
1. `install_env.bat` 经拷贝/压缩传输后变成 **LF-only 行尾** → cmd 解析错位 → `'3.9' is not recognized`（把同构 bat 转 LF-only 后精确复现）。
   `.gitattributes` 的 `*.bat text eol=crlf` **只在 Git 检出时生效**，拷贝/网盘/压缩都不会转行尾。
2. 目标设备**无 Python** → 脚本退出码 2（脚本本就只「找」不「装」，属设计如此，非 bug）。

## 已落地（2026-10-15 实施 + 重编 exe 316928 + 本机验证）
- [x] **C# 行尾自愈**：`RunInstaller` 执行前把 bat 规范化为 CRLF 写临时副本再跑（`NormalizeBatLineEndings`，字节级补 CR，不动编码）。
      本机对照：LF-only 原文件报大量 `not recognized`，规范化后 stderr 干净。→ 比建议1的「分发时转 CRLF」更彻底（不依赖分发环节）。
- [x] **C# `FindPython`** 增加 `.venv\python.exe` 候选，能识别 embeddable 装配出的 Python。
- [x] **`install_env.bat` 自动下载 embeddable**：`:auto_embeddable` 找不到任何 Python 时，下载 Python 3.11.9 embeddable 到
      `backend_fastapi\.venv\`、启用 `python311._pth` 的 `import site`、装 get-pip，与现有「复用虚拟环境」分支无缝衔接
      （免安装、免管理员权限、免勾 PATH）。

## 待办（未落地 / 待验证）
- [ ] **【阻塞发布】目标设备实测 embeddable 下载装配**：本机无法联调联网下载（python.org + bootstrap.pypa.io + 解压 + get-pip）。
      需在目标设备跑一次启动器确认；网络受限时应优雅降级到「手动安装 Python 3.9+」提示（已实现，但需验证落点）。
- [ ] **建议2：C# 启动器彻底去 bat 化（可选）**：把 `python -m venv` + `pip install` 改成 `Process` 直接调用，行尾问题从根上消失。
      当前行尾自愈已解决根因，此项优先级低，但可彻底消除 bat 依赖 + 获得更细的进度/错误处理。回归风险较高，需单独评估。
- [ ] **建议1 补 `fix_eol.ps1` 随包脚本（可选双保险）**：C# 已自愈，但随包附一个脚本可让用户在拷贝/压缩后手动一键修复 bat 行尾，
      无需依赖启动器逻辑。低成本、纯增量。

## 当前改动未提交（2026-10-16 状态）
- 改动文件：`launcher/StockPoolLauncher.cs`、`launcher/install_env.bat`、`股票池追踪系统.exe`（316928 字节）
- 当前在 **main** 分支；按约定「不直接进 main」，应开新分支（如 `fix/cross-device-launch`）再提交/推送。
- 注：之前 `025d0bf` 提交的是筹码/指标重构 + 方案文档 + 旧 exe（314880），与本次修复无重叠。

## 2026-10-17 追加修复（第二台设备报错）

目标设备截图又暴露两个新 bug：

- **`FindPython` 把商店 stub 当真 Python**：`C:\...\WindowsApps\python.exe` 是 Windows 应用商店占位符（执行报 "Python was not found"），
  旧逻辑只要文件存在就返回它 → CheckEnv 报「Python 无法执行」而非「没找到 Python」，挡住了自动安装。
  已修：`FindOnPath` 结果含 `windowsapps` 直接返回 null。本机 `where python` 实测正是返回该 stub，验证逻辑生效。
- **`install_env.bat` 盲目复用损坏 venv**：设备上有旧 `backend_fastapi\venv`（无 pip），旧脚本命中「复用已有虚拟环境」→ `[FATAL] 虚拟环境里没有 pip` 退出码 3。
  已修：复用前先验证 `-m pip --version`，失败则 `rmdir .venv / venv` 删除后重建。
- **`[err] '失败' is not recognized` 行尾回潮**：那台设备跑的是**旧 exe**（无 `NormalizeBatLineEndings`）。新 exe 已含行尾自愈。

**部署要点（关键）**：`install_env.bat` 是独立文件、**不在 exe 内** —— 目标设备必须同时更新
`股票池追踪系统.exe` **和** `launcher\install_env.bat`；只换 exe 不换 bat，仍会命中旧的「复用坏 venv」逻辑。

另：目标设备路径出现 `stockanaly-main\stockanaly-main` 双重嵌套（解压时多包了一层），不影响运行（相对路径一致），但建议解压时避免。

## 目标设备验证清单
1. 干净 Windows（无 Python）双击启动器 → 自动下载 embeddable → 装依赖 → 正常起服务。
2. bat 被传成 LF-only 时启动器仍能跑（行尾自愈）。
3. 断网 → 下载失败 → 提示手动安装，不崩溃。
4. `akshare` 等重依赖在 embeddable 下能否装齐（纯 wheel 应 OK；个别需编译的包若失败会落到 FATAL 提示手动处理）。
