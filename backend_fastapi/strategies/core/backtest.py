# -*- coding: utf-8 -*-
"""回测编排层：范围 → 策略信号 → 统计。

只负责「串起来」，本身不含策略算法，也不含净值/指标算法：
    1. 读回测范围（universe，不设标的上限，「全部本地」全量跑）；
    2. 经 data.load_bars 做一次预检取数（逐标的缓存、并行读库，
       策略/统计后续复用，零额外 I/O），过滤数据不足的标的并记录 data_errors；
    3. 构造 StrategyContext 交给 base.run —— 策略内部自行拉取所需字段；
    4. 经 base.signals_to_positions 把扁平信号（含 code）转成「目标仓位序列」；
    5. 把收盘价交给 stats.compute 做净值与指标统计。

返回结构（前后端契约，metrics.schema_version = 2）：
  {
    "ok": True,
    "strategy": {"id","name","params"},
    "result_id": str,                          # 逐笔明细按需查询的句柄（见 trades_for）
    "scope": {"codes_count","requested","start","end",
              "initial_capital","commission","stamp_duty","slippage",
              "min_commission","lot_size","benchmark","universe_mode",
              "equity_model","per_stock_truncated","feasibility","warnings"},
    "dates": [YYYY-MM-DD, ...],
    "equity": [float|null, ...],               # 组合净值（初始资金起）
    "equity_benchmark": [float|null,...]|None, # 基准净值（默认等权全池买入持有）
    "metrics": {total_return, annual_return, max_drawdown, sharpe,
                win_rate_daily, win_rate_trade, profit_factor, expectancy_pct,
                num_trades_eff, weight_change_count, avg_positions, max_positions,
                turnover_annual, avg_holding_days, capital_utilization,
                return_per_utilized_capital, benchmark_return,
                alpha, beta, ir, tracking_error},
    "per_stock": [{"code","name","pnl","total_return","trades","closed"}, ...],
                   # 降序**双向**截断（头部+尾部），元信息见 scope.per_stock_truncated；
                   # 逐笔明细经 /backtest/trades 按需取
    "data_errors": [str, ...]                  # 跳过/失败的标的说明
  }

⚠️ `scope.warnings` **必须非空且含幸存者偏差声明**（见 data.survivorship_note）：
   偏差消不掉，但必须让读报告的人看见结果是跑在什么样本上的。
"""
from __future__ import annotations

import bisect
import datetime as dt
import json
import threading
import time
import uuid

from . import base, data, stats

_MIN_BARS = 30        # 少于此根数视为数据不足
_WARMUP_BARS = 250    # 短窗口回测的指标预热根数（约一年，够 MACD 这类长周期策略）

# 最近一次回测的上下文（单槽）：per_stock 只带基础列，逐笔明细由前端
# 按 result_id 展开时经 trades_for 现算，避免全量明细 JSON 打到 10MB 级。
_BT_CTX: dict | None = None

def _load_names(codes: list[str]) -> dict[str, str]:
    """兼容别名：名称加载已下沉到 `data.load_names`（进程内 24h 缓存）。

    保留此函数，是因为 `chip_rank_service` / `features.*` 仍按旧路径
    `from strategies.core.backtest import _load_names` 引用；实现只此一份。
    """
    return data.load_names(codes)


def _prepare(codes: list[str], start: str | None, end: str | None,
             adjust: str = "hfq", progress=None) -> tuple[dict, list[str]]:
    """预检取数：返回 (可用标的 {code: DataFrame}, 跳过说明列表)。

    回测与推荐共用——同一份底层缓存（data.load_bars 逐标的缓存），不重复 I/O。
    `progress(done, total)` 用于回测进度条（可选，不给就不报）。
    """
    bars = data.load_bars(codes, start, end, fields=["close"], adjust=adjust,
                          progress=progress)
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


def _require_pool(strategy_id: str, start: str | None, end: str | None) -> None:
    """依赖股票池历史的策略：预检「历史是否覆盖回测区间」，否则给出友好提示。

    注册冒烟测试只直接调 meta.func、不经过本函数，故这里抛错不会让策略注册失败
    （策略本体在无历史时返回空信号，保证注册通过）。
    """
    meta = base.REGISTRY.get(strategy_id)
    if not meta or not getattr(meta, "requires_pool", False):
        return
    mn, mx = data.pool_snapshot_range()
    if mn is None or mx is None:
        raise RuntimeError("暂无股票池历史数据，请先导入/同步股票池历史数据后再运行本策略")
    if start and end:
        # ① 池历史必须与回测区间**相交**：完全在区间之前或之后都取不到任何池成员。
        if mx < start or mn > end:
            raise RuntimeError(
                f"股票池历史数据与回测区间没有交集（历史 {mn}~{mx}，回测 {start}~{end}），"
                f"请先在「股票池」页把覆盖该区间的池数据同步到后端")
        # ② 池历史没覆盖到回测结束日：区间后段没有任何池成员，净值会退化成
        #    「只持有、不换手」的假结果，必须挡住，并直接说清该改哪一头。
        if mx < end:
            raise RuntimeError(
                f"股票池历史数据只到 {mx}，覆盖不到回测结束日 {end}"
                f"（{mx} 之后没有任何池成员），请把回测结束日改到 {mx} 之前，"
                f"或先同步 {mx}~{end} 的股票池数据")
        # ③ pool_warmup 只是「起点前尽量多取几天养连续性」的**尽力而为**项：
        #    历史够不到 lo 时，连续在池天数从历史首日开始算（策略内部按现有数据兜底），
        #    不能因为预热不足就把整个回测拦下 —— 否则池历史短于 warmup 时
        #    该策略在任何区间都会失败（例如池历史 11 天、warmup 30 天，连
        #    「池历史覆盖的最后一天」当结束日都跑不了）。


def _resolve_pool(codes_resolver):
    """调用股票池解析函数，返回 (codes, extra)。

    解析函数可以只返回代码列表，也可以返回 (codes, detail)：
    detail 是**范围明细**（每项剔除只数、判定基准日、口径说明），
    必须原样带进报告 scope —— 否则报告无法自证「这次到底剔了什么」。
    """
    resolved = codes_resolver() or []
    if isinstance(resolved, tuple):
        return list(resolved[0] or []), dict(resolved[1] or {})
    return list(resolved), {}


def run_backtest(strategy_id: str, params: dict | None, codes: list[str],
                 start: str | None, end: str | None,
                 initial_capital: float = 100000.0, commission: float = 0.0003,
                 stamp_duty: float = 0.001, slippage: float = 0.0005,
                 min_commission: float = 5.0, lot_size: int = 100,
                 benchmark: str | None = None, window: int | None = None,
                 universe_mode: str = "interval_data",
                 ruin_threshold=None,
                 scope_extra: dict | None = None,
                 progress=None) -> dict:
    """执行回测，返回标准化结果字典。异常由路由层转成错误响应。

    不设标的上限：「全部本地」（5000+ 只）全量跑——数据层 load_bars
    已并行读库，冷缓存全量也在可等待的量级。

    成本参数全部**进净值**（佣金/印花税/滑点/最低佣金），`lot_size` 只影响
    逐笔明细的可行性判定、不影响净值——见 stats 模块 docstring 的成本分层。

    `window=N` 时只统计**最近 N 个交易日**：取数窗口会额外向前多取 warmup 根
    （策略指标如 MACD 需要历史预热，只给 5 根根本算不出信号），但净值与指标
    只按最后 N 日统计（见 stats.compute 的 tail）。
    """
    codes = [str(c).strip() for c in (codes or []) if str(c).strip()]
    if not codes:
        raise ValueError("股票池为空，请先选择回测范围")

    # 暂停/停止的检查点之一：数据加载阶段每批都会回调进度，
    # 这是唯一既「调用频繁」又「已在调用栈上」的插入点 —— 在这里响应能让
    # load_bars / _prepare 正常 unwind，而不是留一堆半开的读取任务。
    # 第二个检查点在策略内部（base.checkpoint），由策略逐标的调用。
    _raw_progress = progress

    def _tick(done, total):
        base.checkpoint()
        if _raw_progress is not None:
            _raw_progress(done, total)

    progress = _tick

    requested = len(codes)
    if window and window > 0:
        days = data.recent_trading_days(window + _WARMUP_BARS)
        if len(days) >= window + 2:
            start, end = days[0], days[-1]

    _require_pool(strategy_id, start, end)

    usable, errors = _prepare(codes, start, end, progress=progress)
    if not usable:
        raise RuntimeError("所选范围内没有可用数据，无法回测")

    # 取数完了，进入策略计算。策略函数是自己遍历 ctx.codes 的（见 base.run），
    # 外部插不进逐票回调，故这一段只能报**阶段**、不能报「已测只数」——
    # 进度条上要把「加载数据」与「计算信号」区分开，否则用户会以为卡住了。
    if progress is not None:
        try:
            progress(-1, -1)          # 哨兵：-1 表示进入不可细分的阶段
        except Exception:
            pass
    ctx = base.StrategyContext(codes=list(usable.keys()), start=start, end=end, adjust="hfq")
    signals = base.run(strategy_id, ctx, params)

    close_by_code = {c: usable[c]["close"] for c in usable}
    positions = base.signals_to_positions(signals, close_by_code)
    sig_index = base.signal_meta_index(signals)

    bench_close = None
    if benchmark:
        try:
            bmd = data.load_bars([benchmark], start, end, fields=["close"], adjust="hfq")
            if benchmark in bmd and len(bmd[benchmark]) >= 2:
                bench_close = bmd[benchmark]["close"]
        except Exception as exc:
            errors.append(f"基准 {benchmark}: {exc}")

    # raw OHLCV **只取权重变动过的票**：整手/涨跌停/停牌的可行性判定要未复权价，
    # 但全市场 5559 只全取会把内存重新推向 2GB 的老问题（见 data.load_bars_raw_for）。
    # 先用 diff_trades 定位变动集合（一次向量化扫描），再按需取价。
    try:
        changed = list(base.diff_trades(positions, sig_index).keys())
    except Exception:                          # noqa: BLE001 - 定位失败不该拖垮回测
        changed = list(positions.keys())
    raw_by_code: dict = {}
    if changed:
        try:
            raw_by_code = data.load_bars_raw_for(changed, start, end)
            data.release_cache(changed, adjust="raw")   # 取完即还，别挂在内存里
        except Exception as exc:               # noqa: BLE001 - 明细不该拖垮回测
            errors.append(f"可行性判定取原始价失败：{type(exc).__name__}: {exc}")

    core = stats.compute(positions, close_by_code, initial_capital=initial_capital,
                         commission=commission, stamp_duty=stamp_duty,
                         slippage=slippage, min_commission=min_commission,
                         lot_size=lot_size, benchmark_close=bench_close,
                         raw_by_code=raw_by_code, signal_meta=sig_index, tail=window,
                         ruin_threshold=ruin_threshold)

    trades: list[dict] = core.pop("trades", []) or []
    flags: list[str] = core.pop("flags", []) or []

    # 名称补齐（只做展示口径，不影响统计）。逐笔明细**不**随结果下发：
    # 全量 5000+ 只的明细 JSON 会到 10MB 级，改为前端点击展开时按
    # result_id 调 /backtest/trades 现算（上下文缓存在 _BT_CTX，见 trades_for）。
    per_stock, trunc_info = stats.truncate_per_stock(core["per_stock"], 500)
    names = _load_names([x["code"] for x in per_stock])
    for item in per_stock:
        item["name"] = names.get(item["code"]) or None
    for t in trades:
        t["name"] = names.get(t["code"]) or None

    # 可行性汇总（整手/涨跌停/停牌 —— 只影响明细、不影响净值，单列成一组指标）
    feas = _feasibility_summary(trades, len(changed),
                                fill_blocked=core.get("fill_blocked"))

    global _BT_CTX, _BT_TRADES
    rid = uuid.uuid4().hex[:12]
    _BT_CTX = {"rid": rid, "positions": positions, "close": close_by_code,
               "start": start, "end": end, "names": names}
    # 逐笔明细**只**给报告落盘用，**不进 HTTP 响应**（见返回字典处注释）
    _BT_TRADES = trades

    warnings = list(flags)
    # 成交模型：一字板买不进即空仓、跌停卖不出即延后清仓（覆盖所有策略）；
    # 其余成本/整手/停牌约束见 feasibility 字段，不在此重复。
    warnings.append("成交模型：一字涨停买不进即空仓（该份额度当日为现金、不重分配），"
                    "一字跌停卖不出即延后清仓；整手/停牌仍只影响明细可行性")
    # 幸存者偏差：任何模式都必须显式声明，**不允许静默**（偏差消不掉，但必须让人看见）
    warnings.append(data.survivorship_note(universe_mode, len(usable)))
    # 范围口径：每项剔除了多少只 + 哪些判定用的是「当前状态」（含前视偏差）。
    # 以前只声明幸存者偏差，读者会误以为「已声明偏差 = 样本无问题」——
    # 而剔除特征其实是用今天的状态去筛整段历史。
    scope_extra = dict(scope_extra or {})
    if scope_extra.get("note"):
        warnings.append(str(scope_extra["note"]))
    if window:
        warnings.append(f"short_window: 只统计最近 {window} 个交易日（区间被裁剪）")
    warnings.extend(errors[:20])

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
            "stamp_duty": stamp_duty,
            "slippage": slippage,
            "min_commission": min_commission,
            "lot_size": lot_size,
            "benchmark": benchmark,
            "universe_mode": universe_mode,
            # 范围口径（剔除明细 + 判定基准日 + 哪些判定含前视偏差）
            "exclude": list(scope_extra.get("exclude") or []),
            "exclusion": {
                "base_count": scope_extra.get("base_count"),
                "filtered_count": scope_extra.get("filtered_count"),
                "excluded_count": scope_extra.get("excluded_count"),
                "dropped": scope_extra.get("dropped") or {},
                "as_of": scope_extra.get("as_of"),
                "forward_looking": scope_extra.get("forward_looking") or [],
            },
            "equity_model": "weight_strict_fill",
            "per_stock_truncated": trunc_info,
            "feasibility": feas,
            "warnings": warnings,
        },
        "dates": core["dates"],
        "equity": core["equity"],
        "equity_benchmark": core["equity_benchmark"],
        "metrics": core["metrics"],
        "per_stock": per_stock,
        # ⚠️ **逐笔明细刻意不在这里**：全市场一次回测 15 万+ 笔，随响应下发是
        # 24 MB JSON、取结果要 10 秒；前端展开某只票时走 /backtest/trades 按需取。
        # 报告要用的话从 `last_trades()` 取。
        "data_errors": errors[:20],
    }


def _feasibility_summary(trades: list[dict], n_checked: int,
                         fill_blocked: dict | None = None) -> dict:
    """整手 / 涨跌停 / 停牌的可行性汇总（**独立于净值的一组指标**）。

    口径提醒：`equity` 已含「一字板买不进即空仓、跌停卖不出即延后清仓」；
    这里给出的是**逐笔明细**里「这笔在真实市场里能不能成交」，
    两者必须分区展示，不能混读成「实际赚到的钱」。

    `fill_blocked` 是**根本没发生成交**的计数（买不进的建仓 / 卖不掉的清零）——
    它们不会出现在 trades 明细里（trades 只记真的发生了的成交），单独带出来，
    好让报告能回答"有多少次想买没买成"。
    """
    n = len(trades)
    blocked = {"suspended": 0, "limit_up": 0, "limit_down": 0,
               "lot_size": 0, "no_price": 0}
    ok_n = 0
    for t in trades:
        if t.get("feasible", True):
            ok_n += 1
            continue
        b = t.get("blocked_by")
        if b in blocked:
            blocked[b] += 1
    fb = fill_blocked or {}
    return {
        "checked_codes": n_checked,
        "trades": n,
        "tradable_ratio": round(ok_n / n, 4) if n else None,
        "blocked_counts": blocked,
        "fill_blocked": {"buy_blocked": int(fb.get("buy_blocked") or 0),
                         "sell_deferred": int(fb.get("sell_deferred") or 0)},
        "note": "净值已含一字板约束：一字涨停买不进即空仓（该份额度当日为现金、"
                "不重分配），一字跌停卖不出即延后清仓；整手/停牌仍只影响明细可行性",
    }


# ---- 第 21 项：全市场推荐的内存与耗时 ----
# 分批大小。取 500 是权衡：一批 500 只 × 250 天的 OHLCV 约 180MB，
# 远小于全市场一次性驻留的 2GB；又不至于太小以致线程池反复起停。
_RECO_BATCH = 500


class _IdxOnly:
    """只有日期索引的「伪行情」。

    `signals_to_positions` 只读 `close.index`（把信号对齐到该标的的交易日），
    **从不碰价格**。所以算仓位时用这个代替整份 DataFrame，内存从
    O(全市场 × 天数 × 一行 OHLCV) 降到 O(全市场 × 天数 × 一个字符串)。
    """

    __slots__ = ("index",)

    def __init__(self, idx):
        self.index = idx


def _last_pos_at(index, day: str) -> int:
    """`index`（升序）中 **≤ `day`** 的最后一个下标；没有则 -1。

    取「≤ day 的最后一行」而不是「恰好等于 day 的那一行」：停牌 / 缺行情的票
    要按「仍持有」处理，否则它在三档里会因为最后一行落在哪一天而错档。
    """
    if not day:
        return -1
    try:
        return max(-1, int(index.searchsorted(day, side="right")) - 1)
    except Exception:                           # noqa: BLE001 - 索引类型不同则退到字符串比较
        try:
            return max(-1, bisect.bisect_right([str(d)[:10] for d in index], day) - 1)
        except Exception:                       # noqa: BLE001 - 实在取不到就按「无数据」
            return -1


def _close_at(df, day: str) -> tuple[str | None, float | None]:
    """取 `df` 在 `day` 当日（或之前最后一个有效交易日）的收盘价与该日期。

    返回 `(日期, 收盘价)`；取不到返回 `(None, None)`。
    日期一律转成 `YYYY-MM-DD` 字符串再比较 —— 行情的 index 可能是
    DatetimeIndex（字符串比较会抛异常），也可能是字符串索引。
    """
    if df is None or len(df) == 0:
        return None, None
    try:
        dates = [str(d)[:10] for d in df.index]
        i = bisect.bisect_right(dates, str(day)[:10]) - 1
        if i < 0:
            return None, None
        return dates[i], float(df["close"].iloc[i])
    except Exception:                           # noqa: BLE001 - 取不到价按「无价」处理
        return None, None


def _streak_entry(w, sig_day: str, raw, hfq, axis: list[str]) -> tuple[str, float | None]:
    """无基线时的建仓日与成本价：按信号序列回溯本次持仓的起点。

    返回 `(建仓日, 成本价)`；成本价取**执行日**的收盘（还没收盘就取该日之前
    最后一个收盘），取不到再退回信号日。

    口径与旧实现一致（回溯到最后一个空仓日之后的首个持仓日），只是取价改成
    **按日期**找（原来按位置 `iloc[open_i]` 取，行情缺一天就错位）。
    """
    if w is None or len(w) == 0:
        return sig_day, None
    open_i = len(w) - 1
    for i in range(len(w) - 1, -1, -1):
        if float(w.iloc[i]) <= 0.0:
            break
        open_i = i
    entry_sig = str(w.index[open_i])[:10]
    buy_date = _exec_day_of(entry_sig, axis)
    _, cost = _close_at(raw, buy_date)
    if cost is None:
        _, cost = _close_at(raw, entry_sig)
    if cost is None:
        _, cost = _close_at(hfq, buy_date)
    return buy_date, cost


def _exec_day_of(sig_day: str, axis: list[str]) -> str:
    """信号日的**下一个交易日** = 该信号对应的执行日（用户实际下单那天）。

    为什么建仓日要用执行日而不是信号日：信号是「前一交易日收盘算出来的」，
    用户是**执行日**下单的。显示信号日会让「今天持股的成本价」与「昨天买入
    表里的价」差一天（实测：昨天按建议买的票，今天持股表里建仓日写的是前天
    的信号日 —— 用户看到的正是「持仓的数据和前一天买入的对不上」）。

    优先用**统一轴**（各标的交易日并集）里的下一根 —— 离线、且与行情口径
    一致；轴里没有下一根（信号日就是区间最后一天）才退回交易日历。
    """
    if not sig_day:
        return sig_day
    for d in axis:
        if d > sig_day:
            return d
    return next_trading_day(sig_day)


def next_trading_day(date_str: str) -> str:
    """`date_str` 之后的第一个交易日（含未来日），作为推荐的**执行日**。

    推荐信号基于「最新已下载交易日」，但收盘后已无法在当天交易，
    真正能下单的是下一个交易日。日历优先用 A 股交易日历，取不到退化为工作日。
    """
    try:
        base = dt.date.fromisoformat(str(date_str)[:10])
    except (ValueError, TypeError):
        base = dt.date.today()
    try:
        from features.market.service import _a_share_trade_days
        days = _a_share_trade_days()
    except Exception:                       # noqa: BLE001 - 日历不可用退化为星期
        days = set()
    if days:
        cand = [d for d in days if d > base.isoformat()]
        if cand:
            return min(cand)
    cur = base
    for _ in range(14):                     # 退化：逐日找下一个周一~周五
        cur += dt.timedelta(days=1)
        if cur.weekday() < 5:
            return cur.isoformat()
    return base.isoformat()


def run_recommend(strategy_id: str, params: dict | None, codes: list[str],
                  start: str | None = None, end: str | None = None,
                  initial_capital: float = 100000.0, commission: float = 0.0003,
                  lookback: int = 250, progress=None,
                  execution_date: str | None = None,
                  signal_date: str | None = None,
                  baseline: dict | None = None) -> dict:
    """当前策略推荐：按**最新一日**的目标仓位给出买入 / 卖出 / 持股三档。

    与回测共用同一套信号与 T+1 口径，只是不统计历史净值，只回答「今天怎么做」：
      buy   上次建议没持有、本次建仓；
      sell  上次建议持有、本次不在目标组合里了；
      hold  上次建议持有、本次仍在持仓（含加仓 / 减仓），附建仓成本价与浮盈亏金额。

    未指定区间时自动回看最近 `lookback` 个交易日——MACD 这类长周期策略
    只看最近几天是算不出有效信号的。

    金额口径：组合权重 × 目标资金（`amount = initial_capital × weight`），
    与回测的净值口径一致；盈亏用**后复权**序列算（消除除权的假盈亏），
    价格展示用原始收盘（不复权，用户看得懂）。

    `baseline`：上一次推荐结果（整份 dict）。三档以它为**持仓基线** ——
    否则三档描述的是「策略的仓位」而不是「用户按建议建立的仓位」，会出现
    「昨天买入的票今天既不在持股也不在卖出（凭空消失）」。没有基线时
    退回「信号相邻两日差分」口径（首次运行 / 换了策略 / 往回改日期重跑）。

    `progress(done, total)` 与 `run_backtest` 同义（哨兵 done=-1 表示进入
    不可细分的策略计算阶段）——原先这里没往下传，导致异步化之后进度条
    永远停在 0%。
    """
    codes = [str(c).strip() for c in (codes or []) if str(c).strip()]
    if not codes:
        raise ValueError("股票池为空，请先选择回测范围")

    _raw_reco = progress

    def _rtick(done, total):
        base.checkpoint()
        if _raw_reco is not None:
            _raw_reco(done, total)

    progress = _rtick

    if not start or not end:
        days = data.recent_trading_days(lookback)
        if len(days) >= 2:
            start, end = days[0], days[-1]
    # 单日策略：强制把 end 锁在信号日。上面的自动回看会把 end 推到「本地最新
    # 交易日」，收盘后当日数据部分入库时就会读到不完整的 K 线（未来函数）。
    # 必须在回看之后再覆盖，否则被它抹掉。
    if signal_date:
        end = signal_date

    _require_pool(strategy_id, start, end)

    # ---- 第 1 遍：分批预检，**只留日期索引**，不留行情本体----
    # 为什么分批（第 21 项实测）：`_prepare` 一次性把全市场 5585 只 × 250 天
    # 的 OHLCV 塞进 `_PER_CACHE`（进程级、无上限），实测后端常驻内存涨到 2GB、
    # 系统只剩 3.1GB 可用，于是 GC 与换页把小样本实测的444s 放大到实测 **946s**。
    # 取完一批就`release_cache` 还回去，缓存的「批内不重复读」收益全保留，
    # 跨批占用被压住。
    #
    # 为什么只留索引：`signals_to_positions` 只用 `close.index`（对齐信号日期），
    # **从不读价格**。所以这一步没必要留着整份 DataFrame。
    idx_by_code: dict[str, list[str]] = {}
    errors: list[str] = []
    for i in range(0, len(codes), _RECO_BATCH):
        batch = codes[i:i + _RECO_BATCH]
        part, errs = _prepare(batch, start, end, progress=progress)
        errors.extend(errs)
        for c, df in part.items():
            idx_by_code[c] = [str(d)[:10] for d in df.index]
        del part
        data.release_cache(batch, adjust="hfq")
    usable = idx_by_code

    if not usable:
        raise RuntimeError("所选范围内没有可用数据，无法生成推荐")
    if progress is not None:
        progress(-1, len(codes))     # 哨兵：进入不可细分的策略计算阶段

    ctx = base.StrategyContext(codes=list(usable.keys()), start=start, end=end, adjust="hfq")
    signals = base.run(strategy_id, ctx, params)
    close_by_code = {c: _IdxOnly(ix) for c, ix in idx_by_code.items()}
    positions = base.signals_to_positions(signals, close_by_code)
    # 统一日期轴（各标的交易日的并集）：三档必须在**同一天**上比较 —— 见下方
    # 「统一轴」注释。集合只有几百个交易日，但要遍历全部标的的日期，
    # 所以在释放 idx_bycode 之前算一次（遍历成本约 0.2s）。
    axis: set[str] = set()
    for _ix in idx_by_code.values():
        axis.update(_ix)
    days_axis: list[str] = sorted(axis)
    del axis
    # 第 1 遍的索引已不再需要（后面只用需要的标的的行情），尽早释放
    del idx_by_code, close_by_code

    # ---- 统一日期轴：信号日 / 前一交易日 ----
    # ⚠ 为什么必须「统一轴」：原来三档直接取**每只票自己序列的最后两根**。
    #    停牌票的最后两根落在不同日期（有的停在 11-11、有的停在 11-13），
    #    于是同一只票在两次运行里会被归到不同的档、甚至凭空消失
    #    （实测：002623 在 11-13 的推荐里是持仓，改日重跑后三张表里都没有）。
    #    统一轴后：信号日 = 轴上最后一天、前一交易日 = 倒数第二天，
    #    每只票都按「该日（或之前最后一个有效交易日）」取值 → 停牌按「仍持有」处理。
    sig_day = days_axis[-1] if days_axis else ""
    prev_day = days_axis[-2] if len(days_axis) >= 2 else ""

    # 信号日 / 前一交易日的目标权重（>0 才留）
    target: dict[str, float] = {}
    prev_w: dict[str, float] = {}
    for code, w in positions.items():
        i_cur = _last_pos_at(w.index, sig_day)
        if i_cur >= 0:
            cur = float(w.iloc[i_cur])
            if cur > 0.0:
                target[code] = cur
        i_prev = _last_pos_at(w.index, prev_day)
        if i_prev >= 0:
            pv = float(w.iloc[i_prev])
            if pv > 0.0:
                prev_w[code] = pv

    # ---- 持仓基线：**上一次推荐结果**优先；没有才退回「信号相邻两日差分」----
    # 为什么要有基线：三档若只看「策略信号的两日差分」，它描述的是**策略**的仓位，
    # 不是**用户按建议建立的仓位**。于是「昨天买入的票」今天既不在持股、也不在卖出
    # （凭空消失），建仓日/成本价还会随策略信号回溯而变（实测 002086 在 11-13 与
    # 11-16 两次运行里建仓日分别是 10-30 / 11-12）。
    # 以「上一次推荐」为基线后：昨天买入 → 今天要么持股、要么卖出，**不会消失**，
    # 且建仓日/成本价跨运行稳定（= 用户实际那一笔）。
    base_hold: dict[str, dict] = {}
    base_exec = ""
    if isinstance(baseline, dict) and baseline:
        b_sid = str((baseline.get("strategy") or {}).get("id") or "")
        b_exec = str(baseline.get("execution_date") or "")[:10]
        cur_exec = str(execution_date or "")[:10]
        # 只接受「**更早**」的基线：往回改日期重跑时不能拿未来那次当过去；
        # 同一天重跑也不用基线（那会把「今天该买」显示成「已持有」），
        # 退回信号差分 → 同一天跑两次结果一致。
        if b_sid == strategy_id and b_exec and (not cur_exec or b_exec < cur_exec):
            for it in (baseline.get("hold") or []):
                c = str(it.get("code") or "")
                if c:
                    base_hold[c] = {"buy_date": str(it.get("buy_date") or "")[:10],
                                    "cost_price": it.get("cost_price"),
                                    "weight": it.get("weight")}
            for it in (baseline.get("buy") or []):
                c = str(it.get("code") or "")
                if c:
                    base_hold[c] = {"buy_date": b_exec,
                                    "cost_price": it.get("cost_price") or it.get("price"),
                                    "weight": it.get("weight")}
    use_ledger = bool(base_hold)
    # 三档的判定集合：有基线用基线（用户实际持仓），否则用信号差分（策略仓位）
    held = set(base_hold) if use_ledger else set(prev_w)

    # ---- 第 2 遍：**只给有仓位的标的取价**（第 A 项）----
    # 原实现在这里对**全市场**再读一遍raw（`adjust="raw"` 与上面 hfq 是两套独立
    # 缓存，实测 108.8s / 5585 只），可它只为「买 / 卖 / 持」三档取两个**展示价**。
    # 而三档只覆盖有仓位的标的 —— 实测全市场 5585 只里只有
    # 107 只（买 9 + 卖 5 + 持 93）进得去，其余 5478 只的 raw 纯属白读。
    # 空仓的票三个分支都进不去，一个价格都用不到，直接跳过。
    need = sorted(set(target) | held)
    # 取价区间要够到**执行日**：展示价/成本价按执行日收盘口径给（执行日没收盘
    # 则自动退到该票最后一个有效交易日，见 _close_at）。
    price_end = str(end or "")
    if execution_date and str(execution_date)[:10] > price_end:
        price_end = str(execution_date)[:10]

    hfq_bars = data.load_bars(need, start, price_end, fields=["close"], adjust="hfq")
    try:
        raw_bars = data.load_bars(need, start, price_end, fields=["close"], adjust="raw")
    except Exception:                            # noqa: BLE001 - 原始价拿不到就用复权价展示
        raw_bars = {}
    data.release_cache(need)      # 两种口径都取完即还回去，别把内存挂在 107 只上

    names = _load_names(need)
    # 组合权重口径：金额 = 目标资金 × 权重。**没有「资金带」**——
    # 旧口径的 initial_capital/N 会让同一策略在 500 只池与 5559 只池给出差 11 倍的
    # 建议金额（池子越大每票分到越少），推荐结果因此不可横向对比、也不可跨池复用。
    target_capital = float(initial_capital)

    buy: list[dict] = []
    sell: list[dict] = []
    hold: list[dict] = []
    for code in need:
        w = positions.get(code)
        hfq = hfq_bars.get(code)
        raw = raw_bars.get(code)
        cur = target.get(code, 0.0)
        prev = prev_w.get(code, 0.0)
        b = base_hold.get(code) if use_ledger else None
        nm = names.get(code) or None

        # 现价：执行日收盘（该日还没收盘/停牌 → 退到最后一个有效交易日）
        px_day = str(execution_date or "")[:10] or sig_day
        px_date, px = _close_at(raw, px_day)
        if px is None:
            px_date, px = _close_at(hfq, px_day)
        if px is None:
            px_date, px = _close_at(raw, sig_day)
        if px is None:
            px_date, px = _close_at(hfq, sig_day)
        if px is None:
            errors.append(f"{code}: 取价失败（区间内无收盘价）")
            # 卖出档不能因为取不到价就凭空消失：退回基线里那一笔的成本价
            if not (b and b.get("cost_price")):
                continue
            px = float(b["cost_price"])
            px_date = ""

        if cur > 0.0 and code in held:                          # 继续持仓（可能加/减仓）
            if b and b.get("buy_date"):
                # 基线有 → 沿用用户那一笔的建仓日与成本价（跨运行稳定）
                buy_date = str(b["buy_date"])[:10]
                cost = float(b.get("cost_price") or px)
            else:
                # 无基线 → 按信号序列回溯本次持仓的建仓日（**执行日**口径）
                buy_date, cost = _streak_entry(w, sig_day, raw, hfq, days_axis)
                cost = float(cost if cost is not None else px)
            _, hfq_buy = _close_at(hfq, buy_date)
            _, hfq_now = _close_at(hfq, px_date or sig_day)
            pnl_pct = (hfq_now / hfq_buy - 1.0) if (hfq_buy and hfq_now) else 0.0
            amount = target_capital * cur
            # 加仓 / 减仓：跟**基线**的权重比（有基线 = 用户实际那笔；无基线 = 策略上一日）
            w_base = float((b or {}).get("weight") or prev or 0.0)
            hold.append({
                "code": code, "name": nm, "weight": round(cur, 4), "price": round(px, 2),
                "cost_price": round(cost, 2), "buy_date": buy_date,
                "amount": round(amount, 2), "pnl": round(amount * pnl_pct, 2),
                "pnl_pct": round(pnl_pct, 4),
                "action": ("加仓" if cur > w_base + 1e-9
                           else ("减仓" if cur < w_base - 1e-9 else "持股")),
            })
        elif cur > 0.0:                                          # 建仓（本次新增）
            buy.append({"code": code, "name": nm, "weight": round(cur, 4),
                        "price": round(px, 2), "amount": round(target_capital * cur, 2),
                        # 建仓日 = **执行日**（用户实际下单那天），不是信号日；
                        # 成本价 = 本次展示价 → 下一次运行原样继承 → 「昨天买入的价」
                        # 与「今天持股的成本价」永远一致。
                        "buy_date": str(execution_date or "")[:10] or sig_day,
                        "cost_price": round(px, 2)})
        elif code in held:                                       # 清仓（在持仓里、已不在目标组合）
            w_prev = float((b or {}).get("weight") or prev or 0.0)
            if w_prev <= 0.0:
                continue
            sell.append({"code": code, "name": nm, "weight": round(w_prev, 4),
                         "price": round(px, 2), "amount": round(target_capital * w_prev, 2),
                         "buy_date": str((b or {}).get("buy_date") or "")[:10],
                         "cost_price": round(float((b or {}).get("cost_price") or px), 2)})

    buy.sort(key=lambda x: -x["weight"])
    sell.sort(key=lambda x: -x["weight"])
    hold.sort(key=lambda x: -x["pnl"])

    # 信号日 = 统一轴上的最后一天（原来按「各标的最后一根 K 线」取最大值，
    # 两者等价，但统一轴的口径已经在这里算好了，直接用）。
    date = sig_day

    return {
        "ok": True,
        "strategy": {
            "id": strategy_id,
            "name": base.REGISTRY[strategy_id].name,
            "params": params or {},
        },
        "date": date or end,
        # 路由层按「目标执行日」反推后传入 → 结果如实回显用户选的那一天；
        # 直接调用（自检 / 旧调用方）时退化为「信号日的下一交易日」。
        "execution_date": execution_date or next_trading_day(date or end),
        "signal_date": signal_date or date or end,
        # 三档的基线：previous_recommend = 以上一次推荐为持仓基线（账本口径）；
        # signal_diff = 没有基线，退回「策略信号相邻两日差分」。
        # 前端要把它显示出来 —— 否则用户看不懂「为什么这只票今天在卖出里」。
        "baseline": {
            "source": "previous_recommend" if use_ledger else "signal_diff",
            "execution_date": base_exec if use_ledger else "",
            "held_count": len(held),
        },
        "target_capital": round(target_capital, 2),
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


# ---- 异步推荐任务：与下面的异步回测同一套模式 ----
# 同样是「全市场必然超时」：实测 5585 只要 **418.7s**（冷缓存更久，实测
# n=3000 已经276.0s，约 88ms/只），而原前端是**一次同步请求 + 600s 固定超时**，
# 途中零反馈。更糟的是它连 busy 守卫都没有 —— 用户等不及再点一次，就叠了两个
# 全市场计算互相拖慢。改成 start -> progress 轮询 -> result。
#
# 与回测任务**各自独立**的状态槽：两者的阶段语义与结果类型都不同，
# 挤进一份状态会让前端得同时理解两套字段。
_RECO_TASK: dict = {
    "state": "idle",       # idle / running / ready / error
    "phase": "",           # loading（可细分）/ running（策略计算，不可细分）/ done
    "done": 0, "total": 0,
    "strategy_id": "", "strategy_name": "", "codes_count": 0,
    "started_at": None, "finished_at": None, "elapsed": 0.0, "error": None,
}
_RECO_LOCK = threading.Lock()
_RECO_RESULT: dict | None = None
_RECO_CANCEL = threading.Event()      # 与 _BT_CANCEL 同理，见 cancel_task


def _reco_state_locked() -> dict:
    """`_RECO_TASK` 的深拷贝快照。**调用方必须已持有 `_RECO_LOCK`。**

    单独拆出来是因为 `threading.Lock` **不可重入**：若在持锁期间调用
    又会去加同一把锁的 `reco_task_state()`，线程会永久死锁 —— 而这把锁同时
    守着 `reco_task_state` / `reco_result`，一旦锁死，**进度轮询与取结果会一起
    超时**，整个接口看上去就是「启动后什么都不返回」。
    """
    snap = json.loads(json.dumps(_RECO_TASK))
    if snap.get("started_at") and snap.get("state") == "running":
        try:
            t0 = dt.datetime.strptime(snap["started_at"], "%Y-%m-%d %H:%M:%S")
            snap["elapsed"] = int((dt.datetime.now() - t0).total_seconds())
        except Exception:
            pass
    return snap


def reco_task_state() -> dict:
    """当前推荐任务状态（供轮询）：语义与 `task_state` 一致，深拷贝。"""
    with _RECO_LOCK:
        snap = _reco_state_locked()
    ctrl = _RECO_CTRL
    snap["paused"] = bool(ctrl.paused) if ctrl is not None else False
    return snap


def reco_result() -> dict | None:
    """最近一次**已完成**的推荐结果；还在跑 / 从未跑过则None。"""
    with _RECO_LOCK:
        return json.loads(json.dumps(_RECO_RESULT)) if _RECO_RESULT else None


def start_reco_task(strategy_id: str, params: dict | None, codes: list[str],
                    start: str | None = None, end: str | None = None,
                    initial_capital: float = 100000.0,
                    commission: float = 0.0003,
                    execution_date: str | None = None,
                    signal_date: str | None = None,
                    codes_resolver=None) -> dict:
    """启动一次后台推荐并**立即返回**；已有任务在跑时返回 ok=false（不排队）。

    `codes_resolver`：可选的「股票池解析函数」，给了它就在**后台线程**里调用
    并解析出最终 codes —— 与 `start_task` 同一套机制。

    为什么必须有它：`/recommend` 的股票池解析会**联网**（排除 ST 特征时要拉
    全市场股票名称，akshare 分批抓取实测 30~50 秒）。放在启动请求里同步做，
    前端 30 秒超时先到 → 用户看到「无法启动：请求被中止: 操作超时」，
    而后端其实还在跑。`/backtest/start` 早就为此改造过，本次是给
    `/recommend` 补上同一处 —— 两边共用同一个 `_resolve_codes()`，漏一处
    就会换个入口再踩一次同样的坑。

    并发上限为 1，与回测任务同理由：两个全市场并行会把 CPU 与 SQLite 读盘打满，
    而且前端一次也只看不了两个结果。
    """
    global _RECO_RESULT
    with _RECO_LOCK:
        if _RECO_TASK["state"] == "running":
            # ⚠️ 必须用 `_reco_state_locked()`，不能用 reco_task_state()——
            # 后者会再去加同一把不可重入的锁，当前线程会永久死锁。
            return {"ok": False, "error": "已有推荐在运行。可点「停止」中断它，或等它结束",
                    "state": _reco_state_locked()}
        # ⚠ 上一轮结果 = 本轮的**持仓基线**：三档要相对「上次建议」来判，
        #   否则昨天买入的票今天会凭空消失（既不在持股也不在卖出）。
        #   必须在清空 _RECO_RESULT **之前**取出来。
        prev_result = _RECO_RESULT
        _RECO_RESULT = None
        try:
            name = base.REGISTRY[strategy_id].name
        except Exception:
            name = strategy_id
        _RECO_TASK.update({
            "state": "running", "phase": "loading", "done": 0,
            "total": len(codes or []), "strategy_id": strategy_id,
            "strategy_name": name, "codes_count": len(codes or []),
            "started_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "finished_at": None, "elapsed": 0.0, "error": None,
        })

    def _on_progress(done: int, total: int):
        with _RECO_LOCK:
            if done < 0:                 # 哨兵：进入不可细分的策略计算阶段
                _RECO_TASK["phase"] = "running"
                _RECO_TASK["done"] = _RECO_TASK["total"]
            else:
                _RECO_TASK["phase"] = "loading"
                #⚠️ `done` / `total` 是**当前批次**的计数（`run_recommend` 分批
                # 预检，第 21 项为了压内存峰值引入的），不是整个股票池的。
                # 这里必须换算成全局进度，否则：
                #   * 直接写 total 会把「总只数」改成「本批只数」——
                #     1000 只的池会被显示成 500，进度条永远到不了 100%；
                #   * 直接写 done 会让进度在批与批之间**倒退**。
                # 已完成的批数 = 总数 - 本批总数，本批已完成 done。
                overall = _RECO_TASK["total"]
                left = max(0, int(total) - int(done))
                _RECO_TASK["done"] = max(0, min(overall, overall - left))

    def _work():
        global _RECO_CTRL
        nonlocal codes
        t0 = time.time()
        _RECO_CTRL = base.RunControl()
        base.install_control(_RECO_CTRL)
        try:
            if codes_resolver is not None:
                # 解析股票池（含联网拉股票名称，实测 30~50 秒）放到后台，
                # 别卡启动响应 —— 与 `start_task` 同一套做法。
                try:
                    codes, _reco_extra = _resolve_pool(codes_resolver)
                except Exception as exc:
                    with _RECO_LOCK:
                        _RECO_TASK.update(state="error", phase="done",
                                          finished_at=dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                          elapsed=round(time.time() - t0, 1),
                                          error=f"{type(exc).__name__}: {exc}")
                    return
                if not codes:
                    with _RECO_LOCK:
                        _RECO_TASK.update(state="error", phase="done",
                                          finished_at=dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                          elapsed=round(time.time() - t0, 1),
                                          error="股票池为空，请先选择回测范围")
                    return
                with _RECO_LOCK:
                    _RECO_TASK["total"] = len(codes)
                    _RECO_TASK["codes_count"] = len(codes)
            try:
                out = run_recommend(strategy_id, params, codes, start, end,
                                   initial_capital, commission,
                                   progress=_on_progress,
                                   execution_date=execution_date,
                                   signal_date=signal_date,
                                   baseline=prev_result)
            except base.Cancelled:
                with _RECO_LOCK:
                    _RECO_TASK.update(
                        state="cancelled", phase="done",
                        finished_at=dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        elapsed=round(time.time() - t0, 1),
                        error="已被用户停止")
                return
            global _RECO_RESULT
            _RECO_RESULT = out
            with _RECO_LOCK:
                _RECO_TASK.update(
                    state="ready", phase="done", done=_RECO_TASK["total"],
                    finished_at=dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    elapsed=round(time.time() - t0, 1), error=None)
        except Exception as exc:                # noqa: BLE001 - 后台线程要把错误带出去
            with _RECO_LOCK:
                _RECO_TASK.update(
                    state="error", phase="done",
                    finished_at=dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    elapsed=round(time.time() - t0, 1),
                    error=f"{type(exc).__name__}: {exc}")
        finally:
            base.install_control(None)
            _RECO_CTRL = None

    threading.Thread(target=_work, name="recommend-task", daemon=True).start()
    return {"ok": True, "state": reco_task_state()}


# ---- 异步回测任务（第 18 项）：解决「全市场回测必定超时」----
# 全市场 5000+ 只冷缓存读库要几分钟，而前端是**一次同步请求 + 固定超时**
# （实测 5585 只直接「请求被中止：操作超时」）。这不是网络问题，是结构上必然超时
# —— 把超时从 90 秒调到 10 分钟只会让用户对着白屏干等，且中途看不到任何进展。
# 改成与 SCR90 周榜同一套模式：start 立即返回 -> progress 轮询 -> result 取结果。
_BT_TASK: dict = {
    "state": "idle",       # idle / running / ready / error
    "phase": "",           # loading（可细分）/ running（策略计算，不可细分）/ done
    "done": 0, "total": 0,
    "strategy_id": "", "strategy_name": "", "codes_count": 0,
    "started_at": None, "finished_at": None, "elapsed": 0.0, "error": None,
}
_BT_LOCK = threading.Lock()
_BT_RESULT: dict | None = None
# 最近一次回测的逐笔明细：**只**给报告落盘用（`last_trades()`），不进 HTTP 响应。
# 全市场一次回测能到十几万笔（实测 15 万+），随响应下发就是 24 MB JSON、
# 取结果要 10 秒 —— 而前端只在展开某只票时才需要明细（走 /backtest/trades 按需取）。
_BT_TRADES: list = []


def last_trades() -> list:
    """最近一次回测的逐笔明细（供报告落盘；不下发给前端）。"""
    return _BT_TRADES


# ---- 暂停 / 继续 / 停止 ----
# 控制句柄本身在 base.RunControl（协作式，语义与生效边界见那里的 docstring）。
# 这里只持有「本次任务用的那一个」，供 HTTP 层跨请求地置标志。
_BT_CTRL: "base.RunControl | None" = None
_RECO_CTRL: "base.RunControl | None" = None


def _control_state(ctrl, running: bool, extra: dict | None = None) -> dict:
    out = {"ok": True, "running": running, "paused": bool(ctrl.paused) if ctrl else False}
    if extra:
        out.update(extra)
    return out


def pause_task() -> dict:
    """暂停当前回测 / 推荐。可重复调用（已暂停时返回 ok）。"""
    if _BT_CTRL is not None:
        _BT_CTRL.request_pause()
    if _RECO_CTRL is not None:
        _RECO_CTRL.request_pause()
    return _control_state(_BT_CTRL or _RECO_CTRL,
                          (_BT_CTRL is not None) or (_RECO_CTRL is not None),
                          {"message": "已请求暂停：任务会在下一个检查点停下"})


def resume_task() -> dict:
    """继续被暂停的任务。"""
    if _BT_CTRL is not None:
        _BT_CTRL.resume()
    if _RECO_CTRL is not None:
        _RECO_CTRL.resume()
    return _control_state(_BT_CTRL or _RECO_CTRL,
                          (_BT_CTRL is not None) or (_RECO_CTRL is not None),
                          {"message": "已继续"})


def cancel_task() -> dict:
    """停止当前回测 / 推荐。**幂等**，没在跑也返回 ok。

    为什么不做成「后端直接杀线程」：Python 没法安全杀线程，硬杀会让 SQLite
    连接与 `_PER_CACHE` 停在半途。这里只置标志，由回测自己在检查点抛
    `Cancelled` 正常 unwind —— 数据层与缓存都能干净收尾。

    ⚠️ **生效边界**：检查点只有两处 —— 数据加载阶段的每批进度回调，
    以及策略自己循环里的 `base.checkpoint()`。没写 checkpoint 的策略，
    在它自己的计算循环里停不住（详见 base.RunControl 的 docstring）。
    """
    if _BT_CTRL is not None:
        _BT_CTRL.request_cancel()
    if _RECO_CTRL is not None:
        _RECO_CTRL.request_cancel()
    running = (_BT_CTRL is not None) or (_RECO_CTRL is not None)
    return {"ok": True, "requested": running,
            "message": "已请求停止：任务会在下一个检查点退出" if running
                       else "当前没有正在运行的任务"}


def _bt_state_locked() -> dict:
    """`_BT_TASK` 的深拷贝快照。**调用方必须已持有 `_BT_LOCK`。**

    拆出来的原因同 `_reco_state_locked`：`threading.Lock` 不可重入，
    持锁期间调用会再加锁的 `task_state()` 会永久死锁；而这把锁同时守着
    `task_state` / `task_result`，锁死之后**进度轮询与取结果会一起超时**。
    """
    snap = json.loads(json.dumps(_BT_TASK))
    if snap.get("started_at") and snap.get("state") == "running":
        try:
            t0 = dt.datetime.strptime(snap["started_at"], "%Y-%m-%d %H:%M:%S")
            snap["elapsed"] = int((dt.datetime.now() - t0).total_seconds())
        except Exception:
            pass
    return snap


def task_state() -> dict:
    """当前任务状态（供轮询）：深拷贝，不暴露内部可变对象。

    `elapsed` 在**运行中也要有值** —— 只在结束时写的话，进度条旁边的时间会一直
    显示 0s，用户以为卡住了。这里按 `started_at` 实时算。

    `paused` 从控制器读（而不是存进 _BT_TASK）：暂停是**控制句柄的状态**，
    存在两处就会出现「任务说没暂停但实际卡住」的不一致。
    """
    with _BT_LOCK:
        snap = _bt_state_locked()
    ctrl = _BT_CTRL
    snap["paused"] = bool(ctrl.paused) if ctrl is not None else False
    return snap


def task_result() -> dict | None:
    """最近一次**已完成**回测的完整结果（没有则 None）。"""
    return _BT_RESULT


def start_task(strategy_id: str, params: dict | None, codes: list[str],
               start: str | None, end: str | None,
               initial_capital: float = 100000.0, commission: float = 0.0003,
               stamp_duty: float = 0.001, slippage: float = 0.0005,
               min_commission: float = 5.0, lot_size: int = 100,
               benchmark: str | None = None, window: int | None = None,
               universe_mode: str = "interval_data",
               ruin_threshold=None,
               codes_resolver=None) -> dict:
    """启动一次后台回测并**立即返回**；已有任务在跑时返回 ok=false（不排队）。

    `codes_resolver`：可选的「股票池解析函数」。给了它就在**后台线程**里调用
    并解析出最终 codes —— 全市场冷缓存解析可能要扫几千只，放在启动请求里同步做
    会让前端 `/backtest/start` 先超时（用户看到的「无法启动：操作超时」就是它）。

    并发上限为 1：两个全市场回测并行会把 CPU 与 SQLite 读盘打满，反而比串行更慢，
    且用户界面一次也只看不了两个结果。
    """
    global _BT_RESULT
    with _BT_LOCK:
        if _BT_TASK["state"] == "running":
            # ⚠️ 必须用 `_bt_state_locked()`，不能用 task_state()——后者会再去加
            # 同一把不可重入的锁，导致本线程（连带所有轮询）永久死锁。
            # 并发上限是 1（两个全市场并行会把 CPU 与读盘打满，反而更慢）。
            # 这里**必须**给出出路：全市场一次实测约 13 分钟，只让用户干等没有意义。
            # 「停止」是真存在的（POST /backtest/cancel），不再指向一个不存在的按钮。
            return {"ok": False, "error": "已有回测在运行。可点「停止」中断它后重跑，"
                                          "或等它跑完（回测期间只能同时跑一个）",
                    "state": _bt_state_locked()}
        _BT_RESULT = None
        try:
            name = base.REGISTRY[strategy_id].name
        except Exception:
            name = strategy_id
        _BT_TASK.update({
            "state": "running", "phase": "loading", "done": 0,
            "total": len(codes or []), "strategy_id": strategy_id,
            "strategy_name": name, "codes_count": len(codes or []),
            "started_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "finished_at": None, "elapsed": 0.0, "error": None,
        })

    def _on_progress(done: int, total: int):
        with _BT_LOCK:
            if done < 0:                 # 哨兵：进入不可细分的策略计算阶段
                _BT_TASK["phase"] = "running"
            else:
                _BT_TASK["phase"] = "loading"
                _BT_TASK["done"] = int(done)
                if total:
                    _BT_TASK["total"] = int(total)

    def _work():
        global _BT_CTRL
        nonlocal codes
        scope_extra: dict = {}
        t0 = time.time()
        # 装控制器：装上之后 base.checkpoint() 才不是 no-op，
        # 暂停/停止标志也需要一个跨请求可见的落脚点。
        _BT_CTRL = base.RunControl()
        base.install_control(_BT_CTRL)
        try:
            if codes_resolver is not None:
                # 解析股票池（全市场冷缓存可能要扫几千只）放到后台，别卡启动响应
                try:
                    # resolver 可返回 (codes, detail)：detail 是范围明细
                    #（每项剔除只数、判定基准日），要写进报告 scope
                    codes, scope_extra = _resolve_pool(codes_resolver)
                except Exception as exc:
                    with _BT_LOCK:
                        _BT_TASK.update(state="error", phase="done",
                                        finished_at=dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                        elapsed=round(time.time() - t0, 1),
                                        error=f"{type(exc).__name__}: {exc}")
                    return
                if not codes:
                    with _BT_LOCK:
                        _BT_TASK.update(state="error", phase="done",
                                        finished_at=dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                        elapsed=round(time.time() - t0, 1),
                                        error="股票池为空，请先选择回测范围")
                    return
                with _BT_LOCK:
                    _BT_TASK["total"] = len(codes)
                    _BT_TASK["codes_count"] = len(codes)
            try:
                out = run_backtest(strategy_id, params, codes, start, end,
                                   initial_capital, commission, stamp_duty,
                                   slippage, min_commission, lot_size,
                                   benchmark, window, universe_mode,
                                   ruin_threshold, scope_extra=scope_extra,
                                   progress=_on_progress)
            except base.Cancelled:
                # 用户主动停止：**不是错误**。用 paused/cancelled 状态，
                # 界面上按中性提示显示，绝不弹红色报错或堆栈。
                with _BT_LOCK:
                    _BT_TASK.update(state="cancelled", phase="done",
                                    finished_at=dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                    elapsed=round(time.time() - t0, 1),
                                    error="已被用户停止（已完成部分不保留结果）")
                return
            global _BT_RESULT
            _BT_RESULT = out
            try:
                from strategies import report as _report
                saved = _report.save_report(out)
                out["report"] = {"saved": bool(saved.get("ok")),
                                 "path": saved.get("path"),
                                 "error": saved.get("error")}
            except Exception as exc:
                out["report"] = {"saved": False, "path": None, "error": str(exc)}
            with _BT_LOCK:
                _BT_TASK.update(state="ready", phase="done", done=_BT_TASK["total"],
                                finished_at=dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                elapsed=round(time.time() - t0, 1), error=None)
        except Exception as exc:                # noqa: BLE001 - 后台线程要把错误带出去
            with _BT_LOCK:
                _BT_TASK.update(state="error", phase="done",
                                finished_at=dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                elapsed=round(time.time() - t0, 1),
                                error=f"{type(exc).__name__}: {exc}")
        finally:
            # 必须卸掉：否则这次任务的控制器会留给下一次无关的调用去用，
            # 而 base.RUN_CONTROL 是**模块级**的（见 base 的说明）。
            base.install_control(None)
            _BT_CTRL = None

    threading.Thread(target=_work, name="backtest-task", daemon=True).start()
    return {"ok": True, "state": task_state()}


def trades_for(rid: str, code: str) -> list[dict]:
    """按 result_id 取某只的逐笔交易明细（前端展开时按需调用）。

    上下文是**单槽缓存**（只留最近一次回测）；过期/不匹配直接报错，让前端提示重跑。

    ⚠️ 直接从已算好的全市场明细里过滤，**不再单票重算**：
    旧实现有独立的 `stats.trade_detail()`，自己扫权重序列并用硬编码的 100000
    折算股数，于是同一笔交易在「全市场明细」与「单票明细」里口径不一致，
    报告无法交叉核对。组合权重口径下逐笔 pnl 就是全市场口径。
    """
    if not _BT_CTX or _BT_CTX["rid"] != rid:
        raise ValueError("回测结果已过期，请重新运行回测")
    return stats.trades_for_code(_BT_TRADES, code)
