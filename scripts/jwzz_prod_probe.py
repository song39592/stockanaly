# -*- coding: utf-8 -*-
"""JWZZ 对照实验 —— **生产口径**执行器。

⚠️ 为什么不用「直接调 strategies + stats」：
    那样会漏掉编排层的两件事，结果与生产不一致：
      1. `universe.filter_available` —— 区间内行情不足的票要被剔除（注入判定）；
      2. `raw_by_code`（未复权 OHLCV）→ 启用**严格成交模型**
         （一字涨停买不进即空仓、不重分配；一字跌停卖不出即延后清仓）。
         漏掉它 = 把「买不进」当成「买到了」→ 收益系统性偏高。
    实测证据：漏传 raw 时，逐笔里 24% 的笔 `buy_price=None`、`shares=0`，
    且这些笔的「成交收益率」与真实价格涨幅完全对不上（引擎仍按权重记账）。
    所以本脚本**一律走 `backtest.run_backtest`**，唯一变量靠 monkeypatch 控制。

模式：
    --mode ab       A/B：严格成交模型 开 / 关 的差异量级
    --mode shuffle  同强势池打乱排序（分离「集中度」与「选股质量」）
    --mode pool     换股票池抽样，看收益分布（量化抽样噪声）
    --mode period   换时间窗口，看行情依赖（股票池固定不变）

⚠️ 结果**不落盘**到 backtests/（避免污染用户的报告目录）；只打印。
"""
from __future__ import annotations

import os
import re
import sys
import time

import numpy as np

ROOT = r"c:\Users\Administrator\Desktop\stockanaly\backend_fastapi"
sys.path.insert(0, ROOT)
os.chdir(ROOT)
sys.stdout.reconfigure(encoding="utf-8")

from strategies.core import base, data, backtest     # noqa: E402

PARAMS = dict(cost_period=13, threshold=95.0, smooth_period=0, top_n=10,
              position=1.0)
TOP_N = 10


def pick_pool(n=0, seed=20261116):
    """股票池：n<=0 = 全市场；否则固定 seed 随机抽 n 只。"""
    codes = sorted(data.list_universe_codes())
    if n <= 0:
        return codes
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(codes), size=min(n, len(codes)), replace=False)
    return sorted(codes[i] for i in idx)


_OLD = '''            order = day_bull[np.argsort(-strength_m[day_bull, t], kind="stable")]
            picks = {cols[i] for i in order[:top_n]}'''
_NEW = '''            order = day_bull[np.argsort(-strength_m[day_bull, t], kind="stable")]
            pool = order if pick_pool <= 0 else order[:pick_pool]
            _rng.shuffle(pool)
            picks = {cols[i] for i in pool[:top_n]}'''


def load_shuffle_variant():
    """产出「只打乱排序」的策略函数；其余口径全部沿用生产源码。"""
    from strategies import jwzz_cross as JC
    from formulas.jwzz import jwzz_panel

    path = os.path.join(ROOT, "strategies", "jwzz_cross.py")
    src = open(path, encoding="utf-8").read()
    if _OLD not in src:
        raise RuntimeError("生产源码与预期不符（取前 N 那两行没找到）—— 需要重新对齐")
    src = src.replace(_OLD, _NEW)
    src = re.sub(r"^from\s+\.[\w\.]+\s+import\s+.*$", "", src, flags=re.M)
    src = re.sub(r"^from\s+formulas\.[\w\.]+\s+import\s+.*$", "", src, flags=re.M)
    src = re.sub(r"^@strategy\(.*?^\)\s*$", "", src, flags=re.M | re.S)
    src = src.replace("def jwzz_cross(", "def jwzz_cross_shuffle(")
    src, n = re.subn(r"position:\s*float\s*=\s*1\.0\)\s*->\s*list:",
                     "position: float = 1.0, pick_seed: int = 0,\n"
                     "               pick_pool: int = 30) -> list:",
                     src)
    if n != 1:
        raise RuntimeError("签名替换失败（position: float = 1.0) -> list: 没匹配到）")
    src = src.replace(
        "    codes = [str(c).strip() for c in (ctx.codes or []) if str(c).strip()]",
        "    _rng = np.random.default_rng(pick_seed)\n"
        "    pick_pool = int(pick_pool)   # <=0 = 不截断（用当日全部强势票）\n"
        "    codes = [str(c).strip() for c in (ctx.codes or []) if str(c).strip()]")
    ns = {"np": np, "Signal": base.Signal, "ParamSpec": base.ParamSpec,
          "data": data, "DEFAULT_PERIOD": JC.DEFAULT_PERIOD,
          "jwzz_panel": jwzz_panel}
    exec(compile(src, "<jwzz_cross@shuffle>", "exec"), ns)
    return ns["jwzz_cross_shuffle"]


def run(pool, start, end, params=None, shuffle=None, strict=True):
    """跑一次回测（生产编排层）。

    shuffle = None      → 基准组（生产策略，一字不改）
    shuffle = (seed, K) → 对照组（同池打乱排序）
    strict = True       → 启用严格成交模型（生产默认）
    strict = False      → 关掉 raw_by_code（模拟「漏传 raw」的放宽口径）
    """
    kw = dict(PARAMS)
    if params:
        kw.update(params)
    orig_run = base.run
    orig_raw = data.load_bars_raw_for
    try:
        if shuffle is not None:
            fn = load_shuffle_variant()
            seed, K = shuffle

            def patched(strategy_id, ctx, params_):
                if strategy_id != "jwzz_cross":
                    return orig_run(strategy_id, ctx, params_)
                p = dict(kw)
                p["pick_seed"] = seed
                p["pick_pool"] = K
                return fn(ctx, **p)

            base.run = patched
        if not strict:
            data.load_bars_raw_for = lambda *a, **k: {}
        res = backtest.run_backtest("jwzz_cross", kw, list(pool), start, end,
                                    universe_mode="interval_data")
    finally:
        base.run = orig_run
        data.load_bars_raw_for = orig_raw

    m = res["metrics"]
    return {
        "ret": float(m.get("total_return") or 0.0),
        "sharpe": float(m.get("sharpe") or 0.0),
        "mdd": float(m.get("max_drawdown") or 0.0),
        "util": float(m.get("capital_utilization") or 0.0),
        "avg_pos": float(m.get("avg_positions") or 0.0),
        "trades": float(m.get("num_trades_eff") or 0.0),
        "hold": float(m.get("avg_holding_days") or 0.0),
        "bench": float(m.get("benchmark_return") or 0.0),
        "rpu": float(m.get("return_per_utilized_capital") or 0.0),
        "turnover": float(m.get("turnover_annual") or 0.0),
        "codes": int(res["scope"]["codes_count"]),
        "feas": res["scope"].get("feasibility") or {},
        "warnings": res["scope"].get("warnings") or [],
        "equity": res.get("equity") or [],
        "dates": res.get("dates") or [],
        "trades": res.get("trades") or [],
        "per_stock": res.get("per_stock") or [],
        "metrics": m,
    }


def _fmt_row(label, r):
    return ("  %-28s ret=%10.2f%%  sharpe=%6.2f  mdd=%7.2f%%  util=%.3f"
            "  avgPos=%5.2f  trades=%6.0f  持有=%.1f天  基准=%8.2f%%"
            % (label, r["ret"] * 100, r["sharpe"], r["mdd"] * 100, r["util"],
               r["avg_pos"], r["trades"], r["hold"], r["bench"] * 100))


def mode_ab(args, t0):
    """A/B：严格成交模型开 / 关 的差异量级。"""
    pool = pick_pool(args.stocks)
    print("=== A/B：严格成交模型（一字板买不进）对收益的影响 ===")
    print("    池子 %d 只 | 生产口径编排层\n" % len(pool))
    for item in str(args.periods).split(","):
        item = item.strip()
        if not item:
            continue
        s, e = item.split(":")
        for strict in (True, False):
            r = run(pool, s, e, strict=strict)
            print(_fmt_row("%s 严格成交=%s" % (s, "开" if strict else "关"), r),
                  flush=True)
            for w in [x for x in r["warnings"] if "bad_price_data" in x]:
                print("      ⚠ %s" % w[:150])
            fb = {k: v for k, v in (r["feas"] or {}).items()
                  if isinstance(v, (int, float))}
            if fb:
                print("      成交约束: %s" % fb)
    print("\nelapsed = %.1fs" % (time.time() - t0))


def mode_shuffle(args, t0):
    """同强势池打乱排序 —— 分离「集中度」与「选股质量」。"""
    pool = pick_pool(args.stocks)
    print("=== 同强势池打乱排序（生产口径）===")
    print("    池子 %d 只 | 区间 %s ~ %s | top_n=%d\n"
          % (len(pool), args.start, args.end, TOP_N))
    b = run(pool, args.start, args.end)
    print(_fmt_row("基准组（JWZZ 降序前 %d）" % TOP_N, b), flush=True)
    for K in (args.pool, 0):
        label = ("降序前 %d 只里随机取 %d 只" % (K, TOP_N)) if K > 0 else \
                ("当日全部强势票里随机取 %d 只" % TOP_N)
        print("\n--- 对照组：%s ---" % label, flush=True)
        rets = []
        for seed in range(1, args.seeds + 1):
            r = run(pool, args.start, args.end, shuffle=(seed, K))
            rets.append(r["ret"])
            print(_fmt_row("seed=%d" % seed, r), flush=True)
        mean = sum(rets) / len(rets)
        print("  → 均值=%9.2f%%   基准=%9.2f%%   比值=%.4f"
              % (mean * 100, b["ret"] * 100,
                 mean / b["ret"] if b["ret"] else float("nan")), flush=True)
    print("\nelapsed = %.1fs" % (time.time() - t0))


def mode_pool(args, t0):
    """换股票池抽样 —— 量化抽样噪声。"""
    print("=== 换股票池抽样（生产口径）===")
    print("    区间 %s ~ %s | 每个子集 %d 只\n" % (args.start, args.end, args.stocks))
    rets = []
    for ps in [int(x) for x in str(args.pool_seeds).split(",") if x.strip()]:
        pool = pick_pool(args.stocks, seed=ps)
        r = run(pool, args.start, args.end)
        rets.append(r["ret"])
        print(_fmt_row("pool_seed=%d" % ps, r), flush=True)
    if args.all_market:
        pool = pick_pool(0)
        r = run(pool, args.start, args.end)
        rets.append(r["ret"])
        print(_fmt_row("全市场 %d 只" % len(pool), r), flush=True)
    a = np.array(rets)
    print("\n  收益分布：n=%d 中位=%9.2f%% 均值=%9.2f%% min=%9.2f%% max=%9.2f%%"
          % (len(a), np.median(a) * 100, a.mean() * 100, a.min() * 100, a.max() * 100))
    if a.min() > 0:
        print("  倍数跨度 = %.1fx" % (a.max() / a.min()))
    print("\nelapsed = %.1fs" % (time.time() - t0))


def mode_period(args, t0):
    """换区间 —— 股票池固定不变，只让时间窗口变。"""
    pool = pick_pool(args.period_stocks)
    print("=== 换区间验证（生产口径，股票池固定 %d 只）===" % len(pool))
    print("    固定：策略 / 参数 / 成本模型 / 股票池；只换时间窗口\n")
    rows = []
    for item in str(args.periods).split(","):
        item = item.strip()
        if not item:
            continue
        s, e = item.split(":")
        try:
            r = run(pool, s, e)
        except Exception as exc:                    # noqa: BLE001
            print("  %-26s 跳过（%s: %s）" % (s + "~" + e, type(exc).__name__, exc),
                  flush=True)
            continue
        rows.append((s, e, r))
        print(_fmt_row(s + "~" + e, r), flush=True)
    if not rows:
        print("  没有可统计的区间")
        return
    sh = [r["sharpe"] for _, _, r in rows]
    rt = [r["ret"] for _, _, r in rows]
    print("\n  夏普：均值=%.2f 中位=%.2f min=%.2f max=%.2f"
          % (sum(sh) / len(sh), sorted(sh)[len(sh) // 2], min(sh), max(sh)))
    print("  收益：均值=%.2f%% 中位=%.2f%% min=%.2f%% max=%.2f%%"
          % (sum(rt) / len(rt) * 100, sorted(rt)[len(rt) // 2] * 100,
             min(rt) * 100, max(rt) * 100))
    print("\nelapsed = %.1fs" % (time.time() - t0))


def main():
    import argparse
    ap = argparse.ArgumentParser(description="JWZZ 对照实验（生产口径）")
    ap.add_argument("--mode", choices=["ab", "shuffle", "pool", "period"],
                    default="shuffle")
    ap.add_argument("--stocks", type=int, default=1500)
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--pool", type=int, default=30)
    ap.add_argument("--start", default="2023-11-16")
    ap.add_argument("--end", default="2026-11-16")
    ap.add_argument("--periods", default="2021-01-04:2021-12-31,"
                                        "2022-01-04:2022-12-30,"
                                        "2023-01-03:2023-12-29,"
                                        "2024-01-02:2024-12-31,"
                                        "2025-01-02:2025-12-31,"
                                        "2023-11-16:2026-11-16")
    ap.add_argument("--pool-seeds", default="1,2,3,4,5,6,7,8")
    ap.add_argument("--all-market", action="store_true")
    ap.add_argument("--period-stocks", type=int, default=0)
    args = ap.parse_args()

    t0 = time.time()
    if args.mode == "ab":
        return mode_ab(args, t0)
    if args.mode == "shuffle":
        return mode_shuffle(args, t0)
    if args.mode == "pool":
        return mode_pool(args, t0)
    return mode_period(args, t0)


if __name__ == "__main__":
    main()
