# -*- coding: utf-8 -*-
"""【策略包唯一数据入口】—— 策略/回测所需的行情只能从这里取。

约束：
  * 本文件是策略包内**唯一**允许 `import price_store` / 调网络 的地方。
  * 策略实现文件只能 `from .data import load_bars / list_universe_codes`，
    不得自行读库、读文件或调 akshare / 网络。
  * 每个策略自己决定拉哪些字段（fields），满足「各策略数据不同」。

K 线本身**不经本文件直连存储**，而是走 `kline_service.get_bars()`——全项目唯一的
「取数 + 周期合样」出口（与个股页 K 线、指标、筹码共用同一份实现）。
回测**固定日线**：`stats.py` 的年化与夏普按 **252 交易日/年**折算（见其中 `252.0`），
若换成周 / 月序列，这两个指标会静默算错；真要做周期回测，必须先改 stats 的年化口径，
并重新定义「T+1 成交」「持仓天数」等语义，故此处不开放 period。

load_bars 支持批量取数并按 fields 切片，且按 (code, start, end, adjust) **逐标的缓存**
底层全量 OHLCV；「回测预检」「策略拉数」「统计拉数」多次调用（即使字段不同、code 集合不同）
都只触发一次真实取数，不会重复读库/读文件。

默认后复权（hfq）口径（回测用，反映真实持仓盈亏）。技术指标展示用前复权，请勿混用。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pandas as pd

import kline_service
import price_store

# 对外只暴露 OHLCV：保持与原先（经 indicators.data.get_ohlcv）一致的列集合，
# 避免 amount / turnover 等列混进策略的 DataFrame 改变既有行为。
_BAR_COLUMNS = ("open", "high", "low", "close", "volume")
_MIN_ROWS = 2          # 少于 2 根无法算信号 / 回测，视为数据不足


# (code, start, end, adjust) -> DataFrame | None（逐标的缓存全量 OHLCV）
_PER_CACHE: dict = {}


_IO_WORKERS = 6   # 并行读库线程数：SQLite WAL + 每线程独立连接，纯读安全


def load_bars(codes, start: str | None = None, end: str | None = None,
              fields: list[str] | None = None, adjust: str = "hfq") -> dict[str, pd.DataFrame]:
    """批量取数：返回 {code: DataFrame[fields]}（只含成功加载的标的）。

    参数：
      codes  单只字符串或列表，自动归一化、去空白。
      fields 需要的列，如 ["close"] / ["close","volume"]；None 取全部
             open/high/low/close/volume。不存在的列会被忽略。
      start/end/adjust 与 indicators.data.get_ohlcv 一致。

    缓存：每个 (code, start, end, adjust) 只取一次，按 fields 切片返回，
          因此同一标的不同字段的多次请求零额外 I/O。
    """
    if isinstance(codes, str):
        codes = [codes]
    codes = [str(c).strip() for c in codes if str(c).strip()]
    if not codes:
        return {}

    cols = [c for c in (fields or []) if c]
    out: dict[str, pd.DataFrame] = {}

    # 只取未命中的：全量回测（5000+ 只）冷缓存时串行读库要几分钟，
    # 这里用线程池并行读——db.connect 每次新建独立 SQLite 连接（WAL），
    # 线程间无共享连接，安全。命中缓存的直接用，不进线程池。
    def _fetch(code: str):
        try:
            # 统一的 K 线出口（回测固定日线，原因见模块 docstring）
            d = kline_service.get_bars(code, start=start, end=end, adjust=adjust,
                                       period="day").copy()
            d = d[[c for c in _BAR_COLUMNS if c in d.columns]]
            d.index = [str(x)[:10] for x in d.index]
            return d if len(d) >= _MIN_ROWS else None
        except Exception:                    # noqa: BLE001 - 单只失败不拖垮整批
            return None

    missing = [(c, (c, start, end, adjust)) for c in codes
               if (c, start, end, adjust) not in _PER_CACHE]
    if len(missing) > 1:
        with ThreadPoolExecutor(max_workers=_IO_WORKERS) as ex:
            for (code, key), df in zip(missing, ex.map(_fetch, [m[0] for m in missing])):
                _PER_CACHE[key] = df
    elif missing:
        code, key = missing[0]
        _PER_CACHE[key] = _fetch(code)

    for code in codes:
        df = _PER_CACHE.get((code, start, end, adjust))
        if df is None:
            continue
        out[code] = df[[c for c in cols if c in df.columns]] if cols else df
    return out


def list_universe_codes() -> list[str]:
    """列出本地已下载（有日 K）的全部股票代码，供「回测范围」选股票池。"""
    return sorted(price_store.code_latest_dates("raw").keys())
