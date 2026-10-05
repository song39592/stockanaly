# 03 · 前端：HTTP 请求封装补齐三行（纯增量，顺手修一个真 bug）

> 状态：待办　|　优先级：高　|　收益 ★★★★☆ / 风险 ★☆☆☆☆

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

## 验收
- [ ] 三处配置补齐，`csc` 编译 exit 0
- [ ] 大盘/个股/策略/周榜各页各点一次，行为与改动前一致
- [ ] 周榜「重取名称」（120s 超时的大请求）能正常走完
