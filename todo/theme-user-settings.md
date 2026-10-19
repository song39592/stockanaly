# 后期待办：统一为「用户本地设置文件」

## 背景
现状是**同一类「用户本地配置」散落在多处**，且密钥有专门的加密通道：

**① 主题（C#，两套底色 + 一个开关）**
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

**② 运行环境路径（launcher 已有的 INI）**
- `%APPDATA%\StockPoolLauncher\settings.ini` — `:448-450`；
  读 `LoadSettings():1283` / 写 `SaveSettings():1320`；
  字段 `python / node / autostart / minimize / stoponexit / runonboot / **theme**` — `:1327-1333`。
- 状态目录 `LauncherStateDir()` = `%APPDATA%\StockPoolLauncher` — `:1364-1369`。

**③ 历史数据下载的开始年份（前端硬编码、未持久化）**
- `launcher/DownloadPage.cs`：默认 `thisYear - 5`（`:73`）；取起始日 `DlStartDate()` 默认
  `Year - 5`（`:158`）。选了更早年份只是当次生效，**关掉重开就回到 5 年**，没有落地成用户偏好。

**④ 密钥（已有独立加密通道，不应混入明文文件）**
- `backend_fastapi/core/dpapi.py`：Windows DPAPI 封装，把密钥绑定「本机 + 当前用户」
  （非 Windows 退回明文）。`core/config.py`（LLM 密钥）、`core/crypto.py`（`DATA_SECRET_SEALED`
  校验密钥）都已用它。
- `.env`（含 `LLM_BASE_URL` / `LLM_MODEL` / `DATA_DIR` / `DATA_SECRET_SEALED` 等）由
  `core/envfile.py` 原子改写（先 `.bak` 备份，写临时文件后 `os.replace` 替换，避免半截文件）。
- 另有 `backend_fastapi/.env.example` 作为模板（**不**含真实密钥）。

**缺口**
- 主题只有浅/深两套底色，`Palette.C` 语义色不随主题变；无「用户自定义颜色」存储；
- **窗口大小未持久化**（`MainForm` Size 硬编码 — `:454-455`）；
- **下载开始年份未持久化**（上面 ③）；
- 同一类偏好散在 `settings.ini`（路径/主题）、`theme-state.js`（主题广播）、前端硬编码（年份）三处；
- 密钥虽已加密存储，但**缺乏统一入口**让用户在一个 UI 里看到「哪些已配置」。

## 目标
把「非机密的、用户可编辑的本地偏好」统一到一个**用户本地设置文件**（`settings.json`，
放在 `%APPDATA%\StockPoolLauncher\`）驱动；密钥**继续走既有 DPAPI/`.env` 加密通道，
不写进这个明文文件**——设置文件里只为密钥保留「是否已配置」的标记，UI 据此提示，绝不存明文。

## 设置文件结构（推荐 JSON，便于嵌套颜色表）
```json
{
  "theme": "dark",
  "palette": { "up": "#e5484d", "down": "#2ea043", "flat": "#969ca8", "...": "..." },
  "window": { "w": 1020, "h": 700, "x": null, "y": null },
  "download": { "start_year": 2021 },
  "env": { "python": "python", "node": "node" },
  "custom_themes": {},
  "secret_status": { "data_secret": true, "llm_key": false }
}
```
- `theme` / `palette` / `window` / `download` / `custom_themes`：规划中的用户偏好（原本的 theme 扩展）。
- `env.python` / `env.node`：从现有 `settings.ini` 迁移过来（路径类、非机密）。
- `secret_status`：**只读标记**，由后端 DPAPI 状态回填（不在此文件里写密钥本身）。

## 具体步骤
1. **新增 `settings.json`**：取代「主题 + 年份 + 窗口 + 路径」这四个分散点。建议 JSON
   （嵌套颜色表用 INI 难表达）。与现有 `settings.ini` **并存过渡**：先读 `settings.json`，
   缺字段回退 `settings.ini` / 默认值；后续版本再弃用 `settings.ini` 的偏好字段。
2. **让 `Palette.C` 可配**：把只读常量改为**可从设置加载的静态属性**（默认值 = 现有常量），
   `BuildPalette` / `ApplyTheme` 统一从设置取值，消除「两套底色」分裂。
3. **下载年份持久化**：`DlStartDate()` 读 `settings.download.start_year`（缺省回退 `Year - 5`）；
   起始年份 ComboBox 的 `SelectedIndexChanged` 写回设置文件。
4. **窗口偏好**：`MainForm` 的 `Size`/位置从设置读取（无则默认 1020×700 居中），
   `Resize` / `FormClosing` 写回（补 `:454-455` 硬编码缺口）。
5. **读写**：新增 `LoadUserSettings()` / `SaveUserSettings()`（与 `LoadSettings/SaveSettings`
   并列 — `:1283 / :1320`）；`ApplyTheme` / 改年份 / 改窗口后写回。
6. **密钥统一入口（仅展示 + 配置跳转，不存明文）**：设置/主题页增加「凭据」区块，
   显示 `secret_status`（已配置/未配置），点「配置」走既有 DPAPI/`.env` 输入流程；
   **任何情况下不把密钥写进 `settings.json`**。
7. **UI**：设置页增加颜色编辑（每语义色一个 color picker + 预设主题下拉 + 「恢复默认」，
   至少先支持「选预设 / 改主色」）；下载起始年份输入框；运行路径编辑。
8. **兼容**：旧 `settings.ini` 的 `theme=light|dark`、`python`/`node` 继续识别；
   `theme-state.js` 机制保留（网页侧仍读它，作为启动器→网页的单向广播）。

## 验证
- 改主题 / 颜色 / 下载年份 / 窗口大小 → 立即生效，且重启后保持（能从 `settings.json` 读回）。
- 删除 `settings.json` → 回落默认主题与默认 5 年，不崩溃。
- **手工编辑 `settings.json` 里的颜色 / `download.start_year` → 启动后生效**
  （验证它确实是「用户本地设置文件」）。
- 密钥：在 UI 配置后，确认 `settings.json` 里**只有 `secret_status` 标记、无任何明文密钥**；
  且密钥仍可用于后端（DPAPI/`.env` 链路未被破坏）。

## 风险 / 注意
- 中：`Palette.C` 现为 `static readonly` 常量，被大量 `Color` 引用点直接使用；
  改成可变量要确认**没有在静态初始化阶段被缓存**，且换肤需重绘已建控件。
- **密钥分层（高）**：绝不能把 DPAPI 加密的密钥明文落到 `settings.json`；
  该文件应可安全分享 / 提交模板（仅 `.env.example` 进 git，真实 `.env` 与 `settings.json`
  走 `.gitignore`），用户不会因误提交而泄露凭据。
- 设置文件解析要容错（坏 JSON / 缺字段 → 默认值），避免启动即崩。
- 别混淆职责：**设置文件 = 非机密用户偏好源**，`theme-state.js` = 启动器→网页的**单向广播**，
  `.env` + DPAPI = **机密凭据的唯一存储**。

---

## 补充待办：自动恢复上次关闭前的指标与股票显示
> 位置说明：本条排在「统一为用户本地设置文件」之后做——**复用 `settings.json`**，不另起一套存储。
> 现状：`launcher/` 里没有「记住上次个股 / 指标 / 周期」的逻辑（已确认无相关持久化）。

- **目标**：启动 / 重开后自动恢复上次关闭前的界面状态：
  ① 当前个股代码；② 已启用的主图 / 副图指标及参数；③ K 线周期（`day/week/month`）。
- **落点**：`settings.json` 增加 `last_session` 字段（与现有 `theme / palette / window /
  download / env` 并列）：
  ```json
  "last_session": {
    "code": "600519",
    "period": "day",
    "main_indicators": [{ "id": "MA", "params": {} }],
    "sub_indicators": [{ "id": "VOL", "params": {} }]
  }
  ```
- **写入时机**：切股 / 切周期 / 增删指标 / `FormClosing` 时写回；要节流，
  别在鼠标移动、缩放等高频事件里落盘。
- **读取时机**：进入个股页时优先按 `last_session` 恢复；无记录则保持现状（默认代码 / 默认指标）。
- **注意**：
  - 这是「界面偏好」不是「数据」——**绝不**把行情 / 计算结果 / 筹码矩阵塞进来；
  - 恢复指标 id 时先校验它仍在 `indicators.REGISTRY` 里（指标被删 / 改名 → 静默跳过，
    别抛错，其余照常恢复）；
  - 与 `theme-state.js` 的单向广播、`.env`/DPAPI 密钥通道**互不越界**。

### 验证
- 打开某股 + 开 2 个副图 + 切到周线 → 关闭程序 → 重开，自动回到同一股、同一周期、同样指标。
- 删掉 `settings.json` 里的 `last_session` → 启动不报错，回到默认视图。
- 恢复一个已被删除的指标 id → 静默跳过，其余正常恢复。
