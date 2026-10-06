# 12 · 后端：`chip_rank_service` 的阈值常量改为直接引用 `chip_service`

> 状态：**已完成**（2026-10-06）　|　优先级：中　|　收益 ★★★☆☆ / 风险 ★☆☆☆☆
> 注：待办里的文件名是**搬家前**的旧名 —— `chip_service` → `features/chip/scr_service.py`，
> `chip_rank_service` → `features/chip/rank_service.py`（第 34 项）。

## 实际落地

- `DEFAULT_WEEKS  = chip_service.FULL_WEEKS`（5）
- `DEFAULT_LAUNCH = chip_service.LAUNCH_THRESHOLD`（10.0）
- ⭐ **`DEFAULT_CHG_DAYS` 刻意不对齐** —— 待办建议「若 scr_service 里没有对应常量，
  先在它那边补一个 `CHG_DAYS_DEFAULT` 再引用」，**这条建议是错的**，照做会引入
  一个骗人的常量：

  | | 本侧 `DEFAULT_CHG_DAYS` | `scr_service` 的 `chg30` |
  |---|---|---|
  | 30 的含义 | **交易日窗口**（`_ret_at` 里 `s.iloc[-1 - days]`，取交易日序列往前第 30 根） | **导出文件里现成的列名**「30日涨幅%」 |
  | 谁决定 | 本项目可调（路由 `chg_days` 参数可传） | 行情软件的统计口径，**本项目无从控制** |
  | 是「天数」参数吗 | 是 | **不是** —— 它按列名直读，没有天数概念 |

  数值同为 30 **纯属巧合**。若为「看起来统一」而在 `scr_service` 里造一个
  `CHG_DAYS_DEFAULT`，它自己一个调用点都没有，纯装饰且误导后人。
  真正该统一的「涨幅阈值百分比」已由 `DEFAULT_LAUNCH` 接管。

- 顺带把「对齐 chip_service 的人工承诺注释」全删了（模块内该措辞 0 处）。

## 验收
- [x] `rank_service` 里不再有「对齐 chip_service」的人工承诺注释，全部改为直接引用
- [x] `DEFAULT_WEEKS / DEFAULT_LAUNCH / DEFAULT_CHG_DAYS` 数值与改动前一致（5 / 10.0 / 30）
- [x] 改 `chip_service.LAUNCH_THRESHOLD` 时 `rank_service` 会跟着变
      —— 实测：临时把上游改成 33.0，`reload` 后 `DEFAULT_LAUNCH` 读到 33.0（随后复原）
- [x] `weeks=2..12` 的路由校验与 `FULL_WEEKS=5` 无冲突（5 落在区间内）
- [x] 52 个单元测试全绿；`/api/chip/rank`、`/api/chip/dist` 回归正常

## ⚠️ 本项的实测教训（比改动本身更重要）

验证脚本为了测「删掉 processed 目录能否自动重建」，**真删了生产数据盘上的
`<data>/chip/processed/`**，把用户的周榜结果缓存清空了 —— 正是 `bug-04`
（测试夹具污染生产数据库）同形态的错误，自己犯了。

- 正确做法：把 `DATA_DIR` 用环境变量指到临时目录再测（`test_support.py` 的
  `require_data_isolation` 就是干这个的），或只测 `_ensure_dirs()` 的返回值而不删目录。
- 已触发 `POST /api/chip/rank/refresh?force=true` 重算恢复（5585 只，约十几分钟）。
- **教训**：验证「目录能自动重建」这类需求时，要问自己「删的是谁的数据」。

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
