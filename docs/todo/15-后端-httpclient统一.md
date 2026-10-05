# 15 · 后端：raw HTTP 请求 4 份 → 公共 httpclient（含 UA/重试口径统一）

> 状态：待办　|　优先级：中　|　收益 ★★★☆☆ / 风险 ★★☆☆☆

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

## 验收
- [ ] raw HTTP 收敛为 1 份；UA / referer 常量唯一
- [ ] 估值页在**正常**与**数据源抖动**两种情况下，耗时与改动前同量级（重试 1 次）
- [ ] 盘面数据、腾讯行情（流通股本反推）取数正常
- [ ] `/health` 无模块错误；后端启动日志无异常
