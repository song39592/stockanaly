# -*- coding: utf-8 -*-
"""【公式包唯一数据入口】——筹码公式所需数据只能从这里取。

约束（详见 ARCHITECTURE.md）：
  * 本文件是包内**唯一**允许 import 外部取数模块（`indicators.data` / `price_service`）的地方，
    公式实现文件不得自行读库、读文件或发起网络请求。
  * 实际上**公式通常不需要取数**：输入由 `ChipContext` 一次性喂进去，
    公式只管「给定行情与价格轴，筹码怎么摊、怎么衰减」。

取数结果统一为 `ChipInput`：
  * 迭代区间 = **窗口 + 预热**（预热只为把筹码状态养熟，不出现在输出里，见 WARMUP_RATIO）；
  * 价格轴覆盖**整个迭代区间**（含预热），窗口各帧共用同一根轴，才能按帧比较与光标回溯；
  * 换手率 = 成交量(手)×100 ÷ 流通股本；均价 = 成交额 ÷ 成交量（缺失回落 (H+L+C)/3）。
"""
from __future__ import annotations

import dataclasses as dc
from typing import Any

import numpy as np

from indicators import data as ind_data
from periods import normalize

# 预热：窗口首日的筹码只累积了一天，直接算会退化成一根尖刺
# （实测首帧 90% 集中度 0.009、获利比例 0.88%），故迭代区间在窗口之前再取等长的一段。
WARMUP_RATIO = 1.0


@dc.dataclass
class ChipInput:
    """公式包的取数结果（core 内部流转用）。"""
    dates: list[str]
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray          # 手
    turnover: np.ndarray        # 小数 0~1
    vwap: np.ndarray            # 当日成交均价（元）
    float_shares: float         # 流通股本（股）
    edges: np.ndarray           # 价格分箱边界，长度 bins+1
    centers: np.ndarray         # 分箱中心，长度 bins
    win: int                    # 输出帧数（= 窗口 = K 线可见根数）
    warm: int                   # 预热根数（只用于养状态，不输出）
    adjust: str
    period: str = "day"         # K 线周期（与 K 线展示一致）


def load_input(code: str, start: str | None = None, end: str | None = None,
               adjust: str = "qfq", days: int | None = None,
               bins: int = 80, period: str = "day") -> ChipInput:
    """取一段行情并装配成公式输入。

    `period` 与 K 线周期一致：周线的一根 = 一周的成交量合计 + 一周的高低区间，
    换手率（成交量 ÷ 流通股本）自然就是**周换手率**，衰减按周期步进，无需另算。

    异常：RuntimeError 行情不足 / 缺流通股本（由路由层转成业务性失败）。
    """
    period = normalize(period)
    full = ind_data.get_ohlcv(code, start, end, adjust=adjust, period=period)
    n_all = len(full)
    win = int(days) if days and int(days) > 0 else n_all
    win = max(1, min(win, n_all))
    warm = min(n_all - win, int(win * WARMUP_RATIO))
    df = full.iloc[n_all - win - warm:] if warm else full.iloc[n_all - win:]

    turnover = ind_data.get_turnover(code, start, end, adjust=adjust, period=period)
    turnover = turnover.reindex(df.index).fillna(0.0)
    vwap = ind_data.get_vwap(code, start, end, adjust=adjust,
                             period=period).reindex(df.index)
    float_shares = ind_data.get_float_shares(code, str(df.index[-1])[:10])

    low = df["low"].astype(float).to_numpy()
    high = df["high"].astype(float).to_numpy()
    # 价格轴覆盖整个迭代区间（含预热）：区间外直接套分布公式会出现「越界越大」的伪值，
    # 让区间外的筹码堆到边界分箱上，形状失真。
    p_lo, p_hi = float(np.min(low)), float(np.max(high))
    if p_hi <= p_lo:
        p_hi = p_lo + max(0.01, abs(p_lo) * 0.01)
    pad = (p_hi - p_lo) * 0.02
    p_lo, p_hi = p_lo - pad, p_hi + pad
    edges = np.linspace(p_lo, p_hi, int(bins) + 1)

    return ChipInput(
        dates=[str(d)[:10] for d in df.index],
        open=df["open"].astype(float).to_numpy(),
        high=high,
        low=low,
        close=df["close"].astype(float).to_numpy(),
        volume=df["volume"].astype(float).to_numpy(),
        turnover=turnover.astype(float).to_numpy(),
        vwap=vwap.astype(float).to_numpy(),
        float_shares=float(float_shares),
        edges=edges,
        centers=(edges[:-1] + edges[1:]) / 2.0,
        win=win,
        warm=warm,
        adjust=adjust,
        period=period,
    )


def get_float_shares(code: str, date: str | None = None) -> float:
    """取流通股本（股）；缺则先现采一次。"""
    return ind_data.get_float_shares(code, date)


def float_shares_info(code: str, force: bool = False) -> dict[str, Any]:
    """单独查流通股本（单位股），供前端展示 / 排障；force=True 强制重新采集。"""
    try:
        shares = ind_data.get_float_shares(code, auto_sync=False)
        source = "本地库"
    except RuntimeError:
        shares, source = None, ""
    if force or shares is None:
        import price_service
        synced = price_service.sync_share_capital(code, force=force)
        if not synced.get("ok"):
            return {"ok": False, "code": code,
                    "error": "；".join(synced.get("errors") or ["采集失败"])}
        shares = synced.get("float_shares")
        source = synced.get("source") or ""
    if not shares:
        return {"ok": False, "code": code, "error": "仍未取到流通股本"}
    return {"ok": True, "code": code, "float_shares": float(shares),
            "float_yi": round(float(shares) / 1e8, 4), "source": source}
