# -*- coding: utf-8 -*-
"""盘面与板块数据采集服务（供 /api/market/* 使用）。

五类数据：

  ① global_market()     外围环境：美股 / 亚太（韩日）/ 港股 / 大宗商品 / 费城半导体
  ② capital_flow()      大盘资金：两市成交、涨跌家数、主力净流向、特大单方向
  ③ sector_beta()       板块β：行业与概念板块资金流 Top10、申万一级行业涨跌
  ④ limit_up_ladder()   连板梯队：连板结构 + 晋级率
  ⑤ big_loss()          大面股：炸板 + 跌停

数据源（全部为公开接口，任一来源失败只降级并在 errors 中记录，不影响其他模块）：

  - 新浪行情 hq.sinajs.cn        外围指数与大宗商品（GBK 文本）
  - 新浪指数 stock_zh_index_spot_sina  两市成交额
  - 同花顺（akshare）            行业 / 概念板块资金流
  - 乐咕乐股（akshare）          市场活跃度（涨跌家数）
  - 申万（akshare）              一级行业实时行情
  - 东方财富（akshare）          涨停 / 炸板 / 跌停池、大盘资金流向
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import threading
import time

import pandas as pd
import requests

class _MissingAkShare:
    """akshare 缺失时的占位对象：任何属性访问返回 None，交由 _ak 统一降级。"""

    def __getattr__(self, _name):
        return None


try:
    import akshare as ak
except Exception:  # pragma: no cover - 依赖缺失时整体降级
    ak = _MissingAkShare()

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0 Safari/537.36")
SINA_REFERER = "https://finance.sina.com.cn/"

CACHE_TTL = 60                      # 盘中快照缓存 60 秒，避免频繁打数据源
HIST_CACHE_TTL = 6 * 3600           # 历史交易日数据不会再变，缓存 6 小时
_cache: dict = {}
_cache_lock = threading.Lock()
_session = requests.Session()
_session.headers.update({"User-Agent": UA})


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def clear_cache(prefix: str | None = None):
    """清除进程内缓存；prefix 为空时清空全部（供前端强制刷新使用）。"""
    with _cache_lock:
        if not prefix:
            _cache.clear()
            return
        for key in [k for k in _cache if k.startswith(prefix)]:
            _cache.pop(key, None)


# A股收盘时间 15:00。收盘后当天行情不再变化，缓存可延长到当天结束——
# 否则「反复打开页面」每次都要重打一遍数据源，这正是页面加载慢的主因。
_MARKET_CLOSE_MIN = 15 * 60
# 美股时段（北京时间，夏令时近似 21:30 - 次日 04:00）：外围数据在这段仍在变
_US_OPEN_MIN = 21 * 60 + 30
_US_CLOSE_MIN = 4 * 60


def _market_settled(now=None) -> bool:
    """当天 A 股行情是否已定格：周末，或已过 15:00 收盘。

    节假日不做精确判断——那时退化为「按盘中处理」，只是刷新勤一些，不会出错。
    """
    moment = now or dt.datetime.now()
    if moment.weekday() >= 5:                      # 周六 / 周日
        return True
    return moment.hour * 60 + moment.minute >= _MARKET_CLOSE_MIN


def _us_session(now=None) -> bool:
    """是否处于美股交易时段（北京时间 21:30 之后、或凌晨 4:00 之前）。"""
    moment = now or dt.datetime.now()
    minutes = moment.hour * 60 + moment.minute
    return minutes >= _US_OPEN_MIN or minutes < _US_CLOSE_MIN


def _ttl(base: int, follow_us: bool = False) -> int:
    """缓存时长：**行情定格后延长到当天结束**，避免反复打开页面都重拉。

    - 盘中：按 base 走（数据每分钟都在变）
    - 盘后 / 非交易日：延长到当天 23:59:59，跨天自动失效重新拉
    - follow_us=True（外围数据）：美股时段内仍按 base 走，其余时间跟随盘后策略
    """
    now = dt.datetime.now()
    if not _market_settled(now):
        return base
    if follow_us and _us_session(now):
        return base
    end = dt.datetime.combine(now.date(), dt.time(23, 59, 59))
    return max(base, int((end - now).total_seconds()))


def _cached(key: str, producer, ttl: int = CACHE_TTL):
    """带进程内缓存的取值，返回 (value, cached)。"""
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1], True
    value = producer()
    with _cache_lock:
        _cache[key] = (time.time(), value)
    return value, False


def _http_get(url, referer=None, retries=3, timeout=12, encoding=None, params=None):
    """GET 请求，失败重试（退避），全部失败抛出最后一次异常。"""
    last = None
    for attempt in range(retries):
        try:
            headers = {"Referer": referer} if referer else None
            resp = _session.get(url, params=params, headers=headers, timeout=timeout)
            resp.raise_for_status()
            if encoding:
                resp.encoding = encoding
            return resp
        except Exception as exc:      # noqa: BLE001 - 统一重试后抛出
            last = exc
            time.sleep(0.5 * (attempt + 1))
    raise last


AK_TIMEOUT = 15                       # 普通 akshare 调用硬超时（秒），超时视为该来源不可用
AK_V8_TIMEOUT = 60                    # 依赖 py_mini_racer 的调用要串行排队，给更长窗口
_V8_LOCK = threading.Lock()           # V8 并发初始化会让进程直接崩溃，这类调用必须串行
_V8_USERS: dict = {}


def _uses_v8(fn) -> bool:
    """判断 akshare 函数所在模块是否依赖 py_mini_racer（新浪 / 同花顺系接口大量使用）。"""
    module_name = getattr(fn, "__module__", "") or ""
    if not module_name:
        return False
    cached = _V8_USERS.get(module_name)
    if cached is None:
        cached = False
        module = sys.modules.get(module_name)
        path = getattr(module, "__file__", "") or ""
        if path and os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                    cached = "py_mini_racer" in handle.read()
            except OSError:
                cached = False
        _V8_USERS[module_name] = cached
    return cached


def _ak(fn, *args, timeout=None, **kwargs):
    """调用 akshare 接口；未安装、异常或超时均返回 None（单来源降级）。

    akshare 内部多数接口未设置 timeout，数据源无响应时会长时间阻塞，
    因此放到独立线程执行并设硬超时，超时立刻放弃该来源，不拖住整个页面；
    依赖 py_mini_racer 的接口额外串行化，避免并发初始化 V8 崩掉整个后端进程。
    """
    if ak is None or fn is None:
        return None
    is_v8 = _uses_v8(fn)
    limit = timeout or (AK_V8_TIMEOUT if is_v8 else AK_TIMEOUT)
    box: dict = {}

    def worker():
        if is_v8:
            if not _V8_LOCK.acquire(timeout=limit):
                return
            try:
                box["value"] = fn(*args, **kwargs)
            except Exception:             # noqa: BLE001 - 数据源失败静默降级
                box["value"] = None
            finally:
                _V8_LOCK.release()
            return
        try:
            box["value"] = fn(*args, **kwargs)
        except Exception:                 # noqa: BLE001 - 数据源失败静默降级
            box["value"] = None

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(limit)
    if thread.is_alive():
        return None                       # 超时：按数据源不可用降级
    return box.get("value")


def _num(value, digits=2):
    """安全转 float 并保留位数；无效值返回 None。"""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num != num:                    # NaN
        return None
    return round(num, digits)


def _num_cn(value):
    """解析同花顺中文金额（'1.78亿' / '1234万' / 纯数字元）→ 亿元。"""
    text = str(value).strip().replace(",", "")
    if not text or text in ("-", "--", "none"):
        return None
    try:
        if text.endswith("亿"):
            return round(float(text[:-1]), 2)
        if text.endswith("万"):
            return round(float(text[:-1]) / 1e4, 4)
        return round(float(text) / 1e8, 4)
    except ValueError:
        return None


def _compact_date(value) -> str:
    """日期归一成 YYYYMMDD（容忍 2026-09-18 / 20260918 两种写法），便于跨数据源比对。"""
    return "".join(ch for ch in str(value or "") if ch.isdigit())[:8]


def _now_str():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _fmt_seal_time(value):
    """东财封板时间 092500 → 09:25。"""
    text = str(value).strip()
    if len(text) == 6 and text.isdigit():
        return f"{text[:2]}:{text[2:4]}"
    return text or None


# --------------------------------------------------------------------------- #
# ① 外围环境
# --------------------------------------------------------------------------- #
SINA_QUOTE_GROUPS = [
    ("美股", [("gb_dji", "道琼斯"), ("gb_ixic", "纳斯达克"), ("gb_ndx", "纳斯达克100")]),
    ("费城半导体", [("gb_sox", "费城半导体指数")]),
    ("亚太", [("int_nikkei", "日经225")]),
    ("港股", [("rt_hkHSI", "恒生指数")]),
    ("大宗商品", [("hf_CL", "纽约原油"), ("hf_GC", "纽约黄金"),
                  ("hf_SI", "纽约白银"), ("hf_HG", "美铜")]),
]


# 各组的交易时段（北京时间，用「当日 0 点起的分钟数」表示；跨天的拆成两段）
# 说明：美股按夏令时口径（21:30 - 次日 04:00），冬令时实际顺延约 1 小时。
#       这里只用于「开盘中 / 休市」提示，1 小时误差可接受，不引入时区库。
_MARKET_SESSIONS: dict[str, tuple[tuple[int, int], ...]] = {
    "美股": ((21 * 60 + 30, 24 * 60), (0, 4 * 60)),
    "费城半导体": ((21 * 60 + 30, 24 * 60), (0, 4 * 60)),
    "亚太": ((8 * 60, 14 * 60 + 30),),                  # 日经 / 韩国综合（日韩比北京早 1 小时）
    "港股": ((9 * 60 + 30, 12 * 60), (13 * 60, 16 * 60)),
    "大宗商品": ((6 * 60, 24 * 60), (0, 5 * 60)),        # 纽约金银油铜，近似连续交易
}


# ---- 交易日与夏令时：不引入时区库，规则自算 + 交易日历（每天拉一次） ----
_TRADE_DAYS_CACHE: dict = {"day": None, "days": set()}


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    """某月第 n 个星期 weekday（weekday: 0=周一 … 6=周日）的日期。"""
    first = dt.date(year, month, 1)
    return first + dt.timedelta(days=(weekday - first.weekday()) % 7 + (n - 1) * 7)


def _us_dst(now=None) -> bool:
    """按美国规则判断是否夏令时（2007 年起：3 月第 2 个周日 至 11 月第 1 个周日）。

    只用于推算美股开盘时刻是 21:30 还是 22:30（北京时间），
    切换日那天的边界误差不影响「开盘 / 休市」这类粗粒度提示。
    """
    moment = now or dt.datetime.now()
    start = _nth_weekday(moment.year, 3, 6, 2)      # 3 月第 2 个周日
    end = _nth_weekday(moment.year, 11, 6, 1)       # 11 月第 1 个周日
    return start <= moment.date() < end


def _a_share_trade_days() -> set[str]:
    """A 股交易日历，**每天只拉一次**并缓存；取不到返回空集，由调用方退化为按星期判断。"""
    today = dt.date.today().isoformat()
    if _TRADE_DAYS_CACHE["day"] == today and _TRADE_DAYS_CACHE["days"]:
        return _TRADE_DAYS_CACHE["days"]
    days: set[str] = set()
    try:
        frame = _ak(ak.tool_trade_date_hist_sina)
        if frame is not None and not getattr(frame, "empty", True):
            days = {str(item)[:10] for item in frame["trade_date"]}
    except Exception:                               # noqa: BLE001 - 日历拿不到不影响行情
        days = set()
    _TRADE_DAYS_CACHE["day"] = today
    _TRADE_DAYS_CACHE["days"] = days
    return days


def _a_share_is_trade_day(moment) -> bool:
    """某日是否 A 股交易日：以交易日历为准；日历不可用时退化为「工作日」。"""
    days = _a_share_trade_days()
    if days:
        return moment.date().isoformat() in days
    return moment.weekday() < 5


# A 股交易时段（北京时间）
_A_SHARE_SESSIONS = ((9 * 60 + 30, 11 * 60 + 30), (13 * 60, 15 * 60))


def _a_share_session(now=None) -> str:
    """A 股当前处于交易时段还是休市（已考虑节假日），返回 'open' / 'closed'。"""
    moment = now or dt.datetime.now()
    if not _a_share_is_trade_day(moment):
        return "closed"
    minutes = moment.hour * 60 + moment.minute
    for start, end in _A_SHARE_SESSIONS:
        if start <= minutes < end:
            return "open"
    return "closed"


def _session_of(group_name: str, now=None) -> str:
    """该组当前处于交易时段还是休市（北京时间），返回 'open' / 'closed'。

    周末一律休市——商品期货周六凌晨收盘、周一早上才开，与股市一致。
    美股 / 费城半导体的开盘时刻随夏令时变动，故动态计算而非查表。
    """
    moment = now or dt.datetime.now()
    if moment.weekday() >= 5:                   # 周六 / 周日
        return "closed"
    if group_name in ("美股", "费城半导体"):
        sessions = ((21 * 60 + 30, 24 * 60), (0, 4 * 60)) if _us_dst(moment) \
            else ((22 * 60 + 30, 24 * 60), (0, 5 * 60))
    else:
        sessions = _MARKET_SESSIONS.get(group_name, ())
    minutes = moment.hour * 60 + moment.minute
    for start, end in sessions:
        if start <= minutes < end:
            return "open"
    return "closed"


def _parse_sina_quote(symbol, fields):
    """按 symbol 前缀解析新浪行情字段，返回 {value, change, pct}。"""
    try:
        if symbol.startswith("gb_"):          # 美股：名称, 最新价, 涨跌幅%, 时间, 涨跌额
            return {"value": _num(fields[1]), "change": _num(fields[4]), "pct": _num(fields[2])}
        if symbol.startswith("hf_"):          # 外盘期货：最新价 ... 昨结算(7)
            last, prev = _num(fields[0]), _num(fields[7])
            change = round(last - prev, 2) if None not in (last, prev) else None
            pct = round(change / prev * 100, 2) if change is not None and prev else None
            return {"value": last, "change": change, "pct": pct}
        if symbol.startswith("rt_hk"):        # 港股：… 最新价(6), 涨跌额(7), 涨跌幅(8)
            return {"value": _num(fields[6]), "change": _num(fields[7]), "pct": _num(fields[8])}
        # 国际指数：名称, 最新价, 涨跌额, 涨跌幅
        return {"value": _num(fields[1]), "change": _num(fields[2]), "pct": _num(fields[3])}
    except (IndexError, TypeError):
        return None


# 历史模式（指定交易日）：用日线接口取该日收盘与涨跌幅
HIST_QUOTE_GROUPS = [
    ("美股", [("us", ".DJI", "道琼斯"), ("us", ".IXIC", "纳斯达克"), ("us", ".NDX", "纳斯达克100")]),
    ("费城半导体", [("us", ".SOX", "费城半导体指数")]),
    ("港股", [("hk", "HSI", "恒生指数")]),
    ("大宗商品", [("ff", "CL", "纽约原油"), ("ff", "GC", "纽约黄金"),
                  ("ff", "SI", "纽约白银"), ("ff", "HG", "美铜")]),
]
HIST_ONLY_REALTIME = ("日经225", "韩国综合指数")    # 暂无可用历史数据源，仅实时


def _hist_frame(source, symbol):
    """按数据源类型取日线历史：us=新浪美股，hk=新浪港股指数，ff=外盘期货。"""
    if source == "us":
        return _ak(ak.index_us_stock_sina, symbol=symbol)
    if source == "hk":
        return _ak(ak.stock_hk_index_daily_sina, symbol=symbol)
    if source == "ff":
        return _ak(ak.futures_foreign_hist, symbol=symbol)
    return None


def _hist_quote(df, date_str):
    """从日线中取不晚于 date_str(YYYYMMDD) 的最后一个交易日，返回收盘与较前一日涨跌。"""
    if df is None or getattr(df, "empty", True) or len(df) < 2:
        return None
    date_col = next((c for c in ("date", "日期") if c in df.columns), None)
    close_col = next((c for c in ("close", "收盘") if c in df.columns), None)
    if date_col is None or close_col is None:
        return None
    try:
        stamps = pd.to_datetime(df[date_col], errors="coerce")
        target = pd.to_datetime(str(date_str), format="%Y%m%d")
    except Exception:                     # noqa: BLE001
        return None
    position = None
    for index in range(len(df) - 1, -1, -1):
        stamp = stamps.iloc[index]
        if pd.notna(stamp) and stamp <= target:
            position = index
            break
    if position is None or position == 0:
        return None
    last, prev = _num(df.iloc[position][close_col]), _num(df.iloc[position - 1][close_col])
    if last is None or not prev:
        return None
    change = round(last - prev, 2)
    return {"value": last, "change": change, "pct": round(change / prev * 100, 2),
            "date": str(stamps.iloc[position])[:10]}


def _global_history(date_str, errors):
    """① 外围环境（历史交易日）：并行取各标的该日收盘。"""
    tasks = [(f"{source}:{symbol}", source, symbol)
             for _, items in HIST_QUOTE_GROUPS for source, symbol, _ in items]
    quotes: dict = {}

    def fetch(key, source, symbol):
        quotes[key] = _hist_quote(_hist_frame(source, symbol), date_str)

    threads = [threading.Thread(target=fetch, args=task, daemon=True) for task in tasks]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)

    groups = []
    for group_name, items in HIST_QUOTE_GROUPS:
        rows = []
        for source, symbol, label in items:
            quote = quotes.get(f"{source}:{symbol}")
            if quote:
                rows.append({"name": label, "value": quote["value"],
                             "change": quote["change"], "pct": quote["pct"]})
            else:
                errors.append(f"{label} 该交易日无历史数据")
        if rows:
            groups.append({"name": group_name, "items": rows})
    for label in HIST_ONLY_REALTIME:
        errors.append(f"{label} 暂无历史数据源（仅实时行情）")
    return groups


def _kospi_quote(errors):
    """韩国综合指数：东财全球指数历史（该接口易受限，单独降级）。"""
    df = _ak(ak.index_global_hist_em, symbol="韩国KOSPI")
    if df is None or getattr(df, "empty", True) or len(df) < 2:
        errors.append("韩国综合指数数据源暂不可用")
        return None
    try:
        columns = {str(c): c for c in df.columns}
        close_col = next((columns[c] for c in ("收盘", "最新价", "close") if c in columns), None)
        if close_col is None:
            errors.append("韩国综合指数解析失败")
            return None
        last, prev = _num(df.iloc[-1][close_col]), _num(df.iloc[-2][close_col])
        if last is None or not prev:
            errors.append("韩国综合指数解析失败")
            return None
        change = round(last - prev, 2)
        return {"name": "韩国综合指数", "value": last, "change": change,
                "pct": round(change / prev * 100, 2)}
    except Exception as exc:              # noqa: BLE001
        errors.append(f"韩国综合指数解析失败：{exc}")
        return None


def global_market(date=None):
    """① 外围环境。

    date 为空返回实时行情；传 YYYYMMDD 时返回该交易日各标的收盘（历史模式）。
    """
    def build():
        errors = []
        if date:
            return {"ok": True, "as_of": _now_str(), "trade_date": date, "historical": True,
                    "groups": _global_history(date, errors), "errors": errors}

        symbols = [sym for _, items in SINA_QUOTE_GROUPS for sym, _ in items]
        quotes = {}
        try:
            text = _http_get("https://hq.sinajs.cn/list=" + ",".join(symbols),
                             referer=SINA_REFERER, encoding="gbk").text
            for line in text.split(";"):
                line = line.strip()
                if not line.startswith("var hq_str_"):
                    continue
                key, _, payload = line.partition("=")
                payload = payload.strip().strip('"')
                if payload:
                    quotes[key[len("var hq_str_"):]] = payload.split(",")
        except Exception as exc:          # noqa: BLE001
            errors.append(f"新浪行情获取失败：{exc}")

        groups = []
        for group_name, items in SINA_QUOTE_GROUPS:
            rows = []
            for symbol, label in items:
                fields = quotes.get(symbol)
                parsed = _parse_sina_quote(symbol, fields) if fields else None
                if parsed and parsed.get("value") is not None:
                    rows.append({"name": label, "session": _session_of(group_name), **parsed})
                else:
                    errors.append(f"{label} 暂无数据")
            if rows:
                groups.append({"name": group_name, "items": rows})

        kospi = _kospi_quote(errors)
        if kospi:
            kospi["session"] = _session_of("亚太")
            for group in groups:
                if group["name"] == "亚太":
                    group["items"].append(kospi)
                    break
            else:
                groups.append({"name": "亚太", "items": [kospi]})

        return {"ok": True, "as_of": _now_str(), "historical": False,
                "groups": groups, "errors": errors}

    data, cached = _cached(f"global_market:{date or 'rt'}", build,
                           ttl=HIST_CACHE_TTL if date else _ttl(120, follow_us=True))
    return {**data, "cached": cached}


# --------------------------------------------------------------------------- #
# ② 大盘资金
# --------------------------------------------------------------------------- #
def _market_turnover(errors):
    """两市成交额（沪 + 深），单位亿元。"""
    df = _ak(ak.stock_zh_index_spot_sina)
    if df is None or getattr(df, "empty", True):
        errors.append("两市成交额获取失败")
        return None
    try:
        rows = df[df["代码"].isin(["sh000001", "sz399001"])]
        if rows.empty:
            errors.append("两市成交额获取失败")
            return None
        items, total = [], 0.0
        for _, row in rows.iterrows():
            amount = _num(row.get("成交额"))
            if amount is None:
                continue
            amount_yi = round(amount / 1e8, 2)
            total += amount_yi
            items.append({"name": str(row.get("名称")), "value": _num(row.get("最新价")),
                          "pct": _num(row.get("涨跌幅")), "amount": amount_yi})
        return {"total": round(total, 2), "items": items}
    except Exception as exc:              # noqa: BLE001
        errors.append(f"两市成交额解析失败：{exc}")
        return None


def _adv_dec(errors):
    """涨跌家数（乐咕乐股市场活跃度）。"""
    df = _ak(ak.stock_market_activity_legu)
    if df is None or getattr(df, "empty", True):
        errors.append("涨跌家数获取失败")
        return None
    try:
        mapping = {str(row["item"]): _num(row["value"], 0) for _, row in df.iterrows()}
        return {
            "up": mapping.get("上涨"),
            "down": mapping.get("下跌"),
            "flat": mapping.get("平盘"),
            "limit_up": mapping.get("真实涨停") if mapping.get("真实涨停") is not None else mapping.get("涨停"),
            "limit_down": mapping.get("真实跌停") if mapping.get("真实跌停") is not None else mapping.get("跌停"),
            "suspend": mapping.get("停牌"),
            "activity": mapping.get("活跃度"),
            "date": mapping.get("统计日期"),
        }
    except Exception as exc:              # noqa: BLE001
        errors.append(f"涨跌家数解析失败：{exc}")
        return None


def _ths_market_flow(errors):
    """降级口径：同花顺个股资金流汇总 + 大单追踪（东财接口不可用时使用）。

    两个同花顺接口彼此独立，改为并行请求；累加走列向量化，避免逐行 iterrows 的开销
    （个股资金流 5000+ 行，逐行构造 Series 是主要耗时点）。
    """
    box: dict = {}

    def fetch_individual():
        box["individual"] = _ak(ak.stock_fund_flow_individual, symbol="即时")

    def fetch_big_deal():
        box["big_deal"] = _ak(ak.stock_fund_flow_big_deal)

    threads = [threading.Thread(target=fn, daemon=True) for fn in (fetch_individual, fetch_big_deal)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(AK_TIMEOUT + 5)

    df = box.get("individual")
    if df is None or getattr(df, "empty", True):
        errors.append("主力资金流向获取失败（东方财富与同花顺接口均不可用）")
        return None
    try:
        net_col = df.get("净额")
        if net_col is None:
            errors.append("主力资金流向降级解析失败：缺少『净额』列")
            return None
        main_net = 0.0
        for raw in net_col.tolist():
            value = _num_cn(raw)
            if value is not None:
                main_net += value

        result = {
            "date": dt.date.today().isoformat(),
            "main_net": round(main_net, 2),
            "main_pct": None,
            "super_net": None,
            "super_pct": None,
            "big_net": None,
            "big_pct": None,
            "super_direction": None,
            "source": "同花顺汇总",
        }

        deal = box.get("big_deal")
        amount_col = None if deal is None else deal.get("成交额")
        side_col = None if deal is None else deal.get("大单性质")
        if amount_col is not None and side_col is not None:
            buy = sell = 0.0
            for amount, side in zip(amount_col.tolist(), side_col.tolist()):
                value = _num(amount)                    # 单位：万元
                if value is None:
                    continue
                if str(side).strip() == "买盘":
                    buy += value
                else:
                    sell += value
            net_big = round((buy - sell) / 1e4, 2)      # 万元 → 亿元
            result["big_net"] = net_big
            result["super_direction"] = "流入" if net_big > 0 else "流出"
        return result
    except Exception as exc:              # noqa: BLE001
        errors.append(f"主力资金流向降级解析失败：{exc}")
        return None


def _main_fund_flow(date, errors):
    """沪深主力净流向 / 特大单方向。

    首选东方财富大盘资金流（主力 / 超大单 / 大单，单位亿元）：该接口一次性返回约 120 个
    交易日的历史，指定 date 时取对应交易日那一行，因此主力资金流**支持按交易日回溯**
    （原先只取 iloc[-1]，把自带的历史浪费了）；
    日期不在区间内时回退最新一期并在 errors 中说明。
    接口不可用时降级为同花顺汇总口径（实时汇总，无法回溯）。
    """
    df = _ak(ak.stock_market_fund_flow)
    if df is not None and not getattr(df, "empty", True):
        try:
            wanted = _compact_date(date)
            row, matched = df.iloc[-1], False
            if wanted:
                # 东财「日期」列为 2026-09-18，统一成 8 位后再比对
                same_day = df[df["日期"].astype(str).map(_compact_date) == wanted]
                if same_day.empty:
                    errors.append(f"主力资金流无 {wanted} 的数据（东财该接口提供最近约 "
                                  f"{len(df)} 个交易日），已回退最新一期 {row.get('日期')}")
                else:
                    row, matched = same_day.iloc[-1], True

            def pick(*names, digits=2):
                for name in names:
                    if name in df.columns:
                        value = _num(row.get(name), digits)
                        if value is not None:
                            return value
                return None

            def to_yi(value):
                return round(value / 1e8, 2) if value is not None else None

            main_net = pick("主力净流入-净额")
            super_net = pick("超大单净流入-净额")
            big_net = pick("大单净流入-净额")
            return {
                "date": str(row.get("日期")),
                "main_net": to_yi(main_net),
                "main_pct": pick("主力净流入-净占比"),
                "super_net": to_yi(super_net),
                "super_pct": pick("超大单净流入-净占比"),
                "big_net": to_yi(big_net),
                "big_pct": pick("大单净流入-净占比"),
                "super_direction": ("流入" if (super_net or 0) > 0 else "流出") if super_net is not None else None,
                "source": "东方财富",
                "historical": matched,        # 只有真正按日期命中才算回溯（超区间回退不算）
                "history_days": len(df),
            }
        except Exception as exc:          # noqa: BLE001
            errors.append(f"东财主力资金流向解析失败：{exc}")

    fallback = _ths_market_flow(errors)
    if isinstance(fallback, dict):
        fallback.setdefault("historical", False)
        fallback.setdefault("history_note", "同花顺汇总口径仅在实时可用，不支持按交易日回溯")
    return fallback


def capital_flow(date=None):
    """② 大盘资金。

    成交额 / 涨跌家数 / 主力资金流分属三个不同数据源，彼此独立：并行抓取并设置整体兜底超时。
    主力资金流支持按交易日回溯（东财 stock_market_fund_flow 自带约 120 个交易日历史）；
    成交额与涨跌家数当前无可用历史数据源（东财相关接口不可用、新浪/腾讯日线只有成交量不含成交额、
    乐咕仅提供当日快照），指定历史日期时这两项仍是实时快照，通过 notice 告知前端。
    """
    def build():
        errors = []
        parts: dict = {}

        def collect(name, producer):
            try:
                parts[name] = producer()
            except Exception as exc:      # noqa: BLE001 - 单来源失败不影响其他子项
                errors.append(f"{name} 获取失败：{exc}")
                parts[name] = None

        tasks = [
            ("turnover", lambda: _market_turnover(errors)),
            ("adv_dec", lambda: _adv_dec(errors)),
            ("main_flow", lambda: _main_fund_flow(date, errors)),
        ]
        threads = [threading.Thread(target=collect, args=(name, fn), daemon=True)
                   for name, fn in tasks]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)               # 兜底：任一子项超时也照常返回，缺失项由前端降级提示
        if not date:
            # 实时模式下给两市指数补上「开盘 / 休市」状态（已按交易日历排除节假日）
            turnover = parts.get("turnover")
            if isinstance(turnover, dict):
                for item in turnover.get("items") or []:
                    if isinstance(item, dict):
                        item["session"] = _a_share_session()
        payload = {
            "ok": True,
            "as_of": _now_str(),
            "turnover": parts.get("turnover"),
            "adv_dec": parts.get("adv_dec"),
            "main_flow": parts.get("main_flow"),
            "errors": errors,
        }
        if date:
            # notice 按主力资金流的实际回溯结果生成：降级到同花顺时不能声称「已回溯」
            flow = parts.get("main_flow") or {}
            if flow.get("historical"):
                flow_note = (f"主力资金流已回溯到 {date}"
                             f"（东方财富口径，该接口提供最近约 {flow.get('history_days')} 个交易日）")
            elif flow.get("source"):
                flow_note = (f"主力资金流当前为{flow.get('source')}口径"
                             f"（{flow.get('history_note') or '无历史序列'}），未能回溯到 {date}")
            else:
                flow_note = f"主力资金流暂不可用，未能回溯到 {date}"
            payload["historical"] = False     # 成交额 / 涨跌家数仍非历史，整体不算完整历史
            payload["notice"] = f"{flow_note}；成交额 / 涨跌家数当前无可用历史数据源，此处仍为实时快照"
        return payload

    # 缓存键带上日期，避免查询历史日期时命中实时缓存（与 ①③④⑤ 保持一致）
    data, cached = _cached(f"capital_flow:{date or 'rt'}", build,
                           ttl=HIST_CACHE_TTL if date else _ttl(60))
    return {**data, "cached": cached}


# --------------------------------------------------------------------------- #
# ③ 板块β
# --------------------------------------------------------------------------- #
def _rank_sectors(df, errors, label, top=10):
    """按资金净额排序，返回净流入 / 净流出 Top N。"""
    try:
        rows = []
        for _, row in df.iterrows():
            net = _num(row.get("净额"))
            if net is None:
                continue
            rows.append({
                "name": str(row.get("行业")),
                "pct": _num(row.get("行业-涨跌幅")),
                "net": net,
                "inflow": _num(row.get("流入资金")),
                "outflow": _num(row.get("流出资金")),
                "leader": str(row.get("领涨股") or ""),
                "leader_pct": _num(row.get("领涨股-涨跌幅")),
                "count": _num(row.get("公司家数"), 0),
            })
        rows.sort(key=lambda item: item["net"], reverse=True)
        return {"top_in": rows[:top], "top_out": list(reversed(rows[-top:])), "count": len(rows)}
    except Exception as exc:              # noqa: BLE001
        errors.append(f"{label}解析失败：{exc}")
        return {"top_in": [], "top_out": [], "count": 0}


def _sw_first_sectors(df, errors, top=10):
    """申万一级行业实时涨跌幅排行。"""
    try:
        rows = []
        for _, row in df.iterrows():
            last, prev = _num(row.get("最新价")), _num(row.get("昨收盘"))
            if last is None or not prev:
                continue
            rows.append({"name": str(row.get("指数名称")),
                         "pct": round((last - prev) / prev * 100, 2),
                         "amount": _num(row.get("成交额"))})
        rows.sort(key=lambda item: item["pct"], reverse=True)
        return {"top": rows[:top], "bottom": list(reversed(rows[-top:])), "count": len(rows)}
    except Exception as exc:              # noqa: BLE001
        errors.append(f"申万一级行业解析失败：{exc}")
        return {"top": [], "bottom": [], "count": 0}


def _sw_first_sectors_hist(date_str, errors, top=10):
    """申万一级行业历史涨跌：逐行业取日线，算该交易日相对前一交易日的涨跌幅。"""
    info = _ak(ak.index_realtime_sw, symbol="一级行业")
    if info is None or getattr(info, "empty", True):
        errors.append("申万一级行业列表获取失败")
        return {"top": [], "bottom": [], "count": 0}
    pairs = []
    for _, row in info.iterrows():
        code = str(row.get("指数代码") or "").strip()
        name = str(row.get("指数名称") or "").strip()
        if code and name:
            pairs.append((code, name))

    rows = []
    lock = threading.Lock()

    def worker(code, name):
        quote = _hist_quote(_ak(ak.index_hist_sw, symbol=code, period="day"), date_str)
        if quote and quote.get("pct") is not None:
            with lock:
                rows.append({"name": name, "pct": quote["pct"], "amount": None})

    threads = [threading.Thread(target=worker, args=pair, daemon=True) for pair in pairs]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(45)
    if not rows:
        errors.append("申万一级行业该交易日历史行情获取失败")
        return {"top": [], "bottom": [], "count": 0}
    rows.sort(key=lambda item: item["pct"], reverse=True)
    return {"top": rows[:top], "bottom": list(reversed(rows[-top:])), "count": len(rows)}


def sector_beta(date=None):
    """③ 板块β。

    date 为空：行业 / 概念 / 申万三个数据源并行抓取实时数据。
    date 指定历史交易日：申万一级行业按该日收盘计算涨跌；行业/概念资金流无历史数据源，返回空。
    """
    def build():
        errors = []
        if date:
            empty_flow = {"top_in": [], "top_out": [], "count": 0}
            errors.append("行业/概念板块资金流仅有实时数据，历史交易日不显示")
            return {"ok": True, "as_of": _now_str(), "trade_date": date, "historical": True,
                    "industry": empty_flow, "concept": dict(empty_flow),
                    "sw_first": _sw_first_sectors_hist(date, errors), "errors": errors}
        parts: dict = {}

        def fetch_industry():
            df = _ak(ak.stock_fund_flow_industry, symbol="即时")
            if df is None or getattr(df, "empty", True):
                errors.append("行业板块资金流获取失败（同花顺）")
                return {"top_in": [], "top_out": [], "count": 0}
            return _rank_sectors(df, errors, "行业板块资金流")

        def fetch_concept():
            df = _ak(ak.stock_fund_flow_concept, symbol="即时")
            if df is None or getattr(df, "empty", True):
                errors.append("概念板块资金流获取失败（同花顺）")
                return {"top_in": [], "top_out": [], "count": 0}
            return _rank_sectors(df, errors, "概念板块资金流")

        def fetch_sw_first():
            df = _ak(ak.index_realtime_sw, symbol="一级行业")
            if df is None or getattr(df, "empty", True):
                errors.append("申万一级行业行情获取失败")
                return {"top": [], "bottom": [], "count": 0}
            return _sw_first_sectors(df, errors)

        empty_industry = {"top_in": [], "top_out": [], "count": 0}
        empty_sw = {"top": [], "bottom": [], "count": 0}
        tasks = [("industry", fetch_industry), ("concept", fetch_concept), ("sw_first", fetch_sw_first)]
        threads = []
        for name, producer in tasks:
            def collect(_name=name, _fn=producer):
                try:
                    parts[_name] = _fn()
                except Exception as exc:      # noqa: BLE001 - 单来源失败不影响其他子项
                    errors.append(f"{_name} 获取失败：{exc}")
                    parts[_name] = None

            threads.append(threading.Thread(target=collect, daemon=True))
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)                   # 兜底：任一子项超时也照常返回，缺失项由前端降级提示

        return {"ok": True, "as_of": _now_str(), "historical": False,
                "industry": parts.get("industry") or empty_industry,
                "concept": parts.get("concept") or empty_industry,
                "sw_first": parts.get("sw_first") or empty_sw,
                "errors": errors}

    data, cached = _cached(f"sector_beta:{date or 'rt'}", build,
                           ttl=HIST_CACHE_TTL if date else _ttl(90))
    return {**data, "cached": cached}


# --------------------------------------------------------------------------- #
# ④ 连板梯队 / ⑤ 大面股
# --------------------------------------------------------------------------- #
def _resolve_trade_date(errors, back=8):
    """从今天往前找最近一个有涨停池数据的交易日。"""
    today = dt.date.today()
    for offset in range(back):
        date_str = (today - dt.timedelta(days=offset)).strftime("%Y%m%d")
        df = _ak(ak.stock_zt_pool_em, date=date_str)
        if df is not None and not getattr(df, "empty", True):
            return date_str
    errors.append("未找到可用交易日（涨停池数据为空）")
    return None


def _limit_up_detail(df):
    """今日涨停池明细 → 结构化列表。"""
    stocks = []
    for _, row in df.iterrows():
        stocks.append({
            "code": str(row.get("代码") or ""),
            "name": str(row.get("名称") or "").replace(" ", ""),
            "level": int(_num(row.get("连板数"), 0) or 1),
            "pct": _num(row.get("涨跌幅")),
            "price": _num(row.get("最新价")),
            "industry": str(row.get("所属行业") or ""),
            "amount": _num((_num(row.get("成交额"), 0) or 0) / 1e8),
            "turnover": _num(row.get("换手率")),
            "seal_fund": _num((_num(row.get("封板资金"), 0) or 0) / 1e8),
            "stat": str(row.get("涨停统计") or ""),
            "first_seal": _fmt_seal_time(row.get("首次封板时间")),
        })
    stocks.sort(key=lambda item: (-item["level"], item["first_seal"] or ""))
    return stocks


def _promotion_table(today_stocks, prev_df):
    """晋级率：昨日 N 板 → 今日 N+1 板占比。"""
    today_level = {item["code"]: item["level"] for item in today_stocks}
    prev_levels = {}
    if prev_df is None or getattr(prev_df, "empty", True):
        return []
    for _, row in prev_df.iterrows():
        level = int(_num(row.get("昨日连板数"), 0) or 0)
        prev_levels.setdefault(level, []).append(str(row.get("代码") or ""))
    table = []
    for level in sorted(prev_levels):
        codes = prev_levels[level]
        promoted = sum(1 for code in codes if today_level.get(code) == level + 1)
        table.append({
            "from": level,
            "total": len(codes),
            "promoted": promoted,
            "rate": round(promoted / len(codes) * 100, 1) if codes else 0.0,
        })
    return table


def _ladder_structure(stocks):
    """连板结构：各连板数家数。"""
    levels = {}
    for item in stocks:
        levels[item["level"]] = levels.get(item["level"], 0) + 1
    return [{"level": level, "count": count} for level, count in sorted(levels.items())]


def limit_up_ladder(date=None):
    """④ 连板梯队。"""
    def build():
        errors = []
        trade_date = date or _resolve_trade_date(errors)
        if not trade_date:
            return {"ok": False, "as_of": _now_str(), "trade_date": None,
                    "structure": [], "promotion": [], "stocks": [], "errors": errors}

        today_df = _ak(ak.stock_zt_pool_em, date=trade_date)
        if today_df is None or getattr(today_df, "empty", True):
            errors.append("今日涨停池获取失败")
            return {"ok": False, "as_of": _now_str(), "trade_date": trade_date,
                    "structure": [], "promotion": [], "stocks": [], "errors": errors}

        stocks = _limit_up_detail(today_df)
        prev_df = _ak(ak.stock_zt_pool_previous_em, date=trade_date)
        if prev_df is None or getattr(prev_df, "empty", True):
            errors.append("昨日涨停池获取失败，晋级率不可用")

        return {
            "ok": True,
            "as_of": _now_str(),
            "trade_date": trade_date,
            "total": len(stocks),
            "max_level": max((item["level"] for item in stocks), default=0),
            "structure": _ladder_structure(stocks),
            "promotion": _promotion_table(stocks, prev_df),
            "stocks": stocks,
            "errors": errors,
        }

    data, cached = _cached(f"limit_up:{date or 'auto'}", build,
                           ttl=HIST_CACHE_TTL if date else _ttl(120))
    return {**data, "cached": cached}


def _blasted_detail(df):
    """炸板池：相对涨停价的回撤幅度。"""
    rows = []
    for _, row in df.iterrows():
        price, limit_price = _num(row.get("最新价")), _num(row.get("涨停价"))
        drawdown = None
        if price is not None and limit_price:
            drawdown = round((price - limit_price) / limit_price * 100, 2)
        rows.append({
            "code": str(row.get("代码") or ""),
            "name": str(row.get("名称") or "").replace(" ", ""),
            "pct": _num(row.get("涨跌幅")),
            "price": price,
            "high_limit": limit_price,
            "drawdown": drawdown,
            "amplitude": _num(row.get("振幅")),
            "blasted_times": _num(row.get("炸板次数"), 0),
            "stat": str(row.get("涨停统计") or ""),
            "industry": str(row.get("所属行业") or ""),
            "turnover": _num(row.get("换手率")),
        })
    rows.sort(key=lambda item: (item["pct"] if item["pct"] is not None else 0))
    return rows


def _limit_down_detail(df):
    """跌停池。"""
    rows = []
    for _, row in df.iterrows():
        rows.append({
            "code": str(row.get("代码") or ""),
            "name": str(row.get("名称") or "").replace(" ", ""),
            "pct": _num(row.get("涨跌幅")),
            "price": _num(row.get("最新价")),
            "industry": str(row.get("所属行业") or ""),
            "turnover": _num(row.get("换手率")),
            "continuous": _num(row.get("连续跌停"), 0),
            "open_times": _num(row.get("开板次数"), 0),
            "seal_fund": _num((_num(row.get("封单资金"), 0) or 0) / 1e8),
        })
    rows.sort(key=lambda item: (item["pct"] if item["pct"] is not None else 0))
    return rows


def big_loss(date=None):
    """⑤ 大面股（炸板 + 跌停）。"""
    def build():
        errors = []
        trade_date = date or _resolve_trade_date(errors)
        if not trade_date:
            return {"ok": False, "as_of": _now_str(), "trade_date": None,
                    "blasted": [], "limit_down": [], "errors": errors}

        blasted_df = _ak(ak.stock_zt_pool_zbgc_em, date=trade_date)
        blasted = []
        if blasted_df is None or getattr(blasted_df, "empty", True):
            errors.append("炸板池获取失败")
        else:
            blasted = _blasted_detail(blasted_df)

        limit_down_df = _ak(ak.stock_zt_pool_dtgc_em, date=trade_date)
        limit_down = []
        if limit_down_df is None or getattr(limit_down_df, "empty", True):
            errors.append("跌停池获取失败")
        else:
            limit_down = _limit_down_detail(limit_down_df)

        return {"ok": True, "as_of": _now_str(), "trade_date": trade_date,
                "blasted": blasted, "limit_down": limit_down, "errors": errors}

    data, cached = _cached(f"big_loss:{date or 'auto'}", build,
                           ttl=HIST_CACHE_TTL if date else _ttl(120))
    return {**data, "cached": cached}
