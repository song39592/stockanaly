"""LLM 调用的公共封装（被 mentor_routes / stock_routes 共用）。

放在独立模块是为了避免各业务模块互相 import，也便于以后更换模型平台时只改一处。
"""

import requests

import config


def call_llm(prompt: str, timeout: int = 180) -> str:
    """OpenAI 兼容 chat/completions 调用。"""
    url = config.LLM_BASE_URL.rstrip("/") + "/chat/completions"
    payload = {
        "model": config.LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "stream": False,
    }
    headers = {"Authorization": f"Bearer {config.LLM_API_KEY}", "Content-Type": "application/json"}
    response = requests.post(url, json=payload, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def llm_error_detail(exc: requests.HTTPError) -> str:
    """把 LLM 侧的 HTTP 状态翻译成可直接展示的提示，并附上游原始说明（便于判断是哪家平台的 key）。"""
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    upstream = ""
    try:
        payload = response.json()
        upstream = str((payload.get("error") or {}).get("message") or "").strip()
    except Exception:                     # noqa: BLE001 - 上游可能返回非 JSON
        upstream = ""
    if len(upstream) > 200:
        upstream = upstream[:200] + "…"
    suffix = f"｜上游返回：{upstream}" if upstream else ""
    if status == 401:
        return f"LLM 密钥无效（401）：请检查 backend_fastapi/.env 中的 LLM_API_KEY 是否为该平台签发的密钥{suffix}"
    if status == 402:
        return f"LLM 账户余额不足（402）：请到对应平台充值后重试{suffix}"
    if status == 429:
        return f"LLM 调用过于频繁或额度耗尽（429）：请稍后重试{suffix}"
    return f"LLM 调用失败：{exc}{suffix}"
