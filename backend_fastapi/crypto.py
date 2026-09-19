# -*- coding: utf-8 -*-
"""数据完整性保护：密钥管理（绑定本机）与 HMAC 签名。

**用途**：发现数据库被外部工具直接修改（防人为篡改）。

**定位**：HMAC 不是加密——它保证「改了就能被发现」，但不阻止读取。
它防的是**篡改**（谁改的、改没改），不是**保密**（别人能不能看到内容）。

**密钥绑定本机**：Windows 下用 DPAPI（`CryptProtectData`）把密钥"密封"存放，
密文只能被**本机 + 当前用户**解开。库文件被拷到别的机器、或换主板 / 重装系统后，
密钥无法还原 → 既有数据一律校验失败。

**按设计不提供恢复码**：既定原则是「丢数据可重拉，错误数据不可接受」——
解不开时重新抓取即可，不需要为了可迁移而牺牲绑定强度。
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets

import config
import dpapi
import envfile

ENV_PATH = config.BASE_DIR / ".env"
ENV_KEY = "DATA_SECRET"                 # 明文字段（不支持 DPAPI 时的退路）
ENV_SEALED = "DATA_SECRET_SEALED"       # DPAPI 密封字段（首选）
_key: bytes | None = None


# --------------------------------------------------------------------------- #
# DPAPI：把密钥绑定到本机 + 当前用户
# 原语已抽到独立模块 `dpapi`（config 读 LLM 密钥时也要用，放这里会形成循环导入）；
# 以下保留同名薄封装，外部调用点无需改动。
# --------------------------------------------------------------------------- #
def dpapi_available() -> bool:
    """当前环境能否使用 DPAPI（仅 Windows）。"""
    return dpapi.available()


def dpapi_seal(plain: str) -> str:
    """用本机凭据加密，返回十六进制密文。"""
    return dpapi.seal(plain)


def dpapi_unseal(sealed_hex: str) -> str:
    """解开本机加密的密文；换机器 / 换用户会失败。"""
    return dpapi.unseal(sealed_hex)


# --------------------------------------------------------------------------- #
# .env 读写
# --------------------------------------------------------------------------- #
def _env_value(key: str) -> str:
    """环境变量优先，其次 .env 文件。"""
    value = (os.getenv(key) or "").strip()
    if value:
        return value
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(key + "="):
                return line.split("=", 1)[1].strip()
    return ""


def _write_env_with(keys: tuple[str, ...], block: list[str]) -> None:
    """重写 .env：移除旧的 keys 行，再追加新的 block，保留其它配置。"""
    lines: list[str] = []
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            if any(line.strip().startswith(k + "=") for k in keys):
                continue
            lines.append(line)
    while lines and not lines[-1].strip():
        lines.pop()
    # 原子改写：.env 里还有 LLM_BASE_URL / DATA_DIR 等配置，写入中途失败会损坏整个文件
    envfile.rewrite(ENV_PATH, "\n".join(lines + block) + "\n")


def _read_secret() -> str:
    """读取可用密钥：优先解密封存的，其次明文。解不开视为无密钥。"""
    sealed = _env_value(ENV_SEALED)
    if sealed:
        try:
            return dpapi_unseal(sealed)
        except Exception:                       # noqa: BLE001 - 换机器/换用户时必然失败
            return ""
    return _env_value(ENV_KEY)


def _write_secret(value: str) -> None:
    """写入密钥：能密封就密封（绑定本机），否则退回明文。"""
    if dpapi_available():
        try:
            sealed = dpapi_seal(value)
            _write_env_with((ENV_KEY, ENV_SEALED), [
                "",
                "# 数据完整性校验密钥：本机 DPAPI 密封，换机器 / 换用户将无法解开",
                "# 按设计不提供恢复码——解不开时按「丢数据可重拉」重新抓取即可",
                f"{ENV_SEALED}={sealed}",
            ])
            return
        except Exception:                       # noqa: BLE001 - 密封失败则退回明文
            pass
    _write_env_with((ENV_KEY, ENV_SEALED), [
        "",
        "# 数据完整性校验密钥：本机不支持 DPAPI，以明文保存",
        f"{ENV_KEY}={value}",
    ])


# --------------------------------------------------------------------------- #
# 密钥与签名
# --------------------------------------------------------------------------- #
def secret(force_new: bool = False) -> bytes:
    """取当前密钥；不存在则生成（优先以密封形式写入 .env）。"""
    global _key
    if _key is not None and not force_new:
        return _key
    value = "" if force_new else _read_secret()
    if not value:
        value = secrets.token_hex(32)           # 256 位随机
        try:
            _write_secret(value)
        except OSError:
            pass                                # 写不进不致命，本次进程内仍可用
    _key = value.encode("utf-8")
    return _key


def sign(payload: str) -> str:
    """对文本签名，返回十六进制 HMAC-SHA256。"""
    return hmac.new(secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def verify(payload: str, signature: str) -> bool:
    """校验签名（恒定时间比较，避免时序侧信道）。"""
    if not signature:
        return False
    return hmac.compare_digest(sign(payload), str(signature).strip())


def has_secret() -> bool:
    """当前是否已有可用密钥（用于区分「首次运行」与「密钥丢失」）。"""
    return bool(_read_secret()) or _key is not None


def seal_state() -> dict:
    """密钥的存放与可用状态，供页面 / 接口展示。"""
    sealed = _env_value(ENV_SEALED)
    plain = _env_value(ENV_KEY)
    if not sealed:
        return {"sealed": False, "plain": bool(plain), "usable": bool(plain),
                "note": "密钥以明文保存（当前系统不支持 DPAPI）" if not plain else
                        "密钥以明文保存，未绑定本机"}
    try:
        dpapi_unseal(sealed)
        return {"sealed": True, "plain": bool(plain), "usable": True,
                "note": "密钥已由本机 DPAPI 密封，拷到其他机器将无法使用"}
    except Exception as exc:                    # noqa: BLE001
        return {"sealed": True, "plain": bool(plain), "usable": False,
                "error": str(exc),
                "note": "密钥无法解开（可能换了机器或用户），既有数据校验会失败，"
                        "按既定原则重新抓取即可"}


def seal_now() -> dict:
    """把当前密钥改为密封存放（已有数据无需变动，签名仍能验证）。"""
    value = _read_secret()
    if not value:
        return {"ok": False, "error": "当前没有可用密钥"}
    if not dpapi_available():
        return {"ok": False, "error": "当前系统不支持 DPAPI，无法绑定本机"}
    _write_secret(value)
    global _key
    _key = value.encode("utf-8")
    return {"ok": True, **seal_state()}
