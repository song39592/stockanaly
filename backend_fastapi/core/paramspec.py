# -*- coding: utf-8 -*-
"""参数规格（第 13 项）—— 指标 / 策略 / 筹码公式三处 `ParamSpec` 的**唯一权威定义**。

三个注册体系（指标、策略、筹码公式）各自需要「参数名 / 类型 / 默认值 / 上下界 /
候选项 / 显示名」这六份信息，才能让前端**动态渲染输入控件**、后端**校验取值范围**。
搬进 `core/` 前它们是三份**逐字段相同**的 dataclass，只有 docstring 不同 ——
合并的收益不在于省 21 行，而在于**校验规则只需要改一处**：
`type` 允许哪几种、默认值怎么落、越界怎么处理，这套规则今后会持续演进，
三份各改一次必然漏。

## 为什么放 `core/`

依赖图的底部。本模块只依赖标准库（`dataclasses` / `typing`），
**不 import 任何项目模块**，所以 `indicators` / `strategies` /
`features/chip/formulas` 三边各自 import 它都不会构成循环依赖 ——
这一点是合并的前提，若它反过来 import 了注册表就会死锁。

## 三处旧定义仍然能按原名 import（兼容锚点，不要删）

合并的是**定义**，不是**名字**。下列位置继续 `import ParamSpec` 且行为不变：

    indicators/base.py                    ← indicators/__init__.py 从这里再导出
    strategies/core/base.py               ← strategies/__init__.py 与 core/__init__.py 从这里再导出
    features/chip/formulas/core/base.py   ← 筹码公式注册器（LOAD 时按 type 校验）

注意 `strategies/core/base.py` 与 `features/chip/formulas/core/base.py`
里的 `core` 指的是**各自的子包**，不是顶层 `core` 包 ——
下述 import 用的都是**绝对路径** `core.paramspec`，不是相对导入，
否则 `from . import paramspec` 会在两个子包里去找一个不存在的模块。
"""
from __future__ import annotations

import dataclasses as dc
from typing import Any


@dc.dataclass
class ParamSpec:
    """单个参数的规格：类型 / 默认值 / 取值范围 / 显示名。

    `type` 必须是 `"int"` / `"float"` / `"choice"` 三者之一 ——
    这个白名单由三个注册器在 **LOAD 时**各自校验
    （`indicators/registry.py`、`strategies/core/registry.py`、
    筹码公式的 `registry.scan()`），本模块不重复校验，避免两处规则漂移。

    `choices` 只对 `type="choice"` 有意义；`min` / `max` 对 choice 无意义。
    """
    name: str
    type: str
    default: Any
    min: float | None = None
    max: float | None = None
    choices: list | None = None
    label: str = ""


__all__ = ["ParamSpec"]