# -*- coding: utf-8 -*-
"""FastAPI 服务入口：只负责创建应用、容错挂载各业务模块的路由，以及少量通用逻辑。

各业务模块的接口按功能拆分到独立的 *_routes.py，数据采集与计算在对应的 *_service.py：
    chip_routes.py     筹码体系 · SCR 选股
    chip_dist_routes.py 筹码体系 · 筹码分布（筹码峰）
    market_routes.py   盘面及板块分析
    mentor_routes.py   大佬策略实验室
    stock_routes.py    个股调研与股票估值

挂载是容错的：某个模块导入失败（依赖缺失、语法错误、第三方库异常等）只会让该模块的接口不可用，
不会影响主页与其他模块；失败原因通过 GET /health 的 route_errors 字段暴露，便于定位。
"""

import importlib
import os
import subprocess
import time
import platform

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import config
import crypto
import db
import instance_lock
import integrity
import logutil
import mentor_store
import price_store
import storage

logger = logutil.logger          # stderr 实时输出 + <data>/logs/ 落盘（见 logutil.py）

START_TIME = time.time()

def _git_commit():
    """取当前 git 短哈希（打包/无 git 时返回 None，不影响健康检查）。"""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            capture_output=True, text=True, timeout=2,
        )
        if out.returncode == 0:
            c = out.stdout.strip()
            if c:
                return c
    except Exception:
        pass
    return None

_GIT_COMMIT = _git_commit()

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
    ("历史数据下载", "download_routes"),
    ("筹码体系 · SCR 选股", "chip_routes"),
    ("筹码体系 · 筹码分布", "chip_dist_routes"),
    ("筹码体系 · SCR90 周榜", "chip_rank_routes"),
    ("盘面及板块分析", "market_routes"),
    ("大佬策略实验室", "mentor_routes"),
    ("个股调研与股票估值", "stock_routes"),
    ("系统设置 · 数据目录", "system_routes"),
    ("技术指标", "indicators_routes"),
    ("策略回测", "strategies_routes"),
)

# 挂载 / 初始化失败的模块：[{label, module, error}]，供 /health 查询
_module_errors: list = []

# 数据完整性校验结果（启动时算一次并缓存，`GET /api/system/integrity` 可重新校验）
_integrity_state: dict = {"ok": True, "issues": [], "libraries": []}


def _record_error(label: str, module_name: str, exc: BaseException) -> None:
    """记录模块故障并写入日志（stderr 实时显示 + 文件留痕）。

    `exc_info=True` 会把堆栈一起带进日志文件，便于事后定位；
    打到 stderr 的形态与改造前一致（仍是 `[warn] ...` + 堆栈）。
    """
    detail = f"{type(exc).__name__}: {exc}"
    _module_errors.append({"label": label, "module": module_name, "error": detail})
    logger.warning("%s（%s）不可用：%s", label, module_name, detail, exc_info=True)


def _mount_routes() -> None:
    """逐个挂载业务模块；单个模块失败只记录错误，不影响其他模块与主页。"""
    for label, module_name in ROUTE_MODULES:
        try:
            module = importlib.import_module(module_name)
            app.include_router(module.router)
        except Exception as exc:          # noqa: BLE001 - 容错：坏模块不拖垮整个服务
            _record_error(label, module_name, exc)


def _init_stores() -> None:
    """初始化本地存储；失败同样只记录、不阻止服务启动。"""
    try:
        # 第 21 项：旧版本把状态文件平铺在数据根目录，这里一次性搬进 state/。
        # **必须先于 migrate_legacy**：迁移的语义是「目标已有就不动」，
        # 先让根目录的真数据落进 state/，项目内的旧副本才不会反过来把它顶掉。
        result = storage.migrate_to_subdirs()
        if result["moved"] or result["cleaned"]:
            logger.info("数据目录分区迁移：搬入 state/ %s，清理陈旧文件 %s",
                        result["moved"], result["cleaned"])
        if result["errors"]:
            logger.warning("分区迁移有未处理项（已保留原状）：%s", result["errors"])
    except Exception as exc:              # noqa: BLE001
        _record_error("数据目录分区迁移", "storage", exc)
    try:
        # 数据目录已移到项目外（见 config.DATA_DIR）：启动时把项目内旧位置的数据
        # 复制过去，避免看起来像「数据丢了」。只复制、不删除源文件。
        result = storage.migrate_legacy()
        if result["migrated"]:
            logger.info("已迁移 %d 个数据文件：%s → %s",
                        len(result["migrated"]), result["from"], result["to"])
    except Exception as exc:              # noqa: BLE001
        _record_error("数据目录初始化与迁移", "storage", exc)
    try:
        db.init_db()                      # 建齐所有本地表（股票池 / 消息面 / 行情分片 / 复权因子 / 除权）
    except Exception as exc:              # noqa: BLE001
        _record_error("本地库初始化（股票池历史与行情）", "db", exc)
    try:
        # 旧版本把三口径日K放在主库单表里；这里拆到「按年分片 + 因子独立库」结构。
        # 只读源表、不删除，确认无误后可自行清理旧表。
        result = price_store.migrate_legacy_shards()
        if result.get("migrated"):
            logger.info("行情表已迁移到分片结构：%s", result["migrated"])
    except Exception as exc:              # noqa: BLE001
        _record_error("行情分片迁移", "price_store", exc)
    try:
        # 校验密钥绑定本机：若当前仍是明文存放（早期版本），启动时自动改为 DPAPI 密封。
        # 密封后密钥只能被本机 + 当前用户解开，拷到其他机器一律校验失败。
        state = crypto.seal_state()
        if state.get("usable") and not state.get("sealed") and crypto.dpapi_available():
            if crypto.seal_now().get("ok"):
                logger.info("校验密钥已改由本机 DPAPI 密封保存（不再以明文存放在 .env）")
    except Exception as exc:              # noqa: BLE001
        _record_error("校验密钥密封", "crypto", exc)
    try:
        # 按股指纹：为既有数据补算一次（此后每次写入只重算受影响的那几只）
        digests = price_store.ensure_digests()
        if digests.get("created"):
            logger.info("已为 %s 只股票补算数据指纹", digests["created"])
        if digests.get("upgraded"):
            logger.info("数据指纹算法已升级到 v%s：按当前数据重算 %s 只股票的指纹",
                        digests.get("version"), digests.get("upgraded"))
    except Exception as exc:              # noqa: BLE001
        _record_error("数据指纹初始化", "price_store", exc)
    try:
        # 完整性：先为既有数据补一次签名（首次引入本机制时需要），再整体校验一次
        signed = integrity.ensure_signed()
        if signed.get("signed"):
            logger.info("已为 %d 个数据库补写完整性签名", len(signed["signed"]))
        state = integrity.summary()
        _integrity_state.update(state)
        if not state.get("ok"):
            logger.warning("数据完整性校验发现问题：%s", state["issues"])
    except Exception as exc:              # noqa: BLE001
        _record_error("数据完整性校验", "integrity", exc)
    try:
        mentor_store.init_db()
    except Exception as exc:              # noqa: BLE001
        _record_error("大佬策略实验室（本地库初始化）", "mentor_store", exc)


def _is_loaded(module_name: str) -> bool:
    return not any(item["module"] == module_name for item in _module_errors)


# 单实例锁：**必须早于任何「写数据目录」的动作**——否则第二个进程已经开始迁移、
# 建表、刷新指纹，再来拦就已经晚了（两边互相覆盖指纹与签名，属静默损坏）。
# 锁失败时的提示已足够清楚，直接以非 0 退出码结束，便于启动脚本判断。
try:
    instance_lock.acquire(config.STATE_DIR)
except instance_lock.AlreadyRunningError as exc:
    logger.critical("%s", exc)
    raise SystemExit(1) from None

_init_stores()
_mount_routes()

# 后台预热 RPS 等横截面指标需要的「全市场日 K 面板」：约 15 秒，放后台线程，
# 不阻塞 /health 与页面打开；等用户首次勾选 RPS 时面板通常已就绪，避免首帧卡顿。
try:
    import indicators.data as _ind_data
    _ind_data.warm("day")
except Exception as exc:                 # noqa: BLE001 - 预热失败不影响正常启动
    logger.debug("RPS 面板预热失败（不影响启动）：%s", exc)


@app.get("/health")
def health():
    """健康检查。

    llm_ready / llm_problem：LLM 是否真正可用（占位符 / 缺项都算未就绪）；
    mentor_lab / market_board：供前端做版本与可用性检测（保持历史字段兼容）；
    modules：各业务模块是否挂载成功；route_errors：失败模块及原因。
    """
    problem = config.llm_config_problem()
    detail_parts = []
    if _GIT_COMMIT:
        detail_parts.append("commit " + _GIT_COMMIT)
    detail_parts.append("启动 " + time.strftime("%m-%d %H:%M", time.localtime(START_TIME)))
    detail_parts.append("Py" + platform.python_version())
    detail_parts.append("LLM" + ("就绪" if problem is None else "未配置"))
    backend_detail = " · ".join(detail_parts)
    return {
        "ok": True,
        "service": "stock-research",
        "api_version": 5,
        "pid": os.getpid(),
        "stock_history": _is_loaded("history_routes"),
        "mentor_lab": _is_loaded("mentor_routes"),
        "market_board": _is_loaded("market_routes"),
        "llm_ready": problem is None,
        "llm_problem": problem,
        "modules": {label: _is_loaded(name) for label, name in ROUTE_MODULES},
        "route_errors": _module_errors,
        "integrity": _integrity_state,
        "backend_detail": backend_detail,
    }
