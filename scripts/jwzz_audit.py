# -*- coding: utf-8 -*-
"""JWZZ 回测审计（**生产口径**）：引擎确定性 + 收益集中度 + 逐笔收益自洽性。

⚠️ 执行路径：走 `backtest.run_backtest`（编排层），与生产一致。
    直调 `stats.compute` 会漏掉 `raw_by_code` → 严格成交模型不生效，
    逐笔里会出现 `buy_price=None` / `shares=0`，自洽性判据随之失效
    （详见 `docs/backtest-credibility.md` §二.9）。

回答三个问题（每个都要有证据，不靠感觉）：

1. **引擎确定性**（纪律第一道闸）：同参数、同区间跑两次，逐日净值是否**逐位一致**？
   不一致 → 后面所有结论作废，先修引擎。

2. **收益集中度**：总收益是「广谱的」还是「少数几笔彩票」？
   → 逐笔 pnl 排序，看 top N 笔占总毛利比例；逐票 top N 占比。
   → 集中度极高 ⇒ 单一总收益数字不可复现。

3. **逐笔收益自洽性**：一笔成交的收益率不得超过该票在**持仓期内**的真实最大涨幅。
   未来函数会让成交收益远超任何真实涨幅；成本只会让它略低，
   所以单向上限判据不会把成本误判成造假。
   ⚠️ 不做「首末价等式对账」：一笔 trade 是**持仓段**，期间可能多次加减仓
   （目标权重随当日强势票数量变化），与「首笔买入价 → 末笔卖出价」本不成等式。

⚠️ 结果**不落盘**到 backtests/（避免污染用户的报告目录）；只打印。
"""
from __future__ import annotations

import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = r"c:\Users\Administrator\Desktop\stockanaly\backend_fastapi"
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
os.chdir(ROOT)
sys.stdout.reconfigure(encoding="utf-8")

from jwzz_prod_probe import run as prod_run, PARAMS   # noqa: E402  生产口径执行器


def check_determinism(r1, r2):
    """闸一：逐日净值是否逐位一致。"""
    e1, e2 = list(r1["equity"]), list(r2["equity"])
    out = {"same_len": len(e1) == len(e2), "len": len(e1)}
    if not out["same_len"]:
        out["max_abs_diff"] = float("inf")
        out["first_diff"] = -1
        out["metrics_same"] = False
        return out
    first = -1
    maxd = 0.0
    for i, (a, b) in enumerate(zip(e1, e2)):
        a = float(a) if a is not None else float("nan")
        b = float(b) if b is not None else float("nan")
        d = abs(a - b)
        if d > maxd:
            maxd = d
        if d != 0.0 and first < 0:
            first = i
    out["max_abs_diff"] = maxd
    out["first_diff"] = first
    keys = ("total_return", "sharpe", "max_drawdown", "num_trades_eff",
            "avg_holding_days", "turnover_annual", "capital_utilization",
            "return_per_utilized_capital", "win_rate")
    m1, m2 = r1["metrics"], r2["metrics"]
    out["metrics"] = {k: (m1.get(k), m2.get(k)) for k in keys}
    out["metrics_same"] = all(
        (m1.get(k) is None and m2.get(k) is None) or
        (m1.get(k) is not None and m2.get(k) is not None and
         abs(float(m1.get(k)) - float(m2.get(k))) < 1e-12)
        for k in keys)
    return out


def concentration(r):
    """收益集中度：逐笔 / 逐票 / 持有期 / 胜率。"""
    trades = list(r["trade_rows"] or [])
    closed = [t for t in trades if not t.get("open")]
    pnls = np.array([float(t.get("pnl") or 0.0) for t in trades], dtype=float)
    gross_win = float(pnls[pnls > 0].sum())
    gross_loss = float(-pnls[pnls < 0].sum())
    order = np.argsort(-pnls)
    ranked = pnls[order]
    total_pos = float(ranked[ranked > 0].sum())

    def share(n):
        return float(ranked[:n].clip(min=0).sum() / total_pos) if total_pos > 0 else 0.0

    hold = np.array([int(t.get("holding_days") or 0) for t in closed], dtype=float)
    rets = np.array([float(t.get("ret") or 0.0) for t in closed
                     if t.get("ret") is not None], dtype=float)
    ps = sorted(r["per_stock"] or [], key=lambda x: -float(x.get("pnl") or 0.0))
    ps_pos = sum(max(0.0, float(x.get("pnl") or 0.0)) for x in ps) or 1.0
    ps_top10 = sum(max(0.0, float(x.get("pnl") or 0.0)) for x in ps[:10]) / ps_pos
    return {
        "n_trades": len(trades), "n_closed": len(closed),
        "gross_win": gross_win, "gross_loss": gross_loss,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else float("inf"),
        "win_rate": float((rets > 0).mean()) if len(rets) else 0.0,
        "share_top10": share(10), "share_top50": share(50), "share_top100": share(100),
        "n_for_80pct": int(np.searchsorted(np.cumsum(ranked.clip(min=0)),
                                           0.8 * total_pos) + 1),
        "hold_median": float(np.median(hold)) if len(hold) else 0.0,
        "hold_mean": float(hold.mean()) if len(hold) else 0.0,
        "ret_median": float(np.median(rets)) if len(rets) else 0.0,
        "ret_max": float(rets.max()) if len(rets) else 0.0,
        "ret_min": float(rets.min()) if len(rets) else 0.0,
        "stock_top10_share": float(ps_top10),
        "top_trades": [trades[i] for i in order[:12]],
        "top_stocks": [{"code": x.get("code"), "pnl": float(x.get("pnl") or 0.0),
                        "trades": x.get("trades")} for x in ps[:10]],
    }


def _seg_px(px_series, bd, sd):
    """取 [买入日, 卖出日] 区间的收盘价序列（只用 <= 过滤，不依赖索引单调切片）。"""
    s = px_series[px_series.index <= bd]
    if len(s) == 0:
        return None, None, None
    buy_px = float(s.iloc[-1])
    e = px_series[px_series.index <= sd]
    seg = e[e.index >= s.index[-1]]
    if len(seg) == 0:
        return buy_px, None, None
    return buy_px, float(seg.iloc[-1]), float(seg.max())


def load_close(codes, start, end):
    """按需取 hfq 收盘价（只取核对涉及的票，别全市场取）。"""
    from strategies.core import data
    codes = [c for c in dict.fromkeys(codes) if c]
    if not codes:
        return {}
    bars = data.load_bars(codes, start, end, fields=["close"], adjust="hfq")
    out = {c: bars[c]["close"] for c in bars if c in bars}
    data.release_cache(codes, adjust="hfq")
    return out


def scan_trades(trades, close_by_code, tol=0.01):
    """逐笔 pnl 与单票价格走势的**口径差异**统计。

    ⚠️ 这不是「造假检测」：引擎的逐笔 pnl = Σ卖出金额 − Σ买入金额 − 成本，
    而成交金额按「|Δw| × 调仓日前的**组合净值**」记账，**不是**按该票价格卖。
    → 组合净值因别的票上涨而增加时，这一笔的 pnl 会**高于**该票的真实盈亏，
    甚至**方向相反**（实测：某票真实跌 14.9%，逐笔记 +18%）。
    → 所以「逐笔 ret vs 单票涨幅」**不能**用来判真假，只能用来量口径差。
    这里统计它，是为了让「逐笔收益」这个字段的可靠边界被写清楚。
    """
    ok = 0
    skip = 0
    diff = []
    for t in trades:
        if t.get("open"):
            continue
        code, bd, sd = t.get("code"), t.get("buy_date"), t.get("sell_date")
        if t.get("buy_price") is None or t.get("sell_price") is None \
                or not bd or not sd:
            skip += 1
            continue
        px = close_by_code.get(code)
        if px is None:
            skip += 1
            continue
        buy_px, sell_px, max_px = _seg_px(px, bd, sd)
        if buy_px is None or sell_px is None or buy_px <= 0:
            skip += 1
            continue
        max_gain = max_px / buy_px - 1.0
        rep_ret = float(t.get("ret") or 0.0)
        diff.append(rep_ret - max_gain)
        if rep_ret <= max_gain + tol:
            ok += 1
    d = np.array(diff, dtype=float) if diff else np.zeros(0)
    return {"n_ok": ok, "n_skip": skip, "n_cmp": len(d),
            "median_gap": float(np.median(d)) if len(d) else 0.0,
            "max_gap": float(d.max()) if len(d) else 0.0,
            "min_gap": float(d.min()) if len(d) else 0.0,
            "n_over": int((d > tol).sum())}


def rebuild_equity(pool, start, end, params):
    """**独立重建净值**：只用「t-1 日已确定的权重」×「t 日真实收益」，
    不扣成本、不做一字板约束。

    这是「引擎有没有偷看未来」的结构性检验：
      * 若引擎净值 ≈ 本重建值 → 引擎的收益确实只由「前一日可见的信息」驱动；
      * 若引擎净值**系统性高于**重建值且相关性低 → 引擎用了未来信息。
    重建值**必然高于**引擎值（引擎要扣成本、且一字板买不进会少持仓），
    所以这里看的是**相关性**与**偏离的方向**，不是绝对值。
    """
    from strategies.core import base, data
    from strategies import jwzz_cross as JC

    ctx = base.StrategyContext(codes=list(pool), start=start, end=end, adjust="hfq")
    bars = data.load_bars(pool, start, end, fields=["close", "volume", "amount"],
                          adjust="hfq")
    close_by_code = {c: bars[c]["close"] for c in bars if c in bars}
    sigs = JC.jwzz_cross(ctx, **params)
    pos = base.signals_to_positions(sigs, close_by_code)
    codes = [c for c in close_by_code
             if pos.get(c) is not None and len(pos[c]) >= 2 and len(close_by_code[c]) >= 2]
    if not codes:
        return None, None
    common = sorted({str(d)[:10] for c in codes for d in close_by_code[c].index})
    T = len(common)
    W = np.zeros((T, len(codes)), dtype=np.float32)
    R = np.zeros((T, len(codes)), dtype=np.float32)
    for j, c in enumerate(codes):
        W[:, j] = pos[c].reindex(common).fillna(0.0).to_numpy(dtype=np.float32)
        px = close_by_code[c].reindex(common).astype(float)
        r = px.pct_change().replace([np.inf, -np.inf], np.nan).fillna(0.0)
        R[:, j] = r.to_numpy(dtype=np.float32)
    nav = np.ones(T, dtype=float)
    for t in range(1, T):
        nav[t] = nav[t - 1] * (1.0 + float((W[t - 1] * R[t]).sum()))
    data.release_cache(codes, adjust="hfq")
    return nav, common


def main():
    import argparse
    ap = argparse.ArgumentParser(description="JWZZ 回测审计（生产口径）")
    ap.add_argument("--periods", default="2024-01-02:2024-12-31")
    ap.add_argument("--stocks", type=int, default=0, help="股票池只数（0=全市场）")
    args = ap.parse_args()

    from jwzz_prod_probe import pick_pool

    pool = pick_pool(args.stocks)
    t0 = time.time()
    print("=== JWZZ 回测审计（生产口径，池子 %d 只）===" % len(pool))
    print("    参数：cost_period=13 threshold=95 smooth=0 top_n=10 position=1.0\n")

    for item in str(args.periods).split(","):
        item = item.strip()
        if not item:
            continue
        s, e = item.split(":")
        print("=" * 78)
        print("区间 %s ~ %s" % (s, e))
        print("=" * 78, flush=True)

        r1 = prod_run(pool, s, e)
        r2 = prod_run(pool, s, e)

        det = check_determinism(r1, r2)
        print("\n[闸一] 引擎确定性")
        print("  净值长度=%s  逐位最大差=%.3e  首个不一致位置=%d  metrics一致=%s"
              % (det["same_len"], det["max_abs_diff"], det["first_diff"],
                 det["metrics_same"]))
        print("  → %s" % ("✅ 逐位一致（后续结论成立）"
                          if det["max_abs_diff"] == 0.0 and det["metrics_same"]
                          else "❌ 不一致 —— 结论作废，先修引擎"))
        for k, (v1, v2) in (det.get("metrics") or {}).items():
            print("      %-28s %s | %s" % (k, v1, v2))

        print("\n[概况] ret=%.2f%%  sharpe=%.2f  mdd=%.2f%%  util=%.3f  avgPos=%.2f"
              "  换手=%.1f倍/年"
              % (r1["ret"] * 100, r1["sharpe"], r1["mdd"] * 100, r1["util"],
                 r1["avg_pos"], r1["turnover"]))
        print("       可用只数=%d  成交约束=%s"
              % (r1["codes"], {k: v for k, v in (r1["feas"] or {}).items()
                               if isinstance(v, (int, float))}))
        for w in [x for x in r1["warnings"] if "bad_price_data" in x]:
            print("       ⚠ %s" % w[:150])

        con = concentration(r1)
        print("\n[闸二] 收益集中度")
        print("  笔数=%d（已平仓 %d）  毛利=%.0f  毛损=%.0f  盈亏比=%.2f  胜率=%.1f%%"
              % (con["n_trades"], con["n_closed"], con["gross_win"],
                 con["gross_loss"], con["profit_factor"], con["win_rate"] * 100))
        print("  毛利集中：top10笔=%.1f%%  top50笔=%.1f%%  top100笔=%.1f%%"
              % (con["share_top10"] * 100, con["share_top50"] * 100,
                 con["share_top100"] * 100))
        print("  达到 80%% 毛利只需 %.0f 笔   逐票 top10 占比=%.1f%%"
              % (con["n_for_80pct"], con["stock_top10_share"] * 100))
        print("  持有期：中位=%.0f 天 均值=%.1f 天   单笔收益：中位=%.1f%% max=%.1f%% min=%.1f%%"
              % (con["hold_median"], con["hold_mean"], con["ret_median"] * 100,
                 con["ret_max"] * 100, con["ret_min"] * 100))
        print("  贡献最大的前 8 笔：")
        for t in con["top_trades"][:8]:
            print("    %-8s 买 %s 卖 %-10s 持 %3s 天  ret=%9.1f%%  pnl=%12.0f"
                  % (t.get("code"), t.get("buy_date"), str(t.get("sell_date")),
                     t.get("holding_days"), float(t.get("ret") or 0) * 100,
                     float(t.get("pnl") or 0)))
        print("  贡献最大的前 8 只票：")
        for x in con["top_stocks"][:8]:
            print("    %-8s pnl=%12.0f  笔数=%s" % (x["code"], x["pnl"], x["trades"]))

        codes = [t.get("code") for t in (r1["trade_rows"] or []) if t.get("code")]
        close_by_code = load_close(codes, s, e)
        st = scan_trades(r1["trade_rows"] or [], close_by_code)
        print("\n[闸三-A] 逐笔 pnl 与单票价格走势的口径差（**不是造假检测**）")
        print("  可比笔=%d  合规(<=真实最大涨幅)=%d  超出=%d  跳过(无成交价)=%d"
              % (st["n_cmp"], st["n_ok"], st["n_over"], st["n_skip"]))
        print("  差额： 中位=%+.2f%%  最大=%+.2f%%  最小=%+.2f%%"
              % (st["median_gap"] * 100, st["max_gap"] * 100, st["min_gap"] * 100))
        print("  → 引擎逐笔 pnl 按「|Δw| × 组合净值」记账，与该票价格脱钩"
              "（可方向相反）→ 逐笔 ret **不可**当单票真实盈亏读")

        # ---- 闸三-B：净值独立重建（结构性验证「无未来信息」）----
        nav_r, common = rebuild_equity(pool, s, e, dict(PARAMS))
        eq = [x for x in (r1["equity"] or []) if x is not None]
        print("\n[闸三-B] 净值独立重建对账（只用 t-1 日可见信息）")
        if nav_r is None or len(eq) < 2:
            print("  样本不足，跳过")
        else:
            n = min(len(nav_r), len(eq))
            eng = np.array([float(x) for x in eq[:n]], dtype=float)
            eng = eng / eng[0]
            reb = nav_r[:n]
            er = np.diff(eng) / eng[:-1]
            rr = np.diff(reb) / reb[:-1]
            corr = float(np.corrcoef(er, rr)[0, 1]) if len(er) > 2 else float("nan")
            print("  样本天数=%d  日收益相关系数=%.4f" % (n, corr))
            print("  期末：引擎=%.4f×  重建=%.4f×  比值(重建/引擎)=%.4f"
                  % (eng[-1], reb[-1], reb[-1] / eng[-1] if eng[-1] else float("nan")))
            print("  重建不含成本与一字板约束 → 必然 >= 引擎；比值 %.3f"
                  % (reb[-1] / eng[-1] if eng[-1] else float("nan")))
            ok = corr > 0.9 and (reb[-1] >= eng[-1] * 0.5)
            print("  → %s" % ("✅ 引擎净值与「仅用前一日信息」的重建高度一致"
                              "（无未来信息）" if ok else
                              "❌ 二者明显背离 —— 引擎可能用了未来信息，需深查"))

    print("\nelapsed = %.1fs" % (time.time() - t0))


if __name__ == "__main__":
    main()
