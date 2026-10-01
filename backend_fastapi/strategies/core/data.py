# -*- coding: utf-8 -*-
"""【策略包唯一数据入口】—— 策略/回测所需的行情只能从这里取。

约束：
  * 本文件是策略包内**唯一**允许 `import price_store` / 调网络 的地方。
  * 策略实现文件只能 `from .data import load_bars / list_universe_codes`，
    不得自行读库、读文件或调 akshare / 网络。
  * 每个策略自己决定拉哪些字段（fields），满足「各策略数据不同」。

load_bars 支持批量取数并按 fields 切片，且按 (code, start, end, adjust) **逐标的缓存**
底层全量 OHLCV；「回测预检」「策略拉数」「统计拉数」多次调用（即使字段不同、code 集合不同）
都只触发一次真实取数，不会重复读库/读文件。

默认后复权（hfq）口径（回测用，反映真实持仓盈亏）。技术指标展示用前复权，请勿混用。
"""
from __future__ import annotations

import pandas as pd

from indicators.data import get_ohlcv as _get_ohlcv
import price_store


# (code, start, end, adjust) -> DataFrame | None（逐标的缓存全量 OHLCV）
_PER_CACHE: dict = {}


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

    for code in codes:
        key = (code, start, end, adjust)
        df = _PER_CACHE.get(key)
        if df is None and key not in _PER_CACHE:
            try:
                d = _get_ohlcv(code, start, end, adjust).copy()
                d.index = [str(x)[:10] for x in d.index]
                df = d if len(d) else None
            except Exception:
                df = None
            _PER_CACHE[key] = df
        if df is None:
            continue
        out[code] = df[[c for c in cols if c in df.columns]] if cols else df
    return out


def list_universe_codes() -> list[str]:
    """列出本地已下载（有日 K）的全部股票代码，供「回测范围」选股票池。"""
    return sorted(price_store.code_latest_dates("raw").keys())
