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
_WARMUP_BARS = 250    # 短窗口回测的指标预热根数（约一年，够 MACD 这类长周期策略）

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


def _prepare(codes: list[str], start: str | None, end: str | None,
             adjust: str = "hfq") -> tuple[dict, list[str]]:
    """预检取数：返回 (可用标的 {code: DataFrame}, 跳过说明列表)。

    回测与推荐共用——同一份底层缓存（data.load_bars 逐标的缓存），不重复 I/O。
    """
    bars = data.load_bars(codes, start, end, fields=["close"], adjust=adjust)
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
    return usable, errors


def run_backtest(strategy_id: str, params: dict | None, codes: list[str],
                 start: str | None, end: str | None,
                 initial_capital: float = 100000.0, commission: float = 0.0003,
                 benchmark: str | None = None, window: int | None = None) -> dict:
    """执行回测，返回标准化结果字典。异常由路由层转成错误响应。

    不设标的上限：「全部本地」（5000+ 只）全量跑——数据层 load_bars
    已并行读库，冷缓存全量也在可等待的量级。

    `window=N` 时只统计**最近 N 个交易日**：取数窗口会额外向前多取 warmup 根
    （策略指标如 MACD 需要历史预热，只给 5 根根本算不出信号），但净值与指标
    只按最后 N 日统计（见 stats.compute 的 tail）。
    """
    codes = [str(c).strip() for c in (codes or []) if str(c).strip()]
    if not codes:
        raise ValueError("股票池为空，请先选择回测范围")

    requested = len(codes)
    if window and window > 0:
        days = data.recent_trading_days(window + _WARMUP_BARS)
        if len(days) >= window + 2:
            start, end = days[0], days[-1]

    usable, errors = _prepare(codes, start, end)
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
                         commission=commission, benchmark_close=bench_close, tail=window)

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


def run_recommend(strategy_id: str, params: dict | None, codes: list[str],
                  start: str | None = None, end: str | None = None,
                  initial_capital: float = 100000.0, commission: float = 0.0003,
                  lookback: int = 250) -> dict:
    """当前策略推荐：按**最新一日**的目标仓位给出买入 / 卖出 / 持股三档。

    与回测共用同一套信号与 T+1 口径，只是不统计历史净值，只回答「今天怎么做」：
      buy   上一日空仓、当日建仓；
      sell  上一日持仓、当日清仓；
      hold  当日仍在持仓（含加仓 / 减仓），附建仓成本价与浮盈亏金额。

    未指定区间时自动回看最近 `lookback` 个交易日——MACD 这类长周期策略
    只看最近几天是算不出有效信号的。

    金额口径：等权资金带 `band = initial_capital / 有效标的数`，与回测的
    组合口径一致；盈亏用**后复权**序列算（消除除权的假盈亏），
    价格展示用原始收盘（不复权，用户看得懂）。
    """
    codes = [str(c).strip() for c in (codes or []) if str(c).strip()]
    if not codes:
        raise ValueError("股票池为空，请先选择回测范围")

    if not start or not end:
        days = data.recent_trading_days(lookback)
        if len(days) >= 2:
            start, end = days[0], days[-1]

    usable, errors = _prepare(codes, start, end)
    if not usable:
        raise RuntimeError("所选范围内没有可用数据，无法生成推荐")

    ctx = base.StrategyContext(codes=list(usable.keys()), start=start, end=end, adjust="hfq")
    signals = base.run(strategy_id, ctx, params)
    close_by_code = {c: usable[c]["close"] for c in usable}
    positions = base.signals_to_positions(signals, close_by_code)

    raw_bars: dict = {}
    try:
        raw_bars = data.load_bars(list(usable.keys()), start, end,
                                  fields=["close"], adjust="raw")
    except Exception:                            # noqa: BLE001 - 原始价拿不到就用复权价展示
        raw_bars = {}

    names = _load_names(list(usable.keys()))
    band = initial_capital / len(usable)

    buy: list[dict] = []
    sell: list[dict] = []
    hold: list[dict] = []
    for code, w in positions.items():
        close = close_by_code.get(code)
        if close is None or len(w) < 2:
            continue
        prev, cur = float(w.iloc[-2]), float(w.iloc[-1])
        raw = raw_bars.get(code)
        px = float(raw["close"].iloc[-1]) if (raw is not None and len(raw)) else float(close.iloc[-1])
        nm = names.get(code) or None

        if cur > 0.0 and prev <= 0.0:                       # 建仓
            buy.append({"code": code, "name": nm, "weight": round(cur, 4),
                        "price": round(px, 2), "amount": round(band * cur, 2)})
        elif cur <= 0.0 and prev > 0.0:                     # 清仓
            sell.append({"code": code, "name": nm, "weight": round(prev, 4),
                         "price": round(px, 2), "amount": round(band * prev, 2)})
        elif cur > 0.0 and prev > 0.0:                      # 继续持仓（可能加/减仓）
            open_i = len(w) - 1                             # 回溯到本次持仓的建仓日
            for i in range(len(w) - 1, -1, -1):
                if float(w.iloc[i]) <= 0.0:
                    break
                open_i = i
            hfq_buy = float(close.iloc[open_i])
            hfq_now = float(close.iloc[-1])
            pnl_pct = (hfq_now / hfq_buy - 1.0) if hfq_buy else 0.0
            amount = band * cur
            cost = float(raw["close"].iloc[open_i]) if (raw is not None and len(raw) > open_i) else hfq_buy
            hold.append({
                "code": code, "name": nm, "weight": round(cur, 4), "price": round(px, 2),
                "cost_price": round(cost, 2), "buy_date": str(w.index[open_i])[:10],
                "amount": round(amount, 2), "pnl": round(amount * pnl_pct, 2),
                "pnl_pct": round(pnl_pct, 4),
                "action": ("加仓" if cur > prev + 1e-9
                           else ("减仓" if cur < prev - 1e-9 else "持股")),
            })

    buy.sort(key=lambda x: -x["weight"])
    sell.sort(key=lambda x: -x["weight"])
    hold.sort(key=lambda x: -x["pnl"])

    date = ""
    for c in close_by_code.values():
        if len(c):
            d = str(c.index[-1])[:10]
            if d > date:
                date = d

    return {
        "ok": True,
        "strategy": {
            "id": strategy_id,
            "name": base.REGISTRY[strategy_id].name,
            "params": params or {},
        },
        "date": date or end,
        "band": round(band, 2),
        "buy": buy,
        "sell": sell,
        "hold": hold,
        "summary": {
            "buy_count": len(buy),
            "sell_count": len(sell),
            "hold_count": len(hold),
            "buy_amount": round(sum(x["amount"] for x in buy), 2),
            "sell_amount": round(sum(x["amount"] for x in sell), 2),
            "hold_amount": round(sum(x["amount"] for x in hold), 2),
            "hold_pnl": round(sum(x["pnl"] for x in hold), 2),
        },
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
