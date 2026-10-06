# 29 · 后端：`features/stock/`（个股 / 估值 / 板块索引）

> 状态：**已完成**（2026-10-06）　|　优先级：中　|　收益 ★★★☆☆ / 风险 ★★★☆☆
> 依赖第 27 项（已完成）

## 回填实际改动

```
backend_fastapi/features/stock/
├─ __init__.py
├─ profile.py       ← 原 stock_profile.py
├─ routes.py        ← 原 stock_routes.py
├─ valuation.py     ← 原 valuation_service.py
└─ board_index.py   ← 原 board_service.py
```

顶层保留 4 个**别名转发**（`sys.modules[__name__] = _impl`）：
`stock_profile.py` / `stock_routes.py` / `valuation_service.py` / `board_service.py`。

### 只改了「因本次搬家而失效」的 import（其余一律不动）

| 文件 | 改动 |
|---|---|
| `profile.py` | `import price_service/price_store/share_service` → `from core import ...`（第 27 项已把它们搬进 core）；`from market_service import _ak` → `from features.market.service import _ak` |
| `valuation.py` | `import market_service as ms` → `from features.market import service as ms` |
| `board_index.py` | `import db as db_mod` → `from core import db as db_mod`；`_ak` 同上 |
| `routes.py` | 顶部 `import valuation_service` → `from features.stock import valuation as valuation_service`；两处**函数内延迟导入** `import stock_profile` / `import board_service` → `from features.stock import profile / board_index as ...` |

**函数体一行未改** —— 4 个模块的逻辑、缓存、TTL 全部原样。

## 踩坑点逐条处置

1. **没有重构 `stock_profile` 的任何函数** ✅
   `_json_safe` 已是第 09 项留下的薄封装（转发到 `core/jsonutil`），
   `free_top_holders` / `lockup_ratio` 原样保留 —— `chip_formulas/core/data.py`
   正是靠它取锁仓比例，已验证筹码分布接口 `lockup_applied=true` 正常。
2. **没有统一 `_cached`（失败也缓存）与 `lockup_ratio`（失败不缓存）的矛盾** ✅
   两处语义原样保留，统一需单独验收「取数失败后能否重试」，不在本项。
3. **没有动估值出口清洗** ✅ 第 09 项已用 `json_safe_deep` 补上，验证三档情景正常。
4. **没有统一三份 market 前缀规则** ✅ `_market_prefix` / `price_service.market_symbol` /
   `share_service.market_symbol` 原样，留给第 16 项（已在转发文件注释里写明）。
5. **`board_index` 的 TTL 仍是 DB 持久化** ✅ `_TTL_HOURS=24` 未改成进程内缓存，
   转发文件注释里也提醒「别改成 lru_cache」。

## 为什么转发必须用别名（这次多了一个理由）

除下划线私有名外，`stock_profile` 还有**模块级可变状态** `_CACHE`（6 小时 TTL 的股东/行业缓存）。
`from x import *` 复制出来的名字**不含同一份缓存**，于是 `clear_cache()` 清的不是实现里那份 ——
缓存永远清不掉。已验证别名下 `stock_profile._CACHE is features.stock.profile._CACHE` 为 **True**。
## 踩坑（这次踩得比第 28 项更重）

**同一个 here-string 陷阱连续踩了三次**：`valuation.py`、`features/stock/__init__.py`、
以及生成转发文件的模板，三处都因为漏掉 docstring 收尾的 `"""` 而报
`SyntaxError: unterminated triple-quoted string literal`。

其中一次还伴随**内容损坏**：用 PowerShell 的 `.Replace()` 批量改 import 时，
`valuation.py` 整个文件被改成 `rrom __ruture__ mmport annotatmons`（`f`/`i`/`c` 被系统性替换）。
`git diff` 立刻暴露了它，从暂存区恢复后改用 Python 重做才干净。

**改进（已落实）**：批量生成/修改 Python 文件时，脚本内**自带两道检查**再落盘 ——
① `"""` 个数必须为偶数；② `ast.parse` 必须通过；不通过就中止且不写文件。
事后又跑了一次**全仓 .py 扫描**（`chk_all.py`），确认 0 个问题文件。

> 结论：在这个环境里改 Python 代码，**不要用 PowerShell 的 `.Replace()`** ——
> 它的编码/转义处理已经坏过两次。用 Python 脚本做替换，并在脚本里自带校验。

## 验收（2026-10-06 实测）

- [x] `features/stock/` 建立，四个模块迁入 ✅ 另加 `__init__.py` 说明定位与「未做什么」
- [x] 个股页：基本信息 / 十大股东 / 涨停连板 / 所属板块 正常 ✅
      `/api/stock/profile` 1.2s（股东 10 条、有行业）；
      `/api/stock/boards` 0.0s（行业 + 概念各 1）
- [x] 估值页：估值计算与三档情景正常 ✅ 0.7s，`ok=true`，
      情景 `optimistic / neutral / pessimistic` 齐全
- [x] 「重取名称」链路正常 ✅ `/api/stock/quote` 0.3s，返回名称 + 现价
      （`routes.quote_only` → `valuation.quote_only` 链路通）
- [x] `/health` 的 `_module_errors` 为空 ✅ `ok=True`、`integrity.ok=True`、stderr 无告警
- [x] 额外验证（别名转发的正确性）✅
      10 个模块全部导入成功（含 `chip_formulas.core.data`、`main`）；
      4 个转发与实现**是同一对象**（`is` 为 True）；
      私有名 `lockup_ratio` / `_market_prefix` / `_TTL_HOURS` 照常可见；
      **`_CACHE` 是同一份**（这是 `import *` 做不到的）
- [x] 连带回归 ✅ 筹码分布 1.0s（`lockup_applied=true`，锁仓链路通）、K线 0.4s、盘面板块β 1.0s
- [x] 单元测试 52 个全绿 ✅ 真实 `.env` 未被改动
- [x] HTTP 路径零变化 ✅ `router` 的 prefix 仍是 `/api/stock`，
      启动器与网页端不需要改（`main.py` 按字符串 `"stock_routes"` 引用转发）

## 记录

- 2026-10-06 执行，位置为阶段 C 第 18 步（排在 28 之后）。