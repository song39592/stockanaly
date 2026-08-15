# -*- coding: utf-8 -*-
"""FastAPI 个股时效性调研服务。

对外接口：POST /api/stock/research   {"code":"300209","name":"行云科技"}  → markdown
流程：内存缓存(12h) → 采集(元信息/公告/调研/新闻) → 拼 prompt → LLM → markdown。
密钥在 .env，不硬编码。
"""
import time
import threading

import requests
from fastapi import FastAPI
from pydantic import BaseModel

import config
from collectors import (
    get_basic_info, get_announcements, fetch_notice_content, get_news, _clip, SLEEP_NOTICE,
)

app = FastAPI(title="个股时效性调研")

# 允许跨域：前端通过 file:// 打开（Origin 为 null），需放开 CORS
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

CACHE_TTL = 12 * 3600          # 单只股票缓存 12 小时
_cache = {}                     # code -> {"ts": float, "markdown": str}
_cache_lock = threading.Lock()

# 风险声明（无论模型返回什么，固定拼到结尾）
DISCLAIMER = "\n\n---\n⚠️ 本内容由 AI 生成，仅供信息参考，不构成任何投资建议。"

PROMPT_TEMPLATE = """你是股票信息调研助手，仅做客观信息整理，严禁预测股价、严禁给出买入卖出建议。
股票代码：{code}，股票名称：{name}

以下是近期抓取的公开原始资料。请【严格以这些资料为准】整理，资料未覆盖的信息可基于公开常识简要补充并注明“据公开资料”，不要凭空编造最新数据：

{raw_text}

请输出以下四块内容（用 markdown 排版，标题清晰，不要输出多余闲聊）：
1、公司主营业务：简洁说明公司核心业务、主营产品、经营赛道；
2、近期重大事项与公告：梳理该上市公司近期公开的重大公告、业绩预告、重大合同、减持、风险警示、机构调研等公开事件（尽量标注时间）；
3、市场热门多空分析：客观整理市场公开的看多逻辑、潜在风险与看空逻辑，只陈述市场观点，不要自己做判断、不要预测涨跌；
4、风险声明：本内容由 AI 生成，仅供参考，不构成任何投资建议。"""


class ResearchRequest(BaseModel):
    code: str
    name: str = ""


def call_llm(prompt: str) -> str:
    """OpenAI 兼容 chat/completions 调用。"""
    url = config.LLM_BASE_URL.rstrip("/") + "/chat/completions"
    payload = {
        "model": config.LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "stream": False,
    }
    headers = {"Authorization": f"Bearer {config.LLM_API_KEY}", "Content-Type": "application/json"}
    r = requests.post(url, json=payload, headers=headers, timeout=180)
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"]
    return content


def collect_raw_text(code: str, name: str) -> str:
    """采集各数据源并拼成原始文本。任何来源失败都降级，不中断。"""
    parts = []

    # 1) 元信息
    info = get_basic_info(code)
    if info:
        parts.append("【公司基础信息】\n" + info)

    # 2) 公告 + 调研纪要
    anns, note = get_announcements(code)
    if note:
        parts.append("【公告】" + note)
    elif anns:
        research = [a for a in anns if "调研" in a["type"] or "投资者关系" in a["title"]]
        important = [a for a in anns if a["importance"] > 0 and a not in research][:8]

        # 调研纪要（正文问答）
        if research:
            lines = []
            for a in research[:2]:
                body = fetch_notice_content(a["art_code"])
                lines.append(f"- [{a['date']}] {a['title']}\n  {_clip(body, 1500) if body else '（正文获取失败）'}")
                time.sleep(SLEEP_NOTICE)
            parts.append("【机构调研纪要】\n" + "\n".join(lines))

        # 重点公告正文摘要
        if important:
            lines = []
            for a in important:
                body = fetch_notice_content(a["art_code"])
                lines.append(f"- [{a['date']}] {a['title']}：{_clip(body, 400) if body else ''}")
                time.sleep(SLEEP_NOTICE)
            parts.append("【近期重要公告】\n" + "\n".join(lines))

        # 其余公告只留标题
        fetched = {a["art_code"] for a in (research[:2] + important)}
        other = [a for a in anns if a["art_code"] not in fetched][:20]
        if other:
            parts.append("【其他公告（标题）】\n" + "\n".join(f"- [{a['date']}] {a['title']}" for a in other))

    # 3) 财经新闻
    news, nnote = get_news(code)
    if nnote:
        parts.append("【新闻】" + nnote)
    elif news:
        parts.append("【近期新闻】\n" + "\n".join(
            f"- [{n['date']}] {n['title']}（{n['source']}）：{n['content']}" for n in news
        ))

    return "\n\n".join(parts)


def research(code: str, name: str) -> str:
    """完整调研：采集 → prompt → LLM → markdown。"""
    raw = collect_raw_text(code, name)
    if not raw:
        raw = "（未获取到最新公开资料，请基于公开常识整理，并注明信息可能滞后。）"
    prompt = PROMPT_TEMPLATE.format(code=code, name=name or code, raw_text=raw)
    markdown = call_llm(prompt)
    return (markdown or "").strip() + DISCLAIMER


@app.get("/health")
def health():
    return {"ok": True, "service": "stock-research", "llm_ready": config.llm_ready()}


@app.post("/api/stock/research")
def research_endpoint(req: ResearchRequest):
    code = (req.code or "").strip()
    name = (req.name or "").strip()
    if not code:
        return {"ok": False, "error": "缺少股票代码"}
    if not config.llm_ready():
        return {"ok": False, "error": "服务端未配置 LLM（请在 .env 填写 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL）"}

    # 缓存
    with _cache_lock:
        hit = _cache.get(code)
    if hit and (time.time() - hit["ts"]) < CACHE_TTL:
        return {"ok": True, "markdown": hit["markdown"], "cached": True}

    try:
        markdown = research(code, name)
    except requests.HTTPError as e:
        return {"ok": False, "error": f"LLM 调用失败：{e}"}
    except Exception as e:
        return {"ok": False, "error": f"调研失败：{e}"}

    with _cache_lock:
        _cache[code] = {"ts": time.time(), "markdown": markdown}
    return {"ok": True, "markdown": markdown, "cached": False}
