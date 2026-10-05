# 15 · 后端：raw HTTP 请求 4 份 → 公共 httpclient（含 UA/重试口径统一）

> 状态：**已完成**（2026-10-05）　|　优先级：中　|　收益 ★★★☆☆ / 风险 ★★☆☆☆
>
> **回填实际改动**
> 新增 `backend_fastapi/core/httpclient.py`（98 行）：UA / referer 常量唯一、
> 进程内共用 `requests.Session`、GET 带重试与线性退避、POST 超时必填。
> 旧路径留 `httpclient.py` 别名转发（第 27 项那套 `sys.modules` 别名写法）。
>
> **实际收敛了 6 处 raw HTTP（待办只记了 4 处）**，逐处处置：
>
> | 原位置 | 原口径 | 改后 |
> |---|---|---|
> | `market_service._http_get` | session + UA124 + retries=3 + 退避 | `httpclient.get(..., retries=3)` —— **全仓唯一允许重试的调用点** |
> | `valuation_service._get` | 无 session、无重试、UA124 | `httpclient.get(...)`（默认 retries=1） |
> | `core/share_service` 内联 | `requests.get` + **UA120** | `httpclient.get(..., timeout=TIMEOUT)` |
> | `collectors` 内联 | `requests.get` + **UA120 + Referer 字典** | `httpclient.get(..., referer=EASTMONEY_REFERER)` |
> | `stock_routes` ×2（GET+POST） | 自带 **UA120.0.0.0** + Referer | `httpclient.get/post(..., referer=TDX_SLB_REFERER.format(code=code))` |
> | `llm_client` | `requests.post`，无 UA，timeout 180s | `httpclient.post(..., timeout=timeout)` —— 显式传分钟级超时 |
>
> **akshare 侧按待办要求未动** —— `market_service._ak` 已是唯一封装、7 个模块在用。
>
> **UA 从 4 个变体统一为 1 份**（Chrome/124）：原为 `124.0.0`（market、valuation）、
> `120.0`（share_service、collectors）、`120.0.0.0`（stock_routes）。
> 通达信原先那个多一个 `.0` 的 `120.0.0.0` 也顺手归一了。

## 问题
akshare 侧**统一得很好**（`market_service._ak` 是唯一封装，7 个模块在用）；
但 **raw HTTP 侧有 4 份**，且 UA 版本不一致、重试策略不一致。

## 证据

### akshare 侧（现状良好，不要动）
`market_service.py:186` `_ak(fn, *args, timeout=None, **kwargs)` —— V8 检测串行化(166-183)、硬超时线程(215-220)、异常→None 降级。
使用者：`board_service:27`、`stock_profile:25`、`price_service:23`、`download_service:30`、
`collectors:17`、`strategies/core/backtest:55`、`valuation_service:177`。

### raw HTTP 侧（4 份）
| 位置 | 特点 |
|---|---|
| `market_service.py:143` `_http_get(url, referer, retries=3, timeout=12, encoding, params)` | **唯一带重试+指数退避**，用 `_session`(54) |
| `valuation_service.py:73` `_get(url, referer=None, timeout=12, encoding=None)` | 无重试、无 session，每次新建连接 |
| `share_service.py:73-77` | 内联 `requests.get(...)`，连函数都没抽 |
| `mentor_routes.py:9` / `stock_routes.py:13` / `llm_client.py:6` | 各自 import requests |

### 常量重复
- `UA` 三份：`market_service.py:46`（Chrome/124）、`valuation_service.py:27`（Chrome/124）、`share_service.py:26`（**Chrome/120，不一致**）
- `SINA_REFERER` 两份：`market_service.py:48`、`valuation_service.py:29`
- 超时散落：`AK_TIMEOUT=15` / `AK_V8_TIMEOUT=60`(160-161)、`share_service.TIMEOUT=10`(28)、valuation 硬编码 10(106/124)

## 建议做法
新建 `backend_fastapi/httpclient.py`：抽 `market_service._http_get`（session + UA + 重试 + 退避），
暴露 `retries` 参数；`valuation_service._get`、`share_service` 内联接入。

## 注意（踩坑点）—— 这条是本项的核心风险
`market_service._http_get` 的 `retries=3` 退避是**串行阻塞**的（`:156` `time.sleep`）。
若把估值侧的调用也接进去，估值页在数据源抖动时的最坏耗时从 **12s 变成 12×3+1.5+1.0 ≈ 38.5s**。
→ 公共层必须允许调用方指定 `retries`，**估值侧显式传 `retries=1` 保持现状**。

另外：
- UA 统一成 Chrome/124 后，`share_service`（腾讯行情）是最敏感的调用方，需实测确认仍能取到值。
- `llm_client` 走的是 LLM 服务，超时语义（可能分钟级）与行情完全不同，接入时别共用默认超时。

## 踩坑点（两条都按要求处理）
1. **重试默认值定 1 而不是 3** —— 待办指出的风险（估值最坏耗时 12s→38.5s）已规避。
   实测改后估值两处调用**都不传 retries**，最坏耗时与改动前完全相同（仍是单次 `timeout`）。
   反过来让调用方**显式**传 `retries=3`（只有盘面那处），所以「默认重试」这个坑不会再被无意踩到。
2. **UA 统一后逐个数据源实测**（待办点名 `share_service` 最敏感）：见下方验收，7 条链路取值与改前**完全一致**。
3. **`llm_client` 单独处理** —— `httpclient.post` 的 `timeout` 是**必填关键字参数、没有默认值**，
   这是「别共用行情默认超时」唯一可靠的防呆手段；`llm_client` 显式传 180 秒。

> **顺带修掉的一处行为回退**（改的过程中自己引入的）
> `httpclient.post` 最初没加 `referer` 参数，接过去后 `stock_routes._tdx_slb` 的
> `Referer` 头丢了 —— 通达信扫雷宝**要求**带 code 的 referer。已给 `post` 补上 `referer` 并复验。

## 验收（2026-10-05 实测）
- [x] raw HTTP 收敛为 1 份；UA / referer 常量唯一 ✅
      全仓 `requests.get/post/Session` **只剩 `core/httpclient.py` 内 1 处**；
      UA 字面量**只剩 1 处**（`core/httpclient.py:36`），其余命中都在「已删除 XX」的注释里
- [x] 估值页耗时与改动前同量级 ✅ 两处调用都用默认 `retries=1`，最坏耗时 = 单次 timeout（未变）
- [x] 盘面数据、腾讯行情（流通股本反推）取数正常 ✅ **7 条链路逐个实况对照，取值与改前完全一致**：
      新浪多标的 200/570 字符、估值 price=1258.62 & shares=12.5008、
      流通股本 float_shares=1,250,081,836 & turnover=0.31（与估值 shares 自洽）、
      东财公告 6 条、东财新闻 9 条、通达信扫雷 JSON 4 键、通达信扫雷 POST 正常返回
- [x] `/health` 无模块错误；后端启动日志无异常 ✅
      `ok=True`、`module_errors` 空、`integrity.ok=True`、stderr 无任何告警
- [x] 接口冒烟 ✅ 报价、估值(POST)、外围环境、大盘资金、板块β、涨停池、
      个股调研（通达信扫雷 + 东财公告）、数据目录 —— 全部 `ok=true`
- [x] 单元测试 52 个全绿 ✅ 真实 `.env` 未被改动
