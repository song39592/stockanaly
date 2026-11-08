# -*- coding: utf-8 -*-
"""筹码博弈指标（下方副图）：当日「开盘 vs 收盘」的获利盘比例变化。

通达信原式：
    KL:100*(WINNER(CLOSE)-WINNER(OPEN));
    博弈H:80;
    博弈L:-80;

读法：`WINNER(价)` = 筹码分布里「成本低于该价」的占比（获利盘比例）。
于是 KL 看的是**同一天内**，以开盘价和收盘价两个分界切出来的获利盘比例差：

  * KL > 0：收盘时的获利盘比开盘时多 -> 当日**买盘占优**（筹码整体上移）
  * KL < 0：收盘时的获利盘比开盘时更少 -> 当日**卖压占优**
  * |KL| 越过 +-80：**博弈剧烈**（多空分歧大 / 方向选择强烈）

⚠️ **量纲（别改错，这里踩过坑）**：通达信的 `WINNER` 返回 **0~1 的小数**，
所以原式 `KL = 100*(WINNER(C)-WINNER(O))` 的量程是 **±100**，
阈值 +-80 落在量程 80% 的位置 = 「接近极端」才有意义。
而本项目的筹码矩阵每行合计 100（**百分数**），直接拿它当小数用会把 KL
放大 100 倍（实测能到几千）—— 阈值线会被压到 0 附近，整张图没法看。
所以这里取 `winner_fraction`（通达信的小数口径），不是 `winner_series`。

换句话说：**KL 的单位是「获利盘比例变动了多少个百分点」**——
获利盘比例当天从 40% 变到 52%，KL 就是 +12。

口径要点：
  * 筹码矩阵与价格**同复权口径**（都是前复权 qfq），由数据层保证；
  * `WINNER` 的分界口径（严格小于）与指标 `WIN`（筹码获利比例）**完全一致** ——
    两者都走 `formulas/winner.py` 同一份内核，所以「博弈强度」与「获利盘」对得上号。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .base import ParamSpec, indicator, series_line
from .data import chip_rows_of, code_of
from formulas.winner import winner_fraction

# 通达信原式里写死的两条阈值线
BOJIE_HIGH = 80.0
BOJIE_LOW = -80.0


@indicator(
    id="KL",
    name="筹码博弈",
    category="chip",
    panel="lower",                   # 相对量纲（无固定上下界），独立纵轴
    params=[
        ParamSpec("formula", "choice", "tri_decay", label="筹码公式"),
    ],
    # 序列样式（颜色 / 线宽）：随计算结果一起下发，前端据此渲染 ——
    # 新增指标不必再改前端那套按序列名 switch 的配色表。
    styles={
        "KL": {"color": "#9085E9", "width": 1.4},      # 紫：博弈值主曲线
        "博弈H": {"color": "#969EAC", "width": 1.0},    # 灰：阈值线（同极弱/强势的画法）
        "博弈L": {"color": "#969EAC", "width": 1.0},
    },
)
def chip_kl(df: pd.DataFrame, *, ctx=None, formula: str = "tri_decay") -> dict:
    rows, frames = chip_rows_of(df, formula_id=formula, code=code_of(ctx))
    centers = frames.centers
    close = df["close"].astype(float).to_numpy()
    open_ = df["open"].astype(float).to_numpy()
    # 两个分界各算一次获利盘比例（通达信口径：小数 0~1），再按原式取差乘 100
    w_close = winner_fraction(rows, centers, close)
    w_open = winner_fraction(rows, centers, open_)
    kl = 100.0 * (w_close - w_open)
    index = df.index
    return {"series": [
        series_line("KL", pd.Series(kl, index=index)),
        series_line("博弈H", pd.Series(BOJIE_HIGH, index=index)),
        series_line("博弈L", pd.Series(BOJIE_LOW, index=index)),
    ]}