# -*- coding: utf-8 -*-
"""灰色峰簇占比（GRAYP）· 最大选股策略。

逻辑：每 `rebalance` 个交易日，在回测池内按**当日 GRAYP（灰色峰簇占比）**降序排名，
取前 `top_n` 只等权持有，跌出榜单即移出。

GRAYP = EMLK(ema_lock) 三态模型中**灰色（锁仓沉淀）分布**的主峰簇占比（%）：
灰色为「锁仓日把蓝底按 R 抽离的沉淀筹码」，GRAYP 越大说明筹码越向锁仓沉淀主峰集中
（高位锁仓→潜在派发压力；低位锁仓→潜在拉升筹码，策略只负责选股，方向由你解读）。
与下方副图 **GRAYP 指标同一口径**（formula=ema_lock，含峰宽 band_pct / 衰减容差 tol；
锁仓参数 R / M_low 可调）。

**因果性**：ema_lock 的筹码矩阵第 i 帧只由第 0..i 天推出，可逐日横截面排序，无未来函数。
取数区间在 start 前再往前多取约 1.5×window 个自然日养状态（不参与选股）。
"""
from __future__ import annotations

import datetime as dt
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

import chipformulas as chip_formulas
from chipformulas.core.peak import peak_band_series
from .core.base import strategy, ParamSpec, Signal, checkpoint, Cancelled


def _gray_series(code: str, fetch_start, end, bins: int, band_pct: float = 20.0,
                 tol: float = 0.5, R: float = 0.5, M_low: float = 0.7) -> "pd.Series | None":
    """取单只票的 **GRAYP（灰色峰簇占比 %）** 时间序列。

    EMLK(ema_lock) 三态公式输出 gray 通道（锁仓沉淀分布），用与 GRAYP 指标同一份
    peak_band_series 内核算主峰簇占比（分母=全部筹码，结果 0~100）。价格轴固定
    end=None 铺满全历史，end 仅用于裁掉回测终点之后的帧。
    """
    try:
        res = chip_formulas.compute_matrix(
            code, formula_id="ema_lock", start=fetch_start, end=None,
            adjust="qfq", bins=bins, offline=False,
            params={"R": R, "M_low": M_low})
    except Exception:                        # noqa: BLE001 - 单只失败不拖垮整批
        return None
    s = pd.Series(peak_band_series(res.gray, res.centers, band_pct, tol),
                  index=list(res.dates))
    if end:
        s = s[s.index <= end]
    return s


@strategy(
    id="gray_peak_top",
    name="灰色峰簇占比(GRAYP) · 最大选股",
    category="筹码",
    description="每 N 个交易日在池内按 GRAYP（锁仓灰色峰簇占比 %）降序排名，取前 K 只等权持有，"
                "跌出榜单即移出；GRAYP 越大筹码越向锁仓沉淀主峰集中。\n"
                "GRAYP 与下方副图 GRAYP 指标同口径（formula=ema_lock，含峰宽 band_pct / 衰减容差 tol；"
                "锁仓参数 R / M_low 可调）。",
    inputs=["close"],
    outputs="目标权重向量(组合占比)",
    params=[
        ParamSpec("top_n", "int", 1, min=1, max=200, label="持仓数量(N)"),
        ParamSpec("rebalance", "int", 1, min=1, max=60, label="调仓间隔(日)"),
        ParamSpec("window", "int", 0, min=0, max=1000, label="筹码窗口(0=全历史)"),
        ParamSpec("bins", "int", 80, min=20, max=200, label="价格分箱数"),
        ParamSpec("band_pct", "float", 20.0, min=1.0, max=100.0, label="峰宽(价格±%)"),
        ParamSpec("tol", "float", 0.5, min=0.0, max=5.0, label="衰减容差%"),
        ParamSpec("R", "float", 0.5, min=0.05, max=1.0, label="每次染灰比例R"),
        ParamSpec("M_low", "float", 0.7, min=0.1, max=3.0, label="锁仓倍量阈值M_low"),
        ParamSpec("position", "float", 1.0, min=0.1, max=1.0, label="总仓位系数"),
    ],
)
def gray_peak_top(ctx, top_n: int = 1, rebalance: int = 1, window: int = 0,
                  bins: int = 80, position: float = 1.0, band_pct: float = 20.0,
                  tol: float = 0.5, R: float = 0.5, M_low: float = 0.7) -> list:
    """按 GRAYP 降序做横截面选股，返回完整目标组合的 list[Signal]。"""
    codes = [str(c).strip() for c in (ctx.codes or []) if str(c).strip()]
    if not codes:
        return []
    top_n = max(1, int(top_n))
    rebalance = max(1, int(rebalance))
    window = int(window)
    bins = max(20, int(bins))
    pos = min(1.0, max(0.0, float(position)))

    end = str(ctx.end)[:10] if ctx.end else None
    win_start = str(ctx.start)[:10] if ctx.start else None
    if window and window > 0 and win_start:
        try:
            fetch_start = (dt.date.fromisoformat(win_start)
                           - dt.timedelta(days=int(window * 1.5))).isoformat()
        except ValueError:
            fetch_start = None
    else:
        fetch_start = None                      # 全历史：compute_matrix 内部 win=n_all、warm=0

    cols: dict[str, pd.Series] = {}

    def _work(code):
        return code, _gray_series(code, fetch_start, end, bins, band_pct, tol, R, M_low)

    nworkers = max(1, min(os.cpu_count() or 4, 12))
    _ex = ThreadPoolExecutor(max_workers=nworkers)
    futs = {_ex.submit(_work, c): c for c in codes}
    try:
        for _fut in as_completed(futs):
            c = futs[_fut]
            try:
                c, s = _fut.result()
            except Exception:                        # noqa: BLE001 - 单只异常不拖垮整批
                s = None
            if s is not None:
                if win_start:
                    s = s[s.index >= win_start]     # 预热段不参与选股
                s = s.dropna()
                if not s.empty:
                    cols[c] = s
            checkpoint()                            # 暂停/停止点（未装控制器时为 no-op）
    except Cancelled:                               # 用户停止：取消尚未开始的票
        for _f in futs:
            _f.cancel()
        raise
    finally:
        _ex.shutdown(wait=True)
    if not cols:
        return []

    panel = pd.DataFrame(cols).sort_index()
    dates = list(panel.index)
    signals: list[Signal] = []
    held: set[str] = set()              # ★ 自己维护持仓集合

    for k in range(0, len(dates), rebalance):
        day = dates[k]
        row = panel.loc[day].dropna()
        if row.empty:
            continue
        ranked = list(row.sort_values(ascending=False).index[:min(top_n, len(row))])
        picks = set(ranked)
        day_s = str(day)[:10]

        for code in sorted(held - picks):
            signals.append(Signal(code=code, time=day_s, weight=0.0,
                                  reason=f"跌出灰峰簇前{top_n}",
                                  meta={"dropped": 1, "formula": "ema_lock"}))
        w = (pos / len(picks)) if picks else 0.0
        for code in sorted(picks):
            signals.append(Signal(
                code=code, time=day_s, weight=w,
                reason=f"灰色峰簇占比(GRAYP) 排名前{top_n}",
                meta={"grayp": round(float(row[code]), 2), "rank": ranked.index(code) + 1,
                      "formula": "ema_lock"},
            ))
        held = picks
    return signals
