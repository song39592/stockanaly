# -*- coding: utf-8 -*-
"""配置加载：从 .env 读取 LLM 与服务端口，密钥不硬编码。"""
import os
from dotenv import load_dotenv

load_dotenv()  # 加载同目录下的 .env

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "").strip()
LLM_API_KEY = os.getenv("LLM_API_KEY", "").strip()
LLM_MODEL = os.getenv("LLM_MODEL", "").strip()
PORT = int(os.getenv("PORT", "8000"))


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
        return f"服务端未配置 LLM，缺少：{'、'.join(missing)}（请在 backend_fastapi/.env 中填写）"
    if not LLM_API_KEY.isascii() or not LLM_API_KEY.startswith("sk-"):
        return ("LLM_API_KEY 仍是占位符或格式不正确，请在 backend_fastapi/.env 中填入真实密钥"
                "（形如 sk-xxxxxxxx，可在 https://platform.deepseek.com/api_keys 申请）")
    if not LLM_BASE_URL.isascii() or not LLM_BASE_URL.startswith(("http://", "https://")):
        return "LLM_BASE_URL 格式不正确，应形如 https://api.deepseek.com/v1"
    return None
