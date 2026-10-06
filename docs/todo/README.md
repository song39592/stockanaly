# 待办目录 · 现状说明

> 2026-10-06 清理：**39 个已完成的工单文件已删除**（01~18、20~35 共 35 个 +
> bug-01~04 共 4 个），决策与踩坑记录已沉淀到 `docs/changelog.md` 的
> 「未发布」章节；需要工单全文时从 git 历史取（文件名未变）。
>
> **唯一保留的待办：[19-仓库-gitignore按五类重写.md](19-仓库-gitignore按五类重写.md)（状态：待办）**。

## 已完成项一览（详情见 changelog / git 历史）

| 范围 | 内容 | 状态 |
|---|---|---|
| 01~08 | 前端公共件：色彩 / JSON 工具 / HTTP / 取名合一 / SubTab / KPI 卡片 / 图表基类 / 小样板 | ✅（08 为 3/6 完成 + 3 类判定不做） |
| 09~15 | 后端公共化：json_safe / scr90 内核 / 错误体统一 / 阈值对齐 / ParamSpec / ensure_dirs / httpclient | ✅ |
| 16 | market_symbol 三份口径 | ✅ 评估后**保持并存**，行为由 `test_market_symbol.py` 锁定 |
| 17~18 | kline_service 出口收口 / 异步骨架 `AsyncKit.RunUi` | ✅ |
| 20~26 | 仓库与前端结构：依赖锁 / 数据盘重排 / 日志落盘 / dumplog / 定时清理 / 网页分目录 / 公共件归位 | ✅ |
| 27~34 | 后端分层：`core/` 子包 + `features/{market,stock,history,download,system,mentor,chip}` | ✅ |
| 35 | 目录改名 | ✅ **评估后决定不做**（157 处引用 + venv 9668 文件，验收需从零重装 venv）；已交付 `config.py` 提示文案动态化 |
| bug-01~04 | 筹码偶发无响应 / 多源回退硬化 / K 线图变白 / 测试夹具污染生产库 | ✅ 全部修复 |
| bug-05 | `_percentile` 删定义漏删调用 → `/api/chip/dist` 500 | ✅ |
| bug-06 | 周榜 `rows` 未定义 → 每次重算最后一步 NameError、永不落盘 | ✅ |
