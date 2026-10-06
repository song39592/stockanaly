# -*- coding: utf-8 -*-
"""兼容转发：`chip_service` 已移入 `features/chip/scr_service.py`（第 34 项，**并改名**）。

**这是过渡文件，不是实现。** 新代码请直接用 `features.chip.scr_service`。

改名原因：三个 routes 模块搬进 `features/chip/` 后会撞名，故按功能拆成
`scr_service`（SCR 选股）/ `rank_service`（SCR90 周榜）/ `dist_routes`（筹码分布）。
**转发名保持原样 `chip_service`**，这样 `chip_rank_service` 与 `chip_routes`
等既有调用方一行都不用改。

⚠️ **本模块是「周口径」的权威定义方**：
`MARKET_CLOSE_HOUR` / `_as_moment` / `week_start` / `expected_weeks`。
`rank_service` 通过薄封装（`_as_date` / `_week_nodes`）复用它，**这是正确的复用姿势**。
它还在**模块级**被读走一个常量：`rank_service.PROCESSED_DIR = chip_service.PROCESSED_DIR` ——
转发是别名，所以拿到的是同一个值。

## 为什么用 `sys.modules` 别名，而不是 `from ... import *`

别名转发拿到的是**同一个模块对象**，因此 `DATA_DIR` / `RAW_DIR` / `PROCESSED_DIR` /
`META_PATH` 这些**模块级常量是同一份**（`rank_service` 在 import 时就读它），
测试里的 `patch.object(...)` 真正生效。`import *` 只复制名字，且常量只在导入时求值一次，
后续 `patch` 不会传播 —— 做不到别名转发这层。
"""

import sys

from features.chip import scr_service as _impl

sys.modules[__name__] = _impl