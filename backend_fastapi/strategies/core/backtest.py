# -*- coding: utf-8 -*-
"""回测编排层：范围 → 策略信号 → 统计。

只负责「串起来」，本身不含策略算法，也不含净值/指标算法：
    1. 读回测范围（universe + 抽样上限）；
    2. 经 data.load_bars 做一次预检取数（逐标的缓存，策略/统计后续复用，零额外 I/O），
       过滤数据不足的标的并记录 data_errors；
    3. 构造 StrategyContext 交给 base.run —— 策略内部自行拉取所需字段；
    4. 经 base.signals_to_positions 把扁平信号（含 code）转成「目标仓位序列」；
    5. 把收盘价交给 stats.compute 做净值与指标统计。

返回结构（前后端契约）：
  {
    "ok": True,
    "strategy": {"id","name","params"},
    "scope": {"codes_count","requested","sampled","start","end",
              "initial_capital","commission","benchmark"},
    "dates": [YYYY-MM-DD, ...],
    "equity": [float|null, ...],               # 组合净值（初始资金起）
    "equity_benchmark": [float|null,...]|None, # 基准净值（若有）
    "metrics": {total_return, annual_return, max_drawdown, sharpe, win_rate,
                num_trades, num_stocks, trading_days, benchmark_return},
    "per_stock": [{"code","total_return","trades"}, ...],  # 按收益降序，最多 50
    "data_errors": [str, ...]                              # 跳过/失败的标的说明
  }
"""
from __future__ import annotations

import random

from . import base, data, stats

_MAX_UNIVERSE = 800   # 单次回测标的上限（防止误点「全部」时长时间阻塞）
_MIN_BARS = 30        # 少于此根数视为数据不足


def run_backtest(strategy_id: str, params: dict | None, codes: list[str],
                 start: str | None, end: str | None,
                 initial_capital: float = 100000.0, commission: float = 0.0003,
                 benchmark: str | None = None, allow_sample: bool = False) -> dict:
    """执行回测，返回标准化结果字典。异常由路由层转成错误响应。

    allow_sample=True 时，若股票池超过上限则随机抽样到上限（用于「全部本地」），
    并在 scope.sampled 标记；否则超限直接报错。
    """
    codes = [str(c).strip() for c in (codes or []) if str(c).strip()]
    if not codes:
        raise ValueError("股票池为空，请先选择回测范围")

    requested = len(codes)
    sampled = False
    if requested > _MAX_UNIVERSE:
        if not allow_sample:
            raise ValueError(f"股票池过大（{requested} 只，上限 {_MAX_UNIVERSE}），请缩小范围")
        codes = random.Random(42).sample(codes, _MAX_UNIVERSE)
        sampled = True

    # 预检取数：逐标的缓存底层全量 OHLCV，策略/统计后续调用均命中缓存、零额外 I/O。
    bars = data.load_bars(codes, start, end, fields=["close"], adjust="hfq")

    usable: dict[str, object] = {}
    errors: list[str] = []
    for code in codes:
        df = bars.get(code)
        if df is None:
            errors.append(f"{code}: 取数失败或无行情")
        elif len(df) < _MIN_BARS:
            errors.append(f"{code}: 数据不足（<{_MIN_BARS} 根）")
        else:
            usable[code] = df

    if not usable:
        raise RuntimeError("所选范围内没有可用数据，无法回测")

    # 策略自行拉取所需字段（inputs），这里只把范围交出去。
    ctx = base.StrategyContext(codes=list(usable.keys()), start=start, end=end, adjust="hfq")
    signals = base.run(strategy_id, ctx, params)

    close_by_code = {c: usable[c]["close"] for c in usable}
    positions = base.signals_to_positions(signals, close_by_code)

    bench_close = None
    if benchmark:
        try:
            bmd = data.load_bars([benchmark], start, end, fields=["close"], adjust="hfq")
            if benchmark in bmd and len(bmd[benchmark]) >= 2:
                bench_close = bmd[benchmark]["close"]
        except Exception as exc:
            errors.append(f"基准 {benchmark}: {exc}")

    core = stats.compute(positions, close_by_code, initial_capital=initial_capital,
                         commission=commission, benchmark_close=bench_close)

    return {
        "ok": True,
        "strategy": {
            "id": strategy_id,
            "name": base.REGISTRY[strategy_id].name,
            "params": params or {},
        },
        "scope": {
            "codes_count": len(usable),
            "requested": requested,
            "sampled": sampled,
            "start": start,
            "end": end,
            "initial_capital": initial_capital,
            "commission": commission,
            "benchmark": benchmark,
        },
        "dates": core["dates"],
        "equity": core["equity"],
        "equity_benchmark": core["equity_benchmark"],
        "metrics": core["metrics"],
        "per_stock": core["per_stock"][:50],
        "data_errors": errors[:20],
    }
