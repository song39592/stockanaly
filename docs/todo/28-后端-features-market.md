# 28 · 后端：`features/market/`（盘面与板块）

> 状态：**已完成**（2026-10-06）　|　优先级：中　|　收益 ★★★☆☆ / 风险 ★★★☆☆
> 依赖第 27 项（已完成）

## 回填实际改动

```
backend_fastapi/features/
├─ __init__.py
└─ market/
   ├─ __init__.py     说明这一层的定位 + 为什么旧路径必须留别名转发
   ├─ service.py   ← 原 market_service.py   （git mv，内容零改动）
   └─ routes.py    ← 原 market_routes.py    （只改 1 行 import）
```

- 顶层保留两个**别名转发**：`market_service.py` / `market_routes.py`
  （`sys.modules[__name__] = _impl`，与第 27 项同一套写法）。
- **`service.py` 内容零改动** —— `git diff --stat -M` 显示为 `} | 0`，纯移动。
  它唯一的项目内 import `from core import httpclient` 是绝对导入，搬后仍可用。
- `routes.py` 只改 1 行：`import market_service` → `from features.market import service as market_service`。
- **HTTP 路径没有任何变化**：`router` 的 `prefix` 仍是 `/api/market`，
  `main.py` 的 `ROUTE_MODULES` 仍按字符串 `"market_routes"` 引用（靠转发），
  启动器与网页端**不需要改**。

## 踩坑点四条的处置

1. **`_ak` 是下划线私有名，`from x import *` 不会导出** —— 待办预警成立。
   本项直接用**别名转发**（不是 import *），所以那 7 处 `from market_service import _ak`
   **一行都不用改**。已逐个验证：

   | 依赖方 | 形式 | 结果 |
   |---|---|---|
   | `board_service` / `collectors` / `core.price_service` / `download_service` / `stock_profile` / `strategies.core.backtest` | `from market_service import _ak` | 全部 OK |
   | `valuation_service` | `import market_service as ms` + `ms._ak(ms.ak...)` | OK（属性访问，别名下同样可用） |

2. **`_http_get` / `UA` / `SINA_REFERER`** —— 第 15 项已把它们收进 `core/httpclient.py`，
   所以本项**没有这类东西可搬**；`service.py` 里现在只剩一处 `httpclient.get(..., retries=3)`。
   待办这条对本次已不适用。
3. **TTL 缓存范式（`_cached` / `_ttl` 盘后延长到 23:59:59）** —— **未动**。
   `service.py` 零改动，缓存逻辑原样保留，没有顺手「统一」掉。
4. **`_MARKET_CLOSE_MIN = 15*60` 与 `chip_service.MARKET_CLOSE_HOUR` 两份定义** —— **未动**，
   按待办要求留给第 12/16 项。

## 本次未一并搬的（说明理由）

- **`board_service.py`（板块成分股索引）留在顶层**：它服务于**个股**页的
  `/api/stock/boards`（查某只股票所属板块），且只在 `stock_routes` 里被调用 ——
  归到「盘面」不如留给第 29 项（features/stock）一起处理。
- **待办提到的 `bars.py` 不存在**：`MktBars` / `MktRatioBar` 是**启动器里的前端控件**
  （`launcher/MarketPage.cs`），不是后端逻辑，按待办括注「若是前端控件则不动」处理。
## 踩坑（验证时自己踩的）

**PowerShell here-string 吞掉了 docstring 的闭合 `"""`。** 写 `features/market/__init__.py` 时
漏了收尾的三引号，文件语法错误 → `import market_service` 直接
`SyntaxError: unterminated triple-quoted string literal`。
后来加了一道检查（数每个文件里 `"""` 的个数是否为偶数）才定位到。
**教训**：批量写文件时，Python 文件应加「三引号成对 + `ast.parse` 语法校验」两道自动检查，
否则一个漏字符会让整条 import 链断掉。

## 验收（2026-10-06 实测）

- [x] `features/market/` 建立，两个主模块迁入 ✅ `git diff --stat -M` 识别为
      **纯 rename**（`service.py` / `routes.py` 均 `| 0`，即内容零改动）
- [x] 7 个依赖 `_ak` 的模块全部正常（`import ms` 与 `ms._ak` 均可用）✅
      6 处 `from market_service import _ak` + 1 处 `ms._ak` 属性访问，
      加 `main` 共 8 个模块**全部导入成功**；`ms is features.market.service` 为 `True`（别名一致）
- [x] 盘面页：大盘资金、板块、连板结构、涨跌停、炸板、AI 分析入口均正常 ✅
      ① 外围环境 0.6s ② 大盘资金 14.3s ③ 板块β 0.8s ④ 连板结构 0.6s ⑤ 涨跌停/炸板 0.6s
      （AI 分析入口未实测，需真实 LLM 调用；其路由已挂载、`main.py` 的模块清单为 True）
- [x] `/health` 的 `_module_errors` 为空；后端启动日志无 import 错误 ✅
      `ok=True`、`integrity.ok=True`、**11 个路由模块全部挂载**、stderr 无任何告警
- [x] 连带回归（走 akshare 链路的都验了）✅ 筹码分布 1.0s、基本信息 1.2s、
      个股板块 0.0s、K线 0.3s，全 `ok=true`
- [x] 单元测试 52 个全绿 ✅ 真实 `.env` 未被改动

## 记录

- 2026-10-06 执行，位置符合建议（阶段 C 第 17 步，排在 09 与 bug-02 之后）。