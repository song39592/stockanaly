# 34 · 后端：`features/chip/`（筹码体系）—— **被依赖最多，最后搬**

> 状态：待办　|　优先级：高（但**排在最后**）　|　收益 ★★★★☆ / 风险 ★★★★☆　|　**依赖 27~33**

## 问题
筹码体系是**耦合面最大**的一块：既被路由调用，又被 `indicators` 与 `strategies` 反向依赖，
还跨包复用 `chip_service` 的目录常量与周口径工具。所以它必须**最后搬**。

## 建议目标
```
backend_fastapi/features/chip/
├─ __init__.py
├─ scr_service.py   ← 原 chip_service.py（导入 xls → 多期合并 → 三档分类；含 week_start / expected_weeks）
├─ scr_routes.py    ← 原 chip_routes.py（/api/chip/scr/*）
├─ dist_routes.py   ← 原 chip_dist_routes.py（/api/chip/dist/*）
├─ rank_service.py  ← 原 chip_rank_service.py（SCR90 周级三档，本地自算）
├─ rank_routes.py   ← 原 chip_rank_routes.py（/api/chip/rank/*）
└─ formulas/        ← 原 chip_formulas/（整包迁移，它已有独立的 ARCHITECTURE.md 与注册机制）
```

## 注意（踩坑点）—— 本项风险最高，逐条看
1. **`chip_service` 是「周口径」的权威定义方**：
   `week_start`（:299）/ `expected_weeks`（:305）/ `_as_moment`（:279）/ `MARKET_CLOSE_HOUR`（:276）。
   `chip_rank_service` 通过薄封装复用它（`_as_date` / `_week_nodes`，是**正确的复用姿势**）。
   → 移动后**必须**确认 `chip_rank_service` 仍能 import 到，且周节点计算结果不变。
2. **`chip_rank_service` 依赖 `chip_service.PROCESSED_DIR`**（:54）与 `strategies.core.data.list_universe_codes`——
   三方依赖，搬家顺序错了会循环导入风险。
3. **`strategies/scr90.py` 与 `chip_rank_service` 都 import `chip_formulas`** —— `chip_formulas` 移进 `features/chip/` 后，
   要确认 `import chip_formulas` 仍能解析（或保留顶层 `chip_formulas.py` 转发）。
4. `chip_formulas/` 是**独立注册体系**（`@chip_formula`、文件名即 id、LOAD 时五步校验）。
   它有 `ARCHITECTURE.md` 明确规定「core/data.py 是唯一数据入口」—— 整包迁移时**不要打散它的内部结构**。
5. `chip_formulas/core/data.py` 与 `indicators/data.py` 之间有**刻意用延迟 import 打断的循环依赖**
   （见 `indicators/data.py` 相关注释）—— 搬家时若把 import 改成顶层，会立刻触发循环导入。
6. 本项涉及 `chip_rank_service` 时，**必须先完成第 00 项**（`rows` NameError），否则无法验证。

## 建议做法
1. 先整包 `git mv chip_formulas/ → features/chip/formulas/`，并在顶层留 `chip_formulas.py` 转发包。
2. 再迁 `chip_service` / `chip_rank_service`（**先 service 后 routes**，routes 只是 HTTP 层）。
3. 每迁一个就重启后端 + 跑一次小样本周榜验证。
4. 全部迁完并确认无外部 import 后，才删转发。

## 验收
- [ ] `features/chip/` 建立（含 `formulas/` 整包）
- [ ] 周榜：小样本跑通，`state == "ready"`，三档与迁移前一致
- [ ] 周节点计算结果与迁移前一致（打印 `expected_weeks` 对比）
- [ ] 个股页 K 线右侧筹码分布正常（三角形峰形 + 横轴刻度 + 锁仓提示）
- [ ] 策略 `scr90` 仍能跑通（它 import `chip_formulas`）
- [ ] `/health` 的 `_module_errors` 为空
- [ ] `chip_formulas.validation_report()` 中 invalid 为 0
