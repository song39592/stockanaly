# 28 · 后端：`features/market/`（盘面与板块）

> 状态：待办　|　优先级：中　|　收益 ★★★☆☆ / 风险 ★★★☆☆　|　**依赖第 27 项**

## 问题
盘面/板块相关模块与其它功能混在 `backend_fastapi/` 扁平层，按功能归位后依赖关系才看得清。

## 建议目标
```
backend_fastapi/features/market/
├─ __init__.py
├─ service.py   ← 原 market_service.py
├─ routes.py    ← 原 market_routes.py
└─ bars.py      ← 原 MktBars / MktRatioBar 相关（若它们是后端逻辑；若是前端控件则不动）
```

## 建议做法
同第 27 项的**兼容转发**模式：`git mv` → 修正 import → 旧路径留转发 → 全部迁完再删转发。

## 注意（踩坑点）
1. **`market_service` 是全仓 akshare 的唯一封装提供者**：`_ak(fn, *args, timeout=...)`（:186）被 7 个模块使用
   （`board_service:27`、`stock_profile:25`、`price_service:23`、`download_service:30`、`collectors:17`、
   `strategies/core/backtest:55`、`valuation_service:177`）。
   → 移动时**必须**保证旧 import 名仍可用（转发或同步改这 7 处）。其中 `valuation_service:177` 用的是
   `ms._ak(ms.ak...)` —— **`_ak` 是下划线名，`from x import *` 不会导出**，转发文件里必须显式写
   `from features.market.service import _ak, ak`。
2. `_http_get`（:143，带重试与退避）、`UA`（:46）、`SINA_REFERER`（:48）也在这个模块，是第 15 项（httpclient 统一）的种子 ——
   搬运时别把它改坏。
3. `market_service` 有自己的 TTL 缓存范式 `_cached(key, producer, ttl, retry_ttl)`（:124）与 `_ttl()`（:97，盘后延长到 23:59:59），
   **盘后长缓存是它专门的性能优化**，搬运时不要顺手「统一」掉。
4. `_MARKET_CLOSE_MIN = 15*60`（:73）与 `chip_service.MARKET_CLOSE_HOUR = 15` 是同一业务事实的两份定义（见第 16 项相关说明）——
   搬运时不要一起改，留到第 12/16 项处理。

## 验收
- [ ] `features/market/` 建立，两个主模块迁入
- [ ] 7 个依赖 `_ak` 的模块全部正常（`import ms` 与 `ms._ak` 均可用）
- [ ] 盘面页：大盘资金、板块、连板结构、涨跌停、炸板、AI 分析入口均正常
- [ ] `/health` 的 `_module_errors` 为空；后端启动日志无 import 错误

## 附：与第 15 项的关系
若已先做第 15 项（raw HTTP 抽公共 `httpclient`），本项搬运会简单很多 —— 建议 **`15 → 28` 顺序**。
