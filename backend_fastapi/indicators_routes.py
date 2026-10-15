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
    # 与 indicators/__init__.py 的 _EXCLUDE 保持一致：base/registry/data/scan 是
    # 框架模块而非指标，漏掉 scan 会让自检模块自己被当成「有 @indicator 却没 id」的坏指标。
    _EXCLUDE = {"__init__", "base", "registry", "data", "scan"}
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

        # ⚠️ **辅助模块（`helper=True`，如 `rps_data.py`）的 `ok` 恒为 False，这是刻意的**：
        # 它没有指标输出、无数据可校验，`data_ok` 无从谈起（保持初始的 False）。
        # 前端 `IndicatorIndexPage.IndFill` 是**先判 `helper`、再判 `ok`**，所以界面上
        # 显示为灰字「— 辅助模块」并单独计入「辅助 N」，**不会被算成失败**。
        # 看到辅助模块的 `ok=false` 时不要当成缺陷去"修" —— 先看 `helper`。
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


def _check_indicator_io(indicator_id: str):
    """合成数据自检 —— **转调 `indicators.scan.check_io`**，判据只保留一份。

    合成行情怎么造、序列长度/类型怎么查、依赖行情数据的失败怎么算"跳过"，
    这些原都写在本路由文件里；而 `indicators/scan.py` 又独立实现了一遍同样的事。
    两份实现会漂移（改一处忘另一处 → 同一指标在两个端点结论相反），故收敛到包内。

    本函数保留是为`/diagnose` 的调用点与返回结构不变。
    """
    return indicators.scan.check_io(indicators.REGISTRY.get(indicator_id))


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
