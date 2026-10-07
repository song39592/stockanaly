# -*- coding: utf-8 -*-
"""配置加载：从 .env 读取 LLM、服务端口与数据目录，密钥不硬编码。

数据目录（`DATA_DIR`）集中存放所有运行期数据（SQLite 库、SCR 原始文件与计算结果），
与代码分开，便于备份与迁移。解析优先级见 `_resolve_data_dir`。

根目录下按用途分区：`config/`（用户配置）、`state/`（库与锁）、`logs/`、`cache/`，
以及保持在根的 `bars/`（行情分片）与 `chip/`（SCR）。
"""
import os
from pathlib import Path

from dotenv import load_dotenv

from . import dpapi
from . import envfile

# ⚠️ 本模块现住在 `core/` 子包里（第 27 项）。`Path(__file__).parent` 现在是
#    `backend_fastapi/core/`，而**程序目录**（.env 所在处、旧的 BASE_DIR 语义）仍应是
#    `backend_fastapi/` —— 所以要**多退一级**。
#    改错的后果是静默的：`.env` 找不到（LLM 配置全丢）、数据目录指到不存在的位置。
#    「多退一级」全文件只出现在这一处，BASE_DIR 与 ENV_PATH 都由它派生；
#    若日后本模块又被搬进更深的子包，只需改这一行。
PROG_DIR = Path(__file__).resolve().parent.parent

ENV_PATH = PROG_DIR / ".env"

# 第 35 项：用户可见文案里**不再写死程序目录名**，改为动态取。
# 这三项提示原本硬编码了 "backend_fastapi/.env"，是「目录改名」时**必须同步**的点 ——
# 漏改的后果是提示用户去一个不存在的路径填密钥（用户会照做，然后 LM 依然不通）。
# 动态取目录名后，改名只需移动目录，这几处自动跟随。
# ⚠️ 只用于**给人看的文案**；BASE_DIR / ENV_PATH 等运行时路径本就是动态派生的。
_ENV_HINT = f"{PROG_DIR.name}/.env"
# 留下真正来自父进程的覆盖项，避免把 dotenv 注入的旧值误认为外部配置。
DATA_DIR_OVERRIDES = {key: os.environ[key] for key in ("STOCK_DATA_DIR", "DATA_DIR")
                      if os.environ.get(key, "").strip()}
load_dotenv(ENV_PATH)

# 密钥字段：**优先本机 DPAPI 密封值，其次明文**。
# 密封值只能被本机 + 当前用户解开，因此 .env 里不再留可直接读取的明文凭据；
# 明文写法仍然兼容（手工填写、或从其它机器迁移过来时都可能用到）。
LLM_KEY_PLAIN = "LLM_API_KEY"
LLM_KEY_SEALED = "LLM_API_KEY_SEALED"


def _env(key: str, default: str = "") -> str:
    return (os.getenv(key) or default).strip()


def read_secret(sealed_key: str, plain_key: str) -> str:
    """读取密钥：**密封优先、明文回退**；密封存在但解不开则视为未配置。

    解不开通常意味着换了机器 / 换了用户（DPAPI 绑定本机），
    此时返回空串让上层按「未配置」处理，而不是拿着密文去发请求。
    """
    sealed = _env(sealed_key)
    if sealed:
        try:
            return dpapi.unseal(sealed).strip()
        except Exception:                       # noqa: BLE001 - 换机器 / 换用户 / 非 Windows
            return ""
    return _env(plain_key)


LLM_BASE_URL = _env("LLM_BASE_URL")
LLM_API_KEY = read_secret(LLM_KEY_SEALED, LLM_KEY_PLAIN)
LLM_MODEL = _env("LLM_MODEL")
PORT = int(_env("PORT", "8000") or 8000)

# 「程序目录」由文件顶部的 PROG_DIR 统一定义（core/ 要多退一级），别在这里重算。
BASE_DIR = PROG_DIR                                       # 程序目录 backend_fastapi/
PROJECT_DIR = BASE_DIR.parent                             # 仓库根目录 stockanaly-main/
PARENT_DIR = PROJECT_DIR.parent                           # 仓库的上一级目录
LEGACY_DATA_DIR = BASE_DIR / "data"                       # 旧位置：项目内（用于自动迁移）
LEGACY_CHIP_DIR = BASE_DIR / "chip_data"                  # 旧位置：SCR 数据（用于自动迁移）

# 默认数据目录：**程序所在位置再上一级**的 stockanaly-data，即与仓库并列。
#     <上一级>/stockanaly-main/     ← 程序
#     <上一级>/stockanaly-data/     ← 默认数据目录
# 这样做到了数据与代码分离，同时跟随程序自身位置（换机器、移动整个目录都不受影响），
# 且不依赖盘符；需要放到别处时在 .env 里显式指定 DATA_DIR 即可。
DEFAULT_DATA_DIR = PARENT_DIR / "stockanaly-data"


def _resolve_data_dir() -> Path:
    """数据根目录。

    优先级：环境变量 STOCK_DATA_DIR > .env 的 DATA_DIR > 默认（本程序所在目录下的 data）。
    默认值取自程序自身位置，因此不依赖盘符或当前工作目录；
    需要把数据放到别处时，显式配置 DATA_DIR 即可。
    """
    for key in ("STOCK_DATA_DIR", "DATA_DIR"):
        value = (os.getenv(key) or "").strip()
        if value:
            return Path(value).expanduser()
    return DEFAULT_DATA_DIR


DATA_DIR = _resolve_data_dir()

# 数据根目录下的**固定分区**（第 21 项：从「全部平铺在根目录」改为分区存放）。
# 各模块一律引用这些常量，不要自己拼路径 —— 否则下次调整结构又要满仓库改字符串。
#   config/  用户配置：可手工修改的 json（视图 / 阈值等）
#   state/   持久化状态：SQLite 库、单实例锁
#   logs/    日志（第 22 项的落点）
#   cache/   可重建缓存，丢了重算即可
# 注：`bars/`（行情分片，2 GB 级）**刻意留在根目录不搬** —— 移动既慢又易中断，
# 且它本身体积占绝对多数，搬进子目录没有任何收益。
CONFIG_DIR = DATA_DIR / "config"
STATE_DIR = DATA_DIR / "state"
LOGS_DIR = DATA_DIR / "logs"
CACHE_DIR = DATA_DIR / "cache"
# 回测报告：**刻意不放logs/ 也不放 cache/** ——
#   logs/   语义是日志，且定时任务会清*.log.* 与 dump-*.zip（cleanup._clean_dumps）；
#   cache/  清理策略是「全部可清」（定义上可重建）—— 报告丢了就没了。
REPORTS_DIR = DATA_DIR / "backtests"


def llm_ready() -> bool:
    """是否已配置完整的 LLM 信息"""
    return bool(LLM_BASE_URL and LLM_API_KEY and LLM_MODEL)


def llm_config_problem() -> str | None:
    """返回 LLM 配置问题说明；配置可用时返回 None。

    llm_ready() 只判断非空，.env.example 里的占位符（sk-你的密钥）也会通过，
    真正调用时却在请求头编码阶段抛出难以定位的 latin-1 错误，因此这里补格式校验。
    """
    values = (("LLM_BASE_URL", LLM_BASE_URL), ("LLM_API_KEY", LLM_API_KEY), ("LLM_MODEL", LLM_MODEL))
    missing = [name for name, value in values if not value]
    if missing:
        # 密封值存在却解不开时给出可操作的提示，而不是笼统的「未配置」
        if _env(LLM_KEY_SEALED) and not LLM_API_KEY:
            return ("LLM 密钥已密封但当前无法解开（DPAPI 绑定本机，可能换了机器或用户），"
                    f"请在 {_ENV_HINT} 中重新填写 LLM_API_KEY")
        return (f"服务端未配置 LLM，缺少：{'、'.join(missing)}"
                f"（请在 {_ENV_HINT} 中填写）")
    if not LLM_API_KEY.isascii() or not LLM_API_KEY.startswith("sk-"):
        return (f"LLM_API_KEY 仍是占位符或格式不正确，请在 {_ENV_HINT} 中填入真实密钥"
                "（形如 sk-xxxxxxxx，可在 https://platform.deepseek.com/api_keys 申请）")
    if not LLM_BASE_URL.isascii() or not LLM_BASE_URL.startswith(("http://", "https://")):
        return "LLM_BASE_URL 格式不正确，应形如 https://api.deepseek.com/v1"
    return None


def llm_key_state() -> dict:
    """LLM 密钥的存放状态，供页面 / 接口展示（**不返回密钥本身**）。"""
    sealed = _env(LLM_KEY_SEALED)
    plain = _env(LLM_KEY_PLAIN)
    if sealed:
        return {"sealed": True, "plain": bool(plain), "usable": bool(LLM_API_KEY),
                "note": "密钥已由本机 DPAPI 密封，换机器 / 换用户将无法解开" if LLM_API_KEY
                        else "密钥已密封但当前解不开（可能换了机器或用户），需重新填写"}
    return {"sealed": False, "plain": bool(plain), "usable": bool(plain),
            "note": "密钥以明文保存在 .env" if plain else "尚未配置密钥"}


def seal_llm_key() -> dict:
    """把 .env 里的明文 `LLM_API_KEY` 改为**本机 DPAPI 密封**存放（不留明文）。

    **刻意不自动执行**：改写配置文件属于侵入操作，且密封值绑定本机——
    换机器后解不开，需要重新填写 key，因此必须由使用者显式触发。
    明文读取始终兼容（见 `read_secret`），转换后手工填写依旧有效。
    """
    plain = _env(LLM_KEY_PLAIN)
    if not plain:
        return {"ok": False, "error": "当前没有明文 LLM_API_KEY 可密封（可能已密封或尚未配置）"}
    if not dpapi.available():
        return {"ok": False, "error": "当前系统不支持 DPAPI，无法绑定本机"}
    sealed = dpapi.seal(plain)
    path = BASE_DIR / ".env"
    lines: list[str] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith((LLM_KEY_PLAIN + "=", LLM_KEY_SEALED + "=")):
                continue
            lines.append(line)
    while lines and not lines[-1].strip():
        lines.pop()
    lines += [
        "",
        "# LLM 密钥：本机 DPAPI 密封，换机器 / 换用户将无法解开（需重新填写）",
        f"{LLM_KEY_SEALED}={sealed}",
    ]
    # 原子改写：.env 里还有 LLM_BASE_URL / DATA_DIR / DATA_SECRET_SEALED 等全部配置，
    # 直接 write_text 是「打开即截断」，写入中途失败会让整个配置文件损坏。
    written = envfile.rewrite(path, "\n".join(lines) + "\n")
    os.environ.pop(LLM_KEY_PLAIN, None)      # 同步本次进程视图，避免状态仍显示为明文
    return {"ok": True, "backup": written.get("backup"), **llm_key_state()}
