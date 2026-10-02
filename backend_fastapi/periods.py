# -*- coding: utf-8 -*-
"""K 线周期合样：日 / 周 / 月。

**日线是唯一落库的口径**（`price_store` 只存日 K），周线 / 月线在**读取时现算**，
不另外存一份——与「不存 qfq_factor（每天都在变）」同理：只留不可变的数据，
派生口径一律现算，免得两处不一致。

聚合规则（与行情软件一致）：
  * 分组：周 = 自然周（周一 ~ 周日），月 = 自然月；该根的日期取组内**最后一个交易日**；
  * open 取组内第一根的开盘、close 取最后一根的收盘、high / low 取组内极值；
  * volume / amount / turnover 取**合计**：周成交额 = 该周每日成交额之和，
    周换手率 = 该周每日换手率之和（= 周成交量 ÷ 流通股本），口径自洽；
  * 涨跌幅 / 涨跌额 / 振幅按聚合后的 OHLC 现算（基准为上一根收盘），不用日线残留值。

复权注意：必须在**复权之后**再合样（先 `load_bars(adjust=...)` 再 `resample_bars`），
否则除权当周的 OHLC 会跨价格台阶。
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Iterable

PERIODS = ("day", "week", "month")


def normalize(period: str | None) -> str:
    """校验并归一周期；非法值直接抛（由调用方转 4xx）。"""
    key = str(period or "day").strip().lower()
    if key not in PERIODS:
        raise ValueError(f"不支持的周期: {period}（可选：{', '.join(PERIODS)}）")
    return key


def bucket_key(day: str, period: str) -> str:
    """某个交易日所属的周期桶（同一桶内合成一根）。"""
    if period == "day":
        return day
    date = dt.date.fromisoformat(day)
    if period == "week":
        iso = date.isocalendar()          # (年, 周序号, 星期几)
        return f"{iso[0]}-W{iso[1]:02d}"
    return f"{date.year}-{date.month:02d}"


def _merge(bucket: list[dict[str, Any]], period: str) -> dict[str, Any]:
    """把一个周期桶内的日线合成一根。"""
    first, last = bucket[0], bucket[-1]
    highs = [b.get("high") for b in bucket if b.get("high") is not None]
    lows = [b.get("low") for b in bucket if b.get("low") is not None]
    amounts = [b.get("amount") for b in bucket if b.get("amount") is not None]
    turns = [b.get("turnover") for b in bucket if b.get("turnover") is not None]

    item = dict(last)                                  # 其余字段（adjust / source …）沿用最后一根
    item["trade_date"] = str(last.get("trade_date"))[:10]
    item["open"] = first.get("open")
    item["high"] = max(highs) if highs else None
    item["low"] = min(lows) if lows else None
    item["close"] = last.get("close")
    item["volume"] = round(sum(float(b.get("volume") or 0) for b in bucket), 2)
    item["amount"] = round(sum(float(a) for a in amounts), 4) if amounts else None
    item["turnover"] = round(sum(float(t) for t in turns), 4) if turns else None
    item["merged_days"] = len(bucket)                  # 该根由几个交易日合成（前端提示用）
    item["period"] = period
    return item


def resample_bars(bars: Iterable[dict[str, Any]], period: str | None = "day") -> list[dict[str, Any]]:
    """把日线序列合成指定周期；period=day 时原样返回（不复制）。"""
    period = normalize(period)
    bars = list(bars or [])
    if period == "day" or not bars:
        return bars

    out: list[dict[str, Any]] = []
    current_key = None
    bucket: list[dict[str, Any]] = []
    for bar in bars:
        day = str(bar.get("trade_date") or "")[:10]
        if not day:
            continue
        key = bucket_key(day, period)
        if key != current_key:
            if bucket:
                out.append(_merge(bucket, period))
            current_key, bucket = key, []
        bucket.append(bar)
    if bucket:
        out.append(_merge(bucket, period))

    # 涨跌幅 / 涨跌额 / 振幅：按聚合后的 OHLC 现算，丢弃日线残留值
    previous_close = None
    for item in out:
        close = item.get("close")
        high, low = item.get("high"), item.get("low")
        if previous_close and close is not None:
            item["change_amount"] = round(float(close) - float(previous_close), 4)
            item["change_pct"] = round((float(close) - float(previous_close))
                                       / float(previous_close) * 100, 4)
            if high is not None and low is not None:
                item["amplitude"] = round((float(high) - float(low))
                                          / float(previous_close) * 100, 4)
            else:
                item["amplitude"] = None
        else:
            item["change_amount"] = None
            item["change_pct"] = None
            item["amplitude"] = None
        previous_close = close
    return out
