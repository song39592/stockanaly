# -*- coding: utf-8 -*-
"""获利盘比例（通达信 `WINNER`）的**计算层** —— 指标与策略共用的单一来源。

位于**中立内核包** `formulas/`：它既不是指标、也不是策略、更不是筹码 ——
只是「一段被多处复用的纯计算」。

## WINNER 是什么

    WINNER(price) = 筹码分布中「成本低于 price」的筹码占比

也就是**获利盘比例**：拿着这只票的人里，浮盈的比例。
⚠️ **通达信的 `WINNER` 返回 0~1 的小数**，所以由它构成的差值再乘 100，
量程就是 **±100**（如指标 `KL`：博弈H/博弈L 阈值定在 ±80 = 量程的 80% 位置）。
本项目的筹码矩阵每行合计 100（百分数），**两者差 100 倍** ——
本模块因此提供两个函数（百分数 / 通达信小数口径），别拿错。

## 为什么要有这一层

「获利盘比例」这个口径至少被两处消费：
  * 指标 `WIN`（筹码获利比例）—— 按当日收盘价算；
  * 指标 `KL`（筹码博弈）—— 按当日**开盘价与收盘价**各算一次，取差。

两处必须**逐位一致**（否则「博弈强度」与「获利盘」对不上号），
所以口径抽到这里，指标侧只做展示包装。

## 取数边界（重要）

本模块只负责「**已给定的**筹码矩阵 × 价格 → 获利盘比例」，
**不负责取数**：筹码矩阵必须按代码取、且与价格**同复权口径**（都是 qfq），
由调用方（指标侧走 `indicators/data.py` 的 `chip_rows_of`）保证。
这里若自己去取数，就会出现「用 hfq 的价格切 qfq 的筹码」这类错位 —— 错位不报错，
只会让数字看起来合理而已。
"""
from __future__ import annotations

import numpy as np

# 筹码矩阵每行的合计（百分数）—— 量纲换算的锚点。
CHIP_ROW_TOTAL = 100.0


def winner_series(matrix, centers, prices) -> np.ndarray:
    """按给定价格逐行切分筹码矩阵，返回获利盘比例（**百分数**，0~100）。

    参数：
      matrix   筹码分布矩阵，形状 (帧数, 分箱数)；**每行合计 100**（%）。
                缺失日期的行是全 NaN。
      centers  各分箱的**价格中心**，长度 = 分箱数（升序）。
      prices   每帧要查询的价格，长度 = 帧数（通常是该日的 open / close）。

    返回：长度 = 帧数的 float 数组；该帧无筹码数据或价格非有限时为 NaN。

    ⚠️ **分界用严格小于**（`center < price`），与指标 `WIN` 的既有口径一致 ——
    改了会让「获利比例」的历史数值平移，不能随手改。
    """
    matrix = np.asarray(matrix, dtype=float)
    centers = np.asarray(centers, dtype=float)
    prices = np.asarray(prices, dtype=float)
    n = matrix.shape[0] if matrix.ndim == 2 else 0
    out = np.full(n, np.nan, dtype=float)
    if n == 0 or centers.size == 0:
        return out
    for i in range(n):
        row = matrix[i]
        if np.all(np.isnan(row)):
            continue                       # 该日无筹码数据 → 保持 NaN
        price = prices[i]
        if not np.isfinite(price):
            continue                       # 价格缺失 → 保持 NaN
        out[i] = float(row[centers < price].sum())
    return out


def winner_fraction(matrix, centers, prices) -> np.ndarray:
    """**通达信 `WINNER` 口径**：获利盘比例，**小数 0~1**。

    ⚠️ 这两个函数的差别**不是 100 倍的装饰**，而是量纲：
    通达信的 `WINNER(价)` 返回 0~1 的小数，所以
    `KL = 100*(WINNER(C)-WINNER(O))` 的量程是 **±100**；
    而本项目的筹码矩阵每行合计 100（百分数），直接拿它当小数用，
    会把 KL 放大 100 倍 —— 阈值线 ±80 会被压到 0 附近，整张图没法看。

    要**百分数**（0~100）用 `winner_series`；要**通达信口径**（0~1）用本函数。
    """
    return winner_series(matrix, centers, prices) / CHIP_ROW_TOTAL