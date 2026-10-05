# 03 · 前端：HTTP 请求封装补齐三行（纯增量，顺手修一个真 bug）

> 状态：**已完成**（2026-10-05）　|　优先级：高　|　收益 ★★★★☆ / 风险 ★☆☆☆☆

> **回填实际改动**
> 新建 `launcher/Http.cs`，把 `Probe`(×3) / `GetText` / `PostJson` 从 `StockPoolLauncher.cs`
> 搬过去（零行为改动，调用点一行未改），并**补齐缺失的配置**。
>
> | 函数 | 补了什么 |
> |---|---|
> | `GetText` | `ReadWriteTimeout`（**真 bug 修复**）+ `Proxy = null` + `Expect100Continue = false` |
> | `PostJson` | `Proxy = null` + `Expect100Continue = false` |
> | `Probe` | **不动**（见下） |
>
> **真 bug 是什么**：`GetText` 以前只设了 `Timeout`，而 `Timeout` **只保护到「收到响应头」**，
> 之后的响应体读取完全不受保护。请求 `/api/chip/rank?limit=100` 这类大响应时，
> 服务端中途卡住会一直挂着 —— 补上 `ReadWriteTimeout` 后才与 `VRequest` 对齐。
>
> **按踩坑点刻意没做的**
> - **没有统一「抛异常 vs 返回 bool」**：那要动 20 余处错误处理分支，是单点改动面最大的一项，
>   必须单独评估。`VRequest` 把 4xx/5xx 响应体当正常返回的语义**完整保留**。
> - **超时值一律由调用方显式传入，不设默认值** —— 15s / 30s / 120s（重取名称）/ 300s（SSE）各有业务原因。
> - **SSE（`VRequestStream`）没并入同步版**，只是共用「建 req」那几行的思路。
> - `JsonValue`（13 处调用）**没搬**：它是 JSON 助手不是 HTTP 封装，且不属于本项范围。
>
> ⚠️ **留了一处不一致，等你决定**：`Probe` 仍缺 `Proxy = null`（本项刻意收窄范围）。
> 它打的是本机 `127.0.0.1` 健康检查，所以现状能工作；但从「统一口径」角度它与
> `GetText`/`PostJson` 不一致。要不要一并补上？

## 问题
`launcher/` 里有 5 份 HTTP 封装，行为契约并不一致；其中 `GetText`/`PostJson` **漏了三项配置**，
导致「同一台机器上，一半请求走系统代理、一半不走」，且大响应在**读取阶段没有超时保护**。

## 证据

| 函数 | 位置 | 签名 |
|---|---|---|
| `GetText` | StockPoolLauncher.cs:2565 | `static bool GetText(string url, int timeoutMs, out string body)` |
| `PostJson` | StockPoolLauncher.cs:2523 | `static bool PostJson(string url, string json, int timeoutMs, out string body)` |
| `Probe` ×3 | StockPoolLauncher.cs:2466/2471/2477 | 健康检查 |
| `VRequest` | ValuationPage.cs:604 | `static string VRequest(string url, string body=null, int timeoutMs=90000)`（**抛异常**） |
| `VRequestStream` | MarketPage.cs:898 | SSE 流式（硬编码 300000ms） |

差异对照：

| 配置项 | VRequest | VRequestStream | GetText | PostJson |
|---|---|---|---|---|
| `Proxy = null` | ✅ :607 | ✅ :901 | ❌ **缺** | ❌ **缺** |
| `ReadWriteTimeout` | ✅ :610 | ✅ :904 | ❌ **缺（真 bug）** | ✅ |
| `Expect100Continue = false` | ✅ :614 | ✅ :906 | — | ❌ **缺** |
| 错误契约 | 抛异常 | — | 返回 bool | 返回 bool |
| 默认超时 | 90000 | 300000 | 无默认（各处显式传） | 无默认 |

`GetText` 漏 `ReadWriteTimeout` 的实际影响：请求 `/api/chip/rank?limit=100` 这类**大响应体**时，
`Timeout` 只保护到「收到响应头」，之后的 body 读取不受保护。

## 建议做法（本项只做增量，不统一契约）
只给 `GetText` 补三行（与 VRequest 对齐）：
```csharp
req.Proxy = null;                    // 本机直连，绕开系统代理/自动发现
req.ReadWriteTimeout = timeoutMs;    // 大响应体的读取阶段也要超时保护
req.ServicePoint.Expect100Continue = false;   // 少一个 100-continue RTT
```
`PostJson` 补 `Proxy = null` 与 `Expect100Continue = false`。

## 注意（踩坑点）
- **不要在本项统一「抛异常 vs 返回 bool」** —— 那要动 StockPage 11 处 + 其余页约 13 处错误处理分支，
  是单点改动面最大的一项，应单独评估（可另开一项）。
- `VRequest` **会把 4xx/5xx 的响应体当正常返回**（ValuationPage.cs:625-629），是为了能显示后端 `detail`。
  这个语义不能被 bool 版吞掉。
- 超时值不能一刀切：15s / 30s / 120s（重取名称）/ 300s（SSE）各有业务原因。
- SSE 流式（`VRequestStream`）是独立能力，最多共用「建 req 那几行」，不要并入同步版。

## 验收（2026-10-05）
- [x] `GetText` 三处配置补齐 ✅ `Http.cs:131-133`（ReadWriteTimeout / Proxy / Expect100Continue）
- [x] `PostJson` 两处配置补齐 ✅ `Http.cs:87-88`（Proxy / Expect100Continue）
- [x] `csc` 编译 exit 0 ✅ 无 lint 报错；编译通过同时证明**没有重复定义**
- [x] 骨架文件里已无这些函数 ✅ `StockPoolLauncher.cs` 检索为空
- [x] `VRequest` / `VRequestStream` 未被改动 ✅ 仍保留各自的 `Proxy = null`
- [x] 错误契约未统一（按要求） ✅ bool 版与抛异常版并存
- [ ] 大盘 / 个股 / 策略 / 周榜各页点一次，行为与改动前一致 —— **未做**（需人工）
- [ ] 周榜「重取名称」（120s 超时的大请求）能正常走完 —— **未做**（需人工；
      这正是 `ReadWriteTimeout` 修的就是场景，建议重点试）
