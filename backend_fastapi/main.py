# -*- coding: utf-8 -*-
"""FastAPI 服务入口：只负责创建应用、容错挂载各业务模块的路由，以及少量通用逻辑。

各业务模块的接口按功能拆分到独立的 *_routes.py，数据采集与计算在对应的 *_service.py：
    chip_routes.py     筹码体系 · SCR 选股
    market_routes.py   盘面及板块分析
    mentor_routes.py   大佬策略实验室
    stock_routes.py    个股调研与股票估值

挂载是容错的：某个模块导入失败（依赖缺失、语法错误、第三方库异常等）只会让该模块的接口不可用，
不会影响主页与其他模块；失败原因通过 GET /health 的 route_errors 字段暴露，便于定位。
"""

import importlib
import sys
import traceback

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import config
import history_store
import mentor_store

app = FastAPI(title="个股时效性调研")

# 允许跨域：前端通过 file:// 打开（Origin 为 null），需放开 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 业务模块路由：(显示名, 模块名)，模块需暴露 router 对象
ROUTE_MODULES = (
    ("股票池历史 · K线与消息", "history_routes"),
    ("筹码体系 · SCR 选股", "chip_routes"),
    ("盘面及板块分析", "market_routes"),
    ("大佬策略实验室", "mentor_routes"),
    ("个股调研与股票估值", "stock_routes"),
)

# 挂载 / 初始化失败的模块：[{label, module, error}]，供 /health 查询
_module_errors: list = []


def _record_error(label: str, module_name: str, exc: BaseException) -> None:
    """记录模块故障并打印到后端日志（stderr）。"""
    detail = f"{type(exc).__name__}: {exc}"
    _module_errors.append({"label": label, "module": module_name, "error": detail})
    print(f"[warn] {label}（{module_name}）不可用：{detail}", file=sys.stderr)
    traceback.print_exc()


def _mount_routes() -> None:
    """逐个挂载业务模块；单个模块失败只记录错误，不影响其他模块与主页。"""
    for label, module_name in ROUTE_MODULES:
        try:
            module = importlib.import_module(module_name)
            app.include_router(module.router)
        except Exception as exc:          # noqa: BLE001 - 容错：坏模块不拖垮整个服务
            _record_error(label, module_name, exc)


def _init_stores() -> None:
    """初始化各模块的本地存储；失败同样只记录、不阻止服务启动。"""
    try:
        history_store.init_db()
    except Exception as exc:              # noqa: BLE001
        _record_error("股票池历史（本地库初始化）", "history_store", exc)
    try:
        mentor_store.init_db()
    except Exception as exc:              # noqa: BLE001
        _record_error("大佬策略实验室（本地库初始化）", "mentor_store", exc)


def _is_loaded(module_name: str) -> bool:
    return not any(item["module"] == module_name for item in _module_errors)


_init_stores()
_mount_routes()


@app.get("/health")
def health():
    """健康检查。

    llm_ready / llm_problem：LLM 是否真正可用（占位符 / 缺项都算未就绪）；
    mentor_lab / market_board：供前端做版本与可用性检测（保持历史字段兼容）；
    modules：各业务模块是否挂载成功；route_errors：失败模块及原因。
    """
    problem = config.llm_config_problem()
    return {
        "ok": True,
        "service": "stock-research",
        "api_version": 5,
        "stock_history": _is_loaded("history_routes"),
        "mentor_lab": _is_loaded("mentor_routes"),
        "market_board": _is_loaded("market_routes"),
        "llm_ready": problem is None,
        "llm_problem": problem,
        "modules": {label: _is_loaded(name) for label, name in ROUTE_MODULES},
        "route_errors": _module_errors,
    }
