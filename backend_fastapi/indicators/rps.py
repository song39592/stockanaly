# -*- coding: utf-8 -*-
"""RPS 相对价格强度（下方副图）。

RPS（Relative Price Strength，相对价格强度）= 个股 N 日涨幅在**全市场**的
百分比排名 ×100，取值 0~100，越大越强（100 = 全市场最强）。口径参考
「陶博士 / 通达信 扩展数据」的公式设置方法：

    EXTRS = close / close.shift(N) - 1          # N 日区间涨幅
    RPS   = 排名百分比(EXTRS, 全市场) × 100      # 0~100

本指标默认输出常用 5 个周期 —— RPS120 / RPS250 / RPS50 / RPS20 / RPS10
（对应通达信里的 EXTRS(120/250/50/20/10) 日线），统一画在下方副图。

数据来源：全市场横截面由 `data.get_rps` 统一提供（面板构建 / 复权 / 缓存见 data.py）；
本文件只声明周期、调用取数、拼装标准化 series，不直接取数。
"""
from __future__ import annotations

import pandas as pd

from .base import indicator, ParamSpec, series_line
from . import data

_DEFAULT_PERIODS = [120, 250, 50, 20, 10]


@indicator(
    id="rps",
    name="RPS 相对强度",
    category="momentum",
    panel="lower",
    params=[
        ParamSpec("periods", "choice", _DEFAULT_PERIODS, label="周期(交易日)"),
    ],
)
def rps(df: pd.DataFrame, periods=None) -> dict:
    ps = [int(p) for p in (periods or _DEFAULT_PERIODS) if int(p) > 0]
    code = data.code_of(df)
    values = data.get_rps(code, ps, list(df.index), period=data.period_of(df))
    return {"series": [series_line(f"RPS{p}", values[p]) for p in ps if p in values]}
