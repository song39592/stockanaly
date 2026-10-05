# 10 · 后端：SCR90 序列计算合并（2 份逐字符雷同 + 修 1 个判空缺陷）

> 状态：待办　|　优先级：高　|　收益 ★★★★☆ / 风险 ★☆☆☆☆
> ⚠️ **前置：必须先完成第 00 项**（chip_rank_service 的 `rows` NameError），否则该模块现在跑不出结果、无法验证。

## 问题
SCR90 序列的「按累计筹码取价格分位」这一算法，在仓库里有 **2 份逐字符完全一致**的实现，
另有 1 份单帧版写着**判空缺陷**。

## 证据

### 5a · `_percentile` 实为 2 份（不是 3 份）
| 位置 | 签名 | 空筹码返回 |
|---|---|---|
| `indicators/chip_scr.py:19` | `_percentile(centers, row, q) -> float` | `float("nan")` |
| `chip_formulas/core/base.py:148` | `_percentile(centers, pct, q) -> float \| None` | `None` |

函数体本质相同（`np.interp(q/100, cumsum(row)/total, centers)`），**唯一实质差异是空值语义 NaN vs None**。
（`chip_rank_service` 里**没有** `_percentile`，它把插值内联在 `_scr90_series` 的 :113-114）

### 5b · 逐字符雷同的 2 份（本项目最干净的合并候选）
- `chip_rank_service.py:99-119` `_scr90_series(res) -> pd.Series`
- `strategies/scr90.py:37-57` `_scr90_series(res) -> pd.Series`

函数体**完全一致**（含 docstring 措辞、变量名 `pct/centers/out/cum/p5/p95/den`、
`np.full(pct.shape[0], np.nan)` 初始化、跳过 `den == 0`）。

### 5c · 含缺陷的单帧版
`chip_formulas/core/base.py:157-179` `_stats_of(pct, centers, close_i)` —— 第 **170** 行：
```python
if p5 and p95 and (p95 + p5) != 0:
```
**缺陷**：`p5` 为 `0.0`（价格分位恰为 0）时 `if p5` 为假 → 静默返回 `scr90=None`。
另两处用 `den == 0` 判断才是对的。
（另有一份指标集成版 `indicators/chip_scr.py:42-58`，走 `chip_rows_of` 的 NaN 行对齐，同时算 SCR90/SCR70）

## 建议做法
在 `chip_formulas/core/scr.py` 提供两个函数：
- `scr_series(pct, centers, q_lo=5, q_hi=95) -> np.ndarray`（**NaN 语义**）→ 供 5b 两处 + 5c-ii
- `scr_frame(pct_row, centers) -> float | None`（**None 语义**）→ 供 5c-i，并**顺手把 `if p5 and p95` 改成 `den != 0`**

## 注意（踩坑点）
1. **只合并算法内核，不合并调用参数**：
   - `chip_rank_service` 与 `strategies/scr90.py` 都是 `offline=True`（**无锁仓修正**）
   - `indicators/chip_scr.py` 走 `qfq`（**带锁仓修正**）
   两者口径不同，合并代码时不要把参数也统一了。
2. 修 `if p5 and p95` → `den != 0` 是**行为变更**：会多产出少量原本为 `None` 的 scr90 值，
   需确认前端对 `scr90: null` 的处理（副图与周榜都要看一眼）。
3. `chip_rank_service` 已 `import chip_formulas`（:50），无新增依赖。

## 验收
- [ ] 三处 SCR90 计算共用同一内核；两份逐字符雷同的函数消失
- [ ] 周榜结果与合并前一致（可先在合并前后各跑一次小样本，逐值对比 `scr90`）
- [ ] 策略 `scr90` 跑同样池子，信号条数与合并前一致
- [ ] `if p5 and p95` 已修；分位为 0 的极端样本能产出非 None 的 scr90
