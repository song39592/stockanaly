# -*- coding: utf-8 -*-
"""兼容转发：`repair_digests` 已移入 `features/system/repair.py`（第 32 项）。

**这是过渡文件，不是实现。** 新代码请直接用 `features.system.repair`。

⚠️ **本文件原本是独立 CLI**，全仓无人 import 它。
它自己的 docstring 记载了调用方式 `python repair_digests.py [--apply]`，
所以本转发**额外带 __main__ 守卫**，保证那条使用方式不断。
若不需要 CLI，直接 `from features.system.repair import main` 亦可。

## 为什么用 `sys.modules` 别名，而不是 `from ... import *`

别名转发拿到的是**同一个模块对象**，因此下划线私有名照常可见
（`integrity._all_targets` / `_latest_mtime` / `_parse_iso` 三个是跨模块可见的），
`patch.object(...)` 真正生效。`import *` 只复制名字做不到这些。
"""

import sys

from features.system import repair as _impl

sys.modules[__name__] = _impl


# 本文件原本是**独立 CLI 脚本**（不是被 import 的模块），用法是
#   python backend_fastapi/repair_digests.py            # 干跑：只列失配清单，不落库
#   python backend_fastapi/repair_digests.py --apply    # 真正按当前数据重算指纹
# 上面两行在原文件的 docstring 里有记载。搬进包后 __name__ 仍是 __main__，
# 故显式调实现里的 main()，**保证这条使用方式不断**（与第 27 项 storage.py 同样做法）。
if __name__ == "__main__":
    raise SystemExit(_impl.main() or 0)
