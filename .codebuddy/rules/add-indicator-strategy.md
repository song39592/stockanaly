---
description: 新增指标 / 策略的规程：一个文件搞定、口径如何共用、必须跑哪些自检、改完要同步什么
alwaysApply: true
enabled: true
---

# 新增指标 / 策略的规程

目标：**新增一个指标或策略 = 新增一个文件 + 跑一遍自检**，不改架构、不动别人的文件。

## 新增指标

1. **新建一个文件**（`backend_fastapi/indicators/<语义名>.py`），一个指标一个文件。
   文件名用语义名（snake_case），`@indicator(id=...)` 用大写缩写 —— **两者不必同名**。
2. **顶部**：`from .base import indicator, ParamSpec, series_line, series_bar`。
3. **取数只有一个入口**：行情走 `data.get_ohlcv`；需要「按代码 / 周期再取一份数」时
   在签名里显式声明注入参数 `def xxx(df, *, ctx=None, ...)`，用 `data.code_of(ctx)` /
   `data.period_of(ctx)` 取。**禁止** `df.attrs["code"]`。
4. **panel 按量纲选**（唯一依据，不是照抄别的指标）：
   - `main` = 与价格**同量纲**（均线 / 通道 / 成本线）
   - `lower` = 与价格**不同量纲**（比率 / 摆动 / 偏离度）
   - `none` = 能算能枚举但不显示
   - `dist` 类（按价格分箱的直方图）用 `panel="main"` + `kind="dist"`，由前端专用热力图渲染器画
5. **样式可选声明**：在 `@indicator(styles={...})` 里写各条序列的 `{"color": "#RRGGBB", "width": 1.4}`
   → 前端**不必再改**（前端保留硬编码规则作兼容回退；后端下发的值优先，
   但用户在 `settings.json` 里覆盖过的调色板语义色仍然优先）。
   颜色只接受 `#RRGGBB`，写通达信色名会在扫描期被拦下（前端会静默变黑）。
6. **与策略共用的计算内核**放**中立包** `backend_fastapi/formulas/`（一个内核一个文件）：
   指标 import 它做展示包装，策略 import 它做横截面选股 —— 两边数值天然一致。
   **禁止**把共用内核塞进 `chipformulas/core/`（那是筹码语义）或让指标文件互相 import。
7. **验证**：
   ```python
   import indicators
   indicators.list_indicators()          # 出现新指标，panel 正确
   indicators.compute("600000", "<ID>")  # dates 与行情同序、前段 null、结构符合 ARCHITECTURE 6.1(e)
   ```
8. **更新数值基准**（新增指标、或改了任何指标的计算逻辑后**必做**）：
   ```
   ./venv/Scripts/python.exe -m indicators.golden --update
   ```
   基准文件 `indicators/_golden.json` **入库**；`test_indicators_golden` 会比对现算值，
   任何数值变化都让测试失败。不更新基准 → 测试报「新增指标 xxx：未登记数值基准」。

## 新增策略

1. **新建一个文件**（`backend_fastapi/strategies/<id>.py`），**文件名即 id**
   （`@strategy(id=...)` 必须与文件名一致，否则 LOAD 即剔除）。
2. **一个文件恰好注册 1 个策略**；元数据（name / category / inputs / outputs / params）要填全。
3. **取数走** `data.load_bars`（策略包唯一出口），按 `inputs` 声明的列取；
   **禁止**自行读库 / 调 akshare / 网络。
4. **输出目标权重向量**（`Signal.weight` = 占组合净值比例），同日 `Σweight ≤ 1`；
   买卖由引擎对权重做 diff 派生 —— 策略**不要**自己发买卖单。
5. 与指标共用的口径 → 放 `formulas/`，不要在策略里把指标算法重写一遍。

## 三套注册的公共骨架

指标 / 策略 / 筹码公式都是「扫目录 → 导入即注册 → 校验 → 出报告」。
**判据各包自理**（指标的 id 与文件名无关、策略与公式必须相同；策略有 mtime 缓存、另两处没有），
**机械动作统一在** `backend_fastapi/core/registry_scan.py`（遍历 / 强制重导入 / 单注册校验 /
报告组装 / 缓存）。新增第四套注册机制时**复用它**，不要照抄一遍。

## 改完必须做的事

- 改了 `backend_fastapi/**/*.py` → **先结束占用 8000 端口的旧 uvicorn 进程**，再重启后端验证
  （启动器检测到后端已在跑会**不抢占、不重启**，测到的仍是旧代码）。
- 改了 `launcher/**/*.cs` → 重跑 `launcher/build_exe.bat` 重新生成 `stockpool.exe`，
  **发布版 exe 要入库**（本项目约定：exe 虽是生成物，但需要入库，便于别的设备直接跑）。
  注意 `build_exe.bat` 是**显式源文件清单**，新增 / 删除顶层 .cs 必须同步改它。
- 改了 `backend_fastapi/core/bars_schema.py`（日线标准列 / 复权缩放）→ 指标与策略两侧同时生效，
  不需要改两处；但**新增量纲为「价」的列**时必须同时加进 `PRICE_SCALED_COLUMNS`。