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


def bucket_last_indices(dates: Iterable[str], period: str) -> list[int]:
    """每个桶内**最后一个交易日**的下标（第 17 项，供面板合样共用）。

    与 `resample_bars` 的分桶规则同源：都用 `bucket_key`、都取桶内最后一根。
    `indicators/data.py::_resample_panel` 以前自己用 `bucket_key` 手写了一遍
    等价逻辑，一旦 `bucket_key` 的边界规则改动（例如把某周起点挪一天），
    `resample_bars` 会跟着改而RPS 面板**不会** —— 两边静默分叉，
    且症状只是「RPS 排名有点怪」，极难归因。

    只返回下标、不聚合，供「只要收盘价、且要保留矩阵形态」的面板使用；
    需要完整 OHLCV 聚合的调用方仍应走 `resample_bars`。
    """
    keys = [bucket_key(str(d)[:10], period) for d in dates]
    last = len(keys) - 1
    return [i for i in range(len(keys)) if i == last or keys[i + 1] != keys[i]]


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
