# -*- coding: utf-8 -*-
"""策略校验与注册中心（替代原先「盲导入即注册」的做法）。

策略文件**不能**被直接信任地导入即用：每个 .py 文件在可被选中 / 回测前，
必须经过「输入输出接口检查」，通过后才保留在 base.REGISTRY 并对外可见；
未通过的文件记录错误，不会出现在可选列表里，也无法回测。

校验步骤（见 _validate_module）：
  1. 文件可被导入（语法 / 运行期 import 异常会被捕获并记录，不拖垮整个包）；
  2. 恰好用 @strategy 注册了 1 个策略，且 strategy.id == 文件名(去 .py)；
  3. 元数据完整（name / category / outputs 非空，inputs 为 str 列表，
     params 为 ParamSpec 列表且 type ∈ {int,float,choice}）；
  4. 函数签名：首参必须为 ctx，其余参数名与 ParamSpec 一一对应；
  5. 冒烟测试：用假数据替换 data.load_bars，跑一次策略，
     经严格 base._as_signals 确认其返回「list[Signal]」（带 code、合法 action、
     weight ∈ [0,1]）——验证输入/输出契约成立；不通过者 LOAD 即剔除，
     运行期不再做冗余兜底。

通过校验的策略写入 _validated.json 缓存，记录「文件名 + 最后修改时间 +
状态 + 元数据/错误」，作为审计与前端展示依据；文件 mtime 变化则下次扫描
自动重新校验（mtime 未变且曾通过时，仅做轻量复检以省去冒烟测试）。
"""
from __future__ import annotations

import dataclasses as _dc
import importlib
import inspect
import json
import os
import pkgutil
import sys
import traceback

import pandas as pd

from . import base, data

_HERE = os.path.dirname(os.path.abspath(__file__))          # core 框架目录
_STRATEGY_DIR = os.path.dirname(_HERE)                       # 上一层：具体策略所在目录
_STRATEGY_PACKAGE = "strategies"                             # 具体策略模块名空间
_CACHE_FILE = os.path.join(_STRATEGY_DIR, "_validated.json")
_EXCLUDE = {"__init__", "core"}                              # 排除包入口与框架子目录
_FAKE_COLS = ["open", "high", "low", "close", "volume"]


def _load_cache() -> dict:
    try:
        with open(_CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    try:
        with open(_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _snapshot_ids() -> set:
    return set(base.REGISTRY.keys())


def _stub_load_bars(codes, start=None, end=None, fields=None, adjust="hfq"):
    """冒烟测试用假行情：覆盖常见字段，inputs 任意子集都能取到。"""
    cols = _FAKE_COLS
    n = 40
    idx = pd.date_range("2024-01-01", periods=n, freq="D").strftime("%Y-%m-%d")
    if isinstance(codes, str):
        codes = [codes]
    out = {}
    for code in codes:
        df = pd.DataFrame({c: list(range(n)) for c in cols}, index=idx)
        out[code] = df[[c for c in (fields or cols) if c in df.columns]] if fields else df
    return out


def _force_import(mod_name: str):
    """强制重新执行某策略模块（清掉上一次登记 + 从 sys.modules 移除后重导），
    返回新登记到 REGISTRY 的 id 集合；导入异常原样抛出由调用方捕获。"""
    full = f"{_STRATEGY_PACKAGE}.{mod_name}"
    if full in sys.modules:
        for sid in [k for k, v in base.REGISTRY.items()
                    if getattr(v.func, "__module__", None) == full]:
            base.REGISTRY.pop(sid, None)
        del sys.modules[full]
    before = _snapshot_ids()
    importlib.import_module(full)
    return base.REGISTRY.keys() - before


def _validate_module(mod_name: str, filename: str) -> dict:
    """导入并完整校验单个策略模块，返回结果 dict（含 status / error）。"""
    rec = {"filename": filename, "mtime": None, "status": "error",
           "strategy_id": None, "error": None}
    try:
        rec["mtime"] = os.path.getmtime(os.path.join(_HERE, filename))
    except Exception:
        pass

    # 1) 导入
    try:
        new_ids = _force_import(mod_name)
    except Exception as exc:
        rec["error"] = f"导入失败：{exc}\n{traceback.format_exc()}"
        return rec

    # 2) 恰好注册 1 个，且 id == 文件名
    if len(new_ids) != 1:
        for i in new_ids:
            base.REGISTRY.pop(i, None)
        rec["error"] = f"必须用 @strategy 恰好注册 1 个策略（实际 {len(new_ids)} 个）"
        return rec
    sid = next(iter(new_ids))
    expected = filename[:-3]
    if sid != expected:
        base.REGISTRY.pop(sid, None)
        rec["error"] = f"strategy id('{sid}') 必须等于文件名('{expected}')"
        return rec

    meta = base.REGISTRY[sid]

    # 3) 元数据完整性
    try:
        if not str(meta.name).strip():
            raise ValueError("name 不能为空")
        if not str(meta.category).strip():
            raise ValueError("category 不能为空")
        if not str(meta.outputs).strip():
            raise ValueError("outputs 不能为空")
        if not isinstance(meta.inputs, list) or not all(isinstance(x, str) for x in meta.inputs):
            raise ValueError("inputs 必须是 str 列表")
        if not isinstance(meta.params, list):
            raise ValueError("params 必须是列表")
        param_names: set[str] = set()
        for p in meta.params:
            if not isinstance(p, base.ParamSpec):
                raise ValueError("params 元素必须是 ParamSpec")
            if not str(p.name).strip():
                raise ValueError("ParamSpec.name 不能为空")
            if p.type not in ("int", "float", "choice"):
                raise ValueError(f"ParamSpec.type 非法: {p.type}")
            param_names.add(p.name)
    except Exception as exc:
        base.REGISTRY.pop(sid, None)
        rec["error"] = f"元数据不合法：{exc}"
        return rec

    # 4) 函数签名：首参 ctx，其余参数名与 ParamSpec 对应
    try:
        params = list(inspect.signature(meta.func).parameters.values())
        if not params or params[0].name != "ctx":
            raise ValueError("策略函数首参必须命名为 ctx")
        rest = [p.name for p in params[1:]
                if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)]
        if set(rest) != param_names:
            raise ValueError(f"函数参数 {sorted(rest)} 与 ParamSpec {sorted(param_names)} 不一致")
    except Exception as exc:
        base.REGISTRY.pop(sid, None)
        rec["error"] = f"函数签名不合法：{exc}"
        return rec

    # 5) 冒烟测试：用假数据替换 data.load_bars，确认返回 list[Signal] 契约成立。
    #    此处依赖严格版 base._as_signals——它对「非 list[Signal] / 缺 code /
    #    非法 action / weight 越界」直接抛错；未通过者 LOAD 即拒之门外，
    #    运行期不再静默兜底。空列表（窗口内无信号）属合法，不拦截。
    defaults = {p.name: p.default for p in meta.params}
    ctx = base.StrategyContext(codes=["TEST"], start="2024-01-01", end="2024-02-09", adjust="hfq")
    real = data.load_bars
    data.load_bars = _stub_load_bars
    try:
        raw = meta.func(ctx, **defaults)
        base._as_signals(raw)        # 严格校验：类型 / code / action / weight
    except Exception as exc:
        data.load_bars = real
        base.REGISTRY.pop(sid, None)
        rec["error"] = f"冒烟测试失败：{exc}\n{traceback.format_exc()}"
        return rec
    finally:
        data.load_bars = real

    rec["status"] = "ok"
    rec["strategy_id"] = sid
    rec["name"] = meta.name
    rec["category"] = meta.category
    rec["description"] = meta.description
    rec["inputs"] = meta.inputs
    rec["outputs"] = meta.outputs
    rec["params"] = [_dc.asdict(p) for p in meta.params]
    rec["error"] = None
    return rec


def _light_check(mod_name: str, filename: str, cached: dict) -> dict:
    """mtime 未变且曾通过时的轻量复检：重新导入确保登记，仅校验注册数量 + id==文件名，
    跳过元数据/签名/冒烟，直接复用缓存里的元数据。"""
    rec = {"filename": filename, "mtime": cached.get("mtime"), "status": "error",
           "strategy_id": None, "error": None}
    try:
        new_ids = _force_import(mod_name)
    except Exception as exc:
        rec["error"] = f"导入失败：{exc}"
        return rec
    if len(new_ids) != 1:
        for i in new_ids:
            base.REGISTRY.pop(i, None)
        rec["error"] = f"必须用 @strategy 恰好注册 1 个策略（实际 {len(new_ids)} 个）"
        return rec
    sid = next(iter(new_ids))
    if sid != filename[:-3]:
        base.REGISTRY.pop(sid, None)
        rec["error"] = f"strategy id('{sid}') 必须等于文件名('{filename[:-3]}')"
        return rec
    for k in ("strategy_id", "name", "category", "description", "inputs", "outputs", "params"):
        rec[k] = cached.get(k)
    rec["strategy_id"] = sid
    rec["status"] = "ok"
    rec["error"] = None
    return rec


def scan(force: bool = False) -> dict:
    """扫描并校验全部策略文件，返回 {filename: 校验结果}。

    force=True 时忽略缓存、对所有文件执行完整校验（用于「刷新」接口）。
    通过校验的策略保留在 base.REGISTRY；未通过的被移除，不会出现在可选列表。
    """
    cache = _load_cache()
    results: dict[str, dict] = {}
    seen: set[str] = set()

    for _finder, name, ispkg in pkgutil.iter_modules([_STRATEGY_DIR]):
        if ispkg or name in _EXCLUDE:
            continue
        filename = name + ".py"
        seen.add(filename)
        try:
            mtime = os.path.getmtime(os.path.join(_STRATEGY_DIR, filename))
        except Exception:
            mtime = None

        cached = cache.get(filename)
        cached_ok = (cached is not None and cached.get("status") == "ok"
                     and cached.get("mtime") == mtime)

        if (not force) and cached_ok:
            rec = _light_check(name, filename, cached)   # 轻量复检，跳过冒烟
        else:
            rec = _validate_module(name, filename)        # 完整校验
        results[filename] = rec
        cache[filename] = rec

    for fn in list(cache.keys()):          # 清理已删除文件的缓存
        if fn not in seen:
            cache.pop(fn, None)

    _save_cache(cache)
    return results


def validation_report() -> dict:
    """返回全部文件的校验状态（含未通过文件的错误），供前端 / 调试展示。"""
    cache = _load_cache()
    ok = [v for v in cache.values() if v.get("status") == "ok"]
    err = [v for v in cache.values() if v.get("status") != "ok"]
    return {
        "total": len(cache),
        "valid": len(ok),
        "invalid": len(err),
        "files": cache,
    }
