# 待办清单

> 新建于 2026-11-18。按「承接上下文 + 验收要点」记录，做完一条勾一条并补完成日期。

- [ ] **1. 「上市日」不再用「库里最早日K」近似（需接真实上市日数据源）**
  - 背景：当前 `strategies/core/data.code_first_dates()` 取的是**本地库最早日K**。实测 5601 只里 **4283 只**的 first_date = `2021-08-02`（= 本地下载起点），连 `000001` / `600519` 都被记成那天上市。
  - 后果：上游 PR #112 把「新股/次新股」判定基准日改成**区间起点**（`universe._as_of_date`）后，`天龄 = 2021-08-02 − 2021-08-02 = 0 ≤ 90` → `ipo` 一次剔掉 **5599 只**，实跑只剩 2 只；而页面预览按「今天」算只剔 430 只，预览与实跑口径分叉。
  - 验收：接真实上市日数据（数据源待定，如 akshare `stock_info_a_code_name` 之外带上市日的接口）；取不到时**明确中止/告警**，不再拿最早日K硬当上市日；预览与实跑同一口径；改口径需 bump `universe.SPEC_VERSION` 并同步 `docs/requirement.md` / `strategies/ARCHITECTURE.md` / 报告说明；恢复「剔除新股/次新股」后全市场池不被误杀。

- [ ] **2. 基准对比不在回测范围里选择**
  - 背景：`launcher/StrategyPage.cs` 的 `StBuildScope` 里 g4「基准对比」有 `_stBenchChk` / `_stBench`，和股票池、时间、资金混在「回测范围」页。
  - 目标：把基准选择从回测范围页移出（放到「回测结果」页或结果图表的叠加控件里，见第 3 条）。
  - 验收：回测范围页不再出现基准输入；请求体里 `benchmark` 由结果页控制；历史报告重放仍按报告里记录的 `scope.benchmark` 渲染，不依赖当前页面选择。

- [ ] **3. 回测结果可叠加基准对比（只限指数，上证深证这些）**
  - 背景：后端已支持 `benchmark` 传指数代码（`bars/indices.db` / `core/indices.py`，`GET /api/history/indices` 给清单），也能出 `equity_benchmark`；但前端目前只有「买入持有等权全池」默认基准，且入口在回测范围页。
  - 目标：在「回测结果」页的净值图上叠加指数基准（上证 / 深证成指 / 创业板指 / 沪深300 / 中证1000 / 中证全指等），只允许指数，不接受个股代码。
  - 验收：基准下拉只列 `GET /api/history/indices` 返回的指数；叠加后图例/注释标明基准代码与名称；切换基准不重跑回测（若后端已存 equity_benchmark 则直接重绘）；无本地数据的指数给出可操作提示；与第 2 条一起完成后，回测范围页不再有基准项。

- [ ] **4. 策略回测的进度条是跳动的，先打 LOG 记录后再分析**
  - 背景：用户观察「策略回测」的进度条会**回跳**（不是单调递增）。按项目纪律「无法从源码静态定位根因时，先用日志/诊断取证再改」，**先别改逻辑**。
  - 涉及：后端 `strategies/core/backtest.py` 的 `start_task` / `_on_progress`（进度按 `已测只数/总数` 与阶段上报）、前端 `launcher/StrategyPage.cs` 的任务区轮询 `/api/strategies/backtest/progress` 与 `StJobProgress`。
  - 验收：加临时 LOG 记录每次进度更新的时间戳、phase、done/total、前端收到的值；**日志标注「待清理」**，定位并验证后**立即删除再提交**；给出根因（后端 progress 计算 / 前端轮询乱序 / 阶段切换重置）与修复。

- [ ] **5. SCR 周榜意义不大，替换成 PEAKB 的排名**
  - 背景：`launcher/ChipRankPage.cs` + `features/chip/rank_service.py` 目前是 SCR90 **周级三档**（磨主峰 / 向下破位 / 启动型离榜）；用户判断周榜意义不大。上游刚合入 `formulas/peak.py` 与指标 `chip_htop.py`（筹码密集上沿 HTOP）。
  - 目标：把周榜换成 **PEAKB 排名**（口径待与用户确认：是否就是 `formulas/peak` 的 PEAKB、排名周期 / 升降序 / 分档是否保留）。
  - 验收：先确认 PEAKB 的权威口径与唯一实现位置（禁止在页面层再写一份公式）；页面列头与说明同步；若涉及筹码公式/指标计算改动，按约定跑 golden 并同步 `chipformulas/ARCHITECTURE.md` 或 `indicators/ARCHITECTURE.md`；`docs/requirement.md` / `changelog.md` 同步。
