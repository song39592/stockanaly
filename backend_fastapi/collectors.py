# -*- coding: utf-8 -*-
"""数据采集层：元信息 / 巨潮公告 / 财经新闻 / 机构调研纪要。

设计原则：
- 每个采集函数独立，异常只记录不抛出，保证整体流程不因单一来源崩溃。
- 对外返回「结构化字典 + 可选 note」，方便上层拼装 prompt。
- 爬虫请求之间强制 sleep，降低反爬风险。
"""
import re
import time
import datetime
from urllib.parse import quote

import requests
import akshare as ak

from market_service import _ak   # 统一走带硬超时的 akshare 调用，避免数据源无响应时挂死

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Referer": "https://data.eastmoney.com/",
}

# 抓取正文时的请求间隔（秒），防反爬
SLEEP_NOTICE = 1.2

# 公告标题命中以下关键词视为「程序性/无关公告」，直接过滤
BORING_KEYWORDS = [
    "股东大会", "董事会会议决议", "监事会会议决议", "董事会决议", "监事会决议",
    "独立董事意见", "独立董事关于", "续聘会计师事务所", "募集资金存放与使用",
    "年度审计报告", "内部控制自我评价", "公司章程", "分红派息实施",
    "权益分派实施", "回购注销完成",
]

# 命中以下关键词的公告视为「重点」，会抓正文做摘要
IMPORTANT_KEYWORDS = [
    "业绩预告", "业绩快报", "澄清", "重大合同", "中标", "减持", "增持", "质押", "冻结",
    "风险警示", "立案", "重组", "定增", "回购", "诉讼", "担保", "半年报", "年报", "季报",
    "投资者关系", "调研",
]

POSITIVE_EVENT_KEYWORDS = ["中标", "重大合同", "增持", "回购", "业绩预增", "扭亏"]
NEGATIVE_EVENT_KEYWORDS = ["减持", "立案", "风险警示", "诉讼", "质押", "冻结", "业绩预亏"]


def event_signal(title: str) -> str:
    """基于标题的确定性事件标签；只用于分类，不等同于股价方向预测。"""
    positive = any(k in (title or "") for k in POSITIVE_EVENT_KEYWORDS)
    negative = any(k in (title or "") for k in NEGATIVE_EVENT_KEYWORDS)
    if positive and not negative:
        return "positive_event"
    if negative and not positive:
        return "risk_event"
    return "neutral_or_mixed"


def _today():
    return datetime.date.today()


def _days_ago(n):
    return (_today() - datetime.timedelta(days=n)).strftime("%Y%m%d")


def _clip(text: str, limit: int) -> str:
    """压缩长文本，去掉多余空白。"""
    t = re.sub(r"\s+", " ", text or "").strip()
    return t[:limit] + ("…" if len(t) > limit else "")


def _extract_art_code(url: str):
    m = re.search(r"(AN\d+)", url or "")
    return m.group(1) if m else ""


def _is_boring(title: str) -> bool:
    return any(k in (title or "") for k in BORING_KEYWORDS)


def _importance(title: str, typ: str) -> int:
    """越重要越靠前；命中关键词给高分。"""
    s = (title or "") + (typ or "")
    score = 0
    for k in IMPORTANT_KEYWORDS:
        if k in s:
            score += 1
    return score


# ---------------------------------------------------------------- 1. 元信息

def get_basic_info(code: str):
    """个股简介 / 行业。失败返回 None，不中断。"""
    try:
        df = _ak(ak.stock_individual_info_em, symbol=code)
        if df is None or getattr(df, "empty", True):
            return None
        d = dict(zip(df["item"], df["value"]))
        parts = []
        for k, label in [("股票简称", "简称"), ("行业", "行业"), ("主营业务", "主营"),
                         ("上市时间", "上市时间"), ("总股本", "总股本")]:
            if d.get(k):
                parts.append(f"{label}：{d.get(k)}")
        return "；".join(parts) if parts else None
    except Exception as e:
        return None


def get_basic_info_evidence(code: str):
    """公司基础信息证据。返回统一证据对象，便于报告引用与审计。"""
    text = get_basic_info(code)
    if not text:
        return None
    return {
        "id": "BASIC-1",
        "kind": "company_profile",
        "title": f"{code} 公司基础信息",
        "published_at": None,
        "source": "东方财富/akshare",
        "source_url": f"https://quote.eastmoney.com/{code}.html",
        "content": text,
    }


# ---------------------------------------------------------------- 2. 巨潮公告

def get_announcements(code: str, days: int = 60):
    """近 N 个月公告列表（标题/类型/日期/网址/art_code），已过滤程序性公告。"""
    begin = _days_ago(days)
    end = _today().strftime("%Y%m%d")
    try:
        df = _ak(ak.stock_individual_notice_report,
                 security=code, begin_date=begin, end_date=end)
        if df is None or getattr(df, "empty", True):
            return [], "公告抓取失败或超时（数据源无响应）"
    except Exception as e:
        return [], f"公告抓取失败：{e}"

    items = []
    for _, row in df.iterrows():
        title = str(row.get("公告标题", "")).strip()
        if not title or _is_boring(title):
            continue
        url = str(row.get("网址", ""))
        items.append({
            "title": title,
            "type": str(row.get("公告类型", "")).strip(),
            "date": str(row.get("公告日期", ""))[:10],
            "art_code": _extract_art_code(url),
            "importance": _importance(title, row.get("公告类型", "")),
            "source": "巨潮资讯/东方财富",
            "source_url": url,
        })
    items.sort(key=lambda x: (x["importance"], x["date"]), reverse=True)
    return items, None


def fetch_notice_content(art_code: str):
    """抓取公告正文（巨潮公告在东财的 HTML 正文）。失败返回 None。"""
    if not art_code:
        return None
    url = (f"https://np-cnotice-stock.eastmoney.com/api/content/ann"
           f"?art_code={art_code}&client_source=web&page_index=1")
    try:
        r = requests.get(url, headers=UA, timeout=15)
        j = r.json()
        return j.get("data", {}).get("notice_content", "") or None
    except Exception:
        return None


# ---------------------------------------------------------------- 3. 财经新闻

def get_news(code: str, days: int = 30):
    """近 30 天财经新闻（东财个股新闻，含全文）。"""
    try:
        df = _ak(ak.stock_news_em, symbol=code)
        if df is None or getattr(df, "empty", True):
            return [], "新闻抓取失败或超时（数据源无响应）"
    except Exception as e:
        return [], f"新闻抓取失败：{e}"

    cutoff = (_today() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    seen = set()
    items = []
    for _, row in df.iterrows():
        title = str(row.get("新闻标题", "")).strip()
        date = str(row.get("发布时间", ""))[:10]
        content = str(row.get("新闻内容", "")).strip()
        if not title:
            continue
        if date and date < cutoff:
            continue
        # 简单去重（按标题前 20 字）
        key = title[:20]
        if key in seen:
            continue
        seen.add(key)
        items.append({
            "title": title,
            "content": _clip(content, 400),
            "date": date,
            "source": str(row.get("文章来源", "")).strip(),
            "source_url": str(row.get("新闻链接", row.get("新闻网址", ""))).strip(),
        })
    return items, None


def evidence_search_url(code: str, title: str) -> str:
    """当上游未提供文章链接时，给出可核对的站内搜索入口。"""
    return f"https://so.eastmoney.com/web/s?keyword={quote((code + ' ' + title).strip())}"


# ---------------------------------------------------------------- 4. 机构调研纪要

def get_research_notes(announcements, limit: int = 2):
    """从公告里挑「调研活动/投资者关系活动记录表」，抓正文提取问答摘要。"""
    notes = []
    for a in announcements:
        if "调研" in a["type"] or "投资者关系" in a["title"]:
            notes.append(a)
        if len(notes) >= limit:
            break

    result = []
    for a in notes:
        body = fetch_notice_content(a["art_code"])
        if body:
            result.append({"date": a["date"], "title": a["title"], "qa": _clip(body, 2000)})
        time.sleep(SLEEP_NOTICE)
    return result
