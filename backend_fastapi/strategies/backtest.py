# -*- coding: utf-8 -*-
"""策略回测引擎。

给定策略 id / 参数 / 股票池 / 时间区间 / 初始资金 / 佣金 / 基准，对股票池中每只标的：
  1. 经 strategies.run 得到每日目标仓位（0/1）；
  2. 以「次日开盘生效」近似（用当日收盘算信号、次日收益计入），扣减换手佣金；
  3. 等权合成组合每日收益，得到组合净值曲线；
  4. 计算总收益 / 年化 / 最大回撤 / 夏普 / 胜率 / 交易次数等指标；
  5. 可选对比基准（买入持有）净值。

返回结构（前后端契约）：
  {
    "ok": True,
    "strategy": {"id","name","params"},
    "scope": {"codes_count","start","end","initial_capital","commission","benchmark"},
    "dates": [YYYY-MM-DD, ...],
    "equity": [float|null, ...],              # 组合净值（初始资金起）
    "equity_benchmark": [float|null,...]|None,# 基准净值（若有）
    "metrics": {total_return, annual_return, max_drawdown, sharpe, win_rate,
                num_trades, num_stocks, trading_days, benchmark_return},
    "per_stock": [{"code","total_return","trades"}, ...],   # 按收益降序，最多 50
    "data_errors": [str, ...]                              # 跳过/失败的标的说明
  }
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from . import data, base

_MAX_UNIVERSE = 800
_MIN_BARS = 30


def _to_native(series: pd.Series):
    """把 Series 转成 JSON 友好的 list（NaN→None，其余转 float）。"""
    out = []
    for v in series.values:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            out.append(None)
        else:
            out.append(float(v))
    return out


def run_backtest(strategy_id: str, params: dict | None, codes: list[str],
                 start: str | None, end: str | None,
                 initial_capital: float = 100000.0, commission: float = 0.0003,
                 benchmark: str | None = None) -> dict:
    """执行回测，返回标准化结果字典。异常由路由层转成错误响应。"""
    if base.REGISTRY.get(strategy_id) is None:
        raise KeyError(f"未知策略: {strategy_id}")
    codes = [str(c).strip() for c in (codes or []) if str(c).strip()]
    if not codes:
        raise ValueError("股票池为空，请先选择回测范围")
    if len(codes) > _MAX_UNIVERSE:
        raise ValueError(f"股票池过大（{len(codes)} 只，上限 {_MAX_UNIVERSE}），请缩小范围")

    stock_returns: list[tuple[str, pd.Series, int]] = []
    errors: list[str] = []

    for code in codes:
        try:
            df = data.get_ohlcv(code, start, end, adjust="hfq")
        except Exception as exc:
            errors.append(f"{code}: 取数失败（{exc}）")
            continue
        if df is None or len(df) < _MIN_BARS:
            errors.append(f"{code}: 数据不足（<{_MIN_BARS} 根）")
            continue
        try:
            pos = base.run(strategy_id, params, df)
        except Exception as exc:
            errors.append(f"{code}: 策略计算失败（{exc}）")
            continue

        ret = df["close"].pct_change().fillna(0.0)
        pos_next = pos.shift(1).fillna(0.0)          # 信号当日收盘算，次日生效
        turnover = pos_next.diff().abs().fillna(pos_next.abs())
        sret = pos_next * ret - turnover * commission
        trades = int((turnover > 0).sum())
        stock_returns.append((code, sret, trades))

    if not stock_returns:
        raise RuntimeError("所选范围内没有可用数据，无法回测")

    # 对齐到共同交易日（取交集），保证组合收益口径一致。
    common = stock_returns[0][1].index
    for _, sret, _ in stock_returns[1:]:
        common = common.intersection(sret.index)
    common = common.sort_values()
    if len(common) < 2:
        raise RuntimeError("对齐后的共同交易日不足，无法回测")

    n = len(stock_returns)
    port = pd.Series(0.0, index=common)
    per_stock: list[dict] = []
    total_trades = 0
    for code, sret, trades in stock_returns:
        aligned = sret.reindex(common).fillna(0.0)
        port = port + aligned
        total_trades += trades
        eq = (1.0 + aligned).cumprod()
        per_stock.append({
            "code": code,
            "total_return": float(eq.iloc[-1] - 1.0),
            "trades": trades,
        })
    port = port / n

    equity = initial_capital * (1.0 + port).cumprod()

    # 基准（买入持有）净值
    equity_bench = None
    bench_ret = None
    if benchmark:
        try:
            bdf = data.get_ohlcv(benchmark, start, end, adjust="hfq")
            if bdf is not None and len(bdf) >= 2:
                bret = bdf["close"].pct_change().fillna(0.0).reindex(common).fillna(0.0)
                equity_bench = initial_capital * (1.0 + bret).cumprod()
                bench_ret = float(equity_bench.iloc[-1] / equity_bench.iloc[0] - 1.0)
        except Exception as exc:
            errors.append(f"基准 {benchmark}: {exc}")

    eq_vals = equity.values.astype(float)
    total_return = float(equity.iloc[-1] / initial_capital - 1.0)
    n_days = len(equity)
    ann = (equity.iloc[-1] / initial_capital) ** (252.0 / n_days) - 1.0 if n_days > 1 else 0.0
    running_max = np.maximum.accumulate(eq_vals)
    drawdown = eq_vals / running_max - 1.0
    max_dd = float(drawdown.min())
    daily = port.values.astype(float)
    std = float(np.std(daily)) if len(daily) > 1 else 0.0
    mean = float(np.mean(daily))
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
        "ok": True,
        "strategy": {
            "id": strategy_id,
            "name": base.REGISTRY[strategy_id].name,
            "params": params or {},
        },
        "scope": {
            "codes_count": n,
            "start": start,
            "end": end,
            "initial_capital": initial_capital,
            "commission": commission,
            "benchmark": benchmark,
        },
        "dates": [str(d)[:10] for d in common],
        "equity": _to_native(equity),
        "equity_benchmark": _to_native(equity_bench) if equity_bench is not None else None,
        "metrics": metrics,
        "per_stock": per_stock[:50],
        "data_errors": errors[:20],
    }
