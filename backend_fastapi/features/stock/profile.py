# -*- coding: utf-8 -*-
"""个股「基本信息」：行业 / 市值 / 前十大股东 / 涨停与连板。

数据分三类，取数策略不同：
  * 行情与市值（现价 / 总市值 / 流通市值 / 股本）——腾讯行情，经 `share_service`，秒级；
  * 行业 / 上市时间 / 主营业务 / 十大股东——akshare（东财），慢且有频率限制，
    故带 **TTL 缓存**（股东数据一季度才变一次，缓存 6 小时足够）；
  * 涨停与连板——**本地日线现算**，不联网：日 K 就在库里，
    比逐日回溯东财涨停池（一天一次请求）快几个数量级，也不受接口频率限制。

涨停判定用「理论涨停价」而非涨跌幅阈值：
    limit_price = round(前收 × (1 + 涨跌幅限制), 2)，收盘价 >= limit_price - 0.005 即为涨停
这样能避开四舍五入导致的 9.98% / 20.02% 一类误判，也不会把 9.5% 误当成涨停。
"""
from __future__ import annotations

import datetime as dt
import time
from typing import Any

import akshare as ak
from core import price_service
from core import price_store
from core import share_service
from core.fallback import first_ok             # 第 09/bug-02 项：串行多源回退的总时间预算
from core.jsonutil import json_safe          # 第 09 项：清洗逻辑已公共化
from features.market.service import _ak

_CACHE: dict[str, tuple[float, Any]] = {}
_TTL = 6 * 3600                      # 股东 / 行业这类低频数据的缓存时长（秒）

# 锁仓系数的落盘缓存（见 `core.price_store.lockup_cache`）：
#  · 股东数据按**季度**披露，缓存按「报告期」判新鲜，而不是死 TTL；
#  · `_LOCKUP_DISCLOSURE_LAG_DAYS` 是法定披露时限（季报约 1 个月、年报 4 个月）再留的余量，
#    用来算「到今天为止应已出齐的最新报告期」；
#  · `_LOCKUP_MIN_REFRESH_DAYS` 只在「缓存报告期旧于应有水平」时生效（新季已出、
#    或该股数据源只到更早的期），限制重试频率，避免全市场天天打东财接口；
#    报告期已最新的记录**一次请求都不发**，整个季度直接复用。
_LOCKUP_DISCLOSURE_LAG_DAYS = 40
_LOCKUP_MIN_REFRESH_DAYS = 7
_LOCKUP_MIN_PCT = 5.0               # 缓存表不区分 min_pct，只对举牌线口径生效

# 取不到十大流通股东时的兜底口径：按**全流通**处理（r=0、L=1.0，不修正换手率）。
# 次新股（上市不足一个季报周期、尚未披露首份季报）、停牌/退市/长期无交易、
# 以及数据源缺期的股票都会走这里——它们本来也算不出锁仓，中性不修正最稳妥。
_FULL_CIRCULATION_RATIO = 0.0
_FULL_CIRCULATION_FACTOR = 1.0


def _cached(key: str, producer) -> Any:
    now = time.time()
    hit = _CACHE.get(key)
    if hit is not None and now - hit[0] < _TTL:
        return hit[1]
    value = producer()
    _CACHE[key] = (now, value)
    return value


def clear_cache() -> None:
    _CACHE.clear()


def limit_pct(code: str) -> float:
    """涨跌幅限制：主板 10%，创业板 / 科创板 20%，北交所 30%。"""
    c = str(code).zfill(6)
    if c.startswith(("43", "83", "87", "88", "92")):
        return 0.30
    if c.startswith(("30", "68")):
        return 0.20
    return 0.10


def limit_up_stats(code: str) -> dict:
    """用本地**不复权**日线算涨停与连板。

    返回：last_date（上次涨停日）/ last_streak（最近一次连板高度，当前未涨停则为最近那段）
    / max_streak（历史最大连板高度）/ max_range（那段起止）/ total（历史涨停次数）
    / top_runs（板数最多的 5 段）。
    """
    empty = {"last_date": None, "last_streak": 0, "max_streak": 0, "max_range": None,
             "total": 0, "top_runs": [], "bars": 0, "error": None}
    try:
        bars = price_store.load_bars(code, adjust="raw")
    except Exception as exc:                       # noqa: BLE001 - 指纹校验失败也要给出结论
        empty["error"] = str(exc)
        return empty
    if len(bars) < 2:
        empty["bars"] = len(bars)
        return empty

    limit = limit_pct(code)
    flags: list[bool] = []
    for i in range(1, len(bars)):
        prev = float(bars[i - 1]["close"] or 0)
        cur = float(bars[i]["close"] or 0)
        if prev <= 0:
            flags.append(False)
            continue
        limit_price = round(prev * (1.0 + limit), 2)
        flags.append(cur > prev and cur >= limit_price - 0.005)

    # 连续涨停段（段长即「几板」）
    runs: list[dict] = []
    start = None
    for i, hit in enumerate(flags):
        if hit and start is None:
            start = i
        elif not hit and start is not None:
            runs.append(_run(bars, start, i - 1))
            start = None
    if start is not None:
        runs.append(_run(bars, start, len(flags) - 1))

    if not runs:
        empty["bars"] = len(bars)
        return empty
    top = sorted(runs, key=lambda r: (-r["boards"], r["start"]))
    return {
        "last_date": runs[-1]["end"],
        "last_streak": runs[-1]["boards"],
        "max_streak": top[0]["boards"],
        "max_range": [top[0]["start"], top[0]["end"]],
        "total": sum(1 for f in flags if f),
        "top_runs": top[:5],
        "bars": len(bars),
        "error": None,
    }


def _run(bars, i0: int, i1: int) -> dict:
    return {"start": str(bars[i0 + 1]["trade_date"])[:10],
            "end": str(bars[i1 + 1]["trade_date"])[:10],
            "boards": i1 - i0 + 1}


def _report_periods(count: int = 8) -> list[str]:
    """最近若干个报告期（季报 / 年报截止日），由近及远。"""
    today = dt.date.today()
    year = today.year
    ends = [(3, 31), (6, 30), (9, 30), (12, 31)]
    out: list[str] = []
    for _ in range(count * 2):
        for month, day in reversed(ends):
            d = dt.date(year, month, day)
            if d <= today:
                out.append(d.strftime("%Y%m%d"))
        year -= 1
        if len(out) >= count:
            break
    return out[:count]


# 往回试报告期的**总时间预算**（秒）。实现已并入公共件 `core.fallback.first_ok`（bug-02）。
# 单个报告期 `_ak(..., timeout=20)` 由看门狗线程硬切断，报告期有 8 个且**串行**，
# 于是最坏 8×20 = 160s；而调用方（筹码分布 `/api/chip/dist` 只有 30s、
# 个股基本信息 `/api/stock/profile` 90s）—— 上游一慢，整条链路就被判「无响应」。
# 实测正常情况下**第一个报告期就命中**（0.4~0.9s），预算给到 6s 已留了 7 倍余量。
_HOLDERS_BUDGET = 6.0
_HOLDERS_PERIOD_TIMEOUT = 20.0


def _frame_ok(df) -> bool:
    """空 DataFrame 视为「这个源没数据」（`first_ok` 的 is_ok 判定）。"""
    return df is not None and not getattr(df, "empty", True)


def _period_fetch(func, symbol, period):
    """把「取某报告期的十大（流通）股东」包装成 `first_ok` 要的 `fetch(timeout)` 形态。"""
    def fetch(timeout: float):
        return _ak(func, symbol=symbol, date=period, timeout=timeout)
    return fetch


def _first_report_frame(func, symbol):
    """从最近报告期往回试，返回第一个有数据的 `(报告期, DataFrame)`。

    预算用尽即返回 `(None, None)` —— 取不到就按既有约定降级（锁仓修正回退 (0.0, 1.0)、
    股东列表为空并带 error），**不改变任何既有降级语义**。
    """
    return first_ok([(p, _period_fetch(func, symbol, p)) for p in _report_periods()],
                    budget=_HOLDERS_BUDGET, per_call=_HOLDERS_PERIOD_TIMEOUT,
                    is_ok=_frame_ok)


def _json_safe(v: Any) -> Any:
    """已迁到 `core/jsonutil.py`（第 09 项）——这里是别名，保留是为了既有引用不用改。

    原实现叫 `_json_safe`（私有名），公共层改名 `json_safe` 并另加递归版
    `json_safe_deep` 供 HTTP 出口用。`stock_profile` 是逐字段调用，用单值版即可。
    """
    return json_safe(v)


def top_holders(code: str) -> dict:
    """前十大股东（含股份类型，可据 `股份类型` 区分流通 / 限售）。

    十大股东按报告期披露，故从最近报告期往回试，取第一个有数据的期。
    遍历受 `_HOLDERS_BUDGET` 总预算约束（见 `_first_report_frame` 的说明）。
    """
    symbol = price_service.market_symbol(code)
    period, df = _first_report_frame(ak.stock_gdfx_top_10_em, symbol)
    if df is None:
        return {"period": None, "items": [], "error": "未取到十大股东数据"}
    items: list[dict] = []
    for _, row in df.iterrows():
        items.append({
            "rank": _json_safe(row.get("名次")),
            "name": _json_safe(row.get("股东名称")),
            "share_type": _json_safe(row.get("股份类型")),
            "shares": _json_safe(row.get("持股数")),
            "pct": _json_safe(row.get("占总股本持股比例")),
            "change": _json_safe(row.get("增减")),
            "change_ratio": _json_safe(row.get("变动比率")),
        })
    if not items:
        return {"period": None, "items": [], "error": "未取到十大股东数据"}
    return {"period": period, "items": items, "error": None}


def free_top_holders(code: str) -> dict:
    """前十大**流通**股东（含「占总流通股本持股比例」，锁仓修正需要它）。

    与 `top_holders` 的区别在于比例口径：`top_holders` 是「十大股东」按**总股本**的
    比例，这里是「十大流通股东」按**流通股本**的比例——正是筹码锁仓修正要用的口径
    （对应通达信 / 东财「十大流通股东」页的「占流通股比例」列）。

    ⚠️ 本函数在**筹码分布**的冷路径上（`chip_formulas` → `_lockup_of` → `lockup_ratio`），
    启动器给 `/api/chip/dist` 的超时只有 30s，故遍历必须受 `_HOLDERS_BUDGET` 约束。
    """
    symbol = price_service.market_symbol(code)
    period, df = _first_report_frame(ak.stock_gdfx_free_top_10_em, symbol)
    if df is None:
        return {"period": None, "items": [], "error": "未取到十大流通股东数据"}
    items: list[dict] = []
    for _, row in df.iterrows():
        items.append({
            "rank": _json_safe(row.get("名次")),
            "name": _json_safe(row.get("股东名称")),
            "nature": _json_safe(row.get("股东性质")),
            "share_type": _json_safe(row.get("股份类型")),
            "shares": _json_safe(row.get("持股数")),
            "pct": _json_safe(row.get("占总流通股本持股比例")),
            "change": _json_safe(row.get("增减")),
            "change_ratio": _json_safe(row.get("变动比率")),
        })
    if not items:
        return {"period": None, "items": [], "error": "未取到十大流通股东数据"}
    return {"period": period, "items": items, "error": None}


def expected_lockup_period(today: dt.date | None = None) -> str:
    """到今天为止、按法定披露时限**应已出齐**的最新报告期（YYYYMMDD）。

    用于判断落盘缓存是否过期：缓存里的 `period` 比它旧，就说明新一季
    十大流通股东已经披露、该重取了。

    取「季末 + 披露时限 ≤ 今天」的最近一个季末：季报法定披露期约 1 个月
    （年报 4 个月），这里统一留 40 天余量。披露空窗期（如 4 月初年报还没出齐）
    会返回上一个已出齐的报告期，此时旧缓存仍然有效，不会触发无谓重取。
    """
    day = today or dt.date.today()
    lag = dt.timedelta(days=_LOCKUP_DISCLOSURE_LAG_DAYS)
    for period in _report_periods(8):
        try:
            end = dt.datetime.strptime(period, "%Y%m%d").date()
        except ValueError:
            continue
        if end + lag <= day:
            return period
    return _report_periods(8)[-1]


def _lockup_age_days(stamp: Any) -> float | None:
    """`lockup_cache.updated_at`（`db.now_iso()`，带时区的 ISO 串）距今天数。

    解析不了返回 None（调用方按「未知」处理）。刻意不用 `strptime` 固定格式：
    `now_iso()` 产出的是 `2026-10-19T07:48:41.424375+00:00`，按
    `%Y-%m-%d %H:%M:%S` 解析会直接抛错，把缓存误判成永久过期。
    """
    try:
        moment = dt.datetime.fromisoformat(str(stamp or ""))
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is not None:
        moment = moment.astimezone().replace(tzinfo=None)
    return (dt.datetime.now() - moment).total_seconds() / 86400.0


def _lockup_stale(row: dict[str, Any] | None) -> bool:
    """落盘的锁仓记录是否已过期（需要重取）。

    报告期已是最新 → **直接复用，一次请求都不发**（本季内数据不会再变）。

    报告期旧于「应已出齐」的水平时分两种：新一季确实已披露（该重取），
    或该股数据源只到更早的期（披露空窗 / 数据源缺期，重取也是同样结果）。
    后者无法从记录里区分，故用 `_LOCKUP_MIN_REFRESH_DAYS` 限制重试频率，
    避免全市场天天为空窗期打东财接口。
    """
    if not row:
        return True
    period = str(row.get("period") or "")
    if not period:
        return True
    if period >= expected_lockup_period():
        return False
    age = _lockup_age_days(row.get("updated_at"))
    return age is None or age >= _LOCKUP_MIN_REFRESH_DAYS


def _fetch_lockup(code: str, min_pct: float) -> dict:
    """真实取数（akshare 东财），并写进程内缓存；**不读盘、不落盘**。

    取不到十大流通股东时（次新股尚未披露首份季报 / 停牌退市 / 数据源缺期），
    按**全流通**兜底：r=0、L=1.0（不修正换手率），并标记 `fallback=True`。
    这与 `_first_report_frame` 里既有的降级口径一致，区别是这里把它**显式返回**
    给调用方落盘——否则这些股票每轮补采都当失败重试，永远显示「失败」。
    报告期推进到下一季后 `period` 变旧，会自然重取覆盖，不必人工干预。
    """
    src = free_top_holders(code)
    items = src.get("items") or []
    if not items:
        result = {
            "period": expected_lockup_period(),
            "ratio": _FULL_CIRCULATION_RATIO,
            "factor": _FULL_CIRCULATION_FACTOR,
            "holders": [],
            "error": None,
            "fallback": True,
        }
        _CACHE[f"lockup:{code}:{min_pct}"] = (time.time(), result)
        return result
    locked = [it for it in items
              if it.get("pct") is not None and float(it["pct"]) > float(min_pct)]
    ratio = sum(float(it["pct"]) for it in locked) / 100.0
    ratio = min(max(ratio, 0.0), 0.95)          # 防御：≥95% 无意义，且 1-r 不能为 0
    result = {
        "period": src.get("period"),
        "ratio": ratio,
        "factor": (1.0 / (1.0 - ratio)) if ratio > 0 else 1.0,
        "holders": [{"name": it.get("name"), "pct": it.get("pct")} for it in locked],
        "error": None,
        "fallback": False,
    }
    _CACHE[f"lockup:{code}:{min_pct}"] = (time.time(), result)
    return result


def lockup_ratio(code: str, min_pct: float = 5.0) -> dict:
    """锁仓占流通股比例 r 与修正系数 1/(1-r)（供筹码峰衰减使用）。

    阈值 5% 取**举牌线**（要约收购 / 权益变动披露界限）：持股超过 5% 的股东受减持规则
    约束、属长期持有人，其筹码不进入日常流通，故不计入可自由流通部分。
    存在非流通筹码 → 同样的成交量只能在剩余自由筹码内部倒手 → 实际换手更快，
    故把名义换手率放大 1/(1-r) 倍（>1，衰减加快，符合预期）。

    口径对齐「扣除前十大流通股东超 5% 的即可」：
        r = Σ(前十大流通股东中「占流通股比例 > min_pct」者的占比) / 100
        系数 = 1 / (1 - r)        # 例：15.00% + 9.35% → 1/(1-24.35%) = 1.32

    返回 {period, ratio(小数), factor, holders(被计入户明细), error}。

    取数分三级：进程内缓存（6h）→ **落盘缓存**（`price_store.lockup_cache`，
    按报告期判新鲜，季度数据可跨重启复用）→ akshare 东财。取数失败不缓存，
    下次可重试。
    """
    key = f"lockup:{code}:{min_pct}"
    now = time.time()
    hit = _CACHE.get(key)
    if hit is not None and now - hit[0] < _TTL:
        return hit[1]

    # 落盘缓存只覆盖举牌线口径：表里没存 min_pct，别的阈值无从判断是否同源
    disk_ok = float(min_pct) == _LOCKUP_MIN_PCT
    stored = price_store.get_lockup(code) if disk_ok else None
    if stored is not None and not _lockup_stale(stored):
        result = {"period": stored["period"], "ratio": stored["ratio"],
                  "factor": stored["factor"], "holders": [], "error": None}
        _CACHE[key] = (now, result)
        return result

    result = _fetch_lockup(code, min_pct)
    if not result.get("error") and disk_ok and result.get("period"):
        price_store.put_lockup(code, result["period"], result["ratio"], result["factor"])
    return result


def sync_lockup(code: str, min_pct: float = _LOCKUP_MIN_PCT,
                force: bool = False) -> dict[str, Any]:
    """采集锁仓系数并落盘（历史数据下载 / 单只补齐用）。

    缓存里已是最新报告期 → **零请求**返回；`force=True` 强制重采（补救用）。

    取不到十大流通股东（次新股未披露季报 / 停牌退市 / 数据源缺期）时**不算失败**：
    按全流通兜底（r=0、L=1.0）落盘并返回 `fallback=True`，使补采任务能正常收尾、
    不再把这类股票永久标成「失败」；下一报告期会自然重取覆盖。

    返回 {ok, cached, period, ratio, factor, fallback} 或 {ok=False, errors}。
    """
    key = f"lockup:{code}:{min_pct}"
    _CACHE.pop(key, None)                      # 让本次判定只信落盘与数据源
    disk_ok = float(min_pct) == _LOCKUP_MIN_PCT
    if not force and disk_ok:
        stored = price_store.get_lockup(code)
        if stored is not None and str(stored.get("period") or "") >= expected_lockup_period():
            result = {"period": stored["period"], "ratio": stored["ratio"],
                      "factor": stored["factor"], "holders": [], "error": None}
            _CACHE[key] = (time.time(), result)
            return {"ok": True, "cached": True, "period": result["period"],
                    "ratio": result["ratio"], "factor": result["factor"]}

    result = _fetch_lockup(code, min_pct)
    if result.get("error"):
        return {"ok": False, "cached": False,
                "errors": [str(result["error"]) or "未取到十大流通股东数据"]}
    if disk_ok and result.get("period"):
        price_store.put_lockup(code, result["period"], result["ratio"], result["factor"])
    return {"ok": True, "cached": False, "period": result["period"],
            "ratio": result["ratio"], "factor": result["factor"],
            "fallback": bool(result.get("fallback"))}


def company_info(code: str) -> dict:
    """行业 / 上市时间 / 主营业务。

    数据源选择（实测）：东财 push2 在本机被墙（`stock_individual_info_em` 连不上，
    与 valuation 里「东财个股接口当前不可用」同一回事），腾讯行情**没有行业字段**，
    雪球要 token——故用**巨潮公司概况** `stock_profile_cninfo`（cninfo 可用），
    失败再回退**同花顺主营** `stock_zyjs_ths`（只有主营业务、无行业）。
    总股本不在这里取：巨潮的「注册资金」是人民币万元、非股数，口径不对，用腾讯的市值反推值。
    """
    # 巨潮 → 同花顺两源**串行**回退，各 timeout=20 → 最坏 40s。测得结论（bug-02）：
    # 调用方 /api/stock/profile 在启动器一侧有 **90s** 超时（VRequest 默认），40s 低于阈值，
    # 因此**不属于必修，故未加预算**（与表中其他低于阈值的项保持一致）。
    # 实测冷启动 16.4s（营业部分取数常常正常）；若以后要压冷启动时间，
    # 再用 core.fallback.first_ok 给它加预算（此处就是那个入口）。
    def produce():
        try:
            df = _ak(ak.stock_profile_cninfo, symbol=code, timeout=20)
        except Exception:
            df = None
        if df is not None and not getattr(df, "empty", True):
            row = df.iloc[0]
            # 巨潮一个接口就把公司概况给齐了：除行业外，还有所属市场、入选指数
            # （沪深300 / 深证100 等，相当于「指数·板块归属」）、经营范围、成立日期、
            # 法人代表、注册/办公地址、官网、机构简介——全部带出，前端按需展示。
            return {
                "name": _json_safe(row.get("A股简称") or row.get("公司名称")),
                "industry": _json_safe(row.get("所属行业")),
                "market": _json_safe(row.get("所属市场")),
                "indexes": _json_safe(row.get("入选指数")),
                "main_business": _json_safe(row.get("主营业务")),
                "business_scope": _json_safe(row.get("经营范围")),
                "listing_date": _json_safe(row.get("上市日期")),
                "found_date": _json_safe(row.get("成立日期")),
                "legal_rep": _json_safe(row.get("法人代表")),
                "reg_address": _json_safe(row.get("注册地址")),
                "office_address": _json_safe(row.get("办公地址")),
                "website": _json_safe(row.get("官方网站")),
                "profile": _json_safe(row.get("机构简介")),
                "source": "巨潮",
                "error": None,
            }
        try:
            df2 = _ak(ak.stock_zyjs_ths, symbol=code, timeout=20)
            if df2 is not None and not getattr(df2, "empty", True):
                r2 = df2.iloc[0]
                return {"name": None, "industry": None, "market": None, "indexes": None,
                        "main_business": _json_safe(r2.get("主营业务")),
                        "business_scope": _json_safe(r2.get("经营范围")),
                        "product_type": _json_safe(r2.get("产品类型")),
                        "product_name": _json_safe(r2.get("产品名称")),
                        "listing_date": None, "found_date": None, "legal_rep": None,
                        "reg_address": None, "office_address": None, "website": None,
                        "profile": None, "source": "同花顺", "error": None}
        except Exception as exc:                   # noqa: BLE001
            return {"error": str(exc)}
        return {"error": "数据源未返回"}
    return _cached(f"info:{code}", produce)


def profile(code: str) -> dict:
    """个股基本信息的完整装配；各部分失败只写进 errors，不拖垮整体。"""
    errors: list[str] = []
    out: dict[str, Any] = {"ok": True, "code": code}

    quote = share_service.fetch_share_capital(code, errors)
    out.update({
        "name": quote.get("name"),
        "price": quote.get("price"),
        "total_mv_yi": quote.get("total_cap_yi"),
        "float_mv_yi": quote.get("float_cap_yi"),
        "float_shares": quote.get("float_shares"),
        "total_shares_est": quote.get("total_shares"),
    })
    if not quote.get("float_shares"):
        errors.append("未取到市值 / 股本（腾讯行情未返回市值）")

    info = company_info(code)
    # 公司信息全量透出：行业 / 所属市场 / 入选指数 / 经营范围 / 成立日期 /
    # 法人代表 / 注册·办公地址 / 官网 / 公司简介（巨潮一个接口就给齐，见 company_info）。
    out.update({k: v for k, v in info.items() if k not in ("source", "error", "name")})
    # 总股本用腾讯市值反推值（巨潮「注册资金」是人民币万元、非股数，口径不对）
    out["total_shares"] = out.get("total_shares_est")
    out["company_source"] = info.get("source")
    if info.get("name"):
        out["name"] = out.get("name") or info.get("name")
    if info.get("error"):
        errors.append(f"公司信息：{info['error']}")

    holders = _cached(f"holders:{code}", lambda: top_holders(code))
    out["holders_period"] = holders.get("period")
    out["holders"] = holders.get("items") or []
    if holders.get("error"):
        errors.append(f"十大股东：{holders['error']}")

    out["limit_up"] = limit_up_stats(code)
    if out["limit_up"].get("error"):
        errors.append(f"涨停统计：{out['limit_up']['error']}")

    out["limit_pct"] = round(limit_pct(code) * 100, 1)
    out["errors"] = errors
    # 出口统一清洗：任何一路数据源漏进来的 NaN / Inf / numpy 标量都不该到达 HTTP 层
    return _json_safe(out)
