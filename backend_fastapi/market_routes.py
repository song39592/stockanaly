"""盘面及板块分析的五类数据接口（独立模块，不放在 main.py 里）。

数据采集与降级逻辑都在 market_service.py，本模块只负责路由与缓存清理，
新增盘面相关接口时只改这里，不必再动 main.py。

路由前缀 /api/market：
    GET /global     ① 外围环境（美股 / 港股 / 大宗商品 / 费城半导体）
    GET /capital    ② 大盘资金（两市成交 / 涨跌家数 / 主力净流向 / 特大单方向）
    GET /sectors    ③ 板块β（行业与概念资金流 Top10 / 申万一级行业涨跌）
    GET /limit-up   ④ 连板梯队（连板结构 + 晋级率）
    GET /big-loss   ⑤ 大面股（炸板池 + 跌停池）
    POST /ai-analysis ⑥ 盘面 AI 分析（投喂五大维度，调用 LLM，需配置 LLM_API_KEY）
    POST /ai-analysis/stream ⑥ 同上，但以 SSE 流式返回各数据块的拉取进度与最终结果

均支持 ?date=YYYYMMDD 指定交易日（①②③ 仅部分口径有历史数据源，② 恒为实时快照）
与 ?force=1 跳过进程内缓存（ai-analysis 为 POST，date/depth/force 放 body）。
"""

import datetime as dt
import json
import threading
import time

import requests
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

import config
import market_service
from core.jsonutil import json_safe_deep      # 第 09 项：出口清洗
from llm_client import call_llm, llm_error_detail

router = APIRouter(prefix="/api/market", tags=["盘面及板块分析"])

# ---- 盘面 AI 分析（复用与个股调研同一个 LLM_API_KEY）----
_ai_cache = {}
_ai_cache_lock = threading.Lock()
AI_CACHE_TTL = 600  # 秒

DISCLAIMER = "\n\n---\n⚠️ 本内容由 AI 生成，仅供信息参考，不构成任何投资建议。"

MARKET_PROMPT_TEMPLATE = """你是 A 股盘面分析师，仅基于下方 <data> 中的客观数据做分析，严禁编造数据、严禁给出买卖建议。
<data> 是程序从公开行情接口汇总的当日盘面数据（含外围环境、大盘资金、当日/5 日板块资金流向、连板梯队、跌停大面）。

【输出要求】
- 用 markdown 排版，标题清晰，不要输出多余闲聊。
- 严禁以表格或逐条列表的形式复述 <data> 中的原始数值明细（例如板块净流入/净流出逐条排行、晋级率逐档明细等）。原始数据只是你的判断依据，输出必须是结论性、解读性的文字，用概括与归纳来表达。
- 如需引用数据，仅在论证中顺带点出关键方向与代表性的板块/个股即可，绝不罗列完整榜单。

请输出以下四块内容：
1、大盘状态：综合外围环境、两市成交额、涨跌家数、主力/超大单资金方向，判断当前市场整体强弱与风险偏好；
2、热点板块：基于当日与 5 日板块资金净流入，概括资金正在进攻的方向与可能的主线，点出代表性板块（文字概括，勿列表）；
3、连板与情绪：基于连板结构、晋级率与涨停/炸板/跌停家数，概括短线情绪的冷热与持续性（整体描述，勿逐档列晋级率）；
4、风险声明：本内容由 AI 生成，仅供参考，不构成任何投资建议。"""


def _build_prompt(depth: str, data_text: str) -> str:
    """按详细程度拼装最终 prompt。"""
    prompt = MARKET_PROMPT_TEMPLATE
    if depth == "concise":
        prompt += "\n\n【输出篇幅】精简：每块仅 2-3 句要点，总字数 400 以内，仍以结论为主。"
    elif depth == "detailed":
        prompt += "\n\n【输出篇幅】详细：充分展开逻辑与论证，可点名代表性板块/个股方向，但同样禁止罗列原始数值明细。"
    return prompt + "\n\n<data>\n" + data_text + "\n</data>"


def _sse(obj) -> str:
    """把一条事件编码为 SSE 帧（JSON 单行，换行已转义，不会破坏帧结构）。"""
    return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"


class MarketAiRequest(BaseModel):
    date: str = Field(default="", description="指定历史交易日 YYYYMMDD；为空时分析实时盘面")
    depth: str = Field(default="normal", description="concise|normal|detailed，控制 AI 反馈详细程度")
    force: bool = Field(default=False, description="为 true 时跳过缓存，强制重新生成")


@router.get("/global")
def market_global(date: str = "", force: bool = False):
    """① 外围环境：美股 / 港股 / 大宗商品 / 费城半导体。date 为空时返回实时行情。"""
    if force:
        market_service.clear_cache("global_market")
    return json_safe_deep(market_service.global_market(date or None))


@router.get("/capital")
def market_capital(date: str = "", force: bool = False):
    """② 大盘资金：两市成交、涨跌家数、主力净流向、特大单方向。"""
    if force:
        market_service.clear_cache("capital_flow")
    return json_safe_deep(market_service.capital_flow(date or None))


@router.get("/sectors")
def market_sectors(date: str = "", window: str = "今日", force: bool = False):
    """③ 板块β：行业 / 概念板块资金流 Top10、申万一级行业涨跌。date 为空时返回实时/近N日（window）。"""
    if force:
        market_service.clear_cache("sector_beta")
    return json_safe_deep(market_service.sector_beta(date or None, window))


@router.get("/limit-up")
def market_limit_up(date: str = "", force: bool = False):
    """④ 连板梯队：连板结构与晋级率。date 为空时自动取最近交易日。"""
    if force:
        market_service.clear_cache("limit_up")
    return json_safe_deep(market_service.limit_up_ladder(date or None))


@router.get("/big-loss")
def market_big_loss(date: str = "", force: bool = False):
    """⑤ 大面股：炸板池 + 跌停池。date 为空时自动取最近交易日。"""
    if force:
        market_service.clear_cache("big_loss")
    return json_safe_deep(market_service.big_loss(date or None))


@router.post("/ai-analysis")
def market_ai_analysis(req: MarketAiRequest):
    """⑥ 盘面 AI 分析：汇总外围/大盘/当日板块/5日板块/连板/跌停大面，投喂 LLM 生成分析。"""
    if not config.llm_ready() or config.llm_config_problem():
        raise HTTPException(status_code=503, detail=config.llm_config_problem() or "服务端未配置 LLM")

    cache_key = f"{req.date or 'rt'}|{req.depth}"
    with _ai_cache_lock:
        hit = _ai_cache.get(cache_key)
    if hit and not req.force and (time.time() - hit["ts"]) < AI_CACHE_TTL:
        return {"ok": True, **hit["payload"], "cached": True}

    try:
        text, errors = market_service.market_ai_digest(req.date or None)
        markdown = (call_llm(_build_prompt(req.depth, text)) or "").strip() + DISCLAIMER
    except requests.HTTPError as e:
        raise HTTPException(status_code=502, detail=llm_error_detail(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"盘面 AI 分析失败：{e}")

    payload = {"markdown": markdown, "data_errors": errors,
               "data_as_of": dt.date.today().isoformat()}
    with _ai_cache_lock:
        _ai_cache[cache_key] = {"ts": time.time(), "payload": payload}
    return {"ok": True, **payload, "cached": False}


@router.post("/ai-analysis/stream")
def market_ai_analysis_stream(req: MarketAiRequest):
    """⑥ 盘面 AI 分析（SSE 流式）：先逐块推送数据拉取进度，再推送 LLM 生成结果。

    事件格式（每行以 "data: " 开头，JSON 负载）：
      {"stage": "外围环境", "cached": true/false, "errors": [...]}   某块数据就绪
      {"done": true, "ok": true, "markdown": "...", ...}              最终结果
    """
    if not config.llm_ready() or config.llm_config_problem():
        raise HTTPException(status_code=503, detail=config.llm_config_problem() or "服务端未配置 LLM")

    cache_key = f"{req.date or 'rt'}|{req.depth}"
    with _ai_cache_lock:
        hit = _ai_cache.get(cache_key)
    if hit and not req.force and (time.time() - hit["ts"]) < AI_CACHE_TTL:
        payload = {**hit["payload"], "cached": True}

        def cached_gen():
            yield _sse({"done": True, "ok": True, **payload})

        return StreamingResponse(cached_gen(), media_type="text/event-stream")

    def gen():
        text, errors = "", []
        try:
            for kind, payload in market_service.market_ai_digest_progress(req.date or None):
                if kind == "progress":
                    yield _sse({"stage": payload["stage"],
                                "cached": payload["cached"],
                                "errors": payload["errors"]})
                else:
                    text, errors = payload["text"], payload["errors"]
            yield _sse({"stage": "AI 生成", "cached": False, "errors": []})
            markdown = (call_llm(_build_prompt(req.depth, text)) or "").strip() + DISCLAIMER
        except requests.HTTPError as e:
            yield _sse({"done": True, "ok": False, "error": llm_error_detail(e)})
            return
        except Exception as e:
            yield _sse({"done": True, "ok": False, "error": f"盘面 AI 分析失败：{e}"})
            return

        payload = {"markdown": markdown, "data_errors": errors,
                   "data_as_of": dt.date.today().isoformat()}
        with _ai_cache_lock:
            _ai_cache[cache_key] = {"ts": time.time(), "payload": payload}
        yield _sse({"done": True, "ok": True, "cached": False, **payload})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
