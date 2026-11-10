# -*- coding: utf-8 -*-
"""通达信（TDX）**本地**数据读取：只做文件解析，不做网络、不写库。

用途：把本机通达信安装目录里的日线文件读成与 `price_service.fetch_daily`
**结构一致**的 bars，交给既有 `validate_bars` + `price_store.upsert_bars` 落盘。
这样「数据来源」可换，校验与存储逻辑一行都不用改。

日线文件格式（`vipdoc/{sh,sz,bj}/lday/{市场}{代码}.day`，实测 `C:\\new_tdx64`）：
    每条记录 32 字节，小端；字段内低字节在前
      00-03  日期       uint32   YYYYMMDD
      04-07  开盘价     uint32   单位：分（÷100 得元）
      08-11  最高价     uint32   同上
      12-15  最低价     uint32   同上
      16-19  收盘价     uint32   同上
      20-23  成交额     float    单位：元
      24-27  成交量     uint32   单位：**股**（东财口径是「手」，故落盘前 ÷100）
      28-31  保留

除权除息（`T0002/hq_cache/gbbq`）：
    结构是「4 字节记录数 + N×29 字节记录」（实测 193282×29+4 与文件大小精确吻合），
    但内容是**加密 / 压缩**的：全文件按任意偏移搜 YYYYMMDD 只命中几十次（噪声水平），
    记录内也不含可读代码。因此 `read_xdxr` 只做**带严格校验的尝试**，
    解不出就返回 None，由调用方回落到在线源——绝不把猜出来的数据写进库。
"""
from __future__ import annotations

import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

# 市场目录 → 允许的代码前缀（只取 A 股，排除指数 / 基金 / 债券 / B 股）
_MARKETS: dict[str, tuple[str, ...]] = {
    "sh": ("60", "68"),      # 沪市主板 + 科创板
    "sz": ("00", "30"),      # 深市主板 / 中小板 + 创业板
    # 北交所：43/83/87/88/92 是股票；81/89 是指数与基金，排除
    "bj": ("43", "83", "87", "88", "92"),
}

RECORD_SIZE = 32

# 覆盖度全扫的线程数：读 .day 头尾各 32 字节是纯 IO（GIL 会释放），
# 8 线程实测冷盘 ~0.95ms/只、热盘 ~0.1ms/只，比串行快一个数量级。
_SCAN_WORKERS = 8

# 覆盖度结果的缓存时长（秒）：全扫一次 1~10 秒，没必要每次刷新页面都重扫。
_STATS_TTL = 300.0
_stats_lock = threading.Lock()
_stats_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _lday_dir(root: Path, market: str) -> Path:
    return root / "vipdoc" / market / "lday"


def detect(root: str | Path) -> dict[str, Any]:
    """检测某个目录是否是通达信安装目录，并给出可用的日线数据概况。

    **只做轻量校验**（目录存在 + 统计文件数 + 读一只样本股的跨度），
    因为它被 `auto_detect` 之后的「目录是否可用」判断反复调用，
    一次要几秒的全扫不能挂在这条路径上。
    真正的覆盖度统计在 `coverage_stats()`（带缓存，单独调用）。
    """
    base = Path(root)
    markets: dict[str, Any] = {}
    total_files = 0
    sample_range = ""
    for market in _MARKETS:
        path = _lday_dir(base, market)
        if not path.is_dir():
            continue
        files = list(path.glob("*.day"))
        if not files:
            continue
        size = sum(item.stat().st_size for item in files)
        markets[market] = {"files": len(files), "bytes": size,
                           "path": str(path)}
        total_files += len(files)
    if markets and not sample_range:
        # 用一只样本股给出实际时间跨度（用户最关心「到底有几年」）
        sample = _sample_file(base)
        if sample:
            bars = read_day_bars(base, sample)
            if bars:
                sample_range = f"{bars[0]['date']} ~ {bars[-1]['date']}（{len(bars)} 个交易日）"
    return {
        "path": str(base),
        "exists": base.is_dir(),
        "valid": bool(markets),
        "markets": markets,
        "total_files": total_files,
        "sample": sample_range,
        "has_gbbq": (base / "T0002" / "hq_cache" / "gbbq").is_file(),
    }


def _sample_file(base: Path) -> str:
    """挑一只肯定存在的股票做样本（浦发银行，失败则退回任意一个）。"""
    preferred = [("sh", "600000"), ("sz", "000001"), ("bj", "830799")]
    for market, code in preferred:
        if (_lday_dir(base, market) / f"{market}{code}.day").is_file():
            return code
    for market in _MARKETS:
        files = sorted(_lday_dir(base, market).glob("*.day"))
        for item in files:
            code = item.stem[2:]
            if code.startswith(_MARKETS[market]):
                return code
    return ""


# --------------------------------------------------------------------------- #
# 覆盖度统计（全扫）：最新日 / 历史起点 / 例外只数
# --------------------------------------------------------------------------- #
def _a_share_day_files(base: Path) -> list[Path]:
    """本地日线里属于 A 股的 .day 文件（与 `list_codes` 同一口径）。"""
    files: list[Path] = []
    for market, prefixes in _MARKETS.items():
        folder = _lday_dir(base, market)
        if not folder.is_dir():
            continue
        for item in folder.glob("*.day"):
            stem = item.stem
            if len(stem) != 8:
                continue
            code = stem[2:]                     # 去掉 sh / sz / bj 前缀
            if len(code) == 6 and code.isdigit() and code.startswith(prefixes):
                files.append(item)
    return files


def _file_span(path: Path) -> tuple[str, str] | None:
    """读 .day 的**头尾各 32 字节**，取首末交易日；读不出来返回 None。"""
    try:
        size = path.stat().st_size
        records = size // RECORD_SIZE
        if records <= 0:
            return None
        with path.open("rb") as handle:
            head = handle.read(RECORD_SIZE)
            handle.seek((records - 1) * RECORD_SIZE)
            tail = handle.read(RECORD_SIZE)
    except OSError:
        return None
    if len(head) < RECORD_SIZE or len(tail) < RECORD_SIZE:
        return None
    first = _day_text(struct.unpack_from("<I", head, 0)[0])
    last = _day_text(struct.unpack_from("<I", tail, 0)[0])
    return (first, last) if first and last else None


def _mode(values: list[str], prefer_late: bool = True) -> tuple[str, int]:
    """众数与其只数；并列时按日期先后取一端（末日期取晚、首日期取早）。"""
    if not values:
        return "", 0
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    items = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    top = items[0]
    tied = [item for item in items if item[1] == top[1]]
    if len(tied) > 1:
        tied.sort(key=lambda kv: kv[0], reverse=prefer_late)
        top = tied[0]
    return top[0], top[1]


def _median_date(values: list[str]) -> str:
    """日期中位数（ISO 串可直接按字典序比较；偶数个取靠前的一个）。"""
    if not values:
        return ""
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return ordered[mid - 1]


def _scan_coverage(base: Path) -> dict[str, Any]:
    """全扫本地日线，统计每只的首末交易日（并发 8 线程）。"""
    started = time.time()
    files = _a_share_day_files(base)
    spans: list[tuple[str, str]] = []
    bad = 0
    if files:
        with ThreadPoolExecutor(max_workers=_SCAN_WORKERS) as pool:
            for result in pool.map(_file_span, files):
                if result is None:
                    bad += 1
                else:
                    spans.append(result)
    firsts = [span[0] for span in spans]
    lasts = [span[1] for span in spans]
    total = len(spans)
    last_mode, last_mode_count = _mode(lasts, prefer_late=True)
    first_mode, first_mode_count = _mode(firsts, prefer_late=False)
    latest = max(lasts) if lasts else ""
    earliest = min(firsts) if firsts else ""
    at_latest = sum(1 for value in lasts if value == latest) if latest else 0
    stale = sum(1 for value in lasts if latest and value < latest)
    newer = sum(1 for value in lasts if last_mode and value > last_mode)
    # 首日期晚于众数的 = 上市更晚的新股（按上市日起算，不是数据缺口）
    new_stocks = sum(1 for value in firsts if first_mode and value > first_mode)
    early = sum(1 for value in firsts if first_mode and value < first_mode)
    return {
        "files": len(files),
        "total": total,
        "bad": bad,
        "latest": latest,
        "earliest": earliest,
        "last_mode": last_mode,
        "last_mode_count": last_mode_count,
        "last_ratio": round(last_mode_count / total, 4) if total else 0.0,
        "at_latest": at_latest,
        "at_latest_ratio": round(at_latest / total, 4) if total else 0.0,
        "stale_count": stale,
        "newer_count": newer,
        "first_mode": first_mode,
        "first_mode_count": first_mode_count,
        "first_ratio": round(first_mode_count / total, 4) if total else 0.0,
        "new_count": new_stocks,
        "early_count": early,
        "median_first": _median_date(firsts),
        "median_last": _median_date(lasts),
        "elapsed_ms": int((time.time() - started) * 1000),
    }


def coverage_stats(root: str | Path, *, force: bool = False) -> dict[str, Any]:
    """本地日线的**覆盖度统计**（最新日 / 历史起点 / 例外只数）。

    全扫一遍（每只只读头尾各 32 字节），8 线程并发；结果按目录缓存 5 分钟，
    免得每次刷新页面都重付这 1~10 秒。

    口径说明：
    - `latest` / `at_latest`：末日期的最大值，以及停在它上面的只数（绝大多数票一样）。
    - `stale_count`：末日期早于 `latest` 的只数（停牌 / 退市 / 长期没数据的票）。
    - `first_mode` / `new_count`：首日期的众数（老股的共同起点）与首日期更晚的只数
      （新股按上市日起算，**不是**数据缺口）。
    """
    base = Path(root)
    key = str(base).lower()
    now = time.time()
    with _stats_lock:
        hit = _stats_cache.get(key)
        if hit is not None and not force and (now - hit[0]) < _STATS_TTL:
            return hit[1]
    stats = _scan_coverage(base)
    with _stats_lock:
        _stats_cache[key] = (time.time(), stats)
    return stats


def auto_detect() -> str | None:
    """在本机常见安装位置里找通达信目录；找不到返回 None。"""
    candidates = [
        Path(r"C:\new_tdx64"), Path(r"C:\new_tdx"),
        Path(r"D:\new_tdx64"), Path(r"D:\new_tdx"),
        Path(r"E:\new_tdx64"), Path(r"E:\new_tdx"),
        Path(r"C:\Program Files\new_tdx"),
        Path(r"C:\Program Files (x86)\new_tdx"),
    ]
    for item in candidates:
        if _lday_dir(item, "sh").is_dir():
            return str(item)
    return None


def list_codes(root: str | Path) -> list[str]:
    """列出本地日线里覆盖到的 A 股代码（去重、升序）。"""
    base = Path(root)
    codes: set[str] = set()
    for market, prefixes in _MARKETS.items():
        path = _lday_dir(base, market)
        if not path.is_dir():
            continue
        for item in path.glob("*.day"):
            code = item.stem[2:]                    # 去掉 sh / sz / bj 前缀
            if len(code) == 6 and code.isdigit() and code.startswith(prefixes):
                codes.add(code)
    return sorted(codes)


def day_file(root: str | Path, code: str) -> Path | None:
    """某只股票的日线文件路径（按市场依次找），不存在返回 None。"""
    base = Path(root)
    code = str(code).strip().zfill(6)
    for market, prefixes in _MARKETS.items():
        if not code.startswith(prefixes):
            continue
        candidate = _lday_dir(base, market) / f"{market}{code}.day"
        if candidate.is_file():
            return candidate
    return None


def _day_text(value: int) -> str:
    text = str(value)
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}" if len(text) == 8 and value > 0 else ""


def day_range(root: str | Path, code: str) -> tuple[str, str] | None:
    """本地日线文件的**首末日期**（只读头尾各 32 字节，不解析全表）。

    用于判断「库里是不是已经有这段数据了」——够快，才能在遍历里逐只调用。
    """
    path = day_file(root, code)
    if path is None:
        return None
    try:
        with path.open("rb") as handle:
            head = handle.read(RECORD_SIZE)
            if len(head) < RECORD_SIZE:
                return None
            handle.seek(-RECORD_SIZE, 2)          # 2 = os.SEEK_END
            tail = handle.read(RECORD_SIZE)
    except OSError:
        return None
    first = _day_text(struct.unpack_from("<I", head, 0)[0])
    last = _day_text(struct.unpack_from("<I", tail, 0)[0])
    return (first, last) if first and last else None


def read_day_bars(root: str | Path, code: str) -> list[dict[str, Any]]:
    """读某只股票的本地日线；文件不存在或为空返回 []。

    字段与 `price_service.normalize_bars` 对齐，差额字段按现有规则补齐：
    振幅 / 涨跌幅 / 涨跌额 由 OHLC 现算；**换手率**需要流通股本，本地数据里没有，留空。
    """
    path = day_file(root, code)
    if path is None:
        return []
    raw = path.read_bytes()
    bars: list[dict[str, Any]] = []
    previous_close: float | None = None
    for offset in range(0, len(raw) - RECORD_SIZE + 1, RECORD_SIZE):
        # 5 个 uint32（日期/开/高/低/收）+ 1 个 float（成交额）+ 2 个 uint32（成交量/保留）
        day, open_, high, low, close, amount, volume, _reserved = struct.unpack_from(
            "<IIIIIfII", raw, offset)
        if day <= 0 or close <= 0:                  # 空记录 / 尾部的填充
            continue
        text = str(day)
        # 价格单位分 → 元；成交量单位股 → 手（与东财口径一致）
        open_price, high_price = open_ / 100.0, high / 100.0
        low_price, close_price = low / 100.0, close / 100.0
        change_pct = change_amount = amplitude = None
        if previous_close:
            change_amount = round(close_price - previous_close, 4)
            change_pct = round((close_price - previous_close) / previous_close * 100, 4)
            amplitude = round((high_price - low_price) / previous_close * 100, 4)
        previous_close = close_price
        bars.append({
            "date": f"{text[:4]}-{text[4:6]}-{text[6:8]}",
            "open": open_price,
            "high": high_price,
            "low": low_price,
            "close": close_price,
            "volume": round(volume / 100.0, 2),     # 股 → 手
            "amount": float(amount),
            "amplitude": amplitude,
            "change_pct": change_pct,
            "change_amount": change_amount,
            "turnover": None,                       # 本地数据算不出（缺流通股本）
        })
    return bars


# gbbq 解不出的目录记在这里，避免每只股票都白扫一遍
_XDXR_BROKEN: set[str] = set()


def read_xdxr(root: str | Path, code: str) -> list[dict[str, Any]] | None:
    """尝试从本地 `gbbq` 读除权除息；**解不出返回 None**（调用方应回落在线源）。

    gbbq 的容器结构是确定的（4 字节记录数 + N×29 字节），但内容加密，
    这里只做「能不能读出像样的日期」的判定，绝不做猜测性解析。
    """
    path = Path(root) / "T0002" / "hq_cache" / "gbbq"
    key = str(path).lower()
    if not path.is_file() or key in _XDXR_BROKEN:
        return None
    try:
        raw = path.read_bytes()
        if len(raw) < 4 + 29:
            return None
        count = struct.unpack_from("<I", raw, 0)[0]
        if count <= 0 or 4 + count * 29 != len(raw):
            return None
        # 抽查：若真为明文，日期字段必然大量落在合理区间内
        hits = 0
        for index in range(0, min(count, 500)):
            offset = 4 + index * 29
            for shift in range(0, 26, 4):
                value = struct.unpack_from("<I", raw, offset + shift)[0]
                if 19900101 <= value <= 20301231:
                    hits += 1
                    break
        if hits < 100:                              # 明文数据不可能只有个位数命中
            _XDXR_BROKEN.add(key)
            return None
    except Exception:                               # noqa: BLE001 - 读不了就当不可用
        _XDXR_BROKEN.add(key)
        return None
    return None                                     # 结构可解时再补真正的字段解析
