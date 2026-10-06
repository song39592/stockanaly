# -*- coding: utf-8 -*-
"""兼容转发：`chip_formulas` 已移入 `features/chip/formulas/`（第 34 项）。

**这是过渡文件，不是实现。** 新代码请直接用 `features.chip.formulas`。

⚠️ **本转发只保证 `import chip_formulas` 之后能用公开 API**
（`compute` / `compute_matrix` / `list_formulas` / `FORMULAS` / `validation_report` 等）。
**它无法转发「按包名导入子模块」** —— 例如
`importlib.import_module("chip_formulas.tri_decay")`：父模块被换成别名对象后，
Python 会把**同一个文件当成两个模块各加载一次**，公式注册表与 `__module__` 都会变乱。

因此 `core/registry.py` 里的 `_FORMULA_PACKAGE` 已改为**按 `__package__` 自动推导**，
不再依赖顶层名字 —— 以后再搬一次也不必手工同步。

依赖它的四处暂时都走本转发（`indicators/data.py` 的延迟导入、`indicators/registry.py`、
`strategies/scr90.py`、`chip_dist_routes.py`），后续可在各自搬家时改指新路径。

## 为什么用 `sys.modules` 别名，而不是 `from ... import *`

别名转发拿到的是**同一个模块对象**，因此 `FORMULAS` 注册表是同一份
（`@chip_formula` 装饰器在实现侧注册，转发侧看到的是同一个 dict）、
`scan()` 只在实现侧跑一次。`import *` 只复制名字，做不到这些。
"""

import sys

from features.chip import formulas as _impl

sys.modules[__name__] = _impl