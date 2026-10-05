using System;
using System.IO;
using System.Net;
using System.Text;

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
    }
}
