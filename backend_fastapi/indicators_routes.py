# -*- coding: utf-8 -*-
"""技术指标 HTTP 接口（标准化输出，详见 indicators/ARCHITECTURE.md §6）。

路由前缀 /api/indicators：
    GET  ""          指标清单（含 panel / params 规格）
    GET  /compute    单指标计算：?code=&id=&params=（params 为 JSON 字符串）
    POST /batch      批量计算：{"code","items":[{"id","params"}]}

错误统一返回 {"error":{"code","message"}}（单指标计算用对应 HTTP 状态码；
批量接口单个失败只在该项内返回 error，不影响其余项）。
"""
from __future__ import annotations

import json
import os
import ast
import importlib
from typing import Optional

import pandas as pd
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import indicators
import apiutil
from periods import normalize

router = APIRouter(prefix="/api/indicators", tags=["技术指标"])

# 第 11 项：`_CODE_ERR` / `_err` 已并入 apiutil（与 strategies_routes 共用一份）。
# 这里只保留本域用到的更具体的码：未知指标 id 报 UNKNOWN_INDICATOR 而不是通用
# UNKNOWN_KEY —— 前端只读 message，但日志与排障时这个码更可读。
_UNKNOWN = (apiutil.CODE_UNKNOWN_INDICATOR, 400)
_CODE_ERR = {
    KeyError: _UNKNOWN,
    ValueError: ("BAD_PARAM", 400),
    RuntimeError: ("NO_DATA", 404),
}
_err = apiutil.fail                      # (message, code, status)


def _compute_or_error(code: str, id: str, params, period: str = "day") -> dict | JSONResponse:
    try:
        return indicators.compute(code, id, params or {}, period=period)
    except (KeyError, ValueError, RuntimeError) as exc:
        err_code, status = _CODE_ERR[type(exc)]
        return _err(str(exc), err_code, status)


def _safe_compute(code: str, id: str, params, period: str = "day") -> dict:
    """批量用：单个失败不中断，返回带 error 的项。"""
    try:
        return indicators.compute(code, id, params or {}, period=period)
    except (KeyError, ValueError, RuntimeError) as exc:
        err_code, _ = _CODE_ERR[type(exc)]
        return {"id": id, "error": {"code": err_code, "message": str(exc)}}


@router.get("")
def list_indicators():
    """指标清单：每项含 id / name / category / panel / params。

    顺带在后台预热全市场截面面板（RPS 等横截面指标需要），不阻塞本次响应：
    前端进入个股页会先拉本清单，等用户勾选 RPS 时面板通常已就绪，避免首帧超时。
    """
    try:
        indicators.rps_data.warm()
    except Exception:                    # noqa: BLE001 - 预热失败不应影响清单
        pass
    return indicators.list_indicators()


@router.get("/diagnose")
def diagnose_indicators():
    """扫描 indicators 目录下所有指标文件，逐文件做「索引 / 数据」自检。

    返回：
      dir      指标目录的绝对路径（前端做「打开指标目录」链接用）
      dup_ids  全局出现次数 > 1 的索引名称列表
      items[]  每个 .py 文件一条：file / path / id / name / registered /
              id_dup / data_ok / import_error / note / ok / messages
              —— 指标文件内的文件都会列出（含导入失败的），只有 ok=true 的
                 才算通过检查、可经快捷键显示。

    检查项：
      1) 索引是否重复：两个文件定义了同一个 @indicator(id=...)。
      2) 输入输出数据是否正确：用**合成 OHLCV** 跑一遍 meta.func，校验返回结构
         （必须是 dict、含非空 series、每条 series.data 长度与 K 线一致、值均为数值）。
         需要全市场 / 行情数据的指标（本机未下载行情）判为「跳过校验」，不误报。
    """
    # 扫描 indicators 子包目录（本路由文件在 backend_fastapi/ 顶层，故拼一级 "indicators"）
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "indicators")
    _EXCLUDE = {"__init__", "base", "registry", "data"}
    files = []
    for fn in sorted(os.listdir(here)):
        if not fn.endswith(".py"):
            continue
        mod = fn[:-3]
        if mod in _EXCLUDE or mod.startswith("_"):
            continue
        files.append((mod, fn, os.path.join(here, fn)))

    # 1) 解析每个文件的 @indicator(id=, name=) 并统计 id 出现次数
    parsed = {}
    id_counts = {}
    for mod, fn, path in files:
        info = _parse_indicator(path)
        parsed[mod] = info
        pid = info.get("id")
        if pid:
            id_counts[pid] = id_counts.get(pid, 0) + 1
    dup_ids = sorted([k for k, v in id_counts.items() if v > 1])

    items = []
    for mod, fn, path in files:
        info = parsed[mod]
        pid = info.get("id")
        pname = info.get("name")
        has_ind = bool(info.get("has_indicator"))
        is_helper = not has_ind            # 无 @indicator：辅助模块（中间变量），非指标
        messages = []
        import_error = None
        registered = bool(pid) and pid in indicators.REGISTRY
        id_dup = bool(pid) and id_counts.get(pid, 0) > 1

        # 导入是否成功（导入失败通常是语法 / 依赖错误，已注册则跳过）
        if not registered:
            try:
                importlib.import_module(f"indicators.{mod}")
            except Exception as exc:           # noqa: BLE001 - 诊断要暴露原因
                import_error = f"{type(exc).__name__}: {exc}"
                messages.append("导入失败：" + import_error)

        # 数据校验（仅对已注册指标做合成数据自检）
        data_ok = False
        note = ""
        if is_helper:
            # 辅助模块只提供中间变量、没有指标输出，天然无需检查 —— 标为中性，不算失败
            note = "辅助模块（仅中间变量，无指标输出，不参与检查）"
        elif not pid:
            messages.append("有 @indicator 但缺少 id= 定义")
        elif not registered:
            messages.append("未在 REGISTRY 中注册（可能导入失败或 id 不匹配）")
        else:
            data_ok, note = _check_indicator_io(pid)
            if not data_ok and note:
                messages.append("数据校验未通过：" + note)

        if id_dup:
            messages.append(f"索引名称重复：'{pid}' 在多个文件中出现")

        ok = bool(pid) and registered and (not id_dup) and data_ok
        items.append({
            "file": fn,
            "path": path,
            "id": pid,
            "name": pname,
            "registered": registered,
            "id_dup": id_dup,
            "data_ok": data_ok,
            "helper": is_helper,
            "import_error": import_error,
            "note": note,
            "ok": ok,
            "messages": messages,
        })

    # 用统一响应封装（{"ok": True, "dir":…, "dup_ids":…, "items":…}）：
    # 前端 J.IsOk() 要求顶层有 ok=true，否则会被误判为「读取失败」（曾踩此坑）。
    return apiutil.ok({"dir": here, "dup_ids": dup_ids, "items": items})


def _parse_indicator(path: str) -> dict:
    """用 AST 解析文件里第一个 @indicator(id=..., name=...) 装饰器。

    has_indicator 表示文件里**是否存在** @indicator 装饰器：
      - True  这是一个指标文件（此时缺少 id= 才算真错误）；
      - False 只是辅助模块（如 rps_data.py，仅提供中间变量），无指标输出，
              不算失败、不参与「输入输出数据是否正确」检查。
    """
    try:
        tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
    except SyntaxError as exc:
        return {"id": None, "name": None, "has_indicator": False,
                "syntax_error": str(exc)}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for dec in node.decorator_list:
                call = None
                if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name) \
                        and dec.func.id == "indicator":
                    call = dec
                elif isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) \
                        and dec.func.attr == "indicator":
                    call = dec
                if call is None:
                    continue
                kid = kname = None
                for kw in call.keywords:
                    if kw.arg == "id" and isinstance(kw.value, ast.Constant):
                        kid = kw.value.value
                    elif kw.arg == "name" and isinstance(kw.value, ast.Constant):
                        kname = kw.value.value
                return {"id": kid, "name": kname, "has_indicator": True}
    return {"id": None, "name": None, "has_indicator": False}


# 需要全市场 / 行情数据的指标，本机未下载行情时跑不通属正常，不判失败
_DATA_DEP_MARKERS = ("行情数据不足", "缺少流通股本", "取不到股票代码", "无行情数据",
                     "price_store", "get_ohlcv", "akshare", "全市场", "NoData", "market")


def _check_indicator_io(indicator_id: str):
    """用合成 OHLCV 跑一遍指标，校验输入输出是否合法。返回 (ok, note)。"""
    meta = indicators.REGISTRY.get(indicator_id)
    if meta is None:
        return False, "未注册"
    n = 60
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    close = [100.0 + i * 0.3 for i in range(n)]
    df = pd.DataFrame({
        "open": [c + 0.5 for c in close],
        "high": [c + 1.2 for c in close],
        "low": [c - 1.2 for c in close],
        "close": close,
        "volume": [1_000_000.0 + i * 1000.0 for i in range(n)],
    }, index=idx)
    params = {p.name: p.default for p in meta.params}
    try:
        res = meta.func(df, **params)
    except Exception as exc:
        msg = str(exc)
        if any(m in msg for m in _DATA_DEP_MARKERS):
            return True, "需行情/全市场数据，本机未下载，跳过校验"
        return False, f"{type(exc).__name__}: {msg}"
    if not isinstance(res, dict):
        return False, "返回值不是 dict"
    series = res.get("series")
    if series is None:                       # 兼容旧 {"lines": {name: Series}}
        lines = res.get("lines")
        if not lines:
            return False, "缺少 series / lines"
        series = [{"name": k, "data": v} for k, v in lines.items()]
    if not isinstance(series, list) or len(series) == 0:
        return False, "series 为空"
    for s in series:
        data = s.get("data")
        if data is None:
            return False, f"序列 {s.get('name')} 缺少 data"
        if not hasattr(data, "__len__") or len(data) != n:
            return False, f"序列 {s.get('name')} 长度与 K 线不一致"
        bad = sum(1 for v in data
                  if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float))))
        if bad:
            return False, f"序列 {s.get('name')} 含 {bad} 个非数值"
    return True, "OK"


@router.get("/compute")
def compute_indicator(code: str, id: str, params: Optional[str] = None,
                      period: str = "day"):
    try:
        p = json.loads(params) if params else {}
        if not isinstance(p, dict):
            return _err("params 必须是 JSON 对象", "BAD_PARAM", 400)
        normalize(period)
    except json.JSONDecodeError:
        return _err("params 不是合法 JSON", "BAD_PARAM", 400)
    except ValueError as exc:
        return _err(str(exc), "BAD_PARAM", 400)
    return _compute_or_error(code, id, p, period=period)


class BatchItem(BaseModel):
    id: str
    params: dict = {}


class BatchRequest(BaseModel):
    code: str
    items: list[BatchItem]
    period: str = "day"          # K 线周期：必须与图表当前周期一致


@router.post("/batch")
def batch(req: BatchRequest):
    """批量计算：一次拿多指标，避免前端多次请求。"""
    try:
        period = normalize(req.period)
    except ValueError as exc:
        return _err(str(exc), "BAD_PARAM", 400)
    return {"items": [_safe_compute(req.code, it.id, it.params, period=period)
                      for it in req.items]}
