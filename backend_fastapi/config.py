# -*- coding: utf-8 -*-
"""配置加载：从 .env 读取 LLM、服务端口与数据目录，密钥不硬编码。

数据目录（`DATA_DIR`）集中存放所有运行期数据（SQLite 库、SCR 原始文件与计算结果），
与代码分开，便于备份与迁移。解析优先级见 `_resolve_data_dir`。
"""
import os
from pathlib import Path

from dotenv import load_dotenv

import dpapi
import envfile

load_dotenv()  # 加载同目录下的 .env；默认不覆盖真实环境变量，因此环境变量优先

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

BASE_DIR = Path(__file__).resolve().parent          # 程序目录 backend_fastapi/
PROJECT_DIR = BASE_DIR.parent                       # 仓库根目录 stockanaly-main/
PARENT_DIR = PROJECT_DIR.parent                     # 仓库的上一级目录
LEGACY_DATA_DIR = BASE_DIR / "data"                 # 旧位置：项目内（用于自动迁移）
LEGACY_CHIP_DIR = BASE_DIR / "chip_data"            # 旧位置：SCR 数据（用于自动迁移）

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
                    "请在 backend_fastapi/.env 中重新填写 LLM_API_KEY")
        return f"服务端未配置 LLM，缺少：{'、'.join(missing)}（请在 backend_fastapi/.env 中填写）"
    if not LLM_API_KEY.isascii() or not LLM_API_KEY.startswith("sk-"):
        return ("LLM_API_KEY 仍是占位符或格式不正确，请在 backend_fastapi/.env 中填入真实密钥"
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
