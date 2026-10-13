# -*- coding: utf-8 -*-
"""RPS 横截面（全市场收盘价面板）数据层 —— 从 indicators.data 抽出独立维护。

原 indicators.data 是「指标包唯一数据入口」，但「全市场收盘价面板」
（面板构建 / 复权 / 缓存）完全属于 RPS 相对强度指标的中间变量，放在通用
data 入口里既模糊了分层，也让 data.py 膨胀。现独立到此文件，由本模块自行
import price_store（与 indicators.data 同样的「指标侧唯一取数」职责），
indicators.data 只保留通用 OHLCV 入口。

指标层用法：`from .rps_data import get_rps, warm, period_of`。
"""
from __future__ import annotations

import datetime as dt
import threading

import numpy as np
import pandas as pd
import price_store
from periods import bucket_last_indices, normalize

# --------------------------------------------------------------------------- #
# 横截面指标数据：全市场收盘价面板（供 RPS 相对强度等复用）
# --------------------------------------------------------------------------- #
# 与「逐只 get_ohlcv」不同，RPS 需要**同一交易日全市场**的价格截面来排名，
# 因此单独维护一份「全市场宽表」：
#   索引 = 交易日（YYYY-MM-DD），列 = 代码，值 = 后复权收盘价。
# 面板构建较重（~15 秒读全市场 + 复权），故构建一次后常驻内存，跨请求复用；
# 前端进入个股页会先拉指标清单，届时后台线程预热（见 warm），避免首帧卡住。
_RPS_PANEL_YEARS = 6          # 面板回看年数：限制内存；RPS 排名只用近年
# 可重入锁：构建「周 / 月」面板时会复用已缓存的「日」面板（同一线程再次进入）
_MARKET_LOCK = threading.RLock()
_MARKET_PANEL: dict[str, pd.DataFrame] = {}      # period -> 宽表
_MARKET_WARMING: set[str] = set()


def period_of(df: pd.DataFrame) -> str:
    """取 df 对应的 K 线周期（由 base.compute 挂在 df.attrs 上）。"""
    return normalize(str(df.attrs.get("period") or "day"))


def _apply_hfq(panel: pd.DataFrame) -> pd.DataFrame:
    """把原始收盘价宽表按**后复权因子**缩放（只影响价格，用于跨除权的收益比较）。

    第 17 项：实现已收回 `price_store.apply_hfq_factors`。这里原先**自己复制了
    一遍 `load_bars` 的 hfq 算法**（自己算「哪天起该乘哪个因子」），两条链之间
    没有任何交叉验证 —— 一旦 `price_store` 改复权规则，RPS 排名会**静默算错**
    且无人察觉。现在生效区间的判定只有 `price_store.hfq_steps` 一份。

    本函数保留为薄封装：调用点（`_build_market_panel`）零改动，且不改变
    「面板不做指纹校验」这一原有特性 —— `load_market_close` 读回的就是未复权
    收盘价，本函数只负责缩放，两件事分开。
    """
    return price_store.apply_hfq_factors(panel)


def _resample_panel(panel: pd.DataFrame, period: str) -> pd.DataFrame:
    """把日线宽表合样到周 / 月：每桶取**最后一个交易日**那一行。

    第 17 项：桶的判定已改为复用 `periods.bucket_last_indices`（与
    `periods.resample_bars` 同一份 `bucket_key` 规则），不再自己手写一遍
    「下一个 key 变了就取这一根」的判断。

    ⚠️ 性能：这里**只取每桶最后一根**，不做 OHLCV 聚合 —— 面板是
    5585 列 × 多年 的宽表，RPS 只需要收盘价。改用通用 `resample_bars`
    会把成本提高一到两个数量级，所以保留这个优化，只把**分桶规则**共用。
    """
    if panel.empty or period == "day":
        return panel
    labels = [str(d)[:10] for d in panel.index]
    keep = bucket_last_indices(labels, period)
    sub = panel.iloc[keep]
    sub.index = [labels[i] for i in keep]
    return sub


def _build_market_panel(period: str) -> pd.DataFrame:
    # 周 / 月面板直接由已缓存的「日」面板合样得到，避免重复读全市场 + 复权。
    if period == "day":
        start = (dt.date.today() - dt.timedelta(days=365 * _RPS_PANEL_YEARS)).strftime("%Y-%m-%d")
        raw = price_store.load_market_close("raw", start=start)
        return _apply_hfq(raw)
    return _resample_panel(_market_panel("day"), period)


def _market_panel(period: str = "day") -> pd.DataFrame:
    """取（并按需构建）全市场收盘价面板，带进程内缓存 + 并发保护。"""
    key = normalize(period)
    panel = _MARKET_PANEL.get(key)
    if panel is not None:
        return panel
    with _MARKET_LOCK:
        panel = _MARKET_PANEL.get(key)
        if panel is None:
            panel = _build_market_panel(key)
            _MARKET_PANEL[key] = panel
        return panel


def warm(period: str = "day") -> None:
    """后台预热全市场面板（不阻塞调用方）；已在缓存或正在构建则直接返回。"""
    key = normalize(period)
    if key in _MARKET_PANEL or key in _MARKET_WARMING:
        return
    _MARKET_WARMING.add(key)

    def _run():
        try:
            _market_panel(key)
        except Exception:                      # noqa: BLE001 - 预热失败不影响正常请求
            pass
        finally:
            _MARKET_WARMING.discard(key)

    threading.Thread(target=_run, name=f"market-panel-{key}", daemon=True).start()


def market_rps(dates, periods, period: str = "day") -> dict:
    """全市场 RPS（相对价格强度，0~100），对齐到 `dates`（每个日期取 ≤ 它的最近交易日）。

    与 `get_rps` 对单只股票逐次做整表 shift / rank 不同，这里**一次算全市场**
    再按股票池成员挑列，避免对成百上千只成分股重复整表运算（池较大时慢一个量级）。

    返回 {N: pd.DataFrame(index=请求日, columns=代码, 值=RPS 或 NaN)}。
    某个日期在全市场面板里找不到 ≤ 它的交易日时，其对应行全为 NaN。
    """
    panel = _market_panel(period)
    if panel is None or panel.empty:
        raise RuntimeError("全市场横截面数据不可用（本地暂无日 K 数据）")
    periods = [int(n) for n in periods if int(n) > 0]

    # 每个请求日 → 最近的交易日（≤ 它）：面板索引为 ISO 日期字符串，二分查找
    idx_str = sorted(str(x)[:10] for x in panel.index)

    def map_date(d: str):
        d = str(d)[:10]
        lo, hi = 0, len(idx_str)
        while lo < hi:                       # 最后一个 <= d 的下标
            mid = (lo + hi) // 2
            if idx_str[mid] <= d:
                lo = mid + 1
            else:
                hi = mid
        return idx_str[lo - 1] if lo > 0 else None

    mapped = {str(d)[:10]: map_date(d) for d in dates}
    present = sorted({v for v in mapped.values() if v})

    rps_by_period: dict[int, "pd.DataFrame"] = {}
    for n in periods:
        if not present:
            rps_by_period[n] = pd.DataFrame(
                index=[str(d)[:10] for d in dates], columns=panel.columns, dtype="float64")
            continue
        shifted = panel.shift(n).loc[present]
        ret = panel.loc[present] / shifted - 1.0
        rps_by_period[n] = ret.rank(axis=1, pct=True).mul(100.0)

    out: dict[int, "pd.DataFrame"] = {}
    for n in periods:
        df = rps_by_period[n]
        rows = []
        for d in dates:
            td = mapped[str(d)[:10]]
            if td is None or td not in df.index:
                rows.append(pd.Series(np.nan, index=panel.columns, dtype="float64"))
            else:
                rows.append(df.loc[td])
        out[n] = pd.DataFrame(rows, index=[str(d)[:10] for d in dates])
    return out


def get_rps(code: str, periods, dates, period: str = "day") -> dict:
    """某只股票在多个周期的 **RPS（相对价格强度，0~100）**，对齐到 `dates`。

    RPS = 该股 N 日涨幅在全市场的**百分比排名** ×100（陶博士 / 通达信口径）：
        EXTRS = close / close.shift(N) - 1     # N 日区间涨幅（后复权）
        RPS   = rank(EXTRS, 全市场, 升序, pct=True) × 100
    返回 {N: pd.Series(index=dates)}；数据不足 / 未上市处为 NaN。
    """
    panel = _market_panel(period)
    target = [str(d)[:10] for d in dates]
    if panel is None or panel.empty:
        raise RuntimeError("全市场横截面数据不可用（本地暂无日 K 数据）")
    if code not in panel.columns:
        raise RuntimeError(f"全市场横截面数据缺少 {code}")

    present = [d for d in target if d in panel.index]
    out: dict[int, pd.Series] = {}
    if not present:                            # 该股日期与本地面板无交集
        for n in periods:
            out[int(n)] = pd.Series(np.nan, index=target, dtype="float64")
        return out

    sub = panel.loc[present]                   # 只算目标票涉及的交易日，降低排名开销
    for n in periods:
        n = int(n)
        if n <= 0:
            continue
        ret = sub / panel.shift(n).loc[present] - 1.0
        rps = ret.rank(axis=1, pct=True).mul(100.0)[code]
        out[n] = rps.reindex(target)
    return out


def clear_market_panel() -> None:
    """清空全市场面板缓存（供 indicators.data.clear_cache 委派）。"""
    with _MARKET_LOCK:
        _MARKET_PANEL.clear()
