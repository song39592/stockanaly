# 09 · 后端：`_json_safe` 提公共层，并给缺清洗的出口补调用

> 状态：待办　|　优先级：高　|　收益 ★★★★★ / 风险 ★☆☆☆☆

## 问题
`stock_profile._json_safe` 是**唯一写对了的**完整版，但只服务 1 个模块。
`valuation_service.valuate()` 与 `market_service` 的各接口返回体**完全没有出口 NaN 清洗** ——
一旦外部数据出现 NaN，FastAPI 严格模式抛 `ValueError: Out of range float values...`，
Starlette 兜底返回**纯文本 500**，前端只看到「无效的 JSON 基元」。

## 证据

### 完整版（只有 1 份）
`stock_profile.py:135` `_json_safe(v)`：
- NaN / ±Inf → None（:156）
- numpy 标量 → 原生类型（:146 `hasattr(v,"item")`）
- list / dict 原样返回（:155）
- 已在 `top_holders`(177-183)、`free_top_holders`(208-215)、`company_info`(284-311)、`profile()` 出口(360) 使用

### 半成品 4 处（只做了 NaN→None，不做 numpy 标量化）
- `kline_service.py:90`：`{k: (None if pd.isna(v) else v) ...}`
- `indicators/base.py:109`：`[None if pd.isna(v) else float(v) ...]`
- `strategies/core/stats.py:57-62`：`_num(v)` + `math.isnan`
- `chip_service.py:178,186,193,206`：4 次内联 `value is None or (isinstance(value,float) and pd.isna(value))`

### 完全没清洗的出口（风险最大）
- `valuation_service.valuate()` 返回体（441-474）—— 全程靠 `_num()`(60) 返回 None 兜底，**没有出口 NaN 清洗**
- `market_service` 各接口 —— 靠 `_num()`(223) 的 `if num != num: return None`（229）

这个失败模式在 `stock_profile.py:138-141` 的注释里有精确记录。

## 建议做法
1. 新建 `backend_fastapi/jsonutil.py`，把 `_json_safe` 原样迁入（含 `int` 分支在 `float` 之前的顺序）。
2. `stock_profile` 改为 `from jsonutil import _json_safe`（保留导出，不影响既有引用）。
3. 在 `stock_routes` / `market_routes` 的**响应出口**各加一次清洗（即 `quote_only`、盘面各接口返回前）。
4. 可选：`kline_service` / `indicators/base` / `stats` / `chip_service` 那 4 份半成品改为调用公共版。

## 注意（踩坑点）
- **`int` 分支必须在 `float` 之前**（stock_profile.py:146）—— numpy `bool_` 会走 `isinstance(v,(str,bool))` 早退，别改分支顺序。
- 对**已经是 JSON 安全的值**，该函数是恒等映射 —— 不影响任何成功路径的返回值，这是风险极低的根本原因。
- 不要在 service 内部层层清洗（会有性能与语义成本），只在**出口**洗一次。

## 验收
- [ ] `jsonutil._json_safe` 单一实现；`stock_profile` 无本地副本
- [ ] `/api/stock/valuation`、`/api/stock/quote`、盘面各接口在含 NaN 的数据下仍返回合法 JSON（可构造一个 mock 响应验证）
- [ ] 现有正常路径的响应值与改动前逐字段一致
- [ ] 后端启动无 import 错误（`/health` 的 `_module_errors` 为空）
