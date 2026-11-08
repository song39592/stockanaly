# -*- coding: utf-8 -*-
"""价量偏离度（**副图**，对应通达信 JWZZ / JW_M）。

原始公式：
    X_3    := 0.01 * EMA(AMOUNT, 13) / EMA(VOL, 13);
    JWZZ   := (CLOSE - X_3) / X_3 * 1000;
    极弱   := (-150);
    强势   := 95;
    JW_M   := SMA(JWZZ, 13, 1), COLORYELLOW, LINETHICK1;

口径：
  * **JWZZ**：收盘价相对成本线（X_3）的偏离度，单位 ‰（千分比）。
    +1000 = 收盘价是成本线的 2 倍；-500 = 收盘价只有成本线的一半。
    读法：数值越高说明近期买入的钱越"抬轿"（股价远离平均持仓成本）。
  * **JW_M**：SMA(JWZZ, 13, 1) —— 偏离度的 13 期**递推**平滑线（黄线）。
    通达信 SMA(X, N, 1) 的第三个参数 1 = 递推权重，等价于
    ewm(alpha=1/N, adjust=False)（与本项目 RSI / KDJ 里的 1/N 平滑同口径，
    见 rsi.py / kdj.py）。它比 JWZZ 平滑，用来判断偏离是**趋势**还是**噪声**。
  * **极弱 / 强势**：两条固定阈值线（-150 / 95，‰）。跌破极弱 = 股价跌到成本线
    下方 15% 以上（套牢盘压力大）；站上强势 = 高于成本线 9.5% 以上。

与主图成本线（cost_ema.py 的 ECOST）**共用同一条成本线**
（chipformulas.core.jwzz.ema_cost_line）：两边周期参数必须一致，否则副图的偏离度
对不上主图那条线，用户会以为算错了。

⚠️ 偏离度是**相对量纲（‰）**，与价格不同量纲，故 panel="lower"（副图），
不能画在主图上与 K 线共用价格轴。

数据只通过 data.get_ohlcv 获取，本文件不直接碰存储层（见 ARCHITECTURE.md §3）。
"""
from __future__ import annotations

import pandas as pd

from .base import indicator, ParamSpec, series_line
# 计算层在 chipformulas.core.jwzz：与策略（strategies/jwzz_cross.py）共用同一份，
# 本文件只负责把结果包装成副图展示 —— 指标与策略数值天然一致。
from chipformulas.core.jwzz import DEFAULT_PERIOD, jwzz_series, sma1_series

# 原公式里的两条固定阈值（‰）：极弱 -150、强势 95
WEAK_LEVEL = -150
STRONG_LEVEL = 95


@indicator(
    id="JWZZ",
    name="价量偏离度",
    category="oscillator",
    panel="lower",                # 相对量纲（‰），不能与 K 线共用价格轴
    params=[
        ParamSpec("cost_period", "int", DEFAULT_PERIOD, min=2, max=250,
                   label="成本EMA周期"),
        ParamSpec("smooth_period", "int", DEFAULT_PERIOD, min=2, max=250,
                   label="平滑周期"),
        ParamSpec("weak_level", "int", WEAK_LEVEL, min=-2000, max=0,
                   label="极弱线"),
        ParamSpec("strong_level", "int", STRONG_LEVEL, min=0, max=2000,
                   label="强势线"),
    ],
)
def jwzz(df: pd.DataFrame, *, cost_period: int = DEFAULT_PERIOD,
         smooth_period: int = DEFAULT_PERIOD,
         weak_level: int = WEAK_LEVEL,
         strong_level: int = STRONG_LEVEL) -> dict:
    dev = jwzz_series(df, cost_period)
    smooth = sma1_series(dev, smooth_period)
    index = df.index
    return {"series": [
        series_line("JWZZ", dev),
        series_line("JW_M", smooth),
        series_line("极弱", pd.Series(float(weak_level), index=index)),
        series_line("强势", pd.Series(float(strong_level), index=index)),
    ]}
