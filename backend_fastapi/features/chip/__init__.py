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
   打断的循环依赖**。`indicators/data.py` 里的 `from features.chip import formulas`
   **仍留在函数体内**（`_chip_frames` 与 `clear_cache` 两处）。若把它提到模块顶层会立刻
   触发循环导入（`formulas` → `core.data` → `indicators.data` → `formulas`）。

3. **`formulas/core/registry.py` 里的 `_FORMULA_PACKAGE` 已改为按 `__package__` 推导**。
   原来硬编码 `"chip_formulas"`，`importlib.import_module` 靠它拼出公式子模块名。
   顶层转发**无法转发「按包名导入子模块」**（同一文件会被当两个模块加载两次），
   所以必须让它跟着包走。

4. **顶层转发文件（`chip_formulas.py` / `chip_dist_routes.py`）已删除**。
   原先四处依赖（`indicators/data.py`、`indicators/registry.py`、`strategies/scr90.py`、
   `main.py` 的 `ROUTE_MODULES`）已全部改指本包的新路径。现在「筹码包在哪」只有一个答案：
   `features/chip/formulas/`，不必再靠顶层别名维持两种写法。

5. **筹码矩阵的缓存唯一入口是 `formulas.core.base.compute_matrix`**（第 04 项）。
   指标侧的 `indicators/data._chip_frames` **不再自带 `lru_cache`** —— 否则「右侧筹码峰
   窗口 + COST/SCR/WIN 三个指标」会把同一只票的矩阵独立算两遍。缓存键里**必须**含
   `offline`：它决定要不要做锁仓修正，是业务口径差异（见 `core/scr.py` 顶部说明）。
"""
