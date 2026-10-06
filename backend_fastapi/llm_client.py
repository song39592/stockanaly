# -*- coding: utf-8 -*-
"""兼容转发：`llm_client` 已移入 `features/mentor/llm_client.py`（第 33 项）。

**这是过渡文件，不是实现。** 新代码请直接用 `features.mentor.llm_client`。

**跨功能共享**：盘面页（`features/market/routes.py`）与个股页
（`features/stock/routes.py`）都调 `call_llm` / `llm_error_detail`。

⚠️ `call_llm(prompt, timeout=180)` 的 **180 秒是 LLM 语义**，
与行情的十秒级完全不同，且它**显式传 timeout** 给 `core.httpclient.post`
（后者 timeout 是必填参数，正是为了防止误用行情口径）。别"顺手统一"超时。

## 为什么用 `sys.modules` 别名，而不是 `from ... import *`

别名转发拿到的是**同一个模块对象**，因此下划线私有名照常可见、模块级状态是同一份
（`collectors` 的 `ak` / `httpclient`，`llm_client` 的 `config`），
测试里的 `patch.object(...)` 真正生效。`import *` 只复制名字做不到这些。
"""

import sys

from features.mentor import llm_client as _impl

sys.modules[__name__] = _impl
