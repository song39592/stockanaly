# -*- coding: utf-8 -*-
"""流通股本数据源 —— 换手率与筹码分布的基础数据。

**为什么需要它**：本地通达信日线只有 OHLCV，没有流通股本，因此算不出换手率
（见 `tdx_reader.read_day_bars`，`turnover` 一律留空）。而筹码分布的核心就是
「换手率 = 成交量 ÷ 流通股本」驱动的筹码衰减，所以必须单独接一路数据源。

数据源：腾讯行情 `qt.gtimg.cn`（与 `valuation_service.fetch_quote` 同源，实测可用）。
返回体按 `~` 分隔，相关字段：
    [1]  名称      [3]  现价      [6]  成交量（手）
    [38] 换手率%   [44] 流通市值（亿元）   [45] 总市值（亿元）

反推：股本(股) = 市值(亿元) × 1e8 ÷ 现价。
实测自洽性（浦发 / 宁德）：用 [44]÷[3] 得到的流通股本，与「当日成交量 ÷ [38] 换手率」
算出的股本相差 <1%，两种口径互相印证。

本模块只负责**取数 + 反推**，不落库（落库在 `price_service.sync_share_capital`）。
"""
from __future__ import annotations

import re
from typing import Any

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
TIMEOUT = 10

# 字段下标（腾讯行情）
_F_NAME, _F_PRICE, _F_VOLUME = 1, 3, 6
_F_TURNOVER, _F_FLOAT_CAP, _F_TOTAL_CAP = 38, 44, 45
_MIN_FIELDS = 46          # 少于此长度视为返回异常（正常为 88）


def market_symbol(code: str) -> str:
    """6 位代码 → 带市场前缀（sh / sz / bj）的符号。"""
    code = str(code).strip().zfill(6)
    if code.startswith(("60", "68", "51", "58", "11", "5", "6")):
        return "sh" + code
    if code.startswith(("00", "30", "12", "15", "16", "18", "0", "1", "2", "3")):
        return "sz" + code
    return "bj" + code


def _num(value: Any, digits: int = 4) -> float | None:
    if value is None:
        return None
    try:
        text = str(value).replace(",", "").strip()
        if not text or text in {"--", "-"}:
            return None
        return round(float(text), digits)
    except (TypeError, ValueError):
        return None


def fetch_share_capital(code: str, errors: list | None = None) -> dict:
    """取某只股票的流通股本 / 总股本（单位：**股**）。

    返回：{"float_shares", "total_shares", "price", "name", "turnover",
           "float_cap_yi", "total_cap_yi", "source", "as_of"}
    取不到时对应字段为 None，并把原因 append 到 `errors`。
    """
    errors = errors if errors is not None else []
    result: dict[str, Any] = {
        "float_shares": None, "total_shares": None, "price": None, "name": "",
        "turnover": None, "float_cap_yi": None, "total_cap_yi": None,
        "source": "", "as_of": "",
    }
    symbol = market_symbol(code)
    try:
        resp = requests.get(f"https://qt.gtimg.cn/q={symbol}",
                            headers={"User-Agent": UA}, timeout=TIMEOUT)
        resp.encoding = "gbk"
        resp.raise_for_status()
    except Exception as exc:                      # noqa: BLE001 - 单源失败即降级
        errors.append(f"腾讯行情获取失败：{exc}")
        return result

    payload = resp.text.split("=", 1)[-1].strip().strip('";')
    fields = payload.split("~")
    if len(fields) <= _MIN_FIELDS:
        errors.append(f"腾讯行情返回字段不足（{len(fields)}）")
        return result

    price = _num(fields[_F_PRICE], 4)
    float_cap = _num(fields[_F_FLOAT_CAP], 4)     # 流通市值（亿元）
    total_cap = _num(fields[_F_TOTAL_CAP], 4)     # 总市值（亿元）
    result.update({
        "name": re.sub(r"\s+", "", fields[_F_NAME] or ""),
        "price": price,
        "turnover": _num(fields[_F_TURNOVER], 4),     # 换手率 %
        "float_cap_yi": float_cap,
        "total_cap_yi": total_cap,
    })
    if not price or price <= 0:
        errors.append("腾讯行情未返回有效现价，无法反推股本")
        return result
    if float_cap:
        result["float_shares"] = round(float_cap * 1e8 / price, 0)
    if total_cap:
        result["total_shares"] = round(total_cap * 1e8 / price, 0)
    if result["float_shares"] or result["total_shares"]:
        result["source"] = "腾讯行情（流通/总市值 ÷ 现价 反推）"
    else:
        errors.append("腾讯行情未返回市值，无法反推股本")
    return result
