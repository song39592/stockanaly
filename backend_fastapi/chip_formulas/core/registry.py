# -*- coding: utf-8 -*-
"""筹码公式校验与注册中心（与 strategies/core/registry.py 同构）。

公式文件**不能**盲导入即用：每个 .py 文件在可被选中 / 计算前，必须经过
「输入输出接口检查」，通过后才保留在 base.FORMULAS 并对外可见；
未通过的文件记录错误，不会出现在可选列表里，也无法被计算。

校验步骤（见 _validate_module）：
  1. 文件可被导入（语法 / 运行期 import 异常被捕获并记录，不拖垮整个包）；
  2. 恰好用 @chip_formula 注册了 1 个公式，且 id == 文件名（去 .py）；
  3. 元数据完整（name / category 非空，params 为 ParamSpec 列表且 type 合法）；
  4. 函数签名：首参必须为 ctx，其余参数都要有默认值且与 ParamSpec 一一对应；
  5. 冒烟测试：用**假 ChipContext** 跑一次，经严格 base._as_matrix 确认其返回
     「(交易日数, 分箱数) 的非负有限二维数组」，且确实产生了筹码；
     不通过者 LOAD 即剔除，运行期不再做冗余兜底。

与策略注册中心的一个差异：**不做 mtime 缓存**。公式数量少、冒烟成本仅毫秒级，
每次启动全量校验可以保证「改了算法文件立刻生效」，省掉一份需要维护的状态。
"""
from __future__ import annotations

import importlib
import os
import pkgutil
import sys
import traceback

import numpy as np

from . import base

_HERE = os.path.dirname(os.path.abspath(__file__))       # core 框架目录
_FORMULA_DIR = os.path.dirname(_HERE)                     # 上一层：具体公式所在目录
_FORMULA_PACKAGE = "chip_formulas"                        # 具体公式模块名空间
_EXCLUDE = {"__init__", "core"}                           # 排除包入口与框架子目录

_SMOKE_DAYS = 40
_SMOKE_BINS = 32


def _stub_context() -> base.ChipContext:
    """冒烟测试用假输入：一段温和上涨的行情 + 常量换手率，与真实 ctx 结构完全一致。

    用确定性数据（不用随机）：冒烟结果必须可复现，否则「时好时坏」的公式会漏过校验。
    """
    n = _SMOKE_DAYS
    close = 10.0 + 0.05 * np.arange(n) + 0.1 * np.sin(np.arange(n) / 3.0)
    high = close + 0.12
    low = close - 0.12
    open_ = close - 0.02
    p_lo = float(low.min()) - 0.1
    p_hi = float(high.max()) + 0.1
    edges = np.linspace(p_lo, p_hi, _SMOKE_BINS + 1)
    return base.ChipContext(
        dates=[f"2024-01-{i + 1:02d}" if i < 31 else f"2024-02-{i - 30:02d}" for i in range(n)],
        open=open_, high=high, low=low, close=close,
        volume=np.full(n, 12345.0),
        turnover=np.full(n, 0.012),
        vwap=close.copy(),
        float_shares=1.0e9,
        edges=edges,
        centers=(edges[:-1] + edges[1:]) / 2.0,
        p_lo=p_lo, p_hi=p_hi, adjust="qfq",
    )


def _force_import(mod_name: str) -> set[str]:
    """强制重新执行某公式模块（清掉上次登记 + 从 sys.modules 移除后重导）。"""
    full = f"{_FORMULA_PACKAGE}.{mod_name}"
    if full in sys.modules:
        for fid in [k for k, v in base.FORMULAS.items()
                    if getattr(v.func, "__module__", None) == full]:
            base.FORMULAS.pop(fid, None)
        del sys.modules[full]
    before = set(base.FORMULAS.keys())
    importlib.import_module(full)
    return set(base.FORMULAS.keys()) - before


def _validate_module(mod_name: str, filename: str) -> dict:
    """导入并完整校验单个公式模块，返回结果 dict（含 status / error）。"""
    rec = {"filename": filename, "status": "error", "formula_id": None, "error": None}

    # 1) 导入
    try:
        new_ids = _force_import(mod_name)
    except Exception as exc:
        rec["error"] = f"导入失败：{exc}\n{traceback.format_exc()}"
        return rec

    # 2) 恰好注册 1 个，且 id == 文件名
    if len(new_ids) != 1:
        for i in new_ids:
            base.FORMULAS.pop(i, None)
        rec["error"] = f"必须用 @chip_formula 恰好注册 1 个公式（实际 {len(new_ids)} 个）"
        return rec
    fid = next(iter(new_ids))
    expected = filename[:-3]
    if fid != expected:
        base.FORMULAS.pop(fid, None)
        rec["error"] = f"公式 id('{fid}') 必须等于文件名('{expected}')"
        return rec

    meta = base.FORMULAS[fid]

    # 3) 元数据完整性
    try:
        if not str(meta.name).strip():
            raise ValueError("name 不能为空")
        if not str(meta.category).strip():
            raise ValueError("category 不能为空")
        if not isinstance(meta.params, list):
            raise ValueError("params 必须是列表")
        for p in meta.params:
            if not isinstance(p, base.ParamSpec):
                raise ValueError("params 元素必须是 ParamSpec")
            if not str(p.name).strip():
                raise ValueError("ParamSpec.name 不能为空")
            if p.type not in ("int", "float", "choice"):
                raise ValueError(f"ParamSpec.type 非法: {p.type}")
    except Exception as exc:
        base.FORMULAS.pop(fid, None)
        rec["error"] = f"元数据不合法：{exc}"
        return rec

    # 4) 函数签名
    problems = base.signature_problems(meta)
    if problems:
        base.FORMULAS.pop(fid, None)
        rec["error"] = "函数签名不合法：" + "；".join(problems)
        return rec

    # 5) 冒烟测试：严格 base._as_matrix 校验返回矩阵；另要求确实产生了筹码
    defaults = {p.name: p.default for p in meta.params}
    try:
        raw = meta.func(_stub_context(), **defaults)
        matrix = base._as_matrix(raw, _SMOKE_DAYS, _SMOKE_BINS)
        if float(matrix.sum()) <= 0:
            raise ValueError("冒烟结果全为 0，公式没有产生任何筹码")
    except Exception as exc:
        base.FORMULAS.pop(fid, None)
        rec["error"] = f"冒烟测试失败：{exc}\n{traceback.format_exc()}"
        return rec

    rec["status"] = "ok"
    rec["formula_id"] = fid
    rec["name"] = meta.name
    rec["category"] = meta.category
    rec["description"] = meta.description
    rec["error"] = None
    return rec


def scan() -> dict:
    """扫描并校验全部公式文件，返回 {filename: 校验结果}。

    通过校验的公式保留在 base.FORMULAS；未通过的被移除，不会出现在可选列表。
    """
    results: dict[str, dict] = {}
    for _finder, name, ispkg in pkgutil.iter_modules([_FORMULA_DIR]):
        if ispkg or name in _EXCLUDE:
            continue
        results[name + ".py"] = _validate_module(name, name + ".py")
    return results


def validation_report() -> dict:
    """全部文件的校验状态（含未通过文件的错误），供 /health 与排障展示。"""
    results = scan()
    ok = [v for v in results.values() if v.get("status") == "ok"]
    err = [v for v in results.values() if v.get("status") != "ok"]
    return {"total": len(results), "valid": len(ok), "invalid": len(err), "files": results}
