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
import price_service
import price_store
import share_service
from market_service import _ak

_CACHE: dict[str, tuple[float, Any]] = {}
_TTL = 6 * 3600                      # 股东 / 行业这类低频数据的缓存时长（秒）


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


# 往回试报告期的**总时间预算**（秒）。
# 单个报告期 `_ak(..., timeout=20)` 由看门狗线程硬切断，报告期有 8 个且**串行**，
# 于是最坏 8×20 = 160s；而调用方（筹码分布 `/api/chip/dist`、个股基本信息接口）
# 在启动器一侧只有 30s 超时 —— 上游一慢，整条链路就被判「无响应」。
# 实测正常情况下**第一个报告期就命中**（0.4~0.9s），预算给到 6s 已留了 7 倍余量。
_HOLDERS_BUDGET = 6.0
_HOLDERS_PERIOD_TIMEOUT = 20.0


def _walk_report_periods(fetch, budget: float = _HOLDERS_BUDGET):
    """从最近报告期往回试，返回第一个有数据的 `(period, df)`；全都没有则 `(None, None)`。

    `fetch(period, timeout)` 负责真正取数。**`budget` 是硬约束**：预算用尽立即停，
    哪怕后面的报告期其实有数据 —— 取不到就回退到「不做锁仓修正」，
    这正是本模块既有的降级约定（见 `lockup_ratio` 的说明），只是原先只防「失败」、
    没防「变慢」。上游不稳时**不应**让用户等一分钟。
    """
    deadline = time.monotonic() + max(0.5, float(budget))
    for period in _report_periods():
        left = deadline - time.monotonic()
        if left <= 0:
            break
        try:
            df = fetch(period, min(_HOLDERS_PERIOD_TIMEOUT, left))
        except Exception:                                # noqa: BLE001 - 单期失败换下一期
            continue
        if df is not None and not getattr(df, "empty", True):
            return period, df
    return None, None


def _json_safe(v: Any) -> Any:
    """把 akshare / pandas 带来的值洗成 JSON 安全类型。

    关键是 **NaN / Inf → None**：Python 的 `json.dumps` 默认放行 NaN（产出非法 JSON），
    而 FastAPI 的 JSONResponse 默认严格模式，遇到 NaN 直接抛
    `ValueError: Out of range float values are not JSON compliant`，
    Starlette 兜底返回纯文本 500，前端只见「无效的 JSON 基元: Internal」。
    顺带把 numpy 标量（int64 / float64 / bool_）转成 Python 原生类型。
    """
    if v is None or isinstance(v, (str, bool)):
        return v
    if isinstance(v, (int,)) or (hasattr(v, "item") and isinstance(v.item(), int)):
        try:
            return int(v)
        except (ValueError, OverflowError, TypeError):
            return None
    f = None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v if isinstance(v, (list, dict)) else None
    if f != f or f in (float("inf"), float("-inf")):     # NaN / ±Inf
        return None
    return f


def top_holders(code: str) -> dict:
    """前十大股东（含股份类型，可据 `股份类型` 区分流通 / 限售）。

    十大股东按报告期披露，故从最近报告期往回试，取第一个有数据的期。
    遍历受 `_HOLDERS_BUDGET` 总预算约束（见 `_walk_report_periods` 的说明）。
    """
    symbol = price_service.market_symbol(code)
    period, df = _walk_report_periods(
        lambda p, t: _ak(ak.stock_gdfx_top_10_em, symbol=symbol, date=p, timeout=t))
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
    period, df = _walk_report_periods(
        lambda p, t: _ak(ak.stock_gdfx_free_top_10_em, symbol=symbol, date=p, timeout=t))
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
    取数失败不缓存（下次可重试），成功缓存 TTL 6 小时（股东数据一季度才变）。
    """
    key = f"lockup:{code}:{min_pct}"

    def produce() -> dict:
        src = free_top_holders(code)
        items = src.get("items") or []
        locked = [it for it in items
                  if it.get("pct") is not None and float(it["pct"]) > float(min_pct)]
        ratio = sum(float(it["pct"]) for it in locked) / 100.0
        ratio = min(max(ratio, 0.0), 0.95)          # 防御：≥95% 无意义，且 1-r 不能为 0
        return {
            "period": src.get("period"),
            "ratio": ratio,
            "factor": (1.0 / (1.0 - ratio)) if ratio > 0 else 1.0,
            "holders": [{"name": it.get("name"), "pct": it.get("pct")} for it in locked],
            "error": src.get("error"),
        }

    now = time.time()
    hit = _CACHE.get(key)
    if hit is not None and now - hit[0] < _TTL:
        return hit[1]
    result = produce()
    if not result.get("error"):                     # 失败不缓存，留待下次重试
        _CACHE[key] = (now, result)
    return result


def company_info(code: str) -> dict:
    """行业 / 上市时间 / 主营业务。

    数据源选择（实测）：东财 push2 在本机被墙（`stock_individual_info_em` 连不上，
    与 valuation 里「东财个股接口当前不可用」同一回事），腾讯行情**没有行业字段**，
    雪球要 token——故用**巨潮公司概况** `stock_profile_cninfo`（cninfo 可用），
    失败再回退**同花顺主营** `stock_zyjs_ths`（只有主营业务、无行业）。
    总股本不在这里取：巨潮的「注册资金」是人民币万元、非股数，口径不对，用腾讯的市值反推值。
    """
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
