# -*- coding: utf-8 -*-
"""筹码峰公式包入口（与 `strategies` 同构：一个文件一个公式，文件名即 id）。

对外暴露：
  - compute(code, formula_id=None, params=None, ...)  算筹码分布，返回标准化响应
  - compute_matrix(...)                               只算矩阵（指标侧复用）
  - list_formulas()                                   公式清单（前端下拉 / 排障）
  - FORMULAS                                          已通过校验的公式注册表
  - validation_report()                               LOAD 校验结果（含被剔除的文件）

包加载时自动扫描并**完整校验**同目录下所有 .py：
未通过校验的公式会被剔出 FORMULAS，既不出现在可选列表、也不可被计算
（详见 ARCHITECTURE.md §4「新增公式前必读」）。
"""
from .core import base, data, registry

from .core.base import (
    FORMULAS, ChipContext, ChipFormulaMeta, ChipResult, ParamSpec,
    chip_formula, compute, compute_matrix, list_formulas,
)
from .core.data import get_float_shares, float_shares_info
from .core.registry import scan, validation_report

scan()      # 包加载即校验：坏公式在这里就被拦下，不会流到运行期

__all__ = [
    "base", "data", "registry",
    "FORMULAS", "ChipContext", "ChipFormulaMeta", "ChipResult", "ParamSpec",
    "chip_formula", "compute", "compute_matrix", "list_formulas",
    "get_float_shares", "float_shares_info", "scan", "validation_report",
]
