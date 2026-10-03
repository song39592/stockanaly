# -*- coding: utf-8 -*-
"""回测编排层：范围 → 策略信号 → 统计。

只负责「串起来」，本身不含策略算法，也不含净值/指标算法：
    1. 读回测范围（universe，不设标的上限，「全部本地」全量跑）；
    2. 经 data.load_bars 做一次预检取数（逐标的缓存、并行读库，
       策略/统计后续复用，零额外 I/O），过滤数据不足的标的并记录 data_errors；
    3. 构造 StrategyContext 交给 base.run —— 策略内部自行拉取所需字段；
    4. 经 base.signals_to_positions 把扁平信号（含 code）转成「目标仓位序列」；
    5. 把收盘价交给 stats.compute 做净值与指标统计。

返回结构（前后端契约）：
  {
    "ok": True,
    "strategy": {"id","name","params"},
    "result_id": str,                          # 逐笔明细按需查询的句柄（见 trades_for）
    "scope": {"codes_count","requested","start","end",
              "initial_capital","commission","benchmark"},
    "dates": [YYYY-MM-DD, ...],
    "equity": [float|null, ...],               # 组合净值（初始资金起）
    "equity_benchmark": [float|null,...]|None, # 基准净值（若有）
    "metrics": {total_return, annual_return, max_drawdown, sharpe, win_rate,
                num_trades, num_stocks, trading_days, benchmark_return},
    "per_stock": [{"code","name","total_return","trades"}, ...],
                   # 按收益降序，全量不截断；逐笔明细经 /backtest/trades 按需取
    "data_errors": [str, ...]                              # 跳过/失败的标的说明
  }
"""
from __future__ import annotations

import time
import uuid

from . import base, data, stats

_MIN_BARS = 30        # 少于此根数视为数据不足

# 最近一次回测的上下文（单槽）：per_stock 只带基础列，逐笔明细由前端
# 按 result_id 展开时经 trades_for 现算，避免全量明细 JSON 打到 10MB 级。
_BT_CTX: dict | None = None

# ---- 代码→名称映射（进程内缓存 24h）----
_NAME_TTL = 24 * 3600
_name_cache: dict = {"at": 0.0, "map": {}}


def _load_names(codes: list[str]) -> dict[str, str]:
    """批量取股票名称：akshare 全市场表一次拉齐，失败静默降级为空映射
    （名称缺失时前端显示「—」，不影响回测本身）。"""
    now = time.time()
    if not _name_cache["map"] or now - _name_cache["at"] > _NAME_TTL:
        try:
            import akshare as ak
            from market_service import _ak          # 统一的超时/降级封装
            df = _ak(ak.stock_info_a_code_name, timeout=30)
            if df is not None and not getattr(df, "empty", True):
                _name_cache["map"] = {
                    str(r["code"]).zfill(6): str(r["name"])
                    for _, r in df.iterrows()
                }
                _name_cache["at"] = now
        except Exception:                        # noqa: BLE001 - 名称拿不到就算了
            pass
    m = _name_cache["map"]
    return {c: m.get(c) or m.get(c.zfill(6), "") for c in codes}


def run_backtest(strategy_id: str, params: dict | None, codes: list[str],
                 start: str | None, end: str | None,
                 initial_capital: float = 100000.0, commission: float = 0.0003,
                 benchmark: str | None = None) -> dict:
    """执行回测，返回标准化结果字典。异常由路由层转成错误响应。

    不设标的上限：「全部本地」（5000+ 只）全量跑——数据层 load_bars
    已并行读库，冷缓存全量也在可等待的量级。
    """
    codes = [str(c).strip() for c in (codes or []) if str(c).strip()]
    if not codes:
        raise ValueError("股票池为空，请先选择回测范围")

    requested = len(codes)

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

    # 名称补齐（只做展示口径，不影响统计）。逐笔明细**不**随结果下发：
    # 全量 5000+ 只的明细 JSON 会到 10MB 级，改为前端点击展开时按
    # result_id 调 /backtest/trades 现算（上下文缓存在 _BT_CTX，见 trades_for）。
    per_stock = core["per_stock"]
    names = _load_names([x["code"] for x in per_stock])
    for item in per_stock:
        item["name"] = names.get(item["code"]) or None

    global _BT_CTX
    rid = uuid.uuid4().hex[:12]
    _BT_CTX = {"rid": rid, "positions": positions, "close": close_by_code,
               "start": start, "end": end, "names": names}

    return {
        "ok": True,
        "strategy": {
            "id": strategy_id,
            "name": base.REGISTRY[strategy_id].name,
            "params": params or {},
        },
        "result_id": rid,
        "scope": {
            "codes_count": len(usable),
            "requested": requested,
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
        "per_stock": per_stock,
        "data_errors": errors[:20],
    }


def trades_for(rid: str, code: str) -> list[dict]:
    """按 result_id 取某只的逐笔交易明细（前端展开时按需调用）。

    上下文是**单槽缓存**（只留最近一次回测，约几十 MB）；过期/不匹配直接报错，
    让前端提示重跑。价格用原始收盘（未复权），收益按后复权口径。
    """
    if not _BT_CTX or _BT_CTX["rid"] != rid:
        raise ValueError("回测结果已过期，请重新运行回测")
    pos = _BT_CTX["positions"].get(code)
    close = _BT_CTX["close"].get(code)
    if pos is None or close is None:
        return []
    raw = data.load_bars([code], _BT_CTX["start"], _BT_CTX["end"],
                         fields=["close"], adjust="raw")
    r = raw.get(code)
    return stats.trade_detail(pos, close, r["close"] if r is not None else None)
