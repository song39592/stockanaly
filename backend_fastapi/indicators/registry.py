# -*- coding: utf-8 -*-
"""指标清单：把 REGISTRY 转换为前端可用的元数据列表。"""
from __future__ import annotations

from dataclasses import asdict

from .base import REGISTRY, ParamSpec


def _chip_formula_ids() -> list[str]:
    """可用筹码公式 id（供 `formula` 参数的下拉选项）。

    **延迟导入**：chip_formulas.core.data 反过来要 import indicators.data（取行情 /
    换手率 / 均价），模块级互导会形成环；放在函数里双方模块都已加载，环自然断开。
    取不到时返回空列表——指标本身照常可用（会用默认公式），只是下拉没选项。
    """
    try:
        import chip_formulas
        return sorted(chip_formulas.FORMULAS)
    except Exception:                       # noqa: BLE001 - 公式包不可用不影响指标清单
        return []


def list_indicators() -> list[dict]:
    """返回全部已注册指标的元数据（不含计算函数）。

    每项含：id / name / category / panel / params（参数规格列表）。
    panel 取值 "main"|"lower"|"right"|"none"：前三者前端据此分配到对应渲染面板，
    "none" 表示不显示（仅注册、可计算，前端不放入任何渲染面板，如内部辅助指标）。
    """
    out: list[dict] = []
    for meta in REGISTRY.values():
        out.append({
            "id": meta.id,
            "name": meta.name,
            "category": meta.category,
            "panel": meta.panel,
            "params": [asdict(p) for p in meta.params],
        })
    # `formula` 参数（筹码类指标）的选项来自筹码公式包的注册表，运行时填充
    for item in out:
        for spec in item["params"]:
            if spec.get("name") == "formula" and not spec.get("choices"):
                spec["choices"] = _chip_formula_ids()
    # 按类别再按 id 排序，前端展示稳定
    out.sort(key=lambda x: (x["category"], x["id"]))
    return out
