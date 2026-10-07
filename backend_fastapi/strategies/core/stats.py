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


def trade_detail(pos: pd.Series, close: pd.Series,
                 raw_close: pd.Series | None = None,
                 band: float | None = None) -> list[dict]:
    """从目标仓位序列提取逐笔「一买一卖」明细，供前端展开查看。

    仓位口径与收益统计一致：w_t 在 t 日收盘生成、次日承担收益，成交价即
    「w 首次变化那一天的收盘价」（见模块 docstring 的 T+1 语义）。
    价格展示优先用 **原始收盘**（未复权，用户看得懂），收益一律用
    后复权序列计算（消除除权跳空的假盈亏）。未平仓段 sell 为 null。

    返回 [{buy_date, buy_price, sell_date, sell_price, ret, shares, pnl}, ...]（时间升序）。

    `band` = **每只标的的资金带**（`initial_capital / 有效标的数`，与
    `run_recommend` 的金额口径一致）。给了它才输出 `shares` / `pnl`：
      · shares = band × 买入时仓位权重 ÷ **原始**买入价 → 折算成真实股数
        （用原始价才与行情软件显示的股数对得上，便于人工核对）；
      · pnl    = shares × **原始买价** × ret（ret 是**后复权**收益率）→ 真实盈亏。

    ⚠️ pnl **不是** `shares × 后复权价差`：后复权价可能远高于原始价（多次送转后
    能差十倍，如哈药股份 2025-01-03 的 4.30 ↔ 45.44），拿它做价差会让金额凭空放大
    十倍。ret 用后复权是为了**消除除权跳空的假盈亏**，但**金额必须回到原始价口径**——
    两者相乘才是真实盈亏，且 `pnl ÷ (shares × buy_price) ≡ ret`，三者自洽可交叉核对。
    未平仓段 shares 有值、pnl 为 null（还没卖，盈亏定不了）。

    ⚠️ 这两个是**折算值**，不是模拟下单的结果：回测的净值口径是「收益率叠加」
    （见 `compute`：`w_prev * ret − turnover * commission`），全程**没有**按金额
    分配资金、不产生真实成交，故不存在唯一的股数与盈亏金额。这里按等权资金带
    换算成可读数值，**口径必须在报告里写明**，否则会被当成实测成交额。
    """
    w = pos.reindex(close.index).fillna(0.0).astype(float)
    price = raw_close if raw_close is not None and len(raw_close) else close

    def _num(v):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return None if math.isnan(f) else round(f, 3)

    def _price(i: int):
        p = _num(price.iloc[i]) if i < len(price) else None
        return p if p is not None else _num(close.iloc[i])

    def _shares_pnl(open_i: int, i: int | None):
        """折算股数与盈亏；band 缺失 / 价格无效时返回 (None, None)。

        pnl = shares × **原始买价** × 后复权收益率（不是 shares × 后复权价差——
        后复权价可能远高于原始价，用价差会让金额凭空放大十倍）。
        """
        if not band or band <= 0:
            return None, None
        bp = _num(price.iloc[open_i]) if open_i < len(price) else None
        if not bp or bp <= 0:
            return None, None
        try:
            sh = int(round(band * float(w.iloc[open_i]) / bp))
        except (TypeError, ValueError, OverflowError):
            return None, None
        if sh <= 0:
            return 0, 0.0
        if i is None:                      # 未平仓：盈亏还定不了
            return sh, None
        b_adj = float(close.iloc[open_i])
        if not b_adj:
            return sh, None
        # 与 all_trades_detail 一致：ret 先取整，让 pnl 能用报告字段复算出来
        ret = round(float(close.iloc[i]) / b_adj - 1.0, 4)
        return sh, round(sh * float(bp) * ret, 2)

    out: list[dict] = []
    open_i: int | None = None
    for i in range(len(w)):
        cur = float(w.iloc[i])
        if cur > 0.0 and open_i is None:
            open_i = i
        elif cur <= 0.0 and open_i is not None:
            ret = float(close.iloc[i] / close.iloc[open_i] - 1.0)
            sh, pnl = _shares_pnl(open_i, i)
            out.append({
                "buy_date": str(w.index[open_i])[:10], "buy_price": _price(open_i),
                "sell_date": str(w.index[i])[:10], "sell_price": _price(i),
                "ret": round(ret, 4), "shares": sh, "pnl": pnl,
            })
            open_i = None
    if open_i is not None:
        sh, pnl = _shares_pnl(open_i, None)
        out.append({
            "buy_date": str(w.index[open_i])[:10], "buy_price": _price(open_i),
            "sell_date": None, "sell_price": None, "ret": None,
            "shares": sh, "pnl": pnl,
        })
    return out


def all_trades_detail(positions_by_code: dict, close_by_code: dict,
                      raw_by_code: dict | None = None,
                      band: float | None = None) -> list[dict]:
    """**全市场**逐笔「一买一卖」明细，供回测报告落盘（向量化实现）。

    与逐票的 `trade_detail` 结果一致，但**不复用它的循环** —— 全市场 5000+ 只 ×
    每年 700+ 交易日是 400 万次Python 迭代，报告生成会卡到不可接受。这里用
    向量化先定位「仓位由0 变正」/「由正变 0」的转折下标，只对转折点做 Python
    迭代，成本降到每只票两次 numpy 比较。

    `band` / `shares` / `pnl` 的口径见 `trade_detail` 的 docstring（折算值，
    非模拟成交）。返回按 code、再按时间升序。
    """
    out: list[dict] = []
    for code, pos in (positions_by_code or {}).items():
        close = close_by_code.get(code)
        if close is None or len(close) < 2:
            continue
        w = pos.reindex(close.index).fillna(0.0).astype(float)
        raw = (raw_by_code or {}).get(code)
        price = raw if (raw is not None and len(raw)) else close

        prev = w.shift(1).fillna(0.0)
        open_at = [i for i in np.flatnonzero((w > 0.0).to_numpy()
                                             & (prev <= 0.0).to_numpy())]
        close_at = [i for i in np.flatnonzero((w <= 0.0).to_numpy()
                                              & (prev > 0.0).to_numpy())]
        if not open_at:
            continue
        dates = [str(x)[:10] for x in w.index]

        def _px(i, series):
            return float(series.iloc[i]) if i < len(series) else None

        def _emit(o: int, c: int | None):
            bp, sp = _px(o, price), (None if c is None else _px(c, price))
            b_adj, s_adj = _px(o, close), (None if c is None else _px(c, close))
            # ⚠️ ret 先取整再往下算：报告里给的是取整后的 ret，若 pnl 用未取整的值，
            # 拿报告字段复算会对不上（实测差 0.09 元）。取整后
            # pnl ≡ shares × 买价 × ret 严格自洽 ——「可交叉核对」这句话才成立。
            ret = None if c is None or not b_adj else round(s_adj / b_adj - 1.0, 4)
            item = {
                "code": code,
                "buy_date": dates[o],
                "buy_price": None if bp is None else round(bp, 3),
                "sell_date": None if c is None else dates[c],
                "sell_price": None if sp is None else round(sp, 3),
                "ret": ret,
                "shares": None,
                "pnl": None,
            }
            if band and band > 0 and bp and bp > 0:
                sh = int(round(band * float(w.iloc[o]) / bp))
                item["shares"] = sh if sh > 0 else 0
                # ⚠️ pnl = shares × **原始买价** × 后复权收益率，**不是** shares × 后复权价差。
                # 后复权价可能远高于原始价（多次送转后能差十倍，如哈药股份 4.30 ↔ 45.44），
                # 用它做价差会让金额凭空放大十倍 —— 实测就踩到过：3876 股亏 26626 元，
                # 而真实亏损只有 2519 元。ret 必须用后复权（消除除权跳空的假盈亏），
                # 但**金额要回到原始价口径**，两者相乘才是真实盈亏，且
                # pnl / (shares × buy_price) ≡ ret，三者自洽可交叉核对。
                if sh > 0 and c is not None and ret is not None:
                    item["pnl"] = round(sh * bp * ret, 2)
            out.append(item)

        # 配对：每个开仓点配「其后的第一个平仓点」；配不上的（区间结束仍未平）
        # 记为未平仓 —— 与 trade_detail 的逐日扫描语义一致。
        oi = 0
        for c in close_at:
            if oi < len(open_at) and open_at[oi] < c:
                _emit(open_at[oi], c)
                oi += 1
        while oi < len(open_at):
            _emit(open_at[oi], None)
            oi += 1
    return out


def compute(positions_by_code: dict, close_by_code: dict, *,
            initial_capital: float = 100000.0, commission: float = 0.0003,
            benchmark_close: pd.Series | None = None,
            tail: int | None = None) -> dict:
    """按目标仓位序列 + 收盘价序列，统计组合净值与指标。

    参数：
      positions_by_code  {code: pd.Series(目标仓位, 索引 YYYY-MM-DD)}
      close_by_code      {code: pd.Series(收盘价, 索引 YYYY-MM-DD)}
      initial_capital    初始资金
      commission         单边佣金比例（如 0.0003 = 万三）
      benchmark_close    基准收盘价序列（可选，用于对比净值）
      tail               只统计**最后 N 个交易日**（用于「最近 5 日」这类短窗口：
                         传入的序列更长，是为让策略指标预热，但不计入净值/指标）
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
        if tail and len(sret) > tail:
            # 短窗口（如「最近 5 个交易日」）：预热段只用于算指标，不进入统计
            sret = sret.iloc[-tail:]
            turnover = turnover.iloc[-tail:]
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

    # 对齐到「主日历」：全量回测（5000+ 只）时交集会被次新股压到只剩几天
    # （一只 9 月底上市的票只含几个交易日，一交集全组合就没了），故取并集，
    # 各标的缺失交易日 reindex 后按 0 收益补——与「等权资金带、未建仓为现金」
    # 的组合口径天然一致。
    common = pd.Index(sorted(set().union(*[s.index for s in srets.values()])))
    if tail and len(common) > tail:
        # 停牌票会让并集日历多出几天，短窗口下严格截到 N 日
        common = common[-tail:]
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
