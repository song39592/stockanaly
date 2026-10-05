# 12 · 后端：`chip_rank_service` 的阈值常量改为直接引用 `chip_service`

> 状态：待办　|　优先级：中　|　收益 ★★★☆☆ / 风险 ★☆☆☆☆

## 问题
`chip_rank_service` 与 `chip_service` 是「同一套结论形态、换了数据源」（`chip_rank_service.py:9` 原文），
但**两套阈值各自定义**，同步完全依赖注释里的人工承诺 —— 改一处不会触发另一处的任何告警。

## 证据

`chip_service.py`（权威侧）：
- `FULL_WEEKS = 5`（:33）、`MIN_WEEKS_ON = 2`（:34）
- `LAUNCH_THRESHOLD = 10.0`（:37）
- `MIN_MARKET = 100.0`（:35）、`MAX_MARKET = 800.0`（:36）

`chip_rank_service.py`（复制侧，全是「对齐注释」）：
- `:57` `DEFAULT_WEEKS = 5   # 连续几期在榜算「全勤」（对齐 chip_service.FULL_WEEKS）`
- `:60` `DEFAULT_CHG_DAYS = 30  # （对齐 chip_service 的「30日涨幅%」）`
- `:61` `DEFAULT_LAUNCH = 10.0  # （对齐 LAUNCH_THRESHOLD）`

`chip_rank_service.py:51` 已 `import chip_service`，且已复用它的 `PROCESSED_DIR`（:54）与 `expected_weeks` ——
说明依赖方向本来就成立，只是常量没接上。

## 建议做法
把「对齐注释」改成「代码引用」：
```python
DEFAULT_WEEKS    = chip_service.FULL_WEEKS
DEFAULT_LAUNCH   = chip_service.LAUNCH_THRESHOLD
DEFAULT_CHG_DAYS = chip_service.CHG_DAYS_DEFAULT   # 若 chip_service 里没有对应常量，先在它那边定义一个
```
若 `chip_service` 里没有「涨幅窗口」的具名常量，应先在 `chip_service` 侧补一个（它内部现在用「30日涨幅%」这个字段名），
再让 `chip_rank_service` 引用 —— 不要反过来在 chip_rank 侧再抄一份。

## 注意（踩坑点）
- 改完要确认默认值**数值不变**（5 / 10.0 / 30），否则本周已算好的缓存与新的默认值口径不一致，
  建议改完后**清一次本周缓存**重新算（或用 `force=True` 重刷一次）。
- `chip_rank_service` 的 `weeks` 参数有 `ge=2, le=12` 的校验（路由侧），`FULL_WEEKS=5` 落在范围内，无冲突。

## 验收
- [ ] `chip_rank_service` 里不再有「对齐 chip_service」的人工承诺注释，全部改为直接引用
- [ ] `DEFAULT_WEEKS / DEFAULT_LAUNCH / DEFAULT_CHG_DAYS` 数值与改动前一致（5 / 10.0 / 30）
- [ ] 改 `chip_service.LAUNCH_THRESHOLD` 时 `chip_rank_service` 会跟着变（可用一次性临时改动验证）
- [ ] 小样本重跑一次 `ensure(force=True)`，三档结果与改动前一致
