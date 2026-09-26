# -*- coding: utf-8 -*-
"""【指标包唯一数据入口】—— 指标所需行情数据只能从这里取。

约束（详见 ARCHITECTURE.md）：
  * 本文件是指标包内**唯一**允许 `import price_store`（以及将来可能的外部源）的地方。
  * 指标实现文件（trend / oscillator / volatility 等）只能 `from .data import get_ohlcv`，
    不得自行读取数据库、文件或调用 akshare / 网络。
  * 取数结果统一为 pandas.DataFrame，索引为日期字符串（YYYY-MM-DD），列固定为
    open / high / low / close / volume，按日期升序排列，前段缺失保留 NaN。

技术面指标当前只需要 OHLCV；若后续需要成交额、换手率等，在本文件扩展即可，
指标实现文件无需改动。
"""
from __future__ import annotations

import datetime as dt
from functools import lru_cache

import pandas as pd
import price_store

_MIN_ROWS = 2  # 少于此行数视为数据不足，技术指标无法计算


def _norm_date(d) -> str | None:
    if d is None:
        return None
    if isinstance(d, dt.date):
        return d.strftime("%Y-%m-%d")
    return str(d)[:10]


@lru_cache(maxsize=256)
def _load(code: str, start: str | None, end: str | None, adjust: str) -> pd.DataFrame | None:
    """缓存层：同一 (code, 区间, 口径) 只解析一次。

    返回 None 表示无数据；否则返回索引为 date、列为 OHLCV 的 DataFrame。
    """
    rows = price_store.load_bars(code, adjust=adjust, start=start, end=end)
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df = df.rename(columns={"trade_date": "date"}).set_index("date").sort_index()
    cols = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
    return df[cols]


def get_ohlcv(code: str, start: str | dt.date | None = None,
              end: str | dt.date | None = None, adjust: str = "qfq") -> pd.DataFrame:
    """获取某只股票的行情序列（指标包唯一取数函数）。

    参数：
      code    股票代码（如 "600000" / "920000"）
      start   起始日（含），缺省取全部
      end     结束日（含），缺省取全部
      adjust  复权口径，技术指标默认 "qfq"（前复权）；回测用 "hfq" 需调用方显式传入

    返回：
      DataFrame，索引为日期字符串，列 open/high/low/close/volume，升序。

    异常：
      RuntimeError  数据不足或无数据时抛出，由调用方（compute）转成接口错误。
    """
    df = _load(code, _norm_date(start), _norm_date(end), adjust)
    if df is None or len(df) < _MIN_ROWS:
        raise RuntimeError(f"行情数据不足: {code} (adjust={adjust})")
    return df


def clear_cache() -> None:
    """测试或切换数据源后清空取数缓存。"""
    _load.cache_clear()
