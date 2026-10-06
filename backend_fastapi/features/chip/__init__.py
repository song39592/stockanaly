# -*- coding: utf-8 -*-
"""筹码体系（第 34 项）—— 全仓耦合面最大的一块，**所以最后搬**。

    scr_service.py    <- 原 chip_service.py       导入 xls -> 多期合并 -> 三档分类
    rank_service.py   <- 原 chip_rank_service.py  SCR90 周级三档（本地自算）
    scr_routes.py     <- 原 chip_routes.py        /api/chip/scr/*
    dist_routes.py    <- 原 chip_dist_routes.py   /api/chip/dist/*
    rank_routes.py    <- 原 chip_rank_routes.py   /api/chip/rank/*
    formulas/         <- 原 chip_formulas/ 整包迁移（自带 ARCHITECTURE.md 与注册机制）

⚠️ **文件被改名了**（`chip_service` -> `scr_service` 等），因为三个 routes 模块
搬进来会撞名。原顶层名一律保留别名转发（`sys.modules[__name__] = _impl`）。

## 三个「不能顺手统一」的地方

1. **周口径的权威定义方是 `scr_service`**：
   `MARKET_CLOSE_HOUR` / `_as_moment` / `week_start` / `expected_weeks`。
   `rank_service` 通过薄封装（`_as_date` / `_week_nodes`）复用它，**这是正确的复用姿势**，
   不要改成各算一份。本包内 `rank_service` 仍能找到 `scr_service`。

2. **`formulas/core/data.py` 与 `indicators/data.py` 之间有一条**刻意用延迟 import
   打断的循环依赖**。`indicators/data.py` 里的 `import chip_formulas` 仍留在**函数体内** ——
   **本项刻意没动它**（走顶层转发）。若把它提到顶层会立刻触发循环导入。

3. **`formulas/core/registry.py` 里的 `_FORMULA_PACKAGE` 已改为按 `__package__` 推导**。
   原来硬编码 `"chip_formulas"`，`importlib.import_module` 靠它拼出公式子模块名。
   顶层转发**无法转发「按包名导入子模块」**（同一文件会被当两个模块加载两次），
   所以必须让它跟着包走。
"""
