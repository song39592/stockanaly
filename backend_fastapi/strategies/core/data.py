# -*- coding: utf-8 -*-
"""【策略包唯一数据入口】—— 策略/回测所需的行情只能从这里取。

约束：
  * 本文件是策略包内**唯一**允许 `import price_store` / 调网络 的地方。
  * 策略实现文件只能 `from .data import load_bars / list_universe_codes`，
    不得自行读库、读文件或调 akshare / 网络。
  * 每个策略自己决定拉哪些字段（fields），满足「各策略数据不同」。

K 线本身**不经本文件直连存储**，而是走 `kline_service.get_bars()`——全项目唯一的
「取数 + 周期合样」出口（与个股页 K 线、指标、筹码共用同一份实现）。
回测**固定日线**：`stats.py` 的年化与夏普按 **252 交易日/年**折算（见其中 `252.0`），
若换成周 / 月序列，这两个指标会静默算错；真要做周期回测，必须先改 stats 的年化口径，
并重新定义「T+1 成交」「持仓天数」等语义，故此处不开放 period。

load_bars 支持批量取数并按 fields 切片，且按 (code, start, end, adjust) **逐标的缓存**
底层全量 OHLCV；「回测预检」「策略拉数」「统计拉数」多次调用（即使字段不同、code 集合不同）
都只触发一次真实取数，不会重复读库/读文件。

默认后复权（hfq）口径（回测用，反映真实持仓盈亏）。技术指标展示用前复权，请勿混用。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import sys
import time

import pandas as pd

import kline_service
import price_store
from core import db as _core_db

# 只取异常类型（运行控制住在独立模块，避免数据层反向依赖契约层 base）
from .runcontrol import Cancelled

# 对外只暴露 OHLCV：保持与原先（经 indicators.data.get_ohlcv）一致的列集合，
# 避免 amount / turnover 等列混进策略的 DataFrame 改变既有行为。
_BAR_COLUMNS = ("open", "high", "low", "close", "volume")
_MIN_ROWS = 2          # 少于 2 根无法算信号 / 回测，视为数据不足


# (code, start, end, adjust) -> DataFrame | None（逐标的缓存全量 OHLCV）
_PER_CACHE: dict = {}


_IO_WORKERS = 6   # 并行读库线程数：SQLite WAL + 每线程独立连接，纯读安全


def load_bars(codes, start: str | None = None, end: str | None = None,
              fields: list[str] | None = None, adjust: str = "hfq",
              progress=None) -> dict[str, pd.DataFrame]:
    """批量取数：返回 {code: DataFrame[fields]}（只含成功加载的标的）。

    参数：
      codes  单只字符串或列表，自动归一化、去空白。
      fields 需要的列，如 ["close"] / ["close","volume"]；None 取全部
             open/high/low/close/volume。不存在的列会被忽略。
      start/end/adjust 与 indicators.data.get_ohlcv 一致。

    缓存：每个 (code, start, end, adjust) 只取一次，按 fields 切片返回，
          因此同一标的不同字段的多次请求零额外 I/O。
    """
    if isinstance(codes, str):
        codes = [codes]
    codes = [str(c).strip() for c in codes if str(c).strip()]
    if not codes:
        return {}

    cols = [c for c in (fields or []) if c]
    out: dict[str, pd.DataFrame] = {}

    # 只取未命中的：全量回测（5000+ 只）冷缓存时串行读库要几分钟，
    # 这里用线程池并行读——db.connect 每次新建独立 SQLite 连接（WAL），
    # 线程间无共享连接，安全。命中缓存的直接用，不进线程池。
    def _fetch(code: str):
        try:
            # 统一的 K 线出口（回测固定日线，原因见模块 docstring）。
            # 第 22 项：`fields` 一路下推进去 —— 以前这里恒读全部列再切两次，
            # 而调用方几乎都只要 close 1 列（实测多读 14 列、pandas 多建十几次
            # 分配，是 load_bars 比底层 list_bars 慢 4 倍的主因）。
            d = kline_service.get_bars(code, start=start, end=end, adjust=adjust,
                                       period="day", fields=cols or None)
            if d is None or d.empty:
                return None
            keep = [c for c in _BAR_COLUMNS if c in d.columns]
            if keep and list(d.columns) != keep:
                d = d[keep]
            # ⚠️ **必须 intern**：全市场 5578 只 × 730 天，每个 Series 的索引里
            #    都是各自独立的 str 对象 —— 不共享就是 400 万个重复字符串（约 240MB），
            #    在可用内存只有 1.2GB 的机器上这是决定性的。intern 后全进程只保留
            #    ~2000 个交易日对象，全市场回测省下约 240MB。
            #    字符串不可变，intern 不改变任何行为（`str(d)[:10]` 的结果一样）。
            d.index = pd.Index([sys.intern(str(x)[:10]) for x in d.index])
            return d if len(d) >= _MIN_ROWS else None
        except Exception:                    # noqa: BLE001 - 单只失败不拖垮整批
            return None

    # ⚠️ 缓存键**必须包含 fields**（第 22 项）：`_PER_CACHE` 存的是已按列裁剪的
    # DataFrame，若键里没有 fields，先取 `["close"]` 再取 `["close","volume"]`
    # 会直接命中前者、拿到**缺 volume 的表**——而且不报错，是静默的错数据。
    _fkey = tuple(cols)
    missing = [(c, (c, start, end, adjust, _fkey)) for c in codes
               if (c, start, end, adjust, _fkey) not in _PER_CACHE]
    # 进度回调（第 18 项回测进度条）：`ex.map` 是**惰性**的、按提交顺序 yield，
    # 所以每取完一只就报一次「已测/总数」，不用改线程池逻辑。
    # 缓存命中的票瞬间完成（done 一次到位），故基数用 missing 而非全部 codes ——
    # 否则冷热混跑时进度会「先跳一段再慢慢爬」。
    total = len(missing)
    done = 0

    def _tick():
        nonlocal done
        done += 1
        if progress is not None:
            try:
                progress(done, total)
            except Cancelled:
                # ⚠️ **必须放行**：下面那个 `except Exception: pass` 本身是对的
                #   （进度回调不该让取数失败），但它会连「用户点了停止」一起吃掉 ——
                #   实测就踩过：勾了停止，进度照样跑到底，状态永远停在 running。
                #   Cancelled 不是「进度回调出错」，是「整个运行要结束了」。
                raise
            except Exception:            # 进度回调不该影响取数
                pass

    if len(missing) > 1:
        with ThreadPoolExecutor(max_workers=_IO_WORKERS) as ex:
            for (code, key), df in zip(missing, ex.map(_fetch, [m[0] for m in missing])):
                _PER_CACHE[key] = df
                _tick()
    elif missing:
        code, key = missing[0]
        _PER_CACHE[key] = _fetch(code)
        _tick()

    for code in codes:
        df = _PER_CACHE.get((code, start, end, adjust, _fkey))
        if df is None:
            continue
        # `_fetch` 已按 cols 裁剪过，这里只需保证调用方拿到的列齐全
        out[code] = df[[c for c in cols if c in df.columns]] if cols else df
    return out


def release_cache(codes: list[str], adjust: str | None = None,
                  start: str | None = None, end: str | None = None) -> int:
    """释放指定标的在**逐标的缓存**里的条目，返回释放条数。

    为什么需要（第 21 项的内存实测）：`_PER_CACHE` 是**进程级**、**无上限**的，
    全市场一次 recommend/backtest 就会把 5000+ 只 × 250 天的 OHLCV 全留在内存里
    —— 实测后端常驻内存涨到 2GB、系统只剩 3.1GB 可用，于是 GC 与换页把
    「小样本实测 444s」的耗时放大到实测 946s。缓存本身是对的（复用、免重复读盘），
    问题只在于**没人控制它的体量**。

    因此分批处理某个大任务时，取完一批就调它把这一批还回去：
    缓存的收益（同一批内不重复读）完全保留，跨批的占用则被压住。

    `adjust` / `start` / `end` 传None 表示该维度不限（只按 code 匹配）。
    不需要按 `fields` 过滤：同一只票的不同列组合都放掉更安全（宁可重读，
    也不能留下一个占着内存的条目）。
    ⚠️ 释放后若别处仍需要这些标的，`load_bars` 会**重新读盘**（结果一致，
    只是多花时间）——所以只在"确定后面不再用"的批次边界调用。
    """
    want = set(str(c).strip() for c in codes)
    if not want:
        return 0
    drop = [k for k in _PER_CACHE
            if k[0] in want
            and (adjust is None or k[3] == adjust)
            and (start is None or k[1] == start)
            and (end is None or k[2] == end)]
    for k in drop:
        _PER_CACHE.pop(k, None)
    return len(drop)


UNIVERSE_MODES = ("interval_data", "pool_snapshot", "index_constituent")


def list_universe_codes(universe_mode: str = "interval_data",
                        start: str | None = None, end: str | None = None) -> list[str]:
    """列出回测候选股票代码，按 universe_mode 决定口径。

    三档（见 ARCHITECTURE.md「幸存者偏差」）：
      interval_data（默认）
          本地已下载、且**在 [start,end] 内有足够行情**的全部代码。
          实现上先取「本地全部代码」，再由编排层的 _prepare 按区间内根数过滤，
          所以**已退市但历史行情仍在库的票会被纳入**（旧口径把它们按当前代码表
          排除在「当前还活着」之外，正是幸存者偏差的主要来源）。
          ⚠️ 仍**不包含**「从未下载过行情」的票——本地无数据就是无法回测，
          偏差存在是事实，只能显式声明（见 survivorship_note），藏起来才是问题。
      pool_snapshot
          `pool_members` 的**时点**成分，最严格；requires_pool=True 的策略用这档。
      index_constituent
          历史指数成分股（中证全指等）—— **尚未接入**，回退到 interval_data 并
          在 flags 里标注，绝不假装自己用了历史成分股。
    """
    mode = str(universe_mode or "interval_data")
    if mode == "pool_snapshot":
        try:
            members = load_pool_members(start=start, end=end)
            codes = {c for row in members.values() for c in row}
            return sorted(codes)
        except Exception:                    # noqa: BLE001 - 无池历史则退回默认口径
            return sorted(price_store.code_latest_dates("raw").keys())
    # interval_data / index_constituent（后者待接入，暂同前者）
    return sorted(price_store.code_latest_dates("raw").keys())


# ---------------------------------------------------------------- 名称 / 上市日 / 市值（原始数据）
# 供 universe（股票池层）与 backtest（编排层）复用；这里只取数，不做「范围规则」判断。

_NAME_TTL = 24 * 3600
_name_cache: dict = {"at": 0.0, "map": {}}


def load_names(codes: list[str]) -> dict[str, str]:
    """批量取股票名称：akshare 全市场表一次拉齐，失败静默降级为空映射。

    timeout=90：实测这张全市场表**冷启动要 50.8s**（5572 行），给 30s 会超时 →
    返回空映射 → 周榜/回测的**名称整列空白**。名称是「锦上添花」，但整列空白很显眼，
    所以宁可多等。结果进程内缓存 24h。
    """
    now = time.time()
    if not _name_cache["map"] or now - _name_cache["at"] > _NAME_TTL:
        try:
            import akshare as ak
            from market_service import _ak          # 统一的超时/降级封装
            df = _ak(ak.stock_info_a_code_name, timeout=90)
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


def code_first_dates() -> dict[str, str]:
    """{code: 最早日 K 日期}（raw 口径，≈上市日）。"""
    return price_store.code_earliest_dates()


def market_caps(codes) -> dict:
    """{code: 总市值(元)}：总市值 = 最新总股本 × 最新收盘价（raw）。

    总股本取 share_capital 最新一条；收盘价取 price_store.bulk_latest_close。
    任一项缺失的票不进入结果（保守：它不会被当作微盘股剔除）。
    """
    closes = price_store.bulk_latest_close()
    with _core_db.connect(_core_db.FACTORS_DB) as conn:
        rows = conn.execute(
            "SELECT code, total_shares FROM share_capital "
            "WHERE (code, as_of) IN (SELECT code, MAX(as_of) "
            "FROM share_capital GROUP BY code)"
        ).fetchall()
    shares = {r["code"]: r["total_shares"] for r in rows if r["total_shares"]}
    caps: dict = {}
    for code in codes:
        s = shares.get(code)
        c = closes.get(code)
        if s and c:
            caps[code] = float(s) * float(c)
    return caps


def survivorship_note(universe_mode: str, n_codes: int) -> str:
    """幸存者偏差声明 —— **非 pool_snapshot 模式下必须写进报告 scope.warnings**。

    不允许静默：偏差无法消除（本地没有「从未下载过」的票的历史），
    但读者必须知道结果是建立在什么样的样本上，否则指标会被误当成无偏估计。
    """
    mode = str(universe_mode or "interval_data")
    if mode == "pool_snapshot":
        return (f"股票池口径：成员取自 pool_members 的时点成分（{n_codes} 只），"
                f"幸存者偏差最小。")
    if mode == "index_constituent":
        return (f"⚠️ 幸存者偏差：index_constituent 尚未接入，实际按 interval_data "
                f"执行（{n_codes} 只）——本地未下载过行情的历史成分股不在样本内。")
    return (f"⚠️ 幸存者偏差：样本为「本地已下载且区间内有行情」的 {n_codes} 只，"
            f"含已退市但行情仍在库的票，但**不含从未下载过行情的历史成分股**。"
            f"绝对收益不可当作无偏估计，跨策略横向比较仍然有效。")


def load_bars_raw_for(codes, start: str | None = None,
                      end: str | None = None) -> dict[str, pd.DataFrame]:
    """取**未复权** OHLCV，供统计层做「整手 / 涨跌停 / 停牌」可行性判定。

    ⚠️ 必须用 raw：涨跌停是真实市场规则，作用于原始价；hfq 会平滑除权跳空，
    把真实的涨跌停抹平。净值侧仍用 hfq（消除除权假盈亏），两者分工不同、不可混用。

    **只对权重变动过的 code 调**（见 backtest.run_backtest）：全市场日频换手下
    变动过的票通常只有百量级，全取会把内存重新推向 2GB 的老问题。
    """
    return load_bars(codes, start, end,
                     fields=["high", "low", "close", "volume"], adjust="raw")


def load_pool_members(start: str | None = None,
                      end: str | None = None) -> dict[str, dict[str, dict]]:
    """股票池成员（按日期），策略包唯一取数入口内转发到 history 存储。

    仅 RPS 等依赖股票池历史的策略使用；空库优雅返回 {}。
    """
    from features.history import store as _hist
    return _hist.load_pool_members(start=start, end=end)


def pool_snapshot_range() -> tuple[str | None, str | None]:
    """股票池快照的 (最早日, 最晚日)，无数据返回 (None, None)。"""
    from features.history import store as _hist
    return _hist.pool_snapshot_range()


def _rps_num(x):
    """RPS 值清洗：None / NaN → None，否则 float。"""
    if x is None:
        return None
    try:
        if pd.isna(x):
            return None
    except (TypeError, ValueError):
        return None
    return float(x)


def load_strong_pool(start: str, end: str, threshold: int = 90,
                     periods=(50, 120, 250), warmup_days: int = 30):
    """从全市场日线**自行计算** RPS 强势池 —— 与「RPS 页面」同源（indicators.rps_data.market_rps）。

    返回 (pool_by_date, change_by_date, error)：
      pool_by_date   {date: {code: {"rps50","rps120","rps250"}}}  当日强势股
      change_by_date {date: {code: 当日涨幅(百分比)}}             后复权收盘价环比 ×100
      error          str | None  取数/计算失败原因（无则 None）

    强势股口径与 RPS 页面**完全一致**：取 max(RPS50, RPS120, RPS250) >= 阈值 的票；
    这样「页面一套、策略一套」的 RPS 口径分歧不再存在。RPS 策略不再依赖手动导入的
    股票池快照（pool_members），而是从日线现算，回测区间由历史数据覆盖即可。

    设计点（呼应策略包冒烟测试）：本函数不抛异常，失败统一以 error 返回空结果，
    由调用方决定是否降级 —— 因此 LOAD 期冒烟（无横截面数据）也能安全返回 []。
    """
    try:
        import indicators.rps_data as rps_data
    except Exception as exc:                       # noqa: BLE001 - 引擎不可用降级
        return {}, {}, f"RPS 引擎不可用：{exc}"
    try:
        lo = (dt.date.fromisoformat(str(start)[:10])
              - dt.timedelta(days=warmup_days)).isoformat()
        panel = rps_data._market_panel("day")
        if panel is None or panel.empty:
            return {}, {}, "全市场横截面数据不可用（请先下载日线历史）"
        panel_dates = sorted(str(x)[:10] for x in panel.index)
        sel = [d for d in panel_dates if lo <= d <= str(end)[:10]]
        if not sel:
            return {}, {}, "回测区间无交易日"

        rps = rps_data.market_rps(sel, list(periods))
        r50, r120, r250 = rps[50], rps[120], rps[250]

        # 与 RPS 页面完全一致：取三期 RPS 的最大值，>= 阈值即强势股
        m12 = r50.where(r50 >= r120, r120)
        combo = m12.where(m12 >= r250, r250)

        pool: dict[str, dict[str, dict]] = {}
        for d in sel:
            hi = combo.loc[d]
            hi = hi[hi >= threshold]
            row: dict[str, dict] = {}
            for code in hi.index:
                c = str(code)
                row[c] = {
                    "rps50": _rps_num(r50.at[d, code]),
                    "rps120": _rps_num(r120.at[d, code]),
                    "rps250": _rps_num(r250.at[d, code]),
                }
            pool[d] = row

        # 当日涨幅：后复权收盘价环比（×100 转百分比，与旧 import_change 口径一致）
        strong_set = {c for row in pool.values() for c in row}
        col_str = {str(c): c for c in panel.columns}
        strong_codes = [col_str[s] for s in strong_set if s in col_str]
        change: dict[str, dict[str, float | None]] = {}
        if strong_codes:
            chg_panel = panel.loc[:, strong_codes].pct_change()
            str_of = {c: str(c) for c in strong_codes}
            for d in sel:
                try:
                    s = chg_panel.loc[d]
                except KeyError:
                    change[d] = {}
                    continue
                change[d] = {
                    str_of[c]: (None if pd.isna(v) else float(v) * 100.0)
                    for c, v in s.items()
                }
        return pool, change, None
    except Exception as exc:                       # noqa: BLE001 - 计算失败降级
        return {}, {}, f"RPS 强势池计算失败：{exc}"


def recent_trading_days(n: int = 5) -> list[str]:
    """本地最新的 N 个交易日日期（升序），用于「近期策略成果」这类短窗口回测。

    交易日是**全市场统一**的，任取一只票的日历即可；优先用大盘代表票，
    本地没有时退回股票池里任意一只。
    """
    refs = ["000001", "600000", "600519", "000300"] + list_universe_codes()[:10]
    for code in refs:
        try:
            df = kline_service.get_bars(code, period="day", adjust="raw")
        except Exception:                        # noqa: BLE001 - 换下一只做日历
            continue
        if df is not None and len(df) >= n:
            return [str(x)[:10] for x in df.index[-n:]]
    return []
