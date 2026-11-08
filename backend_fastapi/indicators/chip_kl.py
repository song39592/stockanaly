# -*- coding: utf-8 -*-
"""筹码博弈指标（下方副图）：当日「开盘 vs 收盘」的获利盘比例变化。

通达信原式：
    KL:100*(WINNER(CLOSE)-WINNER(OPEN));
    博弈H:80;
    博弈L:-80;

读法：`WINNER(价)` = 筹码分布里「成本低于该价」的占比（获利盘比例，0~100%）。
于是 KL 看的是**同一天内**，以开盘价和收盘价两个分界切出来的获利盘比例差：

  * KL > 0：收盘时的获利盘比开盘时更多 -> 当日**买盘占优**（筹码整体上移）
  * KL < 0：收盘时的获利盘比开盘时更少 -> 当日**卖压占优**
  * |KL| 越过 +-80：**博弈剧烈**（多空分歧大 / 方向选择强烈）

⚠️ **量纲别改错**：WINNER 返回 0~100 的**百分比**，故 `WINNER(C)-WINNER(O)` 的单位是
**百分点**；原式再乘 100，所以「日内获利盘比例变动 1 个百分点」= KL 100，
阈值 +-80 对应的是 **+-0.8 个百分点**的日内变动。
（若把乘 100 去掉，KL 会缩到 +-1 量级，+-80 的阈值线永远碰不到 —— 那是把原式写错了。）

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
from formulas.winner import winner_series

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
    # 两个分界各算一次获利盘比例，再取差（通达信 WINNER(C) - WINNER(O)）
    w_close = winner_series(rows, centers, close)
    w_open = winner_series(rows, centers, open_)
    kl = 100.0 * (w_close - w_open)
    index = df.index
    return {"series": [
        series_line("KL", pd.Series(kl, index=index)),
        series_line("博弈H", pd.Series(BOJIE_HIGH, index=index)),
        series_line("博弈L", pd.Series(BOJIE_LOW, index=index)),
    ]}