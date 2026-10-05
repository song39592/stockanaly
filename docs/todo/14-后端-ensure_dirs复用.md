# 14 · 后端：`chip_rank_service` 复用 `chip_service._ensure_dirs`

> 状态：待办　|　优先级：低　|　收益 ★★☆☆☆ / 风险 ☆☆☆☆☆（零风险，一行）

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
