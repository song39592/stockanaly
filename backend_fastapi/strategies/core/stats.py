# -*- coding: utf-8 -*-
"""统计引擎（与策略完全解耦，可独立复用/单测）。

输入：每只标的的「目标仓位序列」+ 收盘价序列（由编排层从 data 取好后传入），
输出：组合净值曲线与统计指标。本文件不引用任何策略实现，也不直接取数。

仓位口径：
    w_t        目标仓位（0..1），由信号层（base.signals_to_positions）给出；
    w_{t-1}    次日生效（当日收盘算信号，次日承担收益）；
    turnover   目标仓位变化量，按 commission 计「单边」交易成本；
    组合        各标的等权 —— 每只分到 initial_capital / N 的一条「资金带」，
               未建仓时该带为现金（收益 0），天然反映「空仓拖累」。

返回结构：
    {
      "dates": [YYYY-MM-DD, ...],
      "equity": [float|null, ...],                 # 组合净值（以 initial_capital 起）
      "equity_benchmark": [float|null,...] | None, # 基准净值（若有）
      "metrics": {total_return, annual_return, max_drawdown, sharpe, win_rate,
                  num_trades, num_stocks, trading_days, benchmark_return},
      "per_stock": [{"code","total_return","trades"}, ...]  # 按收益降序（未截断）
    }
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def _native(series: pd.Series):
    """Series → JSON 友好的 list（NaN→None，其余转 float）。"""
    out = []
    for v in series.values:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            out.append(None)
        else:
            out.append(float(v))
    return out


def compute(positions_by_code: dict, close_by_code: dict, *,
            initial_capital: float = 100000.0, commission: float = 0.0003,
            benchmark_close: pd.Series | None = None) -> dict:
    """按目标仓位序列 + 收盘价序列，统计组合净值与指标。

    参数：
      positions_by_code  {code: pd.Series(目标仓位, 索引 YYYY-MM-DD)}
      close_by_code      {code: pd.Series(收盘价, 索引 YYYY-MM-DD)}
      initial_capital    初始资金
      commission         单边佣金比例（如 0.0003 = 万三）
      benchmark_close    基准收盘价序列（可选，用于对比净值）
    """
    srets: dict[str, pd.Series] = {}
    per_stock: list[dict] = []
    total_trades = 0

    for code, pos in positions_by_code.items():
        close = close_by_code.get(code)
        if close is None or len(close) < 2:
            continue
        ret = close.pct_change().fillna(0.0)
        w = pos.reindex(close.index).fillna(0.0).astype(float)
        w_prev = w.shift(1).fillna(0.0)                       # 次日生效
        turnover = w_prev.diff().abs().fillna(w_prev.abs())   # 建/平仓换手
        sret = w_prev * ret - turnover * commission
        trades = int((turnover > 1e-12).sum())
        srets[code] = sret
        total_trades += trades
        eq = (1.0 + sret).cumprod()
        per_stock.append({
            "code": code,
            "total_return": float(eq.iloc[-1] - 1.0),
            "trades": trades,
        })

    if not srets:
        raise RuntimeError("没有可统计的标的")

    # 对齐到共同交易日（取交集），保证组合口径一致。
    common = None
    for s in srets.values():
        common = s.index if common is None else common.intersection(s.index)
    common = common.sort_values()
    if len(common) < 2:
        raise RuntimeError("对齐后的共同交易日不足，无法统计")

    n = len(srets)
    port = pd.Series(0.0, index=common)
    for s in srets.values():
        port = port + s.reindex(common).fillna(0.0)
    port = port / n

    equity = initial_capital * (1.0 + port).cumprod()

    # 基准净值（买入持有）
    equity_bench = None
    bench_ret = None
    if benchmark_close is not None and len(benchmark_close) >= 2:
        bret = benchmark_close.pct_change().fillna(0.0).reindex(common).fillna(0.0)
        if len(bret):
            equity_bench = initial_capital * (1.0 + bret).cumprod()
            bench_ret = float(equity_bench.iloc[-1] / equity_bench.iloc[0] - 1.0)

    eq_vals = equity.values.astype(float)
    n_days = len(equity)
    total_return = float(equity.iloc[-1] / initial_capital - 1.0)
    ann = (equity.iloc[-1] / initial_capital) ** (252.0 / n_days) - 1.0 if n_days > 1 else 0.0
    running_max = np.maximum.accumulate(eq_vals)
    max_dd = float((eq_vals / running_max - 1.0).min())
    daily = port.values.astype(float)
    std = float(np.std(daily)) if len(daily) > 1 else 0.0
    mean = float(np.mean(daily)) if len(daily) else 0.0
    sharpe = float(mean / std * math.sqrt(252)) if std > 0 else 0.0
    win_rate = float(np.mean(daily > 0)) if len(daily) else 0.0

    metrics = {
        "total_return": total_return,
        "annual_return": ann,
        "max_drawdown": max_dd,
        "sharpe": sharpe,
        "win_rate": win_rate,
        "num_trades": total_trades,
        "num_stocks": n,
        "trading_days": n_days,
        "benchmark_return": bench_ret,
    }

    per_stock.sort(key=lambda x: x["total_return"], reverse=True)

    return {
        "dates": [str(d)[:10] for d in common],
        "equity": _native(equity),
        "equity_benchmark": _native(equity_bench) if equity_bench is not None else None,
        "metrics": metrics,
        "per_stock": per_stock,
    }
