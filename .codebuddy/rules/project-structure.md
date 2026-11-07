---
description: 目录结构与构建清单约定：launcher 顶层类文件、build_exe.bat 源文件清单、分发单元、指标层跨层契约
alwaysApply: true
enabled: true
---

# 文件结构与构建清单

## launcher\：一个主类一个文件
- 主窗体是分部的（`StockPoolLauncher.cs` + `StockPage.*.cs` 等）；**新抽出来的控件 / 面板类放独立顶层文件**
  （如 `launcher\ChipPanel.cs`、`launcher\KLineChart.cs`、`launcher\BootOverlay.cs`），
  不要塞回 `MainForm` 里当嵌套类。
- 嵌套类只在「外部完全用不到」时保留；一旦别的顶层类要用，它就必须提升为顶层 `internal` 类型
  （同命名空间同程序集可见）。
- 提升时要**一起提升它的基类**（如 `ChartControl`）与它依赖的其它嵌套类型（如 `KBar` / `KMark`），
  否则编译期报 CS0246（类型找不到）。
- 顶层类放在 `namespace StockPool` 下、标 `internal`（不要 public）。

## 新增 / 删除 launcher\*.cs 必须同步 build_exe.bat
- `launcher\build_exe.bat` 是**显式源文件清单**（不是通配符）：新增或删除顶层 .cs 后必须同步改这个清单，
  否则新类不会进 exe（表现为「改了没生效」）。
- 改完清单要重跑 `build_exe.bat` 重新生成 `stockpool.exe`；**发布版 exe 要入库**（见 git-safety 规则）。

## backend_fastapi\indicators\：跨层上下文必须显式
- 指标函数只允许从**签名**拿到「按代码 / 周期再取数」这类上下文：
  `def xxx(df, *, ctx=None, ...)`，框架经 `context.CalcContext` 注入。
- **禁止**再用 `df.attrs["code"]` 这类藏在数据里的通道 —— 签名里看不见 = 契约是假的，
  单元测试还得伪造 attrs 才能跑。
- `ctx` 是框架注入参数：走 `base.INJECTED_PARAMS` 白名单、必须是**关键字参数**且有默认值、
  不进用户参数表（ParamSpec）；`ctx=None` 表示「没有上下文」（合成数据自检），需要 code 的指标据此
  抛「取不到股票代码」，由 `scan` 判为需外部数据跳过。
- 详见 `backend_fastapi\indicators\ARCHITECTURE.md` §5。

## 分发单元：目标设备要能直接跑，缺一不可
- `stockpool.exe`（发布版）
- `launcher\install_env.ps1`（环境安装脚本；exe 之外，必须同步更新）
- `backend_fastapi\`（含 `requirements.txt`）
- `agent_dsh\`（AI 服务）
- 交付说明里要写清「**只换 exe 不换脚本等于没换**」。