# -*- coding: utf-8 -*-
"""JWZZ × 大盘状态 × top_n 分析（**探索性**，无未来信息、不改策略）。

⚠️ 执行路径：只读**产品跑出来的报告**（`backtests/*.json`）的日净值与逐笔成交，
   不重跑回测 → 与产品口径完全一致。
   口径自证：强制校验所有报告的 `spec_version` / `spec_signature` 同版，不同版直接退出。

回答的问题（见 docs/jwzz-backtest-2026-11-16.md §五.3）：
    大盘涨的时候，top_n 该多还是少？

用法：
    python scripts/jwzz_market_state.py                # 默认取最新一批 jwzz_cross 报告
    python scripts/jwzz_market_state.py --date 20261117

⚠️ 「动态切 top_n」的拼接模拟是**近似**：把不同 n 的日收益按规则拼起来，
   不含换仓成本与持仓状态转移 → 只读方向，不作收益承诺。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from collections import Counter

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = r"c:\Users\Administrator\Desktop\stockanaly\backend_fastapi"
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
sys.stdout.reconfigure(encoding="utf-8")

from core import config  # noqa: E402


def p(s=""):
    print(s)


def load_reports(reports_dir, date=None):
    """载入一批 `jwzz_cross` 报告（按 top_n 归并）。"""
    files = sorted(glob.glob(os.path.join(reports_dir, "backtest-jwzz_cross-*.json")))
    if not files:
        raise SystemExit("没找到 jwzz_cross 报告：%s" % reports_dir)
    groups = {}
    for f in files:
        r = json.load(open(f, encoding="utf-8"))
        if r.get("strategy", {}).get("id") != "jwzz_cross":
            continue
        g = (r.get("generated_at") or "")[:10]           # YYYY-MM-DD
        if date and not g.replace("-", "").endswith(str(date)):
            continue
        groups.setdefault(g, []).append((f, r))
    if not groups:
        raise SystemExit("没有匹配日期 %s 的 jwzz_cross 报告" % date)
    key = sorted(groups)[-1]
    picked = groups[key]
    # 口径同版校验（纪律：不同版不可横比）
    sigs = {(r["scope"].get("exclusion") or {}).get("spec_version") for _, r in picked}
    fsigs = {(r["scope"].get("exclusion") or {}).get("spec_signature") for _, r in picked}
    if len(sigs) > 1 or len(fsigs) > 1:
        raise SystemExit("这批报告口径不同版，禁止横比：spec=%s sig=%s" % (sigs, fsigs))
    p("批次日期：%s   报告 %d 份   spec=%s   sig=%s"
      % (key, len(picked), next(iter(sigs)), next(iter(fsigs))))
    return picked


def main():
    ap = argparse.ArgumentParser(description="JWZZ × 大盘状态 × top_n 分析")
    ap.add_argument("--reports-dir", default=str(config.REPORTS_DIR))
    ap.add_argument("--date", default=None, help="YYYYMMDD，默认取最新批次")
    args = ap.parse_args()

    picked = load_reports(args.reports_dir, args.date)
    reps = {}
    for f, r in picked:
        n = int(r["strategy"]["params"]["top_n"])
        reps[n] = {"eq": np.array(r["equity"], float),
                   "bm": np.array(r["equity_benchmark"], float),
                   "trades": r.get("trades") or [],
                   "dates": r["dates"],
                   "feas": r["scope"].get("feasibility") or {}}
    dates = reps[max(reps)]["dates"]
    if any(v["dates"] != dates for v in reps.values()):
        raise SystemExit("日期轴不一致，禁止横比")
    ND = len(dates)
    dates_r = dates[1:]
    bm0 = reps[max(reps)]["bm"]
    bm_r = bm0[1:] / bm0[:-1] - 1
    up = bm_r > 0
    R = {n: reps[n]["eq"][1:] / reps[n]["eq"][:-1] - 1 for n in reps}

    def buy_counts(n):
        c = Counter(t.get("buy_date") for t in reps[n]["trades"] if t.get("buy_date"))
        return np.array([c.get(d, 0) for d in dates_r], float)

    w10, w30 = buy_counts(10), buy_counts(30)
    # 「买入被挡」比例用**报告自带**的成交约束统计（口径权威）；
    # n=30 的每日买入笔数只用作「候选池比 top_n 宽多少」的上界参考。
    feas = reps[10]["feas"] if 10 in reps else {}
    fb = feas.get("fill_blocked") or {}
    n_tr = int(feas.get("trades") or 0)
    blk = int(fb.get("buy_blocked") or 0)

    p()
    p("=" * 92)
    p("【0】大盘 = 同池等权基准（报告自带）；日期轴 %s ~ %s（%d 个交易日）"
      % (dates[0], dates[-1], ND))
    p("=" * 92)
    p("  大盘涨日 %d / 跌日 %d；区间累计 %+.2f%%"
      % (up.sum(), (~up).sum(), (bm0[-1] / bm0[0] - 1) * 100))
    if n_tr:
        p("  成交约束（n=10 报告自带）：买入被挡 %d / %d 笔 = **%.1f%%**（一字涨停买不进即空仓）；"
          "卖出延后 %d 笔"
          % (blk, n_tr, blk / n_tr * 100, int(fb.get("sell_deferred") or 0)))
    p("  n=10 每日实际买入 %.2f 只   n=30 每日 %.2f 只   → 候选池远宽于 top_n，"
      "但能买到的只有三分之一" % (w10.mean(), w30.mean()))
    p("  涨日均 %.2f 只 vs 跌日均 %.2f 只（corr(可买候选数, 大盘日收益) = %+.4f）"
      % (w10[up].mean(), w10[~up].mean(), float(np.corrcoef(w30, bm_r)[0, 1])))
    p()

    p("=" * 92)
    p("【1】涨日 / 跌日 里 n 的排序（组内累计收益）")
    p("=" * 92)
    p("%-5s %14s %9s %12s %9s" % ("n", "涨日累计%", "涨日夏普", "跌日累计%", "跌日夏普"))
    rows = []
    for n in sorted(reps):
        out = [n]
        for m in (up, ~up):
            x = R[n][m]
            sd = x.std(ddof=1)
            out.append((np.prod(1 + x) - 1) * 100)
            out.append(x.mean() / sd * np.sqrt(252) if sd > 0 else float("nan"))
        rows.append(out)
    for r in sorted(rows, key=lambda z: -z[1]):
        p("%-5d %14.2f %9.3f %12.2f %9.3f" % tuple(r))
    p()

    p("=" * 92)
    p("【2】beta 分解")
    p("=" * 92)
    p("%-5s %8s %12s %10s %10s %10s" % ("n", "beta", "年化alpha%", "corr", "σ策略%", "σ大盘%"))
    for n in sorted(reps):
        r = R[n]
        beta = float(np.cov(r, bm_r, ddof=1)[0, 1] / np.var(bm_r, ddof=1))
        a = r.mean() - beta * bm_r.mean()
        p("%-5d %8.3f %12.2f %10.4f %10.4f %10.4f"
          % (n, beta, a * 252 * 100, float(np.corrcoef(r, bm_r)[0, 1]),
             r.std(ddof=1) * 100, bm_r.std(ddof=1) * 100))
    p()

    p("=" * 92)
    p("【3】按「可买候选数」分组（外生变量：n=30 当日买入笔数），n=10 的表现")
    p("=" * 92)
    p("%-14s %6s %12s %9s %8s" % ("可买候选数", "N", "累计%", "夏普", "胜率%"))
    qs = np.percentile(w30, [0, 20, 40, 60, 80, 100])
    for i in range(5):
        lo, hi = qs[i], qs[i + 1]
        m = (w30 >= lo) & (w30 <= hi) if i == 4 else (w30 >= lo) & (w30 < hi)
        if m.sum() < 5:
            continue
        x = R[10][m]
        sd = x.std(ddof=1)
        p("%-14s %6d %12.2f %9.3f %8.1f"
          % ("%.0f~%.0f 只" % (lo, hi), m.sum(), (np.prod(1 + x) - 1) * 100,
             x.mean() / sd * np.sqrt(252) if sd > 0 else float("nan"), (x > 0).mean() * 100))
    p()

    p("=" * 92)
    p("【4】动态切 top_n（拼接近似，不含换仓成本）")
    p("=" * 92)

    def sim(mask_fn, label):
        r = np.zeros(ND - 1)
        for n in reps:
            m = mask_fn(n)
            r[m] = R[n][m]
        eq = np.cumprod(1 + r) * 100000.0
        rets = np.diff(eq) / eq[:-1]
        mdd = float((eq / np.maximum.accumulate(eq) - 1).min() * 100)
        return (label, (eq[-1] / eq[0] - 1) * 100,
                rets.mean() / rets.std(ddof=1) * np.sqrt(252), mdd, (rets > 0).mean() * 100)

    def fixed(k):
        def f(n):
            return np.ones(ND - 1, bool) if n == k else np.zeros(ND - 1, bool)
        return f

    def dyn(u, d):
        def f(n):
            return (up & (n == u)) | (~up & (n == d))
        return f

    for lab, fn in [("固定 n=10（现状）", fixed(10)), ("固定 n=12", fixed(12)),
                    ("涨→10 / 跌→5", dyn(10, 5)), ("涨→10 / 跌→3", dyn(10, 3)),
                    ("涨→10 / 跌→2", dyn(10, 2)), ("涨→8 / 跌→5", dyn(8, 5)),
                    ("涨→12 / 跌→5", dyn(12, 5)), ("涨→13 / 跌→8", dyn(13, 8)),
                    ("涨→20 / 跌→8", dyn(20, 8)), ("涨→30 / 跌→10", dyn(30, 10))]:
        lab2, ret, sh, mdd, win = sim(fn, lab)
        p("  %-22s 收益 %9.2f%%  夏普 %6.3f  回撤 %7.2f%%  胜率 %5.1f%%"
          % (lab2, ret, sh, mdd, win))
    p()

    p("=" * 92)
    p("【5】跨子区间一致性（区间切 3 段）")
    p("=" * 92)
    # 分段边界固定 0 / 242 / 484 / 末（与文档 §五.3 一致，便于对账）
    bounds = [0, 242, 484, ND - 1]
    for a_n in (5, 7, 13, 20):
        d = R[a_n] - R[10]
        p("  --- n=%d 相对 n=10 的日收益差（bp/日, t）---" % a_n)
        for i in range(3):
            s = bounds[i]
            e = bounds[i + 1]
            seg = np.zeros(ND - 1, bool)
            seg[s:e] = True
            for gname, gm in (("涨", up & seg), ("跌", (~up) & seg)):
                x = d[gm]
                if len(x) < 5:
                    continue
                mm, ss = x.mean(), x.std(ddof=1)
                t = mm / (ss / np.sqrt(len(x))) if ss > 0 else float("nan")
                p("      %s ~ %s  %s日 N=%3d  均值 %+7.2f bp/日  t=%+5.2f"
                  % (dates_r[s], dates_r[e - 1], gname, len(x), mm * 1e4, t))
        p()


    p("=" * 96)
    p("【6】信号排名的边际收益曲线（n=30 报告：rank 1~30 齐全，无截断偏差）")
    p("=" * 96)
    date_idx = {d: i for i, d in enumerate(dates)}
    rows = []
    for t in (reps[30]["trades"] if 30 in reps else []):
        rk = (t.get("buy_meta") or {}).get("rank")
        bd = t.get("buy_date")
        if rk is None or bd not in date_idx:
            continue
        i = date_idx[bd]
        if i == 0 or i >= ND or t.get("ret") is None:
            continue
        rows.append({"rank": int(rk), "ret": float(t["ret"]),
                     "pnl": float(t.get("pnl") or 0.0), "up": bool(up[i - 1])})
    p("  可用逐笔 %d 笔（单票 ret 已是**净**收益：含该笔全部成本）" % len(rows))
    p()
    BUCKETS = [(1, 2), (3, 3), (4, 5), (6, 7), (8, 10), (11, 13), (14, 20), (21, 30)]
    for gname, gsel in (("大盘涨日", True), ("大盘跌日", False)):
        sub = [x for x in rows if x["up"] == gsel]
        p("  --- %s（%d 笔）---" % (gname, len(sub)))
        p("  %-10s %7s %13s %10s %9s %12s"
          % ("rank 桶", "笔数", "平均单票ret", "中位ret", "胜率%", "累计pnl"))
        for lo, hi in BUCKETS:
            g = [x for x in sub if lo <= x["rank"] <= hi]
            if not g:
                continue
            rets = np.array([x["ret"] for x in g])
            p("  %-10s %7d %12.2f%% %9.2f%% %8.1f%% %12.0f"
              % ("%d~%d" % (lo, hi), len(g), rets.mean() * 100, float(np.median(rets)) * 100,
                 (rets > 0).mean() * 100, sum(x["pnl"] for x in g)))
        p()

    p("=" * 96)
    p("【7】beta / alpha 分解：把 nA vs nB 的日收益差拆开")
    p("=" * 96)
    for a_n, b_n in ((13, 10), (8, 10)):
        for mask, tag in ((up, "涨日"), (~up, "跌日")):
            ra, rb = R[a_n][mask], R[b_n][mask]
            ba = bm_r[mask]
            beta_a = float(np.cov(ra, ba, ddof=1)[0, 1] / np.var(ba, ddof=1))
            beta_b = float(np.cov(rb, ba, ddof=1)[0, 1] / np.var(ba, ddof=1))
            al_a = ra.mean() - beta_a * ba.mean()
            al_b = rb.mean() - beta_b * ba.mean()
            p("  n=%d vs n=%d（%s）：日收益差 %+.2f bp = beta 部分 %+.2f bp + alpha 部分 %+.2f bp"
              % (a_n, b_n, tag, (ra.mean() - rb.mean()) * 1e4,
                 (beta_a - beta_b) * ba.mean() * 1e4, (al_a - al_b) * 1e4))
    p()

    p("=" * 96)
    p("【8】涨日：n=13 的劣势是否集中在「买不进严重」的日子（按 n=10 额度填充率分组）")
    p("=" * 96)
    fill10 = w10 / 10.0
    d13 = R[13] - R[10]
    p("  %-20s %6s %12s %8s" % ("填充率(买到的/10)", "N", "均值bp/日", "t"))
    for lo, hi, lab in ((0, 0.3, "<=30%（买不进多）"), (0.3, 0.5, "30~50%"),
                        (0.5, 0.7, "50~70%"), (0.7, 1.01, ">=70%（买得进）")):
        m = (fill10 >= lo) & (fill10 < hi) & up
        if m.sum() < 8:
            continue
        x = d13[m]
        mm, ss = x.mean(), x.std(ddof=1)
        t = mm / (ss / np.sqrt(len(x))) if ss > 0 else float("nan")
        p("  %-20s %6d %11.2f %8.2f" % (lab, m.sum(), mm * 1e4, t))
    p()


    p("=" * 96)
    p("【9】把规则改成「按**买得进的比例**调 n」而不是按大盘：模拟（含无前视版本）")
    p("=" * 96)
    fill = w10 / 10.0
    fill_prev = np.concatenate(([fill[0]], fill[:-1]))      # t-1 日填充率 → 无前视

    def sim_seq(seq, label):
        r = np.array([R[int(n)][i] for i, n in enumerate(seq)], dtype=float)
        eq = np.cumprod(1 + r) * 100000.0
        rets = np.diff(eq) / eq[:-1]
        mdd = float((eq / np.maximum.accumulate(eq) - 1).min() * 100)
        p("  %-40s 收益 %9.2f%%  夏普 %6.3f  回撤 %7.2f%%  胜率 %5.1f%%"
          % (label, (eq[-1] / eq[0] - 1) * 100,
             rets.mean() / rets.std(ddof=1) * np.sqrt(252), mdd, (rets > 0).mean() * 100))

    NS = sorted(reps)
    sim_seq([10] * (ND - 1), "固定 n=10（现状）")
    sim_seq([13] * (ND - 1), "固定 n=13")
    sim_seq([8] * (ND - 1), "固定 n=8")
    # 方案A：用当日填充率（有前视 → 只是上界）
    for thr, hi_n, lo_n in ((0.7, 13, 10), (0.7, 13, 8), (0.5, 13, 10), (0.5, 15, 10)):
        seq = [hi_n if f >= thr else lo_n for f in fill]
        sim_seq(seq, "填充率≥%.0f%%→n=%d，否则 n=%d（当日值·有前视）"
                % (thr * 100, hi_n, lo_n))
    # 方案B：用 t-1 日填充率（无前视）
    for thr, hi_n, lo_n in ((0.7, 13, 10), (0.7, 13, 8), (0.5, 13, 10)):
        seq = [hi_n if f >= thr else lo_n for f in fill_prev]
        sim_seq(seq, "填充率(t-1)≥%.0f%%→n=%d，否则 n=%d（无前视）"
                % (thr * 100, hi_n, lo_n))
    # 方案C：按大盘（对照）
    for hi_n, lo_n in ((13, 8), (13, 10), (10, 8)):
        seq = [hi_n if u else lo_n for u in up]
        sim_seq(seq, "大盘涨→n=%d，否则 n=%d" % (hi_n, lo_n))
    p()
    p("  ⚠ 对照标尺：夏普噪声跨度 1.28 / 收益跨度 27.8 倍（§四.2）")
    p()


    p("=" * 96)
    p("【10】替代口径：用「大盘**趋势**」代替「单日涨跌」分组（MA5/10/20/60）")
    p("=" * 96)
    bench = reps[10]["bm"]
    # 日收益索引 i 对应 dates[i+1]；趋势掩码同样右移一位对齐
    p("  %-6s %-10s %6s %13s %9s %11s %8s %11s %8s"
      % ("MA", "状态", "N", "n=10累计%", "夏普", "n13-n10 bp", "t", "n20-n10 bp", "t"))
    for k in (5, 10, 20, 60):
        ma = np.convolve(bench, np.ones(k) / k, mode="valid")      # 索引 t >= k-1
        tup = np.zeros(ND, bool)
        tup[k - 1:] = bench[k - 1:] > ma
        valid = np.arange(ND - 1) >= k - 1
        for tag, m in (("趋势向上", tup[1:] & valid), ("趋势向下", (~tup[1:]) & valid)):
            if m.sum() < 10:
                continue
            x = R[10][m]
            sd = x.std(ddof=1)
            row = ["MA%d" % k, tag, int(m.sum()), "%.2f%%" % ((np.prod(1 + x) - 1) * 100),
                   "%.3f" % (x.mean() / sd * np.sqrt(252) if sd > 0 else float("nan"))]
            for a_n in (13, 20):
                d = (R[a_n] - R[10])[m]
                md, ss = d.mean(), d.std(ddof=1)
                row.append("%+.2f" % (md * 1e4))
                row.append("%+.2f" % (md / (ss / np.sqrt(len(d))) if ss > 0 else float("nan")))
            p("  %-6s %-10s %6s %13s %9s %11s %8s %11s %8s" % tuple(row))
    p()
    p("  --- 同一张表换成「单日涨跌」口径（对照）---")
    for tag, m in (("单日上涨", up), ("单日下跌", ~up)):
        x = R[10][m]
        sd = x.std(ddof=1)
        vals = []
        for a_n in (13, 20):
            d = (R[a_n] - R[10])[m]
            md, ss = d.mean(), d.std(ddof=1)
            vals.append("%+.2f" % (md * 1e4))
            vals.append("%+.2f" % (md / (ss / np.sqrt(len(d))) if ss > 0 else float("nan")))
        p("  %-6s %-10s %6s %13s %9s %11s %8s %11s %8s"
          % ("单日", tag, int(m.sum()), "%.2f%%" % ((np.prod(1 + x) - 1) * 100),
             "%.3f" % (x.mean() / sd * np.sqrt(252) if sd > 0 else float("nan")),
             vals[0], vals[1], vals[2], vals[3]))
    p()

    p("=" * 96)
    p("【11】趋势口径下的「买不进」交互：趋势向上时，放大 n 也要看买不买得进")
    p("=" * 96)
    fill = w10 / 10.0
    for k in (20, 60):
        ma = np.convolve(bench, np.ones(k) / k, mode="valid")
        tup = np.zeros(ND, bool)
        tup[k - 1:] = bench[k - 1:] > ma
        mu = tup[1:]
        for tag, m in (("趋势向上", mu), ("趋势向下", ~mu & (np.arange(ND - 1) >= k - 1))):
            p("  --- MA%d %s ---" % (k, tag))
            for lo, hi, lab in ((0, 0.5, "填充率<50%（买不进多）"),
                                (0.5, 1.01, "填充率>=50%（买得进）")):
                mm_ = m & (fill >= lo) & (fill < hi)
                if mm_.sum() < 8:
                    continue
                d = (R[13] - R[10])[mm_]
                md, sd_ = d.mean(), d.std(ddof=1)
                t = md / (sd_ / np.sqrt(len(d))) if sd_ > 0 else float("nan")
                p("      %-22s N=%3d  n13-n10 均值 %+7.2f bp/日  t=%+5.2f"
                  % (lab, mm_.sum(), md * 1e4, t))
    p()


    p("=" * 96)
    p("【12】大盘趋势（MA20/MA60，**无前视**：用 t-1 日的 MA 决策 t 日）× 持仓上限")
    p("=" * 96)

    def ma_prev(k):
        """out[t] = bench[t-1] > mean(bench[t-k .. t-1])；t>=k 才有定义。
        决策日 t 的信息在 t-1 收盘即已知 → 次日操作无前视。"""
        n = len(bench)
        out = np.zeros(n, bool)
        for t in range(k, n):
            out[t] = bench[t - 1] > bench[t - k:t].mean()
        return out

    for k in (20, 60):
        tu = ma_prev(k)
        valid = np.arange(ND - 1) >= k - 1
        upm = tu[1:] & valid
        dnm = (~tu[1:]) & valid
        p("  --- MA%d（无前视）：趋势向上 %d 天 / 趋势向下 %d 天 ---"
          % (k, upm.sum(), dnm.sum()))
        p("  %-8s %-6s %6s %12s %8s %8s" % ("状态", "n", "N", "累计%", "夏普", "胜率%"))
        for tag, m in (("向上", upm), ("向下", dnm)):
            for n in sorted(reps):
                if m.sum() < 10:
                    continue
                x = R[n][m]
                sd = x.std(ddof=1)
                p("  %-8s n=%-4d %6d %11.2f%% %8.3f %8.1f"
                  % (tag, n, m.sum(), (np.prod(1 + x) - 1) * 100,
                     x.mean() / sd * np.sqrt(252) if sd > 0 else float("nan"),
                     (x > 0).mean() * 100))
            p()

    p("=" * 96)
    p("【13】把趋势当**仓位开关**：趋势弱就降仓/空仓（无前视，模拟）")
    p("=" * 96)

    def sim_seq(r, label):
        eq = np.cumprod(1 + r) * 100000.0
        rets = np.diff(eq) / eq[:-1]
        mdd = float((eq / np.maximum.accumulate(eq) - 1).min() * 100)
        p("  %-34s 收益 %9.2f%%  夏普 %6.3f  回撤 %7.2f%%  胜率 %5.1f%%"
          % (label, (eq[-1] / eq[0] - 1) * 100,
             rets.mean() / rets.std(ddof=1) * np.sqrt(252), mdd, (rets > 0).mean() * 100))

    for n in (8, 10, 13):
        sim_seq(R[n], "满仓 n=%d%s" % (n, "（现状）" if n == 10 else ""))
    for k in (20, 60):
        tu = ma_prev(k)
        valid = np.arange(ND - 1) >= k - 1
        u = tu[1:] & valid
        d = (~tu[1:]) & valid
        sim_seq(R[10] * u, "MA%d 向上满仓 / 向下空仓" % k)
        sim_seq(R[10] * (u + 0.5 * d), "MA%d 向上满仓 / 向下半仓" % k)
        sim_seq(np.where(u, R[13], np.where(d, R[8], 0.0)),
                "MA%d 向上 n=13 / 向下 n=8" % k)
        sim_seq(np.where(u, R[10], np.where(d, R[5], 0.0)),
                "MA%d 向上 n=10 / 向下 n=5" % k)
        sim_seq(np.where(u, R[13], np.where(d, R[5], 0.0)),
                "MA%d 向上 n=13 / 向下 n=5" % k)
        p()
    p("  ⚠ 仍是**单次抽样**：夏普噪声跨度 1.28 / 收益跨度 27.8 倍（§四.2）")
    p()

    p("=" * 96)
    p("【14】趋势仓位开关的**跨子区间一致性**（区间切 3 段）")
    p("=" * 96)
    bounds = [0, 242, 484, ND - 1]
    tu20 = ma_prev(20)
    v20 = np.arange(ND - 1) >= 19
    u20 = tu20[1:] & v20
    d20 = (~tu20[1:]) & v20
    for i in range(3):
        s, e = bounds[i], bounds[i + 1]
        seg = np.zeros(ND - 1, bool)
        seg[s:e] = True
        base_r = R[10]
        filt_r = np.where(u20, base_r, 0.0)
        for lab, rr in (("满仓 n=10", base_r), ("MA20 向下空仓", filt_r)):
            x = rr[seg]
            sd = x.std(ddof=1)
            p("  %s ~ %s  %-16s 累计 %10.2f%%  夏普 %6.3f"
              % (dates_r[s], dates_r[e - 1], lab, (np.prod(1 + x) - 1) * 100,
                 x.mean() / sd * np.sqrt(252) if sd > 0 else float("nan")))
    p()


if __name__ == "__main__":
    main()
