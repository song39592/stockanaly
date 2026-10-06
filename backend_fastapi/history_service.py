# -*- coding: utf-8 -*-
"""兼容转发：`service` 已移入 `features/history/service.py`（第 30 项）。

**这是过渡文件，不是实现。** 新代码请直接用 `features.history.service`。

提供 `load_trusted_bars`（K线读取）、入池/出池轨迹、消息面。

⚠️ `load_trusted_bars` **直接调 `price_store.load_bars`** 以捕获
`price_store.UntrustedDataError`（实现「脏数据自动重抓」），而
`kline_service.get_bars` 不抛这个异常 —— 这是**必要绕过**（第 17 项），
不要"顺手改成走 kline_service"，那会丢掉自动重抓能力。

## 为什么用 `sys.modules` 别名，而不是 `from ... import *`

别名转发拿到的是**同一个模块对象**，因此下划线私有名照常可见、
模块级状态（`history_store` 的连接与建表标志）是同一份，测试里的
`patch.object(...)` 真正生效。`import *` 只复制名字做不到这些。
"""

import sys

from features.history import service as _impl

sys.modules[__name__] = _impl
