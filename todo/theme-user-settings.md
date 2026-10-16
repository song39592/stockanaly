# 后期待办：theme 文件扩展为「用户本地设置文件」

## 背景
**主题现状（C#，两套底色 + 一个开关）**：
- `launcher/StockPoolLauncher.cs`：`BuildPalette()` — `:773`（维护 `_cBg/_cPanel/_cText/_cSub/...` 浅/深两套底色）；
  `ApplyTheme()` — `:749`（换肤 + **写文件**）；`ToggleTheme()` — `:741`；
  按钮 `_btnTheme`「主题：深色」`Click += ToggleTheme` — `:525-535`。
- 语义色常量：`launcher/Palette.cs:20` `internal static class C`（`Color.FromArgb` 常量：
  涨红 `Up/UpErr/UpSoft`、跌绿 `Down/...`、灰 `Flat/...`、警告 `Warn/...`、强调 `Accent/...`、
  指标线 `LineCyan/RpsCyan/...`、筹码峰 `ProfitDark/LockedDark/...`）。
  ⚠️ 注释明确 `Palette.C` **暂不参与换肤**，与 `BuildPalette` 的底色是**两套** — `Palette.cs:15-16`。
- 启动器写出的主题文件：`frontend/shared/theme-state.js`（`window.__LAUNCHER_THEME__ = "light"|"dark"`，
  由 `ApplyTheme` 写 — `:763-767`）；网页侧 `frontend/shared/theme.js`（KEY `stockPool.theme`、
  `data-theme` + localStorage — `:12 / :43-51`）。

**用户偏好持久化现状**：
- 启动器设置（ini）：`%APPDATA%\StockPoolLauncher\settings.ini` — `:448-450`；
  读 `LoadSettings():1283` / 写 `SaveSettings():1320`；
  字段 `python / node / autostart / minimize / stoponexit / runonboot / **theme**` — `:1327-1333`。
- 其它：`history_stock_view.json`（`StockPage.cs:1042`）、`ai_prefs.json`（`StockPage.AI.cs:55-60`）。
- 状态目录 `LauncherStateDir()` = `%APPDATA%\StockPoolLauncher` — `:1364-1369`。
- 自选股（股票池）**不在**启动器侧持久化（在浏览器 `localStorage`）。

**缺口**：主题只有浅/深两套底色，`Palette.C` 语义色不随主题变；
没有任何「用户自定义颜色」的存储；**窗口大小未持久化**（`MainForm` Size 硬编码 — `:454-455`）。

## 目标
把「硬编码两套主题 + light/dark 开关」扩展为**用户本地设置文件驱动**的主题系统：
一个用户可编辑、可持久化的设置文件，定义主题名、各语义色，并顺带纳入窗口大小等偏好；
启动器读取它渲染，用户在 UI 改主题/颜色时写回该文件。

## 具体步骤
1. **定义设置文件**：沿用 `settings.ini`，或新增 `theme.json`（推荐 JSON，便于嵌套颜色表）。建议结构：
   ```json
   { "theme": "dark",
     "palette": { "up": "#e5484d", "down": "#2ea043", "flat": "#969ca8", "...": "..." },
     "window": { "w": 1020, "h": 700, "x": null, "y": null },
     "custom_themes": { } }
   ```
2. **让 `Palette.C` 可配**：把只读常量改为**可从设置加载的静态属性**（默认值 = 现有常量），
   `BuildPalette` / `ApplyTheme` 与应用时统一从设置取值，消除「两套底色」分裂。
3. **读写**：新增 `LoadThemeSettings()` / `SaveThemeSettings()`（与 `LoadSettings/SaveSettings` 并列 —
   `:1283 / :1320`）；`ApplyTheme` 后写回；`ToggleTheme` 同步更新设置。
4. **UI**：设置/主题页增加颜色编辑（每语义色一个 color picker + 预设主题下拉 + 「恢复默认」）；
   至少先支持「选预设 / 改主色」。
5. **窗口偏好**：`MainForm` 的 `Size`/位置从设置读取（无则默认 1020×700 居中），
   `Resize` / `FormClosing` 写回（补 `:454-455` 的硬编码缺口）。
6. **兼容**：旧 `settings.ini` 的 `theme=light|dark` 继续识别；`theme-state.js` 机制保留（网页侧仍读它）。

## 验证
- 改主题 / 颜色 → 立即生效，且重启后保持（能从设置文件读回）。
- 删除设置文件 → 回落默认主题，不崩溃。
- **手工编辑设置文件里的颜色 → 启动后生效**（验证它确实是「用户本地设置文件」）。
- 窗口大小 / 位置改动后重启恢复。

## 风险 / 注意
- 中：`Palette.C` 现为 `static readonly` 常量，被大量 `Color` 引用点直接使用；
  改成可变量要确认**没有在静态初始化阶段被缓存**，且换肤需重绘已建控件。
- 设置文件解析要容错（坏 JSON / 缺字段 → 默认值），避免启动即崩。
- 别混淆职责：**主题设置文件 = 用户设置源**，`theme-state.js` = 启动器→网页的**单向广播**。
