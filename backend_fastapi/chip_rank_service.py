# -*- coding: utf-8 -*-
"""兼容转发：`chip_rank_service` 已移入 `features/chip/rank_service.py`（第 34 项，**并改名**）。

**这是过渡文件，不是实现。** 新代码请直接用 `features.chip.rank_service`。

改名原因：见 `chip_service.py` 转发。**转发名保持原样**，既有调用方
（`chip_rank_routes.py` / `cleanup.py:140` / `dumplog.py:141`）一行都不用改。

⚠️ **本模块有「模块级读取」与「函数内延迟导入」两种依赖，都刻意保持原样**：

1. **模块级**（`:54`）`PROCESSED_DIR = chip_service.PROCESSED_DIR`
   —— 转发是别名，拿到的仍是同一个值（已实测一致）。
2. **函数内**（`:140`）`from strategies.core.backtest import _load_names as remote`
   —— 顶层不导入 `strategies.core.backtest`，是**为了避开循环导入**
   （`strategies.scr90` 依赖 `chip_formulas`，而本模块又依赖 `strategies.core.data`）。
   **本项刻意没把它提到顶层**，否则会立刻触发循环导入。

## 为什么用 `sys.modules` 别名，而不是 `from ... import *`

别名转发拿到的是**同一个模块对象**，因此 `_RUNTIME` 之类的模块级可变状态是同一份
（周榜刷新进度会被 `cleanup` / `dumplog` 看到），`patch.object(...)` 真正生效。
"""
import sys

from features.chip import rank_service as _impl

sys.modules[__name__] = _impl