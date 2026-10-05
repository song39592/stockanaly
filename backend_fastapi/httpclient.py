# -*- coding: utf-8 -*-
"""兼容转发：`httpclient` 实际在 `core/httpclient.py`（第 15 项新增）。

**这是过渡文件，不是实现。** 新代码请直接用 `core.httpclient`。

别名而非 `from core.httpclient import *`（那只是复制一份名字，补丁与重绑定会失效）。
"""
import sys

from core import httpclient as _impl

sys.modules[__name__] = _impl