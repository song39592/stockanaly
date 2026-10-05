# 00 · 阻断级 bug：chip_rank_service 里 `rows` 未定义，周榜结果永不落盘

> 状态：**已完成** —— 复核结论：**该 bug 在当前代码中已不复现，无需改代码**
> （原优先级 ~~最高~~ / 收益 ★★★★★ / 风险 ★☆☆☆☆）

> ### 复核（2026-10-05）
> - 全文件检索 `\brows\b` 与 `names_resolved`：**工作区与 HEAD 均无命中**。
> - 推测成因：在「离榜改成上一期还在、本期才走」那次重写 `_worker()` 时，
>   `names_resolved` 字段随 payload 重构被一并移除，bug 因此**顺带消失**。
> - **实测验收通过**（40 只真实样本）：`state=ready`、`computed=40`、`skipped=0`、耗时 15.3s；
>   结果文件 `E:\stockanaly-data\chip\processed\scr90_rank_20260928.json` **成功生成**；
>   `current` 38 条、名称 38/38；`result(limit=5)` 返回 `ok=True`、`shown=5`。
> - 样本文件已清理，`status()` 已回到 `idle`（不会污染用户首次运行）。
>
> 结论：本项**不改动任何代码**，保留此文件仅作排查记录，避免日后重复排查。

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

## 验收（40 只真实样本 · 2026-10-05 实测）
- [x] 小样本跑 `ensure(force=True)` → 轮询 `status()` → 最终 `state == "ready"`（不是 error）　✅ `ready`
- [x] 结果文件生成成功　✅ `scr90_rank_20260928.json`
- [x] JSON 内容正确　✅ `current` 38 条、名称 38/38。
      注：`names_resolved` 字段已随重构移除，故原「校验它是整数」一项不再适用 ——
      名称可直接从 `current[].name` 读到，实测 38/38 全部有值
- [ ] 前端 SCR90周榜页能正常显示三档 —— **留待人工目测**（后端链路已验证通）
