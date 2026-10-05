# 00 · 阻断级 bug：chip_rank_service 里 `rows` 未定义，周榜结果永不落盘

> 状态：待办　|　优先级：**最高**　|　收益 ★★★★★ / 风险 ★☆☆☆☆

## 问题
`backend_fastapi/chip_rank_service.py` 的 `_worker()` 里用了一个**根本不存在**的变量 `rows`，
导致全市场批量计算每次都在「算完之后、写盘之前」抛 `NameError`。

因为该行位于 `try` 内，`NameError` 会被 `_worker` 末尾的 `except Exception` 捕获，
于是：**跑满 13~25 分钟 → `_STATE` 被置为 error → 结果文件从不生成 → `/api/chip/rank` 永远 `未计算`**。

## 证据
- `backend_fastapi/chip_rank_service.py:279`
  ```python
  "names_resolved": sum(1 for r in rows if r["name"]),
  ```
- 全文件检索 `\brows\b` **只有这一处命中**，既无局部定义、也无模块级全局。
- 同一 payload 里表示「最新一期榜单」的列表变量叫 **`current`**（在构建 `tiers`/`current` 的那几行里）。
- 错误发生时 `except`（约 `:305`）会写 `_STATE(state="error", error="NameError: name 'rows' is not defined", detail=...)`。

## 建议做法
把这一行改成对**实际存在的**列表求值。最小修复：

```python
# 名称取到几条（0 = 没取到，可点「重取名称」补上）
"names_resolved": sum(1 for r in current if r["name"]),
```

注意 `names_resolved` 这行目前位于 payload 字典中**靠前**的位置，而 `current` 是在后面才构建的 ——
要么把该字段挪到 `current` 定义之后，要么先算好一个局部变量（如 `resolved = sum(...)`）再引用。

## 注意
- 别顺手改成遍历 `rows` 而把 `rows` 定义出来 —— `rows` 这个名字与「行」的其它含义容易混，直接用 `current` 更清楚。
- 这是**静默失败**：调用方看不到异常，只能看到状态变成 error。修完建议顺手确认一下
  `_worker` 的 `except` 分支有没有把真实异常传给 `status()`（现在有 `error` + `detail`，够用）。

## 验收
- [ ] 小样本（如 60 只）跑 `ensure(force=True)` → 轮询 `status()` → 最终 `state == "ready"`（不是 error）
- [ ] 结果文件 `E:\stockanaly-data\chip\processed\scr90_rank_YYYYMMDD.json` 生成成功
- [ ] 打开 JSON 确认 `names_resolved` 是个整数（有名称时 > 0）
- [ ] 前端 SCR90周榜页能正常显示三档，状态行不再报「计算失败」
