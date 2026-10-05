# 29 · 后端：`features/stock/`（个股 / 估值 / 板块索引）

> 状态：待办　|　优先级：中　|　收益 ★★★☆☆ / 风险 ★★★☆☆　|　**依赖第 27 项**

## 问题
个股与估值相关模块混在扁平层。这一组是**功能最杂**的一块（个股档案、行情报价、估值、板块索引），
按功能归位后能看清谁依赖谁。

## 建议目标
```
backend_fastapi/features/stock/
├─ __init__.py
├─ profile.py       ← 原 stock_profile.py（行业/市值/前十大股东/涨停连板 + 十大流通股东 + 锁仓比例）
├─ routes.py        ← 原 stock_routes.py（quote / valuation / research）
├─ valuation.py     ← 原 valuation_service.py（估值模型 + 行情抓取）
└─ board_index.py   ← 原 board_service.py（板块索引，DB 持久化 TTL）
```

## 注意（踩坑点）
1. **`stock_profile` 是第 09、10 项的关键模块**：
   - `stock_profile._json_safe`（:135）是全仓**唯一写对了**的 NaN 清洗（第 09 项要把它提公共层）
   - `free_top_holders`（:222）/ `lockup_ratio`（:239）是筹码锁仓修正的数据源（被 `chip_rank_service` 依赖）
   → 本项搬运**不要顺手重构这些函数**，只做位置移动；重构留给 09/10。
2. `stock_profile._cached`（:31）与 `lockup_ratio` 内联的失败语义**不一致**（前者失败也缓存、后者失败不缓存，
   是同一模块内的自相矛盾，见审计第 1 条）—— 搬运时若顺便统一，需单独验收「取数失败后能否重试」。
3. `valuation_service` 的返回体**没有出口 NaN 清洗**（第 09 项要补）—— 搬运时不要顺手改，避免与第 09 项冲突。
4. `valuation_service._market_prefix`（:84）与 `price_service.market_symbol`、`share_service.market_symbol` 是
   **三份互相矛盾的市场前缀规则**（第 16 项）—— 搬运时不要动规则，只搬家。
5. `board_service` 的 TTL 是**持久化在 DB**里的（`_TTL_HOURS=24`，跨进程），搬运时别改成进程内缓存。

## 建议做法
同第 27 项的**兼容转发**模式。

## 验收
- [ ] `features/stock/` 建立，四个模块迁入
- [ ] 个股页：基本信息 / 十大股东 / 涨停连板 / 所属板块 正常
- [ ] 估值页：估值计算与三档情景正常
- [ ] 「重取名称」链路正常（`stock_routes.quote_only` → `valuation_service.quote_only`）
- [ ] `/health` 的 `_module_errors` 为空
