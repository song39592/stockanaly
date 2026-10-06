# -*- coding: utf-8 -*-
"""兼容转发：`storage` 已移入 `core/`（第 27 项）。

**这是过渡文件，不是实现。** 新代码请直接用 `core.storage`；
等 `main.py` / 各 `*_routes` / `test_*.py` 全都改完新路径后，本文件会被统一删除。

做法是**在 sys.modules 里把自己替换成 `core.storage` 本身**（别名），
而不是 `from core.storage import *`（那只是复制一份名字）。原因：

  测试里普遍有 `patch.object(config, "DATA_DIR", ...)` 这类打补丁。
  用 import * 时补丁打在**副本**上，实现模块仍读真实值 —— 补丁静默失效、测试莫名失败。
  用别名时拿到的是同一个模块对象，补丁与运行时重绑定都真正生效。
"""
import sys

from core import storage as _impl

sys.modules[__name__] = _impl
# 启动器把本文件当**脚本**跑（离线改数据目录）：python backend_fastapi/storage.py
# --set-data-dir-base64 <b64>。__name__ 仍是 __main__，故显式调实现里的 main。
if __name__ == "__main__":
    raise SystemExit(_impl.main())