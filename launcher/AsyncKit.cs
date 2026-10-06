using System;
using System.Windows.Forms;

namespace StockPool
{
    /// <summary>
    /// 启动器公共件 · **异步加载骨架**（第 18 项）。
    ///
    /// 这套 `Task.Run(请求) + Invoke(渲染)` 骨架在前端出现 20+ 次。
    /// 抽它不是为了少写几行，而是为了让**竞态守卫从「藏在调用点的闭包里」
    /// 变成「显式的一个参数」**。
    ///
    /// ## 守卫为什么**刻意不内置**
    ///
    /// 本项目有三类互不相同的守卫，硬凑成一种必然出交叉刷新：
    ///   1. round 比对（`if (round != _stockRound) return;`）—— 切个股时作废旧请求
    ///   2. 代码比对（`if (_stockCode.Text.Trim() != code) return;`）—— 名称查询
    ///   3. busy 标志（`if (_scrBusy) return; _scrBusy = true;`）—— 防重复点击
    ///
    /// 抽成「无守卫的通用方法」后，快速切换股票时旧请求后返回，会把
    /// **新股票的数据盖掉**（交叉刷新）—— 本项唯一但致命的风险。
    /// 所以守卫由调用方通过 `stillValid` 表达，语义**一字不改**。
    ///
    /// ## stillValid 固定在 UI 线程求值
    ///
    /// 守卫 1 读的是闭包捕获的局部变量（后台读安全），但守卫 2 要读
    /// `_stockCode.Text` —— WinForms 控件**只能**在 UI 线程访问，
    /// 后台读会抛跨线程异常。故判定点放在 `Invoke` **内部**，与改动前一致。
    ///
    /// ## 不在本骨架范围内的
    ///
    /// - **轮询**（`ChipRankPage._scrTimer` 那类「定时查直到状态变化」）：
    ///   需可取消、会重复触发，是另一回事，仍各页自理。
    /// - **busy 标志的复位**：仍由调用点在 `onOk` / `onFail` 里自己做
    ///   （与改动前一致）。⚠️ 抽公共方法时最容易漏的就是失败路径的复位，
    ///   漏了会导致按钮点一次之后永久卡死。
    /// </summary>
    internal sealed partial class MainForm
    {
        /// <summary>
        /// 后台执行 <paramref name="work"/>，成功/失败都回 UI 线程，
        /// 且**仅当 <paramref name="stillValid"/> 仍为真**时才回调。
        /// </summary>
        /// <param name="work">在**后台线程**执行：发请求 + 反序列化。不要碰控件。</param>
        /// <param name="onOk">在 **UI 线程**执行：渲染。</param>
        /// <param name="onFail">在 **UI 线程**执行：失败提示。同样受守卫约束。</param>
        /// <param name="stillValid">
        /// 在 **UI 线程**求值的守卫；返回 false 表示「结果已过期，丢弃」。
        /// 不需要守卫的调用点传 <c>null</c>。
        /// </param>
        private void RunUi<T>(Func<T> work, Action<T> onOk, Action<Exception> onFail,
                             Func<bool> stillValid)
        {
            if (IsDisposed || Disposing) return;
            try
            {
                System.Threading.Tasks.Task.Run(delegate
                {
                    T result;
                    try
                    {
                        result = work();
                    }
                    catch (Exception ex)
                    {
                        try
                        {
                            Invoke((Action)delegate
                            {
                                if (IsDisposed || Disposing) return;
                                if (stillValid != null && !stillValid()) return;
                                if (onFail != null) onFail(ex);
                            });
                        }
                        catch (Exception) { }
                        return;
                    }
                    try
                    {
                        Invoke((Action)delegate
                        {
                            if (IsDisposed || Disposing) return;
                            if (stillValid != null && !stillValid()) return;
                            if (onOk != null) onOk(result);
                        });
                    }
                    catch (Exception) { }
                });
            }
            catch (Exception) { }
        }
    }
}
