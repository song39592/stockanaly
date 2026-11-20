# 待办清单

> 新建于 2026-11-18。按「承接上下文 + 验收要点」记录。
> **做完一条直接删除**（完成记录、验收数据写进 `docs/changelog.md`，不在这里留痕）。

- [ ] **1. 策略回测的进度条是跳动的，先打 LOG 记录后再分析**
  - 背景：用户观察「策略回测」的进度条会**回跳**（不是单调递增）。按项目纪律「无法从源码静态定位根因时，先用日志/诊断取证再改」，**先别改逻辑**。
  - 涉及：后端 `strategies/core/backtest.py` 的 `start_task` / `_on_progress`（进度按 `已测只数/总数` 与阶段上报）、前端 `launcher/StrategyPage.cs` 的任务区轮询 `/api/strategies/backtest/progress` 与 `StJobProgress`。
  - 验收：加临时 LOG 记录每次进度更新的时间戳、phase、done/total、前端收到的值；**日志标注「待清理」**，定位并验证后**立即删除再提交**；给出根因（后端 progress 计算 / 前端轮询乱序 / 阶段切换重置）与修复。

- [ ] **2. SCR 周榜意义不大，替换成 PEAKB 的排名**
  - 背景：`launcher/ChipRankPage.cs` + `features/chip/rank_service.py` 目前是 SCR90 **周级三档**（磨主峰 / 向下破位 / 启动型离榜）；用户判断周榜意义不大。上游刚合入 `formulas/peak.py` 与指标 `chip_htop.py`（筹码密集上沿 HTOP）。
  - 目标：把周榜换成 **PEAKB 排名**（口径待与用户确认：是否就是 `formulas/peak` 的 PEAKB、排名周期 / 升降序 / 分档是否保留）。
  - 验收：先确认 PEAKB 的权威口径与唯一实现位置（禁止在页面层再写一份公式）；页面列头与说明同步；若涉及筹码公式/指标计算改动，按约定跑 golden 并同步 `chipformulas/ARCHITECTURE.md` 或 `indicators/ARCHITECTURE.md`；`docs/requirement.md` / `changelog.md` 同步。
