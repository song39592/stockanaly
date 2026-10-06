using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Text;
using System.Web.Script.Serialization;

namespace StockPool
{
    /// <summary>
    /// 启动器公共件 · **HTTP 请求封装**（第 03 项）。
    ///
    /// 背景：这些原先住在 `StockPoolLauncher.cs`，被个股页 / 下载页 / 周榜页等共 38 处调用。
    ///
    /// **本项只做增量，不统一错误契约**：`GetText` / `PostJson` / `Probe` 返回 `bool`，
    /// 而 `VRequest`（估值页）**抛异常**且会把 4xx/5xx 的响应体当正常返回（为了显示后端 `detail`）。
    /// 这两种契约各有调用方依赖，改动面覆盖 20 余处错误处理分支，**必须单独评估**。
    ///
    /// **这次真正修的 bug**：`GetText` 以前只设了 `Timeout`（只保护到「收到响应头」），
    /// **响应体读取阶段没有任何超时保护** —— 拉 `/api/chip/rank?limit=100` 这类大响应时，
    /// 服务端中途卡住会一直挂着。补上 `ReadWriteTimeout` 后与 `VRequest` 对齐。
    ///
    /// 另外补的两行是行为对齐，不是修 bug：
    ///   - `Proxy = null`：本机直连，绕开系统代理 / PAC 自动发现
    ///     （否则同一台机器上一半请求走代理、一半不走）；
    ///   - `Expect100Continue = false`：少一个 100-continue 往返。
    ///
    /// ⚠️ **`Probe` 没动**（本项刻意收窄范围）：它仍缺 `Proxy = null`。
    /// 健康检查打的是本机 `127.0.0.1`，要不要一起统一，等你决定。
    ///
    /// 超时值一律由调用方显式传入，不设默认值 —— 15s / 30s / 120s（重取名称）/ 300s（SSE）
    /// 各有业务原因，一刀切会出问题。
    /// </summary>
    internal sealed partial class MainForm
    {
        private static bool Probe(string url)
        {
            return Probe(url, 1500);
        }

        private static bool Probe(string url, int timeoutMs)
        {
            string body;
            return Probe(url, timeoutMs, out body);
        }

        private static bool Probe(string url, int timeoutMs, out string body)
        {
            body = "";
            HttpWebResponse resp = null;
            try
            {
                var req = (HttpWebRequest)WebRequest.Create(url);
                req.Method = "GET";
                req.Timeout = timeoutMs;
                req.ReadWriteTimeout = timeoutMs;
                req.KeepAlive = false;
                resp = (HttpWebResponse)req.GetResponse();
                using (var sr = new StreamReader(resp.GetResponseStream(), Encoding.UTF8))
                {
                    body = sr.ReadToEnd();
                }
                return (int)resp.StatusCode < 400;
            }
            catch
            {
                return false;
            }
            finally
            {
                if (resp != null)
                {
                    try { resp.Close(); } catch { }
                }
            }
        }

        private static bool PostJson(string url, string json, int timeoutMs, out string body)
        {
            body = "";
            HttpWebResponse resp = null;
            try
            {
                var req = (HttpWebRequest)WebRequest.Create(url);
                req.Method = "POST";
                req.ContentType = "application/json; charset=utf-8";
                req.Timeout = timeoutMs;
                req.ReadWriteTimeout = timeoutMs;
                req.Proxy = null;                                // 本项新增：本机直连，绕开系统代理
                req.ServicePoint.Expect100Continue = false;     // 本项新增：少一个 100-continue 往返
                req.KeepAlive = false;
                var bytes = Encoding.UTF8.GetBytes(json);
                req.ContentLength = bytes.Length;
                using (var s = req.GetRequestStream())
                    s.Write(bytes, 0, bytes.Length);
                resp = (HttpWebResponse)req.GetResponse();
                using (var sr = new StreamReader(resp.GetResponseStream(), Encoding.UTF8))
                    body = sr.ReadToEnd();
                return (int)resp.StatusCode < 400;
            }
            catch (WebException ex)
            {
                try
                {
                    if (ex.Response != null)
                        using (var sr = new StreamReader(ex.Response.GetResponseStream(), Encoding.UTF8))
                            body = sr.ReadToEnd();
                }
                catch { }
                return false;
            }
            catch { return false; }
            finally
            {
                if (resp != null)
                {
                    try { resp.Close(); } catch { }
                }
            }
        }

        private static bool GetText(string url, int timeoutMs, out string body)
        {
            body = "";
            HttpWebResponse resp = null;
            try
            {
                var req = (HttpWebRequest)WebRequest.Create(url);
                req.Method = "GET";
                req.Timeout = timeoutMs;
                // ↓↓↓ 本项新增：与 VRequest 对齐。ReadWriteTimeout 是**真 bug 修复** ——
                // Timeout 只保护到「收到响应头」，之后读响应体不受保护，大响应会一直挂着。
                req.ReadWriteTimeout = timeoutMs;
                req.Proxy = null;                                // 本机直连，绕开系统代理 / PAC
                req.ServicePoint.Expect100Continue = false;     // 少一个 100-continue 往返
                req.KeepAlive = false;
                resp = (HttpWebResponse)req.GetResponse();
                using (var sr = new StreamReader(resp.GetResponseStream(), Encoding.UTF8))
                    body = sr.ReadToEnd();
                return (int)resp.StatusCode < 400;
            }
            catch (WebException ex)
            {
                try
                {
                    if (ex.Response != null)
                        using (var sr = new StreamReader(ex.Response.GetResponseStream(), Encoding.UTF8))
                            body = sr.ReadToEnd();
                }
                catch { }
                return false;
            }
            catch { return false; }
            finally
            {
                if (resp != null)
                {
                    try { resp.Close(); } catch { }
                }
            }
        }

        // ================= 业务级请求 =================

        /// <summary>拉取股票名称 + 现价，并在 UI 线程上回调（第 04 项）。
        ///
        /// 背景：个股页 `StockLoadName` 与估值页 `ValuationLookupName` 各自写了一遍
        /// 「调 `/api/stock/quote` → 反序列化 → `Invoke` 回 UI 线程」，约 25 行逐字重复。
        ///
        /// **三处差异刻意留给调用方**，不在这里统一：
        ///   1. **竞态守卫**：个股页比 `_stockCode`、估值页比 `_vCode` —— 依赖各自的输入框。
        ///      回调闭包里能拿到 `code`，所以由调用方在回调第一行自己比。
        ///   2. **失败兜底文案**：估值页多一层「未找到该代码对应的股票」。
        ///   3. **个股页的副作用** `StockAddHistory(code, name)`（补全历史记录里的名称）。
        ///
        /// 回调参数给的是 `(name, display)` 而不是待办建议的 `(name, price)`：
        /// 「名称  +  现价 +  元」这段拼接格式两处也逐字相同，放进本方法才能真正消除
        /// 最后一点重复；现价已格式化进 `display`，目前谁也没单独用到裸 `price`。
        ///
        /// 网络异常统一转成 `onFail("名称查询失败：" + ex.Message)` —— 两处原本逐字相同。
        /// </summary>
        private void FetchStockName(string code, Action<string, string> onOk, Action<string> onFail)
        {
            System.Threading.Tasks.Task.Run(delegate
            {
                try
                {
                    string resp = VRequest("http://127.0.0.1:8000/api/stock/quote?code=" + code, null);
                    var j = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(resp);
                    Invoke((Action)delegate
                    {
                        if (J.IsOk(j) && j.ContainsKey("name"))
                        {
                            string nm = J.Str(J.Get(j, "name"));
                            onOk(nm, nm + PriceSuffix(J.Get(j, "price")));
                        }
                        else
                        {
                            onFail(J.ErrMsg(j));
                        }
                    });
                }
                catch (Exception ex)
                {
                    string m = "名称查询失败：" + ex.Message;
                    try { Invoke((Action)delegate { onFail(m); }); }
                    catch (Exception) { }
                }
            });
        }

        /// <summary>现价后缀：能解析时为「  12.34 元」，否则空串（两处原先逐字相同）。</summary>
        private static string PriceSuffix(object price)
        {
            double? pr = J.NumOrNull(price);
            return pr != null ? "  " + pr.Value.ToString("F2") + " 元" : "";
        }
    }
}
