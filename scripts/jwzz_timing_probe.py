# -*- coding: utf-8 -*-
"""`jwzz_cross_timing`（大盘择时）对照实验 —— **生产口径**执行器。

⚠️ 为什么必须走 `backtest.run_backtest`：
    直接调 `strategies.xxx` + `stats.compute` 会漏掉编排层的两件事：
      1. `universe.filter_available` —— 区间内行情不足的票要被剔除（注入判定）；
      2. `raw_by_code`（未复权 OHLCV）→ 启用**严格成交模型**
         （一字涨停买不进即空仓、不重分配；一字跌停卖不出即延后清仓）。
    漏掉它 = 把「买不进」当成「买到了」→ 收益系统性偏高。

## 股票池口径（与产品跑出的报告完全一致）

    start=2023-11-17, end=2026-11-17
    exclude = ipo / micro / st / star_st / sub_new
    as_of  = 区间起点（2023-11-17）→ 4101 只（base 5589）

## 横向比只看这四个（见《回测可信度纪律》§一.3）

    夏普 / 每单位动用资金收益(rpu) / 最大回撤 / 换手
    **不看总收益** —— 它由「池子里恰好有哪些妖股」决定，换一次抽样能差两个数量级。

模式：
    --mode scan        参数扫描（大盘序列 × MA 周期 × 下跌仓位/持仓上限）
    --mode determinism 引擎确定性（同参数跑两次，净值逐位比对）
    --mode segment     跨子区间一致性（同一参数切 3 段分别跑）
    --mode pool        换池重复（固定参数/区间，只换随机抽到的股票池）
    --mode shuffle     同池打乱排序（分离「集中度」与「选股质量」）

⚠️ 结果**不落盘**到 backtests/（避免污染用户的报告目录）；只打印。
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time
import warnings

import numpy as np
import pandas as pd

# 择时策略会**刻意**清仓（大盘转弱 → 发 weight=0），引擎的「持仓骤降守卫」会为
# 每次清仓打一条 RuntimeWarning —— 那是**预期行为**，不是 bug，屏蔽掉免得刷屏。
warnings.filterwarnings("ignore", category=RuntimeWarning)

ROOT = r"c:\Users\Administrator\Desktop\stockanaly\backend_fastapi"
sys.path.insert(0, ROOT)
os.chdir(ROOT)
sys.stdout.reconfigure(encoding="utf-8")

from strategies.core import base, data, backtest, universe   # noqa: E402

START = "2023-11-17"
END = "2026-11-17"
# ⚠️ **故意不剔除 ST / *ST**：ST 名单要联网取，**取不到时静默降级（不剔除）** →
# 同一个 spec 在不同时间会跑出**不同的股票池**（实测：产品出报告时 4101 只，
# 联网取到名单后 3975 只，差 126 只）。剔除集只留「不依赖网络」的三项：
# 新股 / 次新 / 微盘（都只依赖本地库）→ 池子**确定**且与产品那 12 份报告**完全一致**
# （dropped = ipo 429 / sub_new 311 / micro 865，filtered 4101）。
EXCLUDE = ["ipo", "micro", "sub_new"]

BASE_PARAMS = dict(cost_period=13, threshold=95.0, smooth_period=0, top_n=10,
                   position=1.0)
TIMING_PARAMS = dict(cost_period=13, threshold=95.0, smooth_period=0, top_n=10,
                      position=1.0, market_code="sh000001", ma_period=20,
                      down_position=0.0, down_top_n=0)


# --------------------------------------------------------------------------- #
# 股票池
# --------------------------------------------------------------------------- #
def prod_pool(exclude=None, start=START, end=END, stocks=0, seed=20261116):
    """生产口径的股票池（**用股票池层的唯一入口**构造 spec）。

    `stocks > 0` → 从生产池里**固定 seed** 随机抽 n 只（换池重复用）。
    ⚠️ 抽出来的子池**不再是**「生产口径的池子」—— 结论只能在该子池内部横比，
    不能直接与产品跑出的 4101 只报告对比。
    """
    spec = universe.spec_from_request({"start": start, "end": end,
                                        "exclude": list(exclude or EXCLUDE)})
    codes, detail = universe.build_detail(spec)
    codes = list(codes)
    if stocks and stocks > 0 and stocks < len(codes):
        rng = np.random.default_rng(seed)
        idx = rng.choice(len(codes), size=stocks, replace=False)
        codes = sorted(codes[i] for i in idx)
    return codes, detail


def pick_pool(n=0, seed=20261116):
    """从生产池里随机抽 n 只（换池重复用；n<=0 = 全池）。"""
    codes, _ = prod_pool(stocks=n, seed=seed)
    return codes


# --------------------------------------------------------------------------- #
# 跑一次回测
# --------------------------------------------------------------------------- #
def _num(v, default=0.0):
    if isinstance(v, (list, tuple)):
        v = v[0] if v else default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def run(strategy_id, params, pool, start=START, end=END, benchmark=None):
    """跑一次回测（生产编排层），返回**扁平指标**。"""
    res = backtest.run_backtest(strategy_id, dict(params), list(pool), start, end,
                                universe_mode="interval_data", benchmark=benchmark)
    m = res["metrics"]
    return {
        "ret": _num(m.get("total_return")),
        "sharpe": _num(m.get("sharpe")),
        "mdd": _num(m.get("max_drawdown")),
        "rpu": _num(m.get("return_per_utilized_capital"), float("nan")),
        "turnover": _num(m.get("turnover_annual")),
        "util": _num(m.get("capital_utilization")),
        "avg_pos": _num(m.get("avg_positions")),
        "trades": _num(m.get("num_trades_eff")),
        "hold": _num(m.get("avg_holding_days")),
        "bench": _num(m.get("benchmark_return")),
        "alpha": m.get("alpha"), "beta": m.get("beta"),
        "codes": int(_num(res["scope"].get("codes_count"))),
        "equity": res.get("equity") or [],
        "dates": res.get("dates") or [],
        "warnings": res["scope"].get("warnings") or [],
        "errors": res.get("errors") or [],
        "feas": res["scope"].get("feasibility") or {},
    }


def fmt(label, r):
    return ("  %-34s 夏普=%6.3f  rpu=%9.2f  回撤=%7.2f%%  换手=%6.1f  利用=%.3f"
            "  持仓=%5.2f  笔数=%6.0f  持有=%.1f天"
            % (label, r["sharpe"], r["rpu"], r["mdd"] * 100, r["turnover"],
               r["util"], r["avg_pos"], r["trades"], r["hold"]))


# --------------------------------------------------------------------------- #
# 模式
# --------------------------------------------------------------------------- #
def mode_scan(args, t0):
    """参数扫描：大盘序列 × MA 周期 × 下跌仓位/持仓上限。"""
    pool, detail = prod_pool(stocks=args.stocks)
    print("=== 参数扫描（生产口径）===")
    print("    池 %d 只%s | 区间 %s ~ %s | 基准组 top_n=%d 满仓"
          % (len(pool), "" if not args.stocks else "（抽样子池）", START, END,
             BASE_PARAMS["top_n"]))
    print("    ⚠️ 横向只看 夏普/rpu/回撤/换手（不看总收益）\n")

    b = run("jwzz_cross", BASE_PARAMS, pool)
    print(fmt("基准 jwzz_cross top_n=10 满仓", b), flush=True)

    for mkt in args.markets:
        for ma in args.ma_periods:
            for dpos, dtop in args.down:
                p = dict(TIMING_PARAMS)
                p.update(market_code=mkt, ma_period=ma,
                         down_position=dpos, down_top_n=dtop)
                try:
                    r = run("jwzz_cross_timing", p, pool)
                except Exception as exc:                   # noqa: BLE001
                    print("  %-34s ❌ %s: %s" % (mkt, type(exc).__name__, exc),
                          flush=True)
                    continue
                tag = "%s MA%d 下跌仓位%.2f/上限%d" % (mkt, ma, dpos, dtop)
                print(fmt(tag, r), flush=True)
                print("        Δ夏普=%+.3f  Δ回撤=%+.2f%%  Δ换手=%+.1f"
                      % (r["sharpe"] - b["sharpe"],
                         (r["mdd"] - b["mdd"]) * 100,
                         r["turnover"] - b["turnover"]), flush=True)
    print("\nelapsed = %.1fs" % (time.time() - t0))


def mode_determinism(args, t0):
    """闸一：同参数跑两次，净值逐位一致？"""
    pool, _ = prod_pool(stocks=args.stocks)
    p = dict(TIMING_PARAMS)
    p.update(market_code=args.markets[0], ma_period=args.ma_periods[0])
    print("=== 引擎确定性（同参数两次，生产口径）===")
    a = run("jwzz_cross_timing", p, pool)
    b = run("jwzz_cross_timing", p, pool)
    same = a["equity"] == b["equity"] and a["dates"] == b["dates"]
    print("  净值长度 %d / %d" % (len(a["equity"]), len(b["equity"])))
    print("  逐位一致：%s" % ("✅ 是" if same else "❌ 否（引擎不确定，先修引擎）"))
    if not same:
        diff = sum(1 for x, y in zip(a["equity"], b["equity"]) if x != y)
        print("  不同的点：%d / %d" % (diff, len(a["equity"])))
    print(fmt("run#1", a))
    print(fmt("run#2", b))
    print("\nelapsed = %.1fs" % (time.time() - t0))


def mode_segment(args, t0):
    """跨子区间一致性：同一参数切 3 段分别跑，看改善方向是否一致。"""
    pool, _ = prod_pool(stocks=args.stocks)
    p = dict(TIMING_PARAMS)
    p.update(market_code=args.markets[0], ma_period=args.ma_periods[0],
             down_position=args.down[0][0], down_top_n=args.down[0][1])
    print("=== 跨子区间一致性（生产口径）===")
    print("    参数：%s\n" % p)

    # 3 段等分（按自然日切，段内仍走同一套编排层）
    y0 = int(START[:4])
    bounds = [START, "%d-07-01" % (y0 + 1), "%d-01-01" % (y0 + 2), END]
    rows = []
    for i in range(3):
        s, e = bounds[i], bounds[i + 1]
        try:
            rb = run("jwzz_cross", BASE_PARAMS, pool, s, e)
            rt = run("jwzz_cross_timing", p, pool, s, e)
        except Exception as exc:                          # noqa: BLE001
            print("  段 %s~%s 跳过（%s: %s）" % (s, e, type(exc).__name__, exc))
            continue
        rows.append((s, e, rb, rt))
        print("  段%d %s ~ %s" % (i + 1, s, e))
        print(fmt("    基准", rb))
        print(fmt("    择时", rt))
        print("        Δ夏普=%+.3f  Δ回撤=%+.2f%%  Δ换手=%+.1f"
              % (rt["sharpe"] - rb["sharpe"], (rt["mdd"] - rb["mdd"]) * 100,
                 rt["turnover"] - rb["turnover"]), flush=True)
    if rows:
        ds = [rt["sharpe"] - rb["sharpe"] for _, _, rb, rt in rows]
        pos = sum(1 for x in ds if x > 0)
        print("\n  夏普改善方向：%d/%d 段为正  → %s"
              % (pos, len(ds), "一致" if pos in (0, len(ds)) else "**不一致**"))
        print("  Δ夏普 = %s" % ["%+.3f" % x for x in ds])
    print("\nelapsed = %.1fs" % (time.time() - t0))


def mode_pool(args, t0):
    """换池重复：固定策略/参数/区间，只换随机抽到的股票池。"""
    print("=== 换池重复（生产口径，参数固定）===")
    p = dict(TIMING_PARAMS)
    p.update(market_code=args.markets[0], ma_period=args.ma_periods[0],
             down_position=args.down[0][0], down_top_n=args.down[0][1])
    rows = []
    for seed in args.pool_seeds:
        pool = pick_pool(args.stocks or 1500, seed=seed)
        try:
            rb = run("jwzz_cross", BASE_PARAMS, pool)
            rt = run("jwzz_cross_timing", p, pool)
        except Exception as exc:                          # noqa: BLE001
            print("  seed=%d 跳过（%s: %s）" % (seed, type(exc).__name__, exc))
            continue
        rows.append((seed, rb, rt))
        print("  seed=%d 池=%d 只" % (seed, len(pool)))
        print(fmt("    基准", rb))
        print(fmt("    择时", rt))
        print("        Δ夏普=%+.3f" % (rt["sharpe"] - rb["sharpe"]), flush=True)
    if rows:
        ds = np.array([rt["sharpe"] - rb["sharpe"] for _, rb, rt in rows])
        rs = np.array([rt["sharpe"] for _, _, rt in rows])
        bs = np.array([rb["sharpe"] for _, rb, _ in rows])
        print("\n  Δ夏普：均值=%+.3f 中位=%+.3f min=%+.3f max=%+.3f"
              % (ds.mean(), np.median(ds), ds.min(), ds.max()))
        print("  基准夏普：均值=%.3f min=%.3f max=%.3f"
              % (bs.mean(), bs.min(), bs.max()))
        print("  择时夏普：均值=%.3f min=%.3f max=%.3f"
              % (rs.mean(), rs.min(), rs.max()))
        print("  → %d/%d 组改善" % (int((ds > 0).sum()), len(ds)))
    print("\nelapsed = %.1fs" % (time.time() - t0))


def _load_shuffle_variant():
    """产出「只打乱排序」的择时策略函数；其余口径（含大盘择时）全部沿用生产源码。"""
    import strategies.jwzz_cross_timing as JC
    src = open(os.path.join(ROOT, "strategies", "jwzz_cross_timing.py"),
               encoding="utf-8").read()
    old = ('            order = day_bull[np.argsort(-strength_m[day_bull, t], kind="stable")]\n'
           '            picks = {cols[i] for i in order[:tgt_n]}')
    new = ('            order = day_bull[np.argsort(-strength_m[day_bull, t], kind="stable")]\n'
           '            pool_idx = order if pick_pool <= 0 else order[:pick_pool]\n'
           '            _rng.shuffle(pool_idx)\n'
           '            picks = {cols[i] for i in pool_idx[:tgt_n]}')
    if old not in src:
        raise RuntimeError("生产源码与预期不符（取前 N 那两行没找到）—— 需要重新对齐")
    src = src.replace(old, new)
    src = src.replace("def jwzz_cross_timing(", "def jwzz_cross_timing_shuffle(")
    src = src.replace("                      down_position: float = 0.0, down_top_n: int = 0) -> list:",
                      "                      down_position: float = 0.0, down_top_n: int = 0,\n"
                      "                      pick_seed: int = 0, pick_pool: int = 30) -> list:")
    src = src.replace('    mkt = str(market_code or "sh000001").strip()',
                      '    mkt = str(market_code or "sh000001").strip()\n'
                      '    _rng = np.random.default_rng(pick_seed)\n'
                      '    pick_pool = int(pick_pool)')
    ns = {"np": np, "pd": pd, "Signal": base.Signal, "ParamSpec": base.ParamSpec,
          "data": data, "market": JC.market, "dt": dt,
          "DEFAULT_PERIOD": 13, "jwzz_panel": JC.jwzz_panel}
    exec(compile(src, "<jwzz_cross_timing@shuffle>", "exec"), ns)
    return ns["jwzz_cross_timing_shuffle"]


def mode_shuffle(args, t0):
    """同池打乱排序：分离「集中度」与「选股质量」。

    只在**择时策略**上做对照：把「当日强势票按 JWZZ 降序取前 N」换成
    「同一候选池里随机取 N」，其余口径（含大盘择时）全部不动。
    """
    pool, _ = prod_pool(stocks=args.stocks)
    print("=== 同池打乱排序（生产口径）===")
    print("    池 %d 只 | 区间 %s ~ %s\n" % (len(pool), START, END))

    p = dict(TIMING_PARAMS)
    p.update(market_code=args.markets[0], ma_period=args.ma_periods[0])
    b = run("jwzz_cross_timing", p, pool)
    print(fmt("基准（JWZZ 降序）", b), flush=True)

    fn = _load_shuffle_variant()
    orig_run = base.run
    try:
        for seed in range(1, args.seeds + 1):
            for K in (args.pool, 0):
                def patched(strategy_id, ctx, params_, _fn=fn, _seed=seed, _K=K):
                    if strategy_id != "jwzz_cross_timing":
                        return orig_run(strategy_id, ctx, params_)
                    pp = dict(p)
                    pp["pick_seed"] = _seed
                    pp["pick_pool"] = _K
                    return _fn(ctx, **pp)
                base.run = patched
                res = backtest.run_backtest("jwzz_cross_timing", dict(p), list(pool),
                                            START, END, universe_mode="interval_data")
                m = res["metrics"]
                r = {"sharpe": _num(m.get("sharpe")),
                     "rpu": _num(m.get("return_per_utilized_capital"), float("nan")),
                     "mdd": _num(m.get("max_drawdown")),
                     "turnover": _num(m.get("turnover_annual")),
                     "util": _num(m.get("capital_utilization")),
                     "avg_pos": _num(m.get("avg_positions")),
                     "trades": _num(m.get("num_trades_eff")),
                     "hold": _num(m.get("avg_holding_days"))}
                tag = ("降序前 %d 只里随机取 %d 只" % (K, p["top_n"])) if K > 0 \
                    else "当日全部强势票里随机取 %d 只" % p["top_n"]
                print(fmt("  seed=%d %s" % (seed, tag), r), flush=True)
    finally:
        base.run = orig_run
    print("\nelapsed = %.1fs" % (time.time() - t0))


def main():
    ap = argparse.ArgumentParser(description="jwzz_cross_timing 对照实验（生产口径）")
    ap.add_argument("--mode", choices=["scan", "determinism", "segment", "pool",
                                       "shuffle"], default="scan")
    ap.add_argument("--markets", nargs="*", default=["sh000001"])
    ap.add_argument("--ma-periods", nargs="*", type=int, default=[20])
    ap.add_argument("--down", nargs="*", default=["0:0"],
                    help="下跌仓位:下跌持仓上限（如 0:0 / 0.5:0 / 0:3）")
    ap.add_argument("--stocks", type=int, default=0,
                    help="子池大小（0=全池 4101；换池重复模式默认用 1500）")
    ap.add_argument("--pool-seeds", nargs="*", type=int, default=[1, 2, 3])
    ap.add_argument("--seeds", type=int, default=3, help="打乱排序的 seed 数")
    ap.add_argument("--pool", type=int, default=30, help="打乱排序的候选池大小")
    args = ap.parse_args()

    args.down = [tuple(float(x) if "." in x else int(x)
                       for x in str(item).split(":")) for item in args.down]

    t0 = time.time()
    if args.mode == "scan":
        return mode_scan(args, t0)
    if args.mode == "determinism":
        return mode_determinism(args, t0)
    if args.mode == "segment":
        return mode_segment(args, t0)
    if args.mode == "pool":
        return mode_pool(args, t0)
    return mode_shuffle(args, t0)


if __name__ == "__main__":
    main()
