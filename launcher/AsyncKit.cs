using System;
using System.Collections.Generic;
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
    ///   2. 代码比对（`if (_quickSearch.Text.Trim() != code) return;`）—— 名称查询
    ///   3. busy 标志（`if (_scrBusy) return; _scrBusy = true;`）—— 防重复点击
    ///
    /// 抽成「无守卫的通用方法」后，快速切换股票时旧请求后返回，会把
    /// **新股票的数据盖掉**（交叉刷新）—— 本项唯一但致命的风险。
    /// 所以守卫由调用方通过 `stillValid` 表达，语义**一字不改**。
    ///
    /// ## stillValid 固定在 UI 线程求值
    ///
    /// 守卫 1 读的是闭包捕获的局部变量（后台读安全），但守卫 2 要读
    /// `_quickSearch.Text` —— WinForms 控件**只能**在 UI 线程访问，
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
                        InvokeUi(delegate
                        {
                            if (IsDisposed || Disposing) return;
                            if (stillValid != null && !stillValid()) return;
                            if (onFail != null) onFail(ex);
                        });
                        return;
                    }
                    InvokeUi(delegate
                    {
                        if (IsDisposed || Disposing) return;
                        if (stillValid != null && !stillValid()) return;
                        if (onOk != null) onOk(result);
                    });
                });
            }
            catch (Exception) { }
        }

        // ====================================================================
        //  句柄未创建时的回调排队（构造阶段发起的请求靠它兜住）
        //
        //  实测证据（探针 2，2026-11-20）：句柄未创建时调用 Control.Invoke 会**直接抛**
        //  InvalidOperationException ——「在创建窗口句柄之前，不能在控件上调用 Invoke
        //  或 BeginInvoke」。而本项目里**窗体构造阶段**就会发起一批请求
        //  （各页首次刷新、指数清单…），那会儿窗体还没 Show、句柄还不存在 →
        //  回调被旧的 catch 静默吞掉 → 页面永远等不到结果。
        //
        //  症状（实测）：回测结果页「基准对比」的下拉里永远只有「不对比」，
        //  说明文字停在初始那句，点「重新载入指数清单」也无效 ——
        //  后者是因为「正在取」标志被永久卡住，后续调用只是把回调塞进等待队列。
        //
        //  现在：句柄建好之前到达的回调先排队，等 HandleCreated 之后用
        //  **BeginInvoke** 逐个补发（不能用 Invoke：HandleCreated 是在 UI 线程里
        //  触发的，同步 Invoke 会与它互相等待 → 死锁）。
        // ====================================================================

        /// <summary>句柄未创建期间排队的 UI 回调（HandleCreated 之后补发）。</summary>
        private readonly List<Action> _pendingUiActions = new List<Action>();

        /// <summary>HandleCreated 是否已挂钩（只挂一次）。</summary>
        private bool _pendingUiHooked = false;

        /// <summary>
        /// 回到 UI 线程执行回调 —— **句柄还没建好时不丢**（排队等 HandleCreated）。
        /// </summary>
        private void InvokeUi(Action a)
        {
            if (a == null) return;
            if (IsDisposed || Disposing) return;
            if (IsHandleCreated)
            {
                try { Invoke(a); }
                catch (Exception) { }
                return;
            }
            QueueUiAction(a);
        }

        /// <summary>把回调排队，等句柄建好后补发（句柄已建时直接 BeginInvoke）。</summary>
        private void QueueUiAction(Action a)
        {
            if (a == null) return;
            if (IsDisposed || Disposing) return;
            if (IsHandleCreated)
            {
                try { BeginInvoke(a); }
                catch (Exception) { }
                return;
            }
            lock (_pendingUiActions) { _pendingUiActions.Add(a); }
            if (_pendingUiHooked) return;
            _pendingUiHooked = true;
            HandleCreated += delegate { FlushPendingUiActions(); };
        }

        /// <summary>句柄建好后补发排队的回调。</summary>
        private void FlushPendingUiActions()
        {
            List<Action> list;
            lock (_pendingUiActions)
            {
                list = new List<Action>(_pendingUiActions);
                _pendingUiActions.Clear();
            }
            foreach (Action x in list)
            {
                try { if (IsHandleCreated) BeginInvoke(x); }
                catch (Exception) { }
            }
        }
    }
}
