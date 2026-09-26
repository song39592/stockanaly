# -*- coding: utf-8 -*-
"""指标清单：把 REGISTRY 转换为前端可用的元数据列表。"""
from __future__ import annotations

from dataclasses import asdict

from .base import REGISTRY, ParamSpec


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
    # 按类别再按 id 排序，前端展示稳定
    out.sort(key=lambda x: (x["category"], x["id"]))
    return out
