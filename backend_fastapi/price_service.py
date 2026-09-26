# -*- coding: utf-8 -*-
"""股价采集：把外部行情数据源的日 K 与复权参考数据写入 `price_store`。

与 `price_store` 的分工：
  - 本模块**只负责拉取、清洗与降级**（有网络、有重试）；
  - 复权计算、读写接口都在 `price_store`。
  这样回测等消费方只依赖 `price_store`，完全不碰网络。

数据源：
  - 日 K：东方财富 `stock_zh_a_hist`，失败降级腾讯 `stock_zh_a_hist_tx`
  - 复权因子：新浪 `stock_zh_a_daily(adjust="hfq-factor")`
  - 除权除息明细：新浪 `stock_history_dividend_detail`
"""
from __future__ import annotations

import datetime as dt
from typing import Any

import akshare as ak

import price_store
from market_service import _ak

# 落盘口径：**只存不复权原始价这一份**。
# 复权价由 `hfq_factor` 现算（见 `price_store.load_bars`），不落盘——
# 三套都存的话，5000 只 × 10 年要 7.4 GB，而只存一份只要 2.5 GB。
SYNC_ADJUSTS = ("raw",)
_AK_ADJUST = {"qfq": "qfq", "hfq": "hfq", "raw": ""}     # akshare 用空串表示不复权
BACKFILL_DAYS = 730                                       # 首次同步的默认回看窗口（约 2 年）
OVERLAP_DAYS = 14                                         # 增量重叠窗口，覆盖数据源事后修订


def market_symbol(code: str) -> str:
    """6 位代码 → 带市场前缀（sh / sz / bj）的符号。"""
    if code.startswith(("6", "5")):
        return "sh" + code
    if code.startswith(("0", "1", "2", "3")):
        return "sz" + code
    return "bj" + code


def _number(value: Any) -> float | None:
    try:
        num = float(value)
        return None if num != num else num
    except (TypeError, ValueError):
        return None


def _to_hand_volume(volume, amount, close) -> float | None:
    """把任意数据源的成交量归一成**手**（项目统一口径）。

    判定依据：成交额 ≈ 收盘价 × 成交量(股)，因此
      成交量(股) ≈ amount / close
      成交量(手) ≈ amount / close / 100
    若原始 volume 更接近「股」则 ÷100，更接近「手」则保持。

    东财/通达信本就是手、腾讯接口内部 ×100 后是股、新浪是股——
    用金额反推可兼容各路数据源，不依赖代码前缀特例，
    也避免「腾讯对 sh688/sz000 等跳过 ×100」这类特例被遗漏。
    """
    if volume is None:
        return None
    if amount and close and close > 0 and amount > 0:
        try:
            vol_shares = float(amount) / float(close)     # 反推的股数
            vol_hands = vol_shares / 100.0                # 反推的手数
        except (TypeError, ValueError, ZeroDivisionError):
            return volume
        # 100 倍的差距远大于「成交均价 vs 收盘价」的误差，判定稳定
        if abs(volume - vol_shares) <= abs(volume - vol_hands):
            return volume / 100.0                         # 原始是股，转手
        return volume                                     # 原始已是手
    return volume


def _date_text(value: Any) -> str:
    """日期归一成 YYYY-MM-DD；空值 / NaT / nan 一律返回空串（避免 "NaT" 这类字符串入库）。"""
    text = str(value or "")[:10].replace("/", "-")
    return "" if text in ("NaT", "nat", "None", "nan", "NaN") else text


def normalize_bars(frame) -> list[dict[str, Any]]:
    """akshare 日线 DataFrame → 统一字段的 bar 列表。

    兼容三类列名：东财（中文）、新浪 / 腾讯（英文）。
    """
    if frame is None or getattr(frame, "empty", True):
        return []
    result = []
    for _, row in frame.iterrows():
        day = _date_text(row.get("日期", row.get("date")))
        if not day:
            continue
        result.append({
            "date": day,
            "open": _number(row.get("开盘", row.get("open"))),
            "high": _number(row.get("最高", row.get("high"))),
            "low": _number(row.get("最低", row.get("low"))),
            "close": _number(row.get("收盘", row.get("close"))),
            "volume": _to_hand_volume(
                _number(row.get("成交量", row.get("volume"))),
                _number(row.get("成交额", row.get("amount"))),
                _number(row.get("收盘", row.get("close"))),
            ),
            "amount": _number(row.get("成交额", row.get("amount"))),
            "amplitude": _number(row.get("振幅")),
            "change_pct": _number(row.get("涨跌幅")),
            "change_amount": _number(row.get("涨跌额")),
            "turnover": _number(row.get("换手率", row.get("turnover"))),
        })
    return result


def validate_bars(bars: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """入库前校验：不合格的行**拒绝入库**并给出原因。

    原则：宁可这只股票当日**没有**数据（可以重拉），也不写入不可信的数据——
    错误数据进入系统后会被后续计算引用，纠正成本远高于缺失。

    这里只做**绝对可靠**的校验（日内 OHLC 关系），不会误伤除权等正常情况；
    跨日跳变之类的检查容易与除权混淆，交给复权因子与人工核对，不在此处拦截。
    """
    ok: list[dict[str, Any]] = []
    rejected: list[str] = []
    for bar in bars:
        reason = _bar_problem(bar)
        if reason:
            rejected.append(f"{bar.get('date') or '?'}：{reason}")
        else:
            ok.append(bar)
    return ok, rejected


def _bar_problem(bar: dict[str, Any]) -> str | None:
    """返回不合格原因；合格返回 None。"""
    high, low = bar.get("high"), bar.get("low")
    open_, close = bar.get("open"), bar.get("close")
    values = [open_, high, low, close]
    if any(value is None for value in values):
        return "开高低收存在缺失值"
    if min(values) <= 0:
        return "价格出现非正数"
    if high < low:
        return "最高价低于最低价"
    if not low <= close <= high:
        return "收盘价超出当日高低区间"
    if not low <= open_ <= high:
        return "开盘价超出当日高低区间"
    return None


def fetch_daily(code: str, start: dt.date, end: dt.date,
                adjust: str = "raw") -> tuple[list[dict[str, Any]], str, list[str]]:
    """抓取并校验某区间的日 K。

    数据源策略：
      - **北交所**（920 新段 + 原精选层 8/4 段）：东财/腾讯常取不到，统一走新浪；
      - 其余：东方财富优先，失败降级腾讯证券。

    **只抓取、不写库**——所有写库都由调用方在抓取成功后进行，
    这样数据源失败时库里的原数据保持不动（供「全量重抓修复」使用）。
    返回 (已通过校验的 bars, 数据源名, 被拒行的原因)。
    """
    store_adjust = price_store._store_adjust(adjust)
    ak_adjust = _AK_ADJUST.get(store_adjust, store_adjust)
    sd, ed = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")

    # 北交所（920 新段 + 原精选层 8/4 段）：东财/腾讯接口对该段常返回空，改走新浪
    if code.startswith(("920", "8", "4")):
        source = "新浪财经"
        frame = _ak(ak.stock_zh_a_daily, symbol=market_symbol(code),
                    start_date=sd, end_date=ed, adjust=ak_adjust, timeout=35)
        if frame is None or getattr(frame, "empty", True):
            source = "东方财富"
            frame = _ak(ak.stock_zh_a_hist, symbol=code, period="daily",
                        start_date=sd, end_date=ed, adjust=ak_adjust, timeout=30)
    else:
        source = "东方财富"
        frame = _ak(ak.stock_zh_a_hist, symbol=code, period="daily",
                    start_date=sd, end_date=ed, adjust=ak_adjust, timeout=30)
        if frame is None or getattr(frame, "empty", True):
            source = "腾讯证券"
            frame = _ak(ak.stock_zh_a_hist_tx, symbol=market_symbol(code),
                        start_date=sd, end_date=ed, adjust=ak_adjust, timeout=35)

    bars = normalize_bars(frame)
    if not bars:
        raise RuntimeError("行情数据源未返回日 K")
    bars, rejected = validate_bars(bars)              # 入库前挡住不合格数据
    if not bars:
        raise RuntimeError("行情数据全部未通过校验：" + "；".join(rejected[:3]))
    return bars, source, rejected


def sync_bars(code: str, adjust: str = "raw", start: dt.date | None = None,
              end: dt.date | None = None) -> dict[str, Any]:
    """同步单一口径（raw / hfq / qfq）的日 K。

    `start` 为空时按已落盘的最新日期**增量**补齐（既有行为）；
    给了 `start` 则从该日期起抓取——遍历下载传一个早于上市日的日期（如 1990-01-01），
    数据源会自行截断到上市首日，从而拿到完整历史。
    `end` 用于**分段时间窗**（先近后远）：只抓到该日为止，不碰更新的数据。
    窗口内没有数据（如起始日晚于截止日）时返回 skipped，不视为失败。
    """
    store_adjust = price_store._store_adjust(adjust)
    today = dt.date.today()
    stop = end or today
    if start is not None:
        begin = start
    else:
        latest = price_store.latest_bar_date(code, store_adjust)
        if latest:
            begin = dt.date.fromisoformat(latest) - dt.timedelta(days=OVERLAP_DAYS)
        else:
            begin = today - dt.timedelta(days=BACKFILL_DAYS)
    if begin > stop:
        return {"count": 0, "start": None, "end": None, "source": None,
                "adjust": store_adjust, "rejected": [], "skipped": True}
    bars, source, rejected = fetch_daily(code, begin, stop, store_adjust)
    count = price_store.upsert_bars(code, bars, store_adjust, source=source)
    return {"count": count, "start": bars[0]["date"], "end": bars[-1]["date"],
            "source": source, "adjust": store_adjust, "rejected": rejected}


def refetch_bars(code: str, adjust: str = "raw") -> dict[str, Any]:
    """**全量重抓**某股日 K 并整体替换库中数据（指纹校验失败后的自动修复路径）。

    与 `sync_bars` 的区别：后者只从库中最新日期往前重叠 14 天做增量补齐，
    **碰不到更早的历史行**；因此若被改动的是历史区间，增量同步永远修不好。
    本函数改从库中**既有的最早日期**开始抓取，并整体替换，
    从而能还原**任意位置**的改动，替换后重算指纹即恢复可信。

    **先抓取、后写入**：数据源失败时直接抛错，库中原数据保持不动。
    """
    store_adjust = price_store._store_adjust(adjust)
    # 读既有范围时必须跳过指纹校验，否则「因不可信而修复」的调用会被自己拦住
    existing = price_store.list_bars(code, adjust=store_adjust, verify=False)
    today = dt.date.today()
    if existing:
        start = dt.date.fromisoformat(existing[0]["trade_date"]) - dt.timedelta(days=OVERLAP_DAYS)
    else:
        start = today - dt.timedelta(days=BACKFILL_DAYS)
    bars, source, rejected = fetch_daily(code, start, today, store_adjust)
    result = price_store.overwrite_bars(code, bars, store_adjust, source=source)
    return {
        "code": code, "before": len(existing), "after": result["written"],
        "removed": result["removed"], "start": bars[0]["date"], "end": bars[-1]["date"],
        "source": source, "adjust": store_adjust, "rejected": rejected,
    }


def sync_all_adjusts(code: str, start: dt.date | None = None,
                     end: dt.date | None = None) -> dict[str, Any]:
    """同步日K（当前只落盘**不复权原始价**一份）。

    `start` / `end` 透传给 `sync_bars`：遍历下载用它们指定「抓哪一段时间窗」（见该函数说明）。

    历史上曾同时同步 qfq / hfq / raw 三套，但复权价本就可由 `hfq_factor` 现算，
    落盘三套纯属冗余（5000 只 × 10 年：7.4 GB vs 2.5 GB），故收敛为一份。
    函数名与返回结构保持不变，以免影响既有调用方。
    """
    results: dict[str, Any] = {}
    errors: list[str] = []
    for adjust in SYNC_ADJUSTS:
        try:
            results[adjust] = sync_bars(code, adjust, start=start, end=end)
        except Exception as exc:          # noqa: BLE001 - 单口径失败不影响其余口径
            results[adjust] = None
            errors.append(f"{adjust}：{exc}")
    primary = results.get("raw") or {}
    return {
        "count": sum((item or {}).get("count") or 0 for item in results.values()),
        "start": primary.get("start"),
        "end": primary.get("end"),
        "source": primary.get("source"),
        "adjusts": results,
        "errors": errors,
    }


def import_bars(code: str, bars: list[dict[str, Any]], source: str = "",
                adjust: str = "raw") -> dict[str, Any]:
    """把**已经取到的** bars 按统一口径校验后落盘。

    与 `sync_bars` 的区别：后者自己去数据源抓，这里只负责「校验 + 写库」。
    因此本地通达信文件、任何第三方接口都能复用同一套校验与存储——
    换数据来源时，校验规则与存储结构一行都不用改。
    """
    store_adjust = price_store._store_adjust(adjust)
    ok, rejected = validate_bars(bars)
    if not ok:
        raise RuntimeError("行情数据全部未通过校验：" + "；".join(rejected[:3]))
    count = price_store.upsert_bars(code, ok, store_adjust, source=source)
    return {"count": count, "start": ok[0]["date"], "end": ok[-1]["date"],
            "source": source, "adjust": store_adjust, "rejected": rejected}


def fill_missing_turnover(code: str, bars: list[dict[str, Any]],
                          adjust: str = "raw") -> None:
    """本地来源（通达信）算不出换手率——缺流通股本。用库里已有的值补上。

    不补的话 `upsert_bars` 会用「换手率=空」整行覆盖掉此前在线抓到的值，
    等于把已有信息抹掉；这里只在**该股已有数据**时才回读，避免无谓的库扫描。
    """
    if not bars or all(bar.get("turnover") is not None for bar in bars):
        return
    store_adjust = price_store._store_adjust(adjust)
    try:
        if not price_store.latest_bar_date(code, store_adjust):
            return
        existing = price_store.list_bars(code, bars[0]["date"], bars[-1]["date"],
                                         adjust=store_adjust, verify=False)
    except Exception:                            # noqa: BLE001 - 读不到就不补，不影响导入
        return
    known = {row.get("trade_date"): row.get("turnover") for row in existing}
    for bar in bars:
        if bar.get("turnover") is None:
            bar["turnover"] = known.get(bar.get("date"))


def sync_factors(code: str) -> dict[str, Any]:
    """采集后复权因子（新浪 hfq-factor）并落盘。

    **只采集 hfq_factor**：其基准是「上市首日」，历史值不随新的除权变化；
    qfq_factor 以「最新日」为基准、每天都在变，入库等于又引入了会变的数据。
    前复权价请在读取时现算（见 `price_store.load_bars`）。
    """
    frame = _ak(ak.stock_zh_a_daily, symbol=market_symbol(code), adjust="hfq-factor", timeout=30)
    if frame is None or getattr(frame, "empty", True):
        # 数据源未返回因子（如 CDR、部分北交所）：不阻断整只下载，留空即可
        return {"count": 0, "source": "新浪", "note": "复权因子数据源未返回数据"}
    rows = []
    for _, row in frame.iterrows():
        ex_date = _date_text(row.get("date"))
        factor = _number(row.get("hfq_factor"))
        if ex_date and factor:
            rows.append({"ex_date": ex_date, "hfq_factor": factor})
    count = price_store.upsert_factors(code, rows, source="新浪")
    if not count:
        return {"count": 0, "source": "新浪", "note": "复权因子解析后为空"}
    return {"count": count, "source": "新浪"}


def sync_dividends(code: str) -> dict[str, Any]:
    """采集除权除息明细（分红送转）并落盘。

    用途：审计复权因子、与数据源对拍口径、计算税后真实持仓成本。
    送股 / 转增 / 派息均为「每 10 股」口径（与数据源一致）。
    """
    frame = _ak(ak.stock_history_dividend_detail, symbol=code, indicator="分红", timeout=30)
    if frame is None or getattr(frame, "empty", True):
        return {"count": 0, "source": "新浪", "note": "无分红送转记录"}
    rows = []
    for _, row in frame.iterrows():
        ex_date = _date_text(row.get("除权除息日"))
        if not ex_date:                    # 只有已实施（有除权日）的方案才能用于复权
            continue
        rows.append({
            "ex_date": ex_date,
            "announce_date": _date_text(row.get("公告日期")),
            "record_date": _date_text(row.get("股权登记日")),
            "bonus_per_10": _number(row.get("送股")),
            "transfer_per_10": _number(row.get("转增")),
            "cash_per_10": _number(row.get("派息")),
            "progress": str(row.get("进度") or ""),
        })
    count = price_store.upsert_dividends(code, rows, source="新浪")
    return {"count": count, "source": "新浪"}


def sync_reference(code: str, force: bool = False) -> dict[str, Any]:
    """采集「复权因子 + 除权明细」这两类参考数据。

    与日 K 不同，它们只在发生除权时才变化，因此默认**只在首次采集**（已有因子则跳过），
    避免每次导入股票池都为每只股票多发两次请求；需要补救时用 force=True。
    """
    factors = price_store.list_factors(code)
    dividends = price_store.list_dividends(code)
    if not force and factors:
        return {"cached": True, "factors": len(factors), "dividends": len(dividends)}
    errors: list[str] = []
    result: dict[str, Any] = {"cached": False}
    for name, producer in (("factors", sync_factors), ("dividends", sync_dividends)):
        try:
            result[name] = producer(code)
        except Exception as exc:          # noqa: BLE001 - 参考数据失败不影响日 K
            result[name] = None
            errors.append(f"{name}：{exc}")
    result["errors"] = errors
    return result
