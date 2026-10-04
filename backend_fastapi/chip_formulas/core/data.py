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

# 取数链路里藏着两个**联网兜底**（单只票无感，全市场批量会致命）：
#   · 锁仓比例 → stock_profile → akshare 东财
#   · 库里没有流通股本 → get_float_shares(auto_sync) → 腾讯行情现采
# 全市场几千只逐一打到外部接口会被拖垮 / 触发限流，故提供**按次**的 offline 开关：
# offline=True 时锁仓系数一律取 1.0（不做修正）、缺流通股本直接抛 RuntimeError
# （调用方标注「数据不足」），全程不联网。
# 按次而非全局：后台跑批量榜时，交互式的单票请求仍走联网口径拿到完整锁仓修正。


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
    lockup_ratio: float = 0.0   # 锁仓占比 r（前十大流通股东 占流通股比例 > 5% 合计）
    lockup_factor: float = 1.0  # 衰减修正系数 1/(1-r)，1.0 = 未修正 / 取数失败


def _lockup_of(code: str) -> tuple[float, float]:
    """锁仓比例 r 与换手放大系数 1/(1-r)，取自前十大**流通**股东（见 stock_profile）。

    真实换手发生在**可自由流通**的筹码上，锁定部分（大股东 / 战投等不参与日常交易）
    不随换手衰减，故把换手率放大 1/(1-r) 倍，等价于「扣除非流通筹码后再算衰减」
    （口径对齐图片：15.00%+9.35% → 1/(1-24.35%)=1.32）。

    **失败一律回退 (0.0, 1.0)**：股东数据是外部网络取数，任何异常都不该拖垮筹码主流程。
    """
    try:
        import stock_profile
        info = stock_profile.lockup_ratio(code, 5.0)
        r = float(info.get("ratio") or 0.0)
        f = float(info.get("factor") or 1.0)
        if 0.0 <= r < 1.0 and 1.0 <= f <= 20.0:
            return r, f
    except Exception:                                   # noqa: BLE001 - 取数失败不致命
        pass
    return 0.0, 1.0


def load_input(code: str, start: str | None = None, end: str | None = None,
               adjust: str = "qfq", days: int | None = None,
               bins: int = 80, period: str = "day",
               offline: bool = False) -> ChipInput:
    """取一段行情并装配成公式输入。

    `period` 与 K 线周期一致：周线的一根 = 一周的成交量合计 + 一周的高低区间，
    换手率（成交量 ÷ 流通股本）自然就是**周换手率**，衰减按周期步进，无需另算。

    `offline=True` 用于全市场批量：**不联网**（锁仓系数取 1.0、缺流通股本即抛错），
    详见模块开关处的说明。

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
    last_date = str(df.index[-1])[:10]
    # offline：缺股本不再走腾讯行情现采，直接抛错由调用方标注「数据不足」
    float_shares = ind_data.get_float_shares(code, last_date, auto_sync=not offline)
    lockup_ratio, lockup_factor = (0.0, 1.0) if offline else _lockup_of(code)

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
        lockup_ratio=lockup_ratio,
        lockup_factor=lockup_factor,
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
