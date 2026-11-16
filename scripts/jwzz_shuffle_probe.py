# -*- coding: utf-8 -*-
"""同强势池打乱排序 —— 分离「集中度」与「选股质量」的判决性对照。

问题：`jwzz_cross`（每日取 JWZZ 最强的前 N 只）在 2023-11~2026-11 跑出 11591%。
    这 115 倍到底来自「按 JWZZ 降序排序」这个动作，还是仅来自
    「用 10% 仓位集中持有少数几只妖股」？

设计（唯一变量 = 打乱排序）：
    基准组  top10  ：当日 JWZZ **降序前 10**            （生产代码，一字不改）
    对照组  shuffle ：当日 JWZZ 降序**前 K 只里随机取 10**（K=30）

    两组选的都是**同样强势**的票（强势度分布几乎一致），
    唯一差别 = 「按 JWZZ 降序」这个动作被去掉。

判据：
    · 对照组收益也接近几千 %  → 排序没起作用（是 bug，必须立刻查）
    · 对照组接近 0 / 为负      → 115 倍 = 集中度赌法，JWZZ 排序无增量价值
                                  （那么 jwzz_hold 追不上它就不是缺陷）

口径：
    · 同子集（固定 seed 随机 1500 只，避免按代码序取样带来的上市顺序偏差）
    · 同区间、同参数、同成本模型；**基准与对照共用同一子集**
    · 全部走生产代码路径（strategies.jwzz_cross → base.signals_to_positions
      → stats.compute），复权 hfq
    · 对照组 = 读生产源码 → 只替换「取前 N」那 2 行 → exec，不复制其余逻辑

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

from strategies.core import base, data, stats          # noqa: E402
from strategies import jwzz_cross as JC                # noqa: E402
from formulas.jwzz import jwzz_panel                   # noqa: E402

COSTS = dict(initial_capital=100000.0, commission=0.0003, stamp_duty=0.001,
              slippage=0.0005, min_commission=5.0, lot_size=100)

N_STOCKS = 1500
POOL_SEED = 20261116
START, END = "2023-11-16", "2026-11-16"
PICK_POOL = 30          # 对照组：降序前 K 只里随机取 top_n 只
TOP_N = 10


def pick_pool(n=N_STOCKS, seed=POOL_SEED):
    codes = sorted(data.list_universe_codes())
    if n <= 0:                       # n<=0 = 全市场口径（不抽样）
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
    """读生产策略源码，只替换「取前 N」那 2 行 → 得到打乱排序的对照版。

    不复制其余逻辑：信号口径、状态机、权重、meta 全是生产代码那份。
    """
    path = os.path.join(ROOT, "strategies", "jwzz_cross.py")
    src = open(path, encoding="utf-8").read()
    if _OLD not in src:
        raise RuntimeError("生产源码与预期不符（取前 N 那两行没找到）—— 需要重新对齐")
    src = src.replace(_OLD, _NEW)
    # 去掉注册装饰器与相对导入，避免重复注册 / 相对导入失败
    src = re.sub(r"^from\s+\.[\w\.]+\s+import\s+.*$", "", src, flags=re.M)
    src = re.sub(r"^from\s+formulas\.[\w\.]+\s+import\s+.*$", "", src, flags=re.M)
    src = re.sub(r"^@strategy\(.*?^\)\s*$", "", src, flags=re.M | re.S)
    # 函数名改掉，避免与注册表里的名字混淆
    src = src.replace("def jwzz_cross(", "def jwzz_cross_shuffle(")
    # 签名加对照参数（pick_seed / pick_pool）
    src, n = re.subn(r"position:\s*float\s*=\s*1\.0\)\s*->\s*list:",
                      "position: float = 1.0, pick_seed: int = 0,\n"
                      "               pick_pool: int = 30) -> list:",
                      src)
    if n != 1:
        raise RuntimeError("签名替换失败（position: float = 1.0) -> list: 没匹配到）")
    # 在函数体开头插入：固定 seed 的随机源 + 对照参数
    src = src.replace(
        "    codes = [str(c).strip() for c in (ctx.codes or []) if str(c).strip()]",
        "    _rng = np.random.default_rng(pick_seed)\n"
        "    pick_pool = int(pick_pool)   # <=0 = 不截断（用当日全部强势票）\n"
        "    codes = [str(c).strip() for c in (ctx.codes or []) if str(c).strip()]")
    ns = {"np": np, "Signal": base.Signal, "ParamSpec": base.ParamSpec,
          "data": data, "DEFAULT_PERIOD": JC.DEFAULT_PERIOD,
          "jwzz_panel": jwzz_panel}
    exec(compile(src, "<jwzz_cross@shuffle>", "exec"), ns)
    fn = ns["jwzz_cross_shuffle"]
    # 把对照参数做成带默认值的包装
    def wrapped(ctx, cost_period=JC.DEFAULT_PERIOD, threshold=95.0,
                smooth_period=0, top_n=TOP_N, position=1.0,
                pick_seed=0, pick_pool=PICK_POOL):
        return fn(ctx, cost_period=cost_period, threshold=threshold,
                   smooth_period=smooth_period, top_n=top_n, position=position,
                   pick_seed=pick_seed, pick_pool=pick_pool)
    return wrapped


def run(fn, pool, params=None, **extra):
    ctx = base.StrategyContext(codes=list(pool), start=START, end=END, adjust="hfq")
    bars = data.load_bars(pool, START, END, fields=["close", "volume", "amount"],
                          adjust="hfq")
    close_by_code = {c: bars[c]["close"] for c in bars if c in bars}
    kw = dict(cost_period=13, threshold=95.0, smooth_period=0, top_n=TOP_N,
              position=1.0)
    if params:
        kw.update(params)
    kw.update(extra)
    sigs = fn(ctx, **kw)
    pos = base.signals_to_positions(sigs, close_by_code)
    core = stats.compute(pos, close_by_code, **COSTS)
    m = core["metrics"]
    return {
        "ret": float(m.get("total_return") or 0.0),
        "sharpe": float(m.get("sharpe") or 0.0),
        "mdd": float(m.get("max_drawdown") or 0.0),
        "util": float(m.get("capital_utilization") or 0.0),
        "avg_pos": float(m.get("avg_positions") or 0.0),
        "trades": float(m.get("num_trades_eff") or 0.0),
        "hold": float(m.get("avg_holding_days") or 0.0),
    }


def _mode_pool(args, t0):
    """基准组在不同**股票池抽样**下的收益分布 —— 量化「同策略同区间」的抽样噪声。

    固定：策略 / 参数 / 区间 / 成本模型全同；只换「抽到哪些票」。
    """
    seeds = [int(x) for x in str(args.pool_seeds).split(",") if str(x).strip()]
    print("=== 基准组（jwzz_cross top_n=%d）在不同股票池抽样下的收益分布 ===" % TOP_N)
    print("    固定：策略 / 参数 / 区间 %s ~ %s / 成本模型全同；只换「抽到哪些票」" % (START, END))
    print("    每个子集 %d 只（随机抽）\n" % args.stocks)
    rets = []
    for ps in seeds:
        pool = pick_pool(args.stocks, seed=ps)
        r = run(JC.jwzz_cross, pool)
        rets.append(r["ret"])
        print("  pool_seed=%-3d 子集%4d只  ret=%10.2f%%  sharpe=%6.2f  mdd=%7.2f%%  trades=%.0f"
              % (ps, len(pool), r["ret"] * 100, r["sharpe"], r["mdd"] * 100, r["trades"]),
              flush=True)
    if args.all_market:
        pool = pick_pool(0)
        r = run(JC.jwzz_cross, pool)
        print("  全市场     %4d只  ret=%10.2f%%  sharpe=%6.2f  mdd=%7.2f%%  trades=%.0f"
              % (len(pool), r["ret"] * 100, r["sharpe"], r["mdd"] * 100, r["trades"]),
              flush=True)
        rets.append(r["ret"])
    a = np.array(rets)
    print("\n  收益分布：n=%d  中位=%9.2f%%  均值=%9.2f%%  min=%9.2f%%  max=%9.2f%%"
          % (len(a), np.median(a) * 100, a.mean() * 100, a.min() * 100, a.max() * 100))
    if a.min() > 0:
        print("  倍数跨度 = %.1fx   （同为「生产代码 + 同区间」，只因股票池抽样不同）"
              % (a.max() / a.min()))
    print("  → 离散度极大 ⇒ 单次跑出的高倍数是**抽样产物**，不是策略性质")
    print("\nelapsed = %.1fs" % (time.time() - t0))


def main():
    global START, END, PICK_POOL
    import argparse
    ap = argparse.ArgumentParser(description="同强势池打乱排序对照")
    ap.add_argument("--stocks", type=int, default=N_STOCKS)
    ap.add_argument("--seeds", type=int, default=3, help="对照组随机种子个数")
    ap.add_argument("--pool", type=int, default=PICK_POOL, help="对照池 K（降序前 K 只里随机取）")
    ap.add_argument("--start", default=START)
    ap.add_argument("--end", default=END)
    ap.add_argument("--mode", choices=["shuffle", "pool"], default="shuffle",
                    help="shuffle=打乱排序对照；pool=换股票池抽样看收益分布")
    ap.add_argument("--pool-seeds", default="1,2,3,4,5,6,7,8",
                    help="mode=pool 时要跑的股票池随机种子")
    ap.add_argument("--all-market", action="store_true",
                    help="mode=pool 时额外跑一次全市场口径作参照")
    args = ap.parse_args()

    START, END, PICK_POOL = args.start, args.end, args.pool

    t0 = time.time()
    if args.mode == "pool":
        return _mode_pool(args, t0)

    pool = pick_pool(args.stocks)
    print("子集 %d 只 | 区间 %s ~ %s | top_n=%d | 对照池 K=%d" %
          (len(pool), START, END, TOP_N, PICK_POOL), flush=True)

    print("\n=== 基准组：JWZZ 降序前 10（生产代码）===", flush=True)
    b = run(JC.jwzz_cross, pool)
    print("  ret=%9.2f%%  sharpe=%6.2f  mdd=%7.2f%%  util=%.3f  avgPos=%.2f"
          "  trades=%.0f  持有=%.1f天" % (b["ret"] * 100, b["sharpe"], b["mdd"] * 100,
                                       b["util"], b["avg_pos"], b["trades"], b["hold"]),
          flush=True)

    sh = load_shuffle_variant()
    for K in (args.pool, 0):
        label = ("降序前 %d 只里随机取 %d 只" % (K, TOP_N)) if K > 0 else \
                ("当日**全部**强势票里随机取 %d 只" % TOP_N)
        print("\n=== 对照组：%s（只打乱排序）===" % label, flush=True)
        results = []
        for seed in range(1, args.seeds + 1):
            r = run(sh, pool, pick_seed=seed, pick_pool=K)
            results.append(r)
            print("  seed=%d  ret=%9.2f%%  sharpe=%6.2f  mdd=%7.2f%%  util=%.3f"
                  "  avgPos=%.2f  trades=%.0f" % (seed, r["ret"] * 100, r["sharpe"],
                                                 r["mdd"] * 100, r["util"], r["avg_pos"],
                                                 r["trades"]), flush=True)
        rets = [r["ret"] for r in results]
        mean = sum(rets) / len(rets)
        print("  → 均值=%9.2f%%   基准组=%9.2f%%   比值=%.4f" % (
            mean * 100, b["ret"] * 100, mean / b["ret"] if b["ret"] else float("nan")),
            flush=True)

    print("\n  判读（三组）：")
    print("   基准   = 降序前 10（生产代码）")
    print("   对照A  = 前 K 只里随机取 10  → 只去掉「降序」")
    print("   对照B  = 全部强势票随机取 10 → 再去掉「极端程度筛选」")
    print("   · 对照A ≈ 基准  → 「降序」这个动作没有价值")
    print("   · 对照B ≈ 0/负  → 「极端程度筛选」也没有价值，全靠「只持 10 只」+ 行情")
    print("\nelapsed = %.1fs" % (time.time() - t0))


if __name__ == "__main__":
    main()
