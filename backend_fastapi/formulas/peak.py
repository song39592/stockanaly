# -*- coding: utf-8 -*-
"""筹码峰簇占比（PEAKB）的**计算层** —— 指标与策略共用的单一来源。

位于**中立内核包** `formulas/`：它既不是指标、也不是策略、更不是筹码，
只是「一段被多处复用的纯计算」。

与 PEAK（只取最高单箱）不同，本模块把峰值两侧的**连续衰减**箱也归入同一座峰：
从最高箱向两侧累加，只要仍在「比内侧更低」地单调衰减就继续，一旦遇到回升
（已到谷底 / 进入另一座峰的上升沿）即停 —— 保证累加的只属于同一座峰，
不会把邻近次峰也吃进来。

对「峰宽」的限制用**价格带宽**（而非累计占比封顶）：以最高峰所在价位为中心，
向上下各取 `band_pct`（默认 20）% 的价格区间，区间之外的箱一律不计入。

`peak_band` 算单帧；`peak_band_series` 算整段（与每日筹码矩阵同序）。
指标侧（indicators/chip_peak_band.py）只负责把本层结果包装成副图展示；
策略侧（strategies/peak_top.py、peak_thresh.py）直接复用本层做横截面选股，
两处数值天然一致，不再手动同步两边。

**峰右沿**（本模块另一族出口）：`right_decay_end` 求「主峰右侧仍在单调递减的
最后一个分箱」—— `peak_band_series` 的右向扫描直接复用它，所以「峰右沿」这个
口径只有一处实现。「什么算单调递减」只有一个判据（`_decay_mask`）。

**价格最高的峰**（第三族出口）：`top_price_peak_price_series` 求「位置最靠上的
那座山」的**峰顶价位**（供主图叠加）。它**不看筹码高度** —— 在价格轴上找
**局部极大值**，取其中**位置最高**的那个，哪怕它比别的山矮得多。
这与「取占比最高的山」（= 峰位价 / COST）是**两个不同的点**，别混用：
一个是「位置最靠上」，一个是「最胖」。
"""
from __future__ import annotations

import numpy as np


def peak_band(row: np.ndarray, centers: np.ndarray, band_pct: float, tol: float) -> float:
    """沿峰值两侧累加「连续衰减」箱；遇回升、或价格超出峰位 ±band_pct% 即停。

    返回主峰簇占比（%）。`centers` 为各价格分箱中心（与 `row` 等长）。
    """
    band_pct = max(0.0, float(band_pct))
    tol = max(0.0, float(tol)) / 100.0            # 百分比 → 比例
    n = len(row)
    peak_i = int(np.nanargmax(row))
    peak = float(row[peak_i])
    peak_price = float(centers[peak_i])
    half = band_pct / 100.0 * abs(peak_price)     # 价格带宽（围绕峰位）
    total = peak
    # 向右：超出价格带宽或非连续衰减（回升）即停
    j = peak_i + 1
    while j < n:
        if abs(float(centers[j]) - peak_price) > half:
            break
        if row[j] >= row[j - 1] * (1.0 - tol):    # 不再衰减 → 停
            break
        total += float(row[j])
        j += 1
    # 向左：对称
    j = peak_i - 1
    while j >= 0:
        if abs(float(centers[j]) - peak_price) > half:
            break
        if row[j] >= row[j + 1] * (1.0 - tol):
            break
        total += float(row[j])
        j -= 1
    return total


def peak_band_series(pct: np.ndarray, centers: np.ndarray,
                     band_pct: float = 20.0, tol: float = 0.5) -> np.ndarray:
    """对每日筹码矩阵（行=日期、列=价格分箱，行合计 100）逐帧算主峰簇占比。

    返回长度 = 帧数的 ndarray；整行 NaN 的帧保持 NaN（缺数据日）。

    纯 numpy 向量化：用前缀和 + “首个不满足索引”的扫描，**一次算完整段**，
    不再逐帧 Python 循环（原逐帧循环 ~1250 次/只，约 30ms/只）。
    """
    pct = np.asarray(pct, dtype=float)
    centers = np.asarray(centers, dtype=float)
    n, b = pct.shape
    out = np.full(n, np.nan)
    bad = np.isnan(pct).all(axis=1)
    good = ~bad
    if not good.any():
        return out

    gi = np.where(good)[0]                              # 只在（非全 NaN）行上算
    P = pct[gi]
    # NaN 箱当 0 仅用于求和；是否「可延续」由 valid 控制（NaN 箱会打断累加）。
    valid = ~np.isnan(P)
    rows = np.where(valid, P, 0.0)
    band = max(0.0, float(band_pct)) / 100.0
    t = max(0.0, float(tol)) / 100.0

    peak_i = np.nanargmax(P, axis=1)                   # 每帧峰位箱 (m,)
    peak_price = centers[peak_i]                       # (m,)
    half = band * np.abs(peak_price)                   # (m,) 价格带宽

    # 价格带掩码（随票不同）：|centers[j] - 峰位价| <= 带宽
    in_band = np.abs(centers[None, :] - peak_price[:, None]) <= half[:, None]   # (m,b)

    csum = np.cumsum(rows, axis=1)                     # 前缀和
    csum2 = np.concatenate([np.zeros((len(gi), 1)), csum], axis=1)  # csum2[:, j+1] = csum[:, j]

    # ---- 右侧：peak_i+1 起，要求 in_band 且仍在单调递减，首个不满足即停 ----
    # 扫描内核与「右沿价位」共用（right_decay_end）→ 峰右沿口径单一来源。
    end_r = right_decay_end(P, tol=tol, peak_i=peak_i, extra_ok=in_band)
    e = np.where(end_r >= 0, end_r, peak_i)            # 无右沿 → 右侧累加 0
    right_sum = csum[np.arange(len(gi)), e] - csum[np.arange(len(gi)), peak_i]

    # ---- 左侧：peak_i-1 向下，要求 in_band 且 row[j] < row[j+1]*(1-t)，首个不满足即停 ----
    not_ok_l = np.ones((len(gi), b), dtype=bool)
    not_ok_l[:, :-1] = ~(valid[:, :-1]
                         & (rows[:, :-1] < rows[:, 1:] * (1.0 - t))
                         & in_band[:, :-1])
    idx_l = np.where(not_ok_l, np.arange(b), -1)
    prev_false = np.maximum.accumulate(idx_l, axis=1)  # 最后一个 <=j 的 not_ok 索引（无则 -1）
    pi0 = peak_i - 1
    col_l = np.clip(pi0, 0, b - 1)                     # 防 -1 越界取数（pi0<0 时结果会被覆盖）
    m = prev_false[np.arange(len(gi)), col_l]
    m = np.where(pi0 >= 0, m, -1)
    left_sum = (csum[np.arange(len(gi)), np.clip(pi0, 0, b - 1)]
                - csum2[np.arange(len(gi)), np.clip(m, -1, b - 1) + 1])
    left_sum[pi0 < 0] = 0.0                            # 峰值在首箱 → 无左侧

    total = rows[np.arange(len(gi)), peak_i] + right_sum + left_sum
    out[gi] = total
    return out


# --------------------------------------------------------------------------- #
# 峰右沿：从主峰向高价侧扫描，单调递减「结束」的那个分箱
# --------------------------------------------------------------------------- #
def _decay_mask(rows: np.ndarray, tol: float) -> np.ndarray:
    """「仍在单调递减」的判据 —— **本模块唯一来源**。

    返回形状 (帧数, 分箱数-1) 的布尔矩阵：`out[:, j]` 为 True 表示
    第 j+1 箱相对第 j 箱**仍在衰减**（`cur < prev * (1 - tol/100)`）。
    NaN 箱在调用侧另行排除（会打断扫描）。

    峰簇占比（`peak_band_series`）与右沿价位（`right_edge_price_series`）
    都以本判据为准 —— 容差语义只此一处。
    """
    t = max(0.0, float(tol)) / 100.0
    rows = np.asarray(rows, dtype=float)
    return rows[:, 1:] < rows[:, :-1] * (1.0 - t)


def right_decay_end(pct: np.ndarray, tol: float = 0.5,
                    peak_i=None, extra_ok: np.ndarray | None = None) -> np.ndarray:
    """逐帧求「主峰右侧仍在单调递减的**最后一个**分箱索引」（无右沿 = -1）。

    从主峰箱向**高价侧**扫描：只要下一箱仍满足「单调递减」（`_decay_mask`）就继续，
    一旦不再满足即停 —— **最后那个仍在递减的箱**就是右沿的终点。

    参数
    ----
    pct : (帧数, 分箱数) 的占比矩阵（行=日期、列=价格箱，每行合计 100）
    tol : 衰减容差（%），语义见 `_decay_mask`
    peak_i : 每帧的峰位箱索引；None = 逐帧取 argmax
    extra_ok : (帧数, 分箱数) 布尔掩码，额外的「可延续」条件
               （如「仍在价格带宽内」）；None = 不额外限制

    返回：长度 = 帧数的 int 数组；-1 表示该帧**没有右沿**
          （峰后第一个箱就不递减，或峰已在最右箱）。
    """
    pct = np.asarray(pct, dtype=float)
    if pct.ndim != 2:
        raise ValueError("pct 必须是二维 (帧数, 分箱数)")
    n, b = pct.shape
    out = np.full(n, -1, dtype=int)
    if n == 0 or b == 0:
        return out
    good = ~np.isnan(pct).all(axis=1)
    gi = np.where(good)[0]
    if gi.size == 0:
        return out
    P = pct[gi]
    valid = ~np.isnan(P)
    rows = np.where(valid, P, 0.0)
    if peak_i is None:
        pi = np.nanargmax(P, axis=1)
    else:
        pi = np.asarray(peak_i, dtype=int)[gi]
    cont = _decay_mask(rows, tol) & valid[:, 1:]
    if extra_ok is not None:
        cont = cont & np.asarray(extra_ok, dtype=bool)[gi][:, 1:]
    m = len(gi)
    not_ok = np.ones((m, b), dtype=bool)
    not_ok[:, 1:] = ~cont
    idx = np.where(not_ok, np.arange(b), b)
    nxt = np.minimum.accumulate(idx[:, ::-1], axis=1)[:, ::-1]   # 首个 >= j 的 not_ok 索引
    pi1 = pi + 1
    col = np.where(pi1 < b, pi1, 0)          # 防越界取数（pi1==b 的行结果会被覆盖）
    k = nxt[np.arange(m), col]
    k = np.where(pi1 < b, k, b)              # 一路减到最右箱 → k = b
    end = k - 1
    end = np.where(end >= pi1, end, -1)      # 峰后无递减段 → 无右沿
    out[gi] = end
    return out


def top_price_peak_indices(pct: np.ndarray, min_pct: float = 0.0,
                            min_ratio: float = 0.1) -> np.ndarray:
    """逐帧求「**位置最靠上**的那座峰」的峰顶分箱索引（没有则 -1）。

    判据是「**位置**」而不是「胖瘦」：在价格轴上找**局部极大值**
    （占比不低于左邻、且高于右邻），取其中**价格最高**的那一个 ——
    哪怕它比别的山矮得多（这正是与「峰位价」的区别：峰位价取的是最**胖**的山）。
    等价说法：从最高价往低价走，遇到的**第一座山**的峰顶。

    ⚠️ **「不看胖瘦」不等于「不看噪声」**：筹码分布的高价端常挂着**极细的残留
    尖刺**（占比 0.0000x%，来自早被换手稀释掉的历史持仓）。只看位置会把它们
    选成「位置最高的峰」→ 画出来的价位**远高于当日真实的筹码密集区**
    （实测 601595 / 2026-07-14：选到 37.81，而那天收盘才 15.83）。

    故加**相对高度门槛** `min_ratio`：该峰占比须 ≥ 本帧**主峰**占比的
    `min_ratio` 倍（默认 10%）才算一座「山」。主峰自身恒满足门槛，候选不会为空。
    `min_pct`（%）是另一道**绝对**下限；两道门槛取更严的。
    两道门槛都只用来把噪声挡在外面，**不参与「挑哪座山」**（那只看位置）。

    `min_ratio` 给 0 = 退回「完全不看高度」（会选到残留尖刺，仅供对照）。
    """
    pct = np.asarray(pct, dtype=float)
    if pct.ndim != 2:
        raise ValueError("pct 必须是二维 (帧数, 分箱数)")
    n, b = pct.shape
    out = np.full(n, -1, dtype=int)
    if n == 0 or b < 3:
        return out
    good = ~np.isnan(pct).all(axis=1)
    gi = np.where(good)[0]
    if gi.size == 0:
        return out
    P = pct[gi]
    valid = ~np.isnan(P)
    mid = P[:, 1:-1]
    # NaN 参与比较恒为 False —— 无需额外掩码，NaN 邻居自然不会造出「峰」。
    is_peak = (mid >= P[:, :-2]) & (mid > P[:, 2:]) & valid[:, 1:-1]
    # 相对门槛：本帧主峰占比 × min_ratio（主峰自身恒满足 → 候选不会为空）
    peak_max = np.nanmax(P, axis=1)
    is_peak &= mid >= peak_max[:, None] * max(0.0, float(min_ratio))
    is_peak &= mid > float(min_pct)
    rev = is_peak[:, ::-1]
    has = rev.any(axis=1)
    first = np.argmax(rev, axis=1)          # 反向的第一个 True = 最靠右的峰
    out[gi] = np.where(has, (b - 2) - first, -1)
    return out


def top_price_peak_price_series(pct: np.ndarray, centers: np.ndarray,
                                 min_pct: float = 0.0,
                                 min_ratio: float = 0.1) -> np.ndarray:
    """逐帧返回「**位置最靠上的**那座筹码峰」的**峰顶价位**。

    与 `peak_band_series` / 峰位价（COST）是**不同的点**：
      - 峰位价：最**胖**的那座山（占比最高）的峰顶；
      - 本函数：**位置最高**的那座山（哪怕比别的山矮）的峰顶。
    两者在同一张筹码分布图上可以是完全不同的两个价位。

    噪声门槛见 `top_price_peak_indices`（`min_ratio` 相对主峰、`min_pct` 绝对）。

    没有峰（整帧无有效占比）的帧 → NaN（不画）。
    """
    pct = np.asarray(pct, dtype=float)
    centers = np.asarray(centers, dtype=float)
    idx = top_price_peak_indices(pct, min_pct, min_ratio)
    out = np.full(pct.shape[0], np.nan)
    ok = idx >= 0
    if ok.any():
        out[ok] = centers[idx[ok]]
    return out
