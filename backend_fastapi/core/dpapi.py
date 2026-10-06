# -*- coding: utf-8 -*-
"""Windows DPAPI 封装：把密钥绑定到「本机 + 当前用户」。

**为什么独立成模块**：校验密钥（`crypto`）与 LLM 密钥（`config`）都要用它，
而 `crypto` 依赖 `config`——若把 DPAPI 留在 `crypto` 里，`config` 反向导入会形成循环。
本模块只依赖标准库，不依赖任何项目模块。

非 Windows（或缺少 crypt32）时 `available()` 返回 False，调用方退回明文存放。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> _DataBlob:
    buf = ctypes.create_string_buffer(data, len(data))
    return _DataBlob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _take(blob: _DataBlob) -> bytes:
    out = ctypes.string_at(blob.pbData, blob.cbData)
    ctypes.windll.kernel32.LocalFree(blob.pbData)
    return out


def available() -> bool:
    """当前环境能否使用 DPAPI（仅 Windows）。"""
    return hasattr(ctypes, "windll") and hasattr(ctypes.windll, "crypt32")


def seal(plain: str) -> str:
    """用本机凭据加密，返回十六进制密文。"""
    if not available():
        raise RuntimeError("当前系统不支持 DPAPI")
    out = _DataBlob()
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(_blob(plain.encode("utf-8"))), None, None, None, None, 0,
        ctypes.byref(out))
    if not ok:
        raise OSError(f"CryptProtectData 失败：{ctypes.GetLastError()}")
    return _take(out).hex()


def unseal(sealed_hex: str) -> str:
    """解开本机加密的密文；换机器 / 换用户会失败。"""
    if not available():
        raise RuntimeError("当前系统不支持 DPAPI")
    out = _DataBlob()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(_blob(bytes.fromhex(sealed_hex.strip()))), None, None, None, None, 0,
        ctypes.byref(out))
    if not ok:
        raise OSError(f"CryptUnprotectData 失败：{ctypes.GetLastError()}")
    return _take(out).decode("utf-8")
