# -*- coding: utf-8 -*-
"""「JWZZ 安全版（跳过炸板买入）」对照 —— **生产口径**执行器。

⚠️ 为什么必须走编排层（`backtest.run_backtest`）：
    直调 `strategies.xxx` + `stats.compute` 会漏掉编排层的两件事，
    结果与生产不一致、且**系统性偏高**：
      ① `universe.filter_available` —— 区间内行情不足的票要被剔除（注入判定）；
      ② `raw_by_code`（未复权 OHLCV）→ 启用**严格成交模型**
         （一字涨停买不进即空仓、不重分配；一字跌停卖不出即延后清仓）。
    实测（JWZZ 族）：漏传 raw 时收益高估 **46 倍**（8457% vs 183%）。

## 对照的三组

    A  jwzz_cross                —— 基线（每日重选前 N，掉出即卖）
    B  jwzz_cross_safe(开)      —— A + 「涨停开盘但炸板 → 这一笔不买」
    C  jwzz_cross_safe(关)      —— 应当与 A **逐位相同**（证明它就是 A + 一条规则）

## 怎么读结果（别只盯总收益）

横向比**只看**夏普 / 每单位动用资金收益 / 最大回撤 / 换手；总收益在这套体系里
由「池子里恰好有哪些妖股」决定，换一次抽样就能差两个数量级。
本脚本是**单次运行**，只用于验证「这条规则生效了、影响多大」，
**不作为策略优劣结论**（结论要做同池打乱 + 换池重复，见 docs/backtest-credibility.md）。

用法：
    python scripts/jwzz_safe_probe.py --stocks 1500 --start 2023-11-16 --end 2026-11-16
    python scripts/jwzz_safe_probe.py --stocks 0        # 全市场（慢）
"""
from __future__ import annotations

import os
import sys
import time

import numpy as np

# 路径**从脚本自身位置推导**（不写死绝对路径）——拷到别的电脑也能跑
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.join(_ROOT, "backend_fastapi")
sys.path.insert(0, ROOT)
os.chdir(ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:                                        # noqa: BLE001
    pass

from strategies.core import base, data, backtest     # noqa: E402

PARAMS = dict(cost_period=13, threshold=95.0, smooth_period=0, top_n=10,
              position=1.0)


def pick_pool(n=0, seed=20261116):
    """股票池：n<=0 = 全市场；否则固定 seed 随机抽 n 只（可复现）。"""
    codes = sorted(data.list_universe_codes())
    if n <= 0:
        return codes
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(codes), size=min(n, len(codes)), replace=False)
    return sorted(codes[i] for i in idx)


def _num(v, default=0.0):
    """metrics 里可能是 list/None 的值收敛成标量（历史上真炸过整轮实验）。"""
    if isinstance(v, (list, tuple)):
        v = v[0] if v else default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def run(strategy_id: str, pool, start, end, params=None):
    """跑一次回测（生产口径：走编排层）。"""
    kw = dict(PARAMS)
    if params:
        kw.update(params)
    res = backtest.run_backtest(strategy_id, kw, list(pool), start, end,
                                universe_mode="interval_data")
    m = res["metrics"]
    trades = backtest.last_trades() or []
    return {
        "ret": _num(m.get("total_return")),
        "sharpe": _num(m.get("sharpe")),
        "mdd": _num(m.get("max_drawdown")),
        "util": _num(m.get("capital_utilization")),
        "rpu": _num(m.get("return_per_utilized_capital")),
        "turnover": _num(m.get("turnover_annual")),
        "avg_pos": _num(m.get("avg_positions")),
        "trades": _num(m.get("num_trades_eff")),
        "hold": _num(m.get("avg_holding_days")),
        "codes": int(_num(res["scope"]["codes_count"])),
        "feas": res["scope"].get("feasibility") or {},
        "equity": res.get("equity") or [],
        "trades_rows": trades,
    }


def count_skips(pool, start, end, params=None):
    """策略层的**精确**「放弃了几笔」统计（同一份策略代码，只数信号、不进引擎）。

    为什么单独数一遍：引擎的逐笔明细里只有「成交了的笔」，
    「成分没变但当天有放弃」的日子不会产生成交 → 从成交明细数会**漏**。
    """
    kw = dict(PARAMS)
    if params:
        kw.update(params)
    ctx = base.StrategyContext(codes=list(pool), start=start, end=end, adjust="hfq")
    sig = base.run("jwzz_cross_safe", ctx, kw)
    by_day: dict = {}
    for s in sig:
        n = int((s.meta or {}).get("skipped_broken") or 0)
        by_day[s.time] = max(by_day.get(s.time, 0), n)   # 同一天各票带的是同一个计数
    return {"skipped_total": sum(by_day.values()),
            "skipped_days": len([1 for v in by_day.values() if v > 0])}


def _fmt(label, r, extra=""):
    return ("  %-26s ret=%10.2f%%  sharpe=%6.2f  mdd=%7.2f%%  rpu=%8.2f"
            "  util=%.3f  换手=%6.1f倍  持有=%.1f天  成交=%5.0f  %s"
            % (label, r["ret"] * 100, r["sharpe"], r["mdd"] * 100, r["rpu"],
               r["util"], r["turnover"], r["hold"], r["trades"], extra))


def main():
    import argparse
    ap = argparse.ArgumentParser(description="JWZZ 安全版（跳过炸板买入）对照 · 生产口径")
    ap.add_argument("--stocks", type=int, default=1500, help="股票池只数（0=全市场）")
    ap.add_argument("--start", default="2023-11-16")
    ap.add_argument("--end", default="2026-11-16")
    ap.add_argument("--top-n", type=int, default=10)
    ap.add_argument("--threshold", type=float, default=95.0)
    ap.add_argument("--seed", type=int, default=20261116)
    args = ap.parse_args()

    t0 = time.time()
    pool = pick_pool(args.stocks, seed=args.seed)
    params = dict(top_n=args.top_n, threshold=args.threshold)
    print("=== JWZZ 安全版（跳过炸板买入）对照 · 生产口径 ===")
    print("    池子 %d 只 | 区间 %s ~ %s | top_n=%d | 阈值=%.0f‰\n"
          % (len(pool), args.start, args.end, args.top_n, args.threshold))

    a = run("jwzz_cross", pool, args.start, args.end, params)
    print(_fmt("A jwzz_cross（基线）", a), flush=True)

    b = run("jwzz_cross_safe", pool, args.start, args.end, params)
    print(_fmt("B jwzz_cross_safe（开）", b), flush=True)

    c = run("jwzz_cross_safe", pool, args.start, args.end,
            dict(params, skip_broken_limit_up=0))
    same = all(abs(x - y) < 1e-12 for x, y in zip(a["equity"], c["equity"]))
    print(_fmt("C jwzz_cross_safe（关）", c,
               "  ← 与 A 净值%s" % ("逐位相同 ✔" if same else "**不一致 ✘**")), flush=True)

    sk = count_skips(pool, args.start, args.end, params)
    print("\n  炸板放弃：共 %d 笔（分布在 %d 个交易日）"
          % (sk["skipped_total"], sk["skipped_days"]))

    fb = (b["feas"] or {}).get("fill_blocked") or {}
    if fb:
        print("  严格成交模型：因一字涨停**根本没发生**的建仓 %s 次"
              % fb.get("buy_blocked"))
    print("\n  ⚠️ 单次运行，只用于验证「规则生效 + 影响多大」；"
          "横向比只看夏普/回撤/换手，结论还需同池打乱 + 换池重复。")
    print("elapsed = %.1fs" % (time.time() - t0))


if __name__ == "__main__":
    main()
