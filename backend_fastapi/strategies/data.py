# -*- coding: utf-8 -*-
"""【策略包唯一数据入口】—— 策略所需行情数据只能从这里取。

约束（详见 ARCHITECTURE.md）：
  * 本文件是策略包内**唯一**允许 `import price_store` / 列本地代码 的地方。
  * 策略实现文件只能 `from .data import get_ohlcv / list_universe_codes`，
    不得自行读取数据库、文件或调用 akshare / 网络。
  * 取数结果统一为 pandas.DataFrame，索引为日期字符串（YYYY-MM-DD），
    列固定为 open / high / low / close / volume，按日期升序。

回测使用**后复权（hfq）**口径（默认），使收益率反映真实持仓盈亏；
技术指标展示用前复权，两者口径不同，请勿混用。
"""
from __future__ import annotations

import pandas as pd

# 复用指标包的取数实现（复权/对齐/缺失处理已在 indicators.data 统一解决）。
from indicators.data import get_ohlcv as _get_ohlcv
import price_store


def get_ohlcv(code: str, start: str | None = None, end: str | None = None,
              adjust: str = "hfq") -> pd.DataFrame:
    """获取某只股票的行情序列（策略包唯一取数函数）。

    参数与 indicators.data.get_ohlcv 一致，仅默认口径改为 "hfq"（回测用后复权）。
    """
    return _get_ohlcv(code, start, end, adjust)


def list_universe_codes() -> list[str]:
    """列出本地已下载（有日 K）的全部股票代码，供「回测范围」选择股票池。"""
    return sorted(price_store.code_latest_dates("raw").keys())
