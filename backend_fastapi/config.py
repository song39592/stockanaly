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
