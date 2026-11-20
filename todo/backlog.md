# 待办清单

> 新建于 2026-11-18。按「承接上下文 + 验收要点」记录。
> **做完一条直接删除**（完成记录、验收数据写进 `docs/changelog.md`，不在这里留痕）。

- [ ] **1. 策略回测的进度条是跳动的，先打 LOG 记录后再分析**
  - 背景：用户观察「策略回测」的进度条会**回跳**（不是单调递增）。按项目纪律「无法从源码静态定位根因时，先用日志/诊断取证再改」，**先别改逻辑**。
  - 涉及：后端 `strategies/core/backtest.py` 的 `start_task` / `_on_progress`（进度按 `已测只数/总数` 与阶段上报）、前端 `launcher/StrategyPage.cs` 的任务区轮询 `/api/strategies/backtest/progress` 与 `StJobProgress`。
  - 验收：加临时 LOG 记录每次进度更新的时间戳、phase、done/total、前端收到的值；**日志标注「待清理」**，定位并验证后**立即删除再提交**；给出根因（后端 progress 计算 / 前端轮询乱序 / 阶段切换重置）与修复。

- [ ] **2. 单日策略「数据齐否」改成按下载任务状态判断（不再逐票覆盖率 + 比例阈值）**
  - 背景：现在的判据是「全库逐票算落后于信号日的只数，超过 `max(5, 总数×1%)` 就算没齐」
    （`features/download/service.py` 的 `_staleness_tolerance`；`freshness()` 与
    `trading_day_context()` 两处共用）。2026-11-20 实测这个阈值**顶不住**：
    全市场 5637 只里 **5572 只已更新到信号日**，剩下 65 只与
    `core.stock_status.suspended_like_codes()` **完全重合**（交集 65、剩余 0）
    —— 全是**停牌/退市**票，本来就没有信号日的行情、补也补不到，
    却把阈值（当时 56 只）顶穿 → 单日策略被拦，还提示「收盘数据未下载完成 /
    等待补齐」（那 65 只永远补不齐）。
  - 已做的**临时**止血：阈值放大到 5%（`STALENESS_TOLERANCE_RATIO`）只为不误拦，
    **不是最终判据**（放大是安全的：真故障会让成千上万只一起落后，不是几十只）。
  - 目标：改成查**下载任务状态**（任务表里的完成结论 / `span_end` / 覆盖到的日期），
    直接回答「这次下载有没有把信号日的数据取齐」——而不是全库逐票扫再按比例猜。
  - 验收：
    · 不再全库扫 `code_latest_dates()` 逐票算覆盖率（现在每判一次 2~6 秒，
      `strategies_routes.py` 已在抱怨重复扫描）；
    · 下载任务「已完成且覆盖到信号日」→ 放行；未完成 / 未覆盖到信号日 → 拦，
      并说清「还差哪一天 / 任务还在跑」；
    · 停牌/退市票**不再参与**这个判定（它们本来就没有行情）；
    · 「真漏下载」仍要拦得住（任务整体失败 / 只覆盖了一部分）；
    · 前端不再靠「5637 只里只有 5572 只更新」这种只数让用户自己猜；
    · `freshness()` 与 `trading_day_context()` 两处**共用同一份**判据（禁止两份实现）。

- [ ] **3. SCR 周榜意义不大，替换成 PEAKB 的排名**
  - 背景：`launcher/ChipRankPage.cs` + `features/chip/rank_service.py` 目前是 SCR90 **周级三档**（磨主峰 / 向下破位 / 启动型离榜）；用户判断周榜意义不大。上游刚合入 `formulas/peak.py` 与指标 `chip_htop.py`（筹码密集上沿 HTOP）。
  - 目标：把周榜换成 **PEAKB 排名**（口径待与用户确认：是否就是 `formulas/peak` 的 PEAKB、排名周期 / 升降序 / 分档是否保留）。
  - 验收：先确认 PEAKB 的权威口径与唯一实现位置（禁止在页面层再写一份公式）；页面列头与说明同步；若涉及筹码公式/指标计算改动，按约定跑 golden 并同步 `chipformulas/ARCHITECTURE.md` 或 `indicators/ARCHITECTURE.md`；`docs/requirement.md` / `changelog.md` 同步。
