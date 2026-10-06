# 09 · 后端：`_json_safe` 提公共层，并给缺清洗的出口补调用

> 状态：**已完成**（2026-10-06）　|　优先级：高　|　收益 ★★★★★ / 风险 ★☆☆☆☆

## ⭐ 关键发现：待办的建议做法 3 照原样做是**空操作**

建议写的是「在响应出口各加一次清洗（即 `quote_only`、盘面各接口返回前）」。
但实测发现：**`_json_safe` 遇到 `dict` / `list` 是原样返回**（`float(dict)` 抛 TypeError，
走 `return v if isinstance(v, (list, dict)) else None`）：

    body = {'ok': True, 'rows': [{'close': nan}], 'n': inf}
    out  = _json_safe(body)
    out is body          -> True
    嵌套 NaN 还在吗      -> True

也就是说 `return _json_safe(some_dict)` **什么都不会被清洗**。
现有代码一直是**逐字段**调用（`stock_profile` 里 12 处，全在列表推导里），
所以从没暴露这个问题 —— 一旦按建议改成「整块清洗」，会得到一个**看起来加了、实际没加**的假修复。

**因此本项新增了递归版 `json_safe_deep`**，并在注释与文档里写明两者分工：

| 函数 | 用途 | 对 dict/list |
|---|---|---|
| `json_safe(v)` | **单值**（原 `_json_safe` 原样迁入，语义一行未改） | **恒等映射**（原样返回） |
| `json_safe_deep(body)` | **整块响应体**（HTTP 出口用） | **递归**清洗 |

## 回填实际改动

- 新增 `core/jsonutil.py`（70 行）+ 旧路径 `jsonutil.py` 别名转发
- `stock_profile._json_safe` 改为**薄封装**（`return json_safe(v)`），
  12 处既有调用**一行未改**；原实现已迁出
- **7 个 HTTP 出口**加递归清洗：`/api/stock/valuation`、`/api/stock/quote`、
  盘面 5 个 GET（global / capital / sectors / limit-up / big-loss）
- 另统一 2 处**语义等价**的半成品：
  - `core/kline_service.to_records`：内联 `pd.isna` → `json_safe`（另多挡 ±Inf 与 numpy 标量）
  - `indicators/base.py` 指标序列：`None if pd.isna(v) else float(v)` → `json_safe`

### 两处**没有**统一（刻意，说明理由）

1. **`strategies/core/stats.py` 的 `_num`** —— 它是 `round(f, 3)`，**带四舍五入**，
   与 `json_safe` 语义不同（后者不改变数值）。强行替换会**悄悄丢掉 rounding**。
   待办把它列为「半成品」有误。
2. **`chip_service.py` 的 4 处内联**（`clean_code` / `_clean_name` / `parse_market_cap` /
   `_to_number`）—— 细看后确认它们是**输入侧的解析守卫**，不是出口清洗：
   `clean_code` 命中 NaN 时返回 `""` 而不是 `None`，`parse_market_cap` 命中后还要继续做
   字符串解析。换成 `_json_safe` 会**改变它们的返回值**。待办归类有误。

> 附带发现（未改）：那 4 处的判断是 `isinstance(value, float) and pd.isna(value)`，
> 而 `np.float32` **不是** Python `float` 的子类 —— 理论上会漏。
> 但它们的输入来自 akshare 的 DataFrame，实际几乎都是 `np.float64`（是 float 子类）。
> 属独立议题，不在本项范围。

## 踩坑点落实

- ✅ **`int` 分支在 `float` 之前**：原样迁入，并在 `jsonutil.py` 里就地注释说明原因
  （numpy `bool_` 的 `.item()` 是 `bool`，`isinstance(True, int)` 为真，先命中 int 分支）。
  实测 `np.True_ → 1`，与改动前一致。
- ✅ **只清洗一次、且在出口**：service 层不动，避免层层清洗的性能与语义成本。
- ✅ **对已 JSON 安全的值是恒等映射**：实测正常响应逐字段不变（见验收）。

## 验收（2026-10-06 实测）

- [x] `json_safe` 单一实现；`stock_profile` 无本地副本 ✅
      全仓 `def json_safe` / `def _json_safe` 共 2 处：`core/jsonutil.py`（实现）
      与 `stock_profile.py`（薄封装 `return json_safe(v)`）
- [x] 含 NaN 的数据下仍返回合法 JSON ✅ **6 个异常场景**用
      `json.dumps(allow_nan=False)`（等同 FastAPI `JSONResponse`）验证，全部由**非法变合法**：
      嵌套 dict+list 含 NaN/Inf、numpy 标量 int64/float64/bool_、顶层就是 NaN、
      list 里含 NaN、含 tuple、正常响应（应保持合法）
      > 顺带发现**第二个失败模式**：numpy 标量在严格序列化下是 `TypeError` 而非 `ValueError`。
      > 我第一版验证脚本只 catch 了 `ValueError`，导致脚本自己崩了 —— 记下来，别重犯。
- [x] 正常路径的响应值与改动前逐字段一致 ✅ 抓 9 个接口改前/改后快照做逐字段对比。
      **确定性接口完全一致**：`kline`、`profile`、`quote`
      （`market_*` 与 `valuation.as_of` 有差异，但见下）
- [x] 后端启动无 import 错误 ✅ `/health` `ok=True`、`_module_errors` 为空、9 个接口全 200
- [x] 恒等性与类型保持 ✅ 正常响应 `==` 原值；`tuple` 清洗后仍是 `tuple`；`dict` 被重建（非原地）
- [x] 单元测试 52 个全绿 ✅ 真实 `.env` 未被改动

### 关于「逐字段一致」的取证方法

改前/改后快照有 24 处差异，**但不能就此认定是回归** —— 盘面是**实时数据**，
两次抓取相隔 3 分钟，价格本就会变。于是又抓了第三份快照做**噪声基线**：
**同一份代码、间隔 20 秒的两次快照互比 = 17 处差异**，位置与那 24 处**完全重合**
（`as_of` 时间戳、`market_global.groups[*].items[*]` 实时值、`cached` 标志，
以及上游接口偶发失败后恢复导致的 `errors: 1 → 0`）。

结论：这些差异是**数据源波动**，不是本项引入。确定性接口（`kline` / `profile` / `quote`）
在两次对比中均**完全一致**，这才是本项该有的证据。

> 方法论记档：验证「输出没变」时，**必须先建立噪声基线**（同代码、间隔一段时间、自比），
> 否则会把数据源波动误判成回归 —— 或者反过来，把真回归当成波动放过去。

## 尚未覆盖的出口（未做，供后续判断）

本项只按待办范围处理了 `stock_routes` / `market_routes` 的 7 个出口。仍有同类风险：

- `/api/history/stock/{code}`（K 线，已由 `kline_service.to_records` 逐字段清洗，**已覆盖**）
- `/api/indicators/*`（已由 `indicators/base.py` 覆盖）
- `/api/chip/*`（`chip_service` / `chip_formulas`）—— **未验证是否有 NaN 出口**
- `/api/mentor/*`、`/api/download/*`、`/api/history/*` 其余出口

要彻底闭环，可以在 `main.py` 加一个**统一响应清洗中间件**（对所有路由统一 `json_safe_deep`），
那样就不必逐个出口加。**本项没做**——中间件会影响全部接口（含 SSE 流式响应，
`market_ai_analysis_stream` 不能整块 JSON 化），属于需要单独评估的改动。