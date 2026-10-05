# 30 · 后端：`features/history/`（股票池历史）

> 状态：待办　|　优先级：中　|　收益 ★★★☆☆ / 风险 ★★☆☆☆　|　**依赖第 27 项**

## 问题
股票池历史相关模块混在扁平层。

## 建议目标
```
backend_fastapi/features/history/
├─ __init__.py
├─ service.py   ← 原 history_service.py（K线/入池出池/消息面；含 load_trusted_bars）
├─ routes.py    ← 原 history_routes.py
└─ store.py     ← 原 history_store.py（pool_snapshots 快照）
```

## 注意（踩坑点）
1. **`history_service` 是 kline_service 的「已知例外」绕过方**：
   `load_trusted_bars`（153-183）直接调 `price_store.load_bars` 以捕获 `price_store.UntrustedDataError`（170），
   而 `kline_service.get_bars` 不抛这个异常。这是**必要绕过**（见第 17 项），搬运时**不要顺手改成走 kline_service**，
   否则会丢掉「脏数据自动重抓」的能力。建议在本项完成后，把这条例外**写进 kline_service 的文档**。
2. 周/月合样仍走 `kline_service.resample`（218）—— 这部分是对的，保持。
3. `history_store.latest_snapshot_codes()` 是「当前股票池代码」的来源之一（审计里提到），
   `strategies` 的池选择可能依赖它 —— 移动后确认仍能 import。

## 建议做法
同第 27 项的**兼容转发**模式。

## 验收
- [ ] `features/history/` 建立，三个模块迁入
- [ ] 个股页：K 线、入池/出池轨迹、消息面 正常
- [ ] 周/月周期切换正常
- [ ] 股票池历史列表与快照正常
- [ ] `/health` 的 `_module_errors` 为空
