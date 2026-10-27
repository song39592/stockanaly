# -*- coding: utf-8 -*-
"""股票池层（独立于编排层）：按规则生成回测标的代码，并做进程内缓存。

## 为什么要独立成文件
「范围 → 代码列表」是一段有状态的规则计算（板块前缀 / ST 名称 / 上市日 / 市值），
既不属于 HTTP 路由（routes 只该转发请求），也不属于回测编排（backtest 只该消费 codes）。
把它抽成独立层后：

    routes  ──spec──▶  universe.build()  ──codes──▶  backtest.start_task()

## 缓存
结果只由「规则 + 基准日」决定。连续多次回测不改范围时**没必要重算** —— 尤其 ST 名称表
要联网、市值要全库扫一遍。这里用**小型键值缓存**（key = 规范化后的 spec，最多 8 种范围）
把生成结果留在内存；范围一变就按新 key 重建，旧范围仍可命中。TTL 兜底，防止长期运行后
数据已更新而池子仍是旧的。

## 分层
universe（本文件）→ data（原始取数：基池 / 名称 / 上市日 / 市值）。
**不依赖 backtest**，避免「编排层 ↔ 股票池层」反向依赖。
"""
from __future__ import annotations

import datetime as dt
import time

from . import data

# 与前端约定的排除特征键
CATEGORIES = ("chinext", "star", "bse", "st", "star_st", "ipo", "sub_new", "micro")

# 默认阈值（可在 spec 里覆盖，便于以后调参而不用改代码）
IPO_DAYS = 90                 # 上市 ≤90 自然日 = 新股
SUB_NEW_DAYS = 365            # 90 < 上市 ≤365 自然日 = 次新股
MICRO_CAP = 3_000_000_000     # 总市值 ≤30 亿 = 微盘股

_CACHE_TTL = 3600             # 秒；范围不变时的复用窗口
_CACHE_MAX = 8                # 最多保留多少种范围（预览与回测不会互相挤掉）
_cache: dict = {}             # key -> (codes, base_count, at)


def _board_key(code: str) -> str:
    """代码前缀 → 板块键。主板 / 中小板归 main（排除双创+北交所即剩它）。"""
    c = str(code).zfill(6)
    if c.startswith(("300", "301")):
        return "chinext"
    if c.startswith(("688", "689")):
        return "star"
    if c.startswith(("43", "83", "87", "88", "92")):
        return "bse"
    return "main"


def _parse_date(value):
    if not value:
        return None
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except Exception:  # noqa: BLE001
        return None


def _spec_key(spec: dict) -> tuple:
    """把 spec 规范化成可比较的键（顺序无关、去重）。"""
    return (
        str(spec.get("universe_mode") or "interval_data"),
        tuple(sorted(set(spec.get("exclude") or []))),
        str(spec.get("start") or ""),
        str(spec.get("end") or ""),
        str(spec.get("as_of") or ""),
        int(spec.get("ipo_days") or IPO_DAYS),
        int(spec.get("sub_new_days") or SUB_NEW_DAYS),
        float(spec.get("micro_cap") or MICRO_CAP),
    )


def _apply_exclusions(base: list[str], spec: dict) -> list[str]:
    """基池剔除 exclude 里的特征。各特征独立计算、取并集；数据缺失时静默降级。"""
    ex = set(spec.get("exclude") or [])
    if not ex:
        return list(base)

    ipo_days = int(spec.get("ipo_days") or IPO_DAYS)
    sub_days = int(spec.get("sub_new_days") or SUB_NEW_DAYS)
    micro_cap = float(spec.get("micro_cap") or MICRO_CAP)
    as_of_d = _parse_date(spec.get("as_of") or spec.get("end")) or dt.date.today()

    drop: set = set()

    # 板块：纯代码前缀，零成本
    if ex & {"chinext", "star", "bse"}:
        for code in base:
            if _board_key(code) in ex:
                drop.add(code)

    # ST / *ST：名称（akshare 全市场表，24h 缓存）
    if ex & {"st", "star_st"}:
        names = data.load_names(base)
        for code in base:
            nm = names.get(code) or ""
            is_star = "*ST" in nm
            is_st = ("ST" in nm) and not is_star
            if ("star_st" in ex and is_star) or ("st" in ex and is_st):
                drop.add(code)

    # 新股 / 次新股：上市日距今（自然日）
    if ex & {"ipo", "sub_new"}:
        first = data.code_first_dates()
        for code in base:
            list_d = _parse_date(first.get(code))
            if list_d is None:
                continue
            days = (as_of_d - list_d).days
            if ("ipo" in ex and days <= ipo_days) or (
                "sub_new" in ex and ipo_days < days <= sub_days
            ):
                drop.add(code)

    # 微盘股：最新总市值
    if "micro" in ex:
        caps = data.market_caps(base)
        for code in base:
            mc = caps.get(code)
            if mc is not None and mc <= micro_cap:
                drop.add(code)

    return [c for c in base if c not in drop]


def build(spec: dict, force: bool = False) -> list[str]:
    """按 spec 生成回测代码列表（命中缓存直接返回，force=True 强制重建）。

    spec 字段（全部可选）：
        universe_mode  interval_data / pool_snapshot / index_constituent
        start / end    区间（pool_snapshot 用；end 也是新股/次新的「距今」基准候选）
        as_of          新股/次新基准日（缺省用 end，再缺省用今天）
        exclude        排除特征键列表（见 CATEGORIES）
        ipo_days / sub_new_days / micro_cap   阈值覆盖
    """
    key = _spec_key(spec)
    now = time.time()
    ent = _cache.get(key)
    if not force and ent and now - ent[2] < _CACHE_TTL:
        return list(ent[0])

    base = data.list_universe_codes(
        universe_mode=spec.get("universe_mode") or "interval_data",
        start=spec.get("start"), end=spec.get("end"),
    )
    codes = _apply_exclusions(base, spec)
    _cache[key] = (codes, len(base), now)
    if len(_cache) > _CACHE_MAX:
        _cache.pop(min(_cache, key=lambda k: _cache[k][2]), None)  # 丢掉最旧的
    return list(codes)


def summary(spec: dict, force: bool = False) -> dict:
    """回测范围预览：基池数量、剔除后数量、被剔除数量（复用 build 的缓存）。"""
    codes = build(spec, force=force)
    ent = _cache.get(_spec_key(spec))
    base_n = ent[1] if ent else len(codes)
    return {
        "base_count": base_n,
        "filtered_count": len(codes),
        "excluded_count": base_n - len(codes),
    }


def invalidate() -> None:
    """清空缓存（例如下载新数据后希望下次重建）。"""
    _cache.clear()
