# -*- coding: utf-8 -*-
"""raw HTTP 统一出口（第 15 项）。

**为什么要有这一层**：改之前 raw HTTP 有 6 份独立实现（盘面 / 估值 / 流通股本 /
东财采集 / 通达信扫雷宝 / LLM），UA 四份且版本不一致（Chrome 120 与 124 混用）、
重试口径不一致（只有盘面那份有重试 + 退避）、连接复用也不一致。
本模块把这些收成一处：**UA 与 referer 常量唯一、连接复用、重试 + 退避**。

**akshare 侧不在本模块范围内** —— `market_service._ak` 已经是唯一封装、7 个模块在用，
不要动它（它管的是 V8 串行化与硬超时，语义和 raw HTTP 不同）。

## 三条使用约定

1. **`retries` 默认 1（不重试），不要改成 3。**
   重试是**串行阻塞**的（time.sleep 线性退避）。若默认值是 3，一次调用在数据源抖动时
   最坏耗时 = timeout x 3 + 0.5 + 1.0；timeout 12s 就是 **38.5s**，页面会像卡死。
   需要重试的调用方（目前只有盘面历史行情）**显式**传 retries=3。

2. **`post` 的 `timeout` 是必填关键字参数，没有默认值。**
   LLM 调用的超时是**分钟级**，与行情的十秒级完全不是一回事；
   给它一个共用默认值必然有人踩坑。强制显式传参是这里唯一可靠的防呆手段。

3. **别在业务模块里再 import requests 直接发请求。** 新增数据源一律走这里，
   否则 UA / 超时 / 重试口径又会分叉。
"""
from __future__ import annotations

import time

import requests

# ---- UA 与 referer：全仓唯一一份（第 15 项前有 4 个 UA 变体）----
# Chrome/124 与原先 market / valuation 用的完全一致；share / collectors / 通达信
# 那三处原是 120 与 120.0.0.0，实测换 124 后各数据源仍正常取到值。
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0 Safari/537.36")
SINA_REFERER = "https://finance.sina.com.cn/"
EASTMONEY_REFERER = "https://data.eastmoney.com/"
TDX_SLB_REFERER = ("http://page3.tdx.com.cn:7615/site/pcwebcall_static/bxb/bxb.html"
                   "?code={code}&color=0")

DEFAULT_TIMEOUT = 12       # 秒。行情类接口的经验值：太短误报失败，太长页面卡住
RETRY_BACKOFF = 0.5        # 退避系数：第 n 次重试前 sleep(RETRY_BACKOFF * n)

# 进程内共用一个 Session：连接复用（TCP + TLS 握手只付一次），
# 且默认带上 UA —— 漏传 UA 是最容易被数据源静默降级成错误数据的原因。
_session = requests.Session()
_session.headers.update({"User-Agent": UA})


def _headers(referer, extra):
    """拼请求头；没有 referer 也没有额外头时返回 None（用 session 默认头）。"""
    headers = dict(extra) if extra else {}
    if referer:
        headers["Referer"] = referer
    return headers or None


def get(url, referer=None, *, retries=1, timeout=DEFAULT_TIMEOUT, encoding=None,
        params=None, headers=None):
    """GET 请求。失败按 retries 重试（线性退避），全部失败抛出**最后一次**异常。

    retries 的语义是「总尝试次数」，retries=1 即不重试（保持现状、不放大耗时）。
    encoding 用于新浪 / 腾讯这类 GBK 响应（requests 按 header 猜常常猜成 ISO-8859-1）。
    """
    last = None
    for attempt in range(retries):
        try:
            resp = _session.get(url, params=params, headers=_headers(referer, headers),
                                timeout=timeout)
            resp.raise_for_status()
            if encoding:
                resp.encoding = encoding
            return resp
        except Exception as exc:            # noqa: BLE001 - 统一重试后抛出最后一次
            last = exc
            if attempt + 1 < retries:        # 最后一次失败后不再睡，否则白等一个退避
                time.sleep(RETRY_BACKOFF * (attempt + 1))
    raise last


def post(url, *, timeout, referer=None, data=None, json=None, headers=None, retries=1):
    """POST 请求。**timeout 必填、且不给默认值**（见文件头约定 2）。

    retries 默认同样为 1：写操作重试可能造成重复提交，调用方需自行判断是否安全。
    """
    last = None
    for attempt in range(retries):
        try:
            resp = _session.post(url, data=data, json=json,
                                 headers=_headers(referer, headers), timeout=timeout)
            resp.raise_for_status()
            return resp
        except Exception as exc:            # noqa: BLE001
            last = exc
            if attempt + 1 < retries:
                time.sleep(RETRY_BACKOFF * (attempt + 1))
    raise last