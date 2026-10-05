# 17 · 后端：`kline_service` 「唯一取数+合样出口」不成立（**高风险，谨慎**）

> 状态：待办　|　优先级：中　|　收益 ★★★☆☆ / 风险 ★★★☆☆
> ⚠️ 涉及**核心口径**（后复权），改错会导致 RPS 排名静默算错。建议单独评估、小步推进。

## 问题
`kline_service.py:2` 自称「全项目唯一的『取数 + 周期合样』出口」，实际有 **4 处绕过**，
其中 2 处是真正的重复实现（自写后复权、第二份合样）。

## 证据

### 遵守的三处（现状正确）
- `indicators/data.py:58` → `kline_service.get_bars(...)`
- `strategies/core/data.py:72` → `kline_service.get_bars(code, ..., period="day")`
- `history_service.py:218-219` → `kline_service.resample(bars, period)`

### 绕过的四处
1. **`history_service.py:169,181,211`** —— 直接 `price_store.load_bars(...)`。
   *可接受*：`load_trusted_bars`(153-183) 需要捕获 `price_store.UntrustedDataError`(170) 做全量重抓，
   而 `kline_service.get_bars` 不抛这个异常、也不暴露指纹校验；周/月合样仍走 `kline_service.resample`(218)。
   → 判为**必要绕过**，但应在 kline_service 文档里登记为已知例外。

2. **`stock_profile.py:65`** —— `price_store.load_bars(code, adjust="raw")`（涨停统计）。
   日线专用、不需合样 → **无害**，但同样未登记。

3. ⚠️ **`indicators/data.py:251-278` `_apply_hfq`** —— **自己实现了一遍后复权**（最高风险）：
   ```python
   factors = price_store.load_all_factors()
   pos = bisect_left(idx, str(row["ex_date"])[:10])
   adj[pos:, j] = base[pos:, j] * float(fac)
   ```
   该函数 docstring(252-256) 自己都写了「与 load_bars 的 hfq 口径一致…」—— **承认在复刻 `price_store` 的 hfq 算法**。
   一旦 `price_store` 改后复权规则（累计因子 vs 单事件因子），**RPS 排名会静默算错且无人察觉**。

4. ⚠️ **`indicators/data.py:281-290` `_resample_panel`** —— 又一份周/月合样：
   ```python
   keys = [bucket_key(d, period) for d in labels]
   keep = [i for i in range(len(keys)) if i == len(keys)-1 or keys[i+1] != keys[i]]
   ```
   与 `periods.resample_bars`(67-109) 用同一个 `bucket_key`、同一个「取桶内最后一根」规则，
   风险低于 3（它只取最后一行、不聚合 OHLC/volume，面板只需要收盘价），
   但一旦 `periods._merge` 改边界规则，RPS 面板**不会**跟着改 —— docstring(:282) 只有一句「口径同 periods.resample_bars」的自觉注释。

### 合法的底层直读（不算绕过，勿动）
- `download_service` → `tdx_reader`（数据源层，本就在 kline_service 之下）
- `price_service` / `integrity.py:215` / `repair_digests.py:20` / `main.py:32` → `price_store`（存储层/运维）
- `test_price_store.py` / `test_integrity.py` → 测试

## 建议做法
1. **先把宣传语改成准确的**：「行情**消费方**的唯一取数出口（数据源层与运维脚本除外）」，
   并把 1、2 两处登记为**已知例外**（写在 kline_service 文档里）。
2. **`_apply_hfq`**（第 3 处）：改为调用 `kline_service` 暴露的 `get_market_panel(adjust="hfq")`，
   或在 `price_store` 侧加一个 `apply_hfq_factors(panel)` 公共函数，两边共用 —— **消除「两份后复权」**。
3. **`_resample_panel`**（第 4 处）：可保留（只取最后一行是合理性能优化），
   但应复用 `periods` 导出的桶判定，而不是自己用 `bucket_key` 手写一遍 `keep` 逻辑
   （或在 `periods.py` 导出 `bucket_last_indices(dates, period)` 供两处共用）。

## 注意（踩坑点）—— 本项最容易翻车的地方
1. **后复权是核心口径**：改完必须做**对拍验证** —— 用同一批股票、同一时间区间，
   对比 `_apply_hfq` 面板与 `price_store.load_bars(adjust="hfq")` 的收盘价，逐值相等才算通过。
2. `price_store.load_bars` 带**按股指纹校验**，而面板路径（`load_market_close`）**不做指纹校验**（`price_store.py:884` 注释）——
   两者的「脏数据」容忍度不同，合并后别把指纹校验意外引入或去掉。
3. `_resample_panel` 服务于 RPS 横截面面板（全市场 ~5585 列），**性能敏感**：
   改成通用 `resample_bars` 会做完整 OHLCV 聚合，可能显著变慢 —— 保留只取最后一行的优化。

## 验收
- [ ] kline_service 文档口径准确，已知例外已登记
- [ ] 后复权只有 1 份实现；`_apply_hfq` 面板与 `load_bars(hfq)` **逐值对拍一致**（至少 20 只 × 250 日）
- [ ] RPS 指标与改动前结果一致（抽查若干只的 RPS 值）
- [ ] 面板构建耗时不显著劣化
