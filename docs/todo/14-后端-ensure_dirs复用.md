# 14 · 后端：`chip_rank_service` 复用 `chip_service._ensure_dirs`

> 状态：**已完成**（2026-10-06）　|　优先级：低　|　收益 ★★☆☆☆ / 风险 ☆☆☆☆☆（零风险，一行）
> 注：待办里的文件名是**搬家前**的旧名（`chip_service` → `features/chip/scr_service.py`，
> `chip_rank_service` → `features/chip/rank_service.py`，第 34 项）。

## 实际落地

`rank_service._worker` 写盘前那行内联的 `os.makedirs(PROCESSED_DIR, exist_ok=True)`
已换成 `chip_service._ensure_dirs()`（模块内 `makedirs` 只剩注释里那一处提及）。

按待办的建议**只做复用、不改名**（`_ensure_dirs` 改公开名要动 6 个调用点，留给后续）。
调用跨模块私有函数与它现有的 `PROCESSED_DIR` / `expected_weeks` 复用同性质，
代码里就地注明了这一点。

## 验收
- [x] `rank_service` 不再内联 makedirs
- [x] 调用 `_ensure_dirs()` 后 `processed/` 可自动重建（实测：删除后调用即重建，
      连带 `DATA_DIR` / `RAW_DIR` 一并建好）
- [x] 52 个单元测试全绿

> ⚠️ 验证时**误删了生产 `processed/` 目录**，详见第 12 项里记的教训；
> 已触发 `force=true` 重算恢复。

## 问题
`chip_rank_service` 已经 `import chip_service`（:51），也直接复用了它的 `PROCESSED_DIR`（:54），
却自己又内联了一次 `os.makedirs(PROCESSED_DIR, exist_ok=True)`，没复用现成的 `_ensure_dirs`。

## 证据
- `chip_service.py:52-54`：
  ```python
  def _ensure_dirs():
      for path in (DATA_DIR, RAW_DIR, PROCESSED_DIR):
          os.makedirs(path, exist_ok=True)
  ```
  被 `chip_service` 调用 6 次（:70, 82, 92, 117, 143, 508）
- `chip_rank_service.py:296`（`_worker` 写盘前）：内联的 `os.makedirs(PROCESSED_DIR, exist_ok=True)`

## 建议做法
把 `chip_rank_service.py:296` 那行改成 `chip_service._ensure_dirs()`。

## 注意（踩坑点）
- `_ensure_dirs` 是下划线开头的**模块私有**函数，`chip_rank_service` 跨模块调用它属于「跨包用私有」——
  与它现在复用 `PROCESSED_DIR`、`expected_weeks` 是同一性质（这两个也是模块级成员，不算私有）。
  若要讲究，可顺手把 `_ensure_dirs` 改名为公开名 `ensure_dirs`，但这会动到 `chip_service` 的 6 个调用点 ——
  **本项建议只做复用，改名留给后续**。
- 行为差异：`_ensure_dirs` 会一并创建 `DATA_DIR` 和 `RAW_DIR`（不只是 processed），无副作用。

## 验收
- [ ] `chip_rank_service` 不再内联 makedirs
- [ ] 删除 `<data>/chip/processed` 目录后跑一次小样本，目录能自动重建、结果正常落盘
