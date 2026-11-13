# -*- coding: utf-8 -*-
"""股票池层（独立于编排层）：按规则生成回测标的代码，并做进程内缓存。

## 为什么要独立成文件
「范围 → 代码列表」是一段有状态的规则计算（板块前缀 / ST 名称 / 上市日 / 市值），
既不属于 HTTP 路由（routes 只该转发请求），也不属于回测编排（backtest 只该消费 codes）。
把它抽成独立层后：

    routes ──spec_from_request()──▶  universe.build_detail()  ──codes+明细──▶  backtest.start_task()

**spec 也归这一层构造**（`universe.spec_from_request`）：路由层只转发请求，
不自己拼 spec —— 预览与实跑各拼一份时，改口径必然漏一处（漏一处 → 界面与实跑
口径不一致 → 静默失真）。改范围口径的**允许改动面 = 本文件**（+ 需要新取数能力时
的 `data.py`），前端与报告不用动。

## 缓存
结果只由「规则 + 基准日」决定。连续多次回测不改范围时**没必要重算** —— 尤其 ST 名称表
要联网、市值要全库扫一遍。这里用**小型键值缓存**（key = 规范化后的 spec，最多 8 种范围）
把生成结果留在内存；范围一变就按新 key 重建，旧范围仍可命中。TTL 兜底，防止长期运行后
数据已更新而池子仍是旧的。

## 判定口径（**与时间的关系**）
剔除特征不是「按回测区间逐日判断」，而是**一次性把整段历史的池子筛出来**：
  · 微盘股 → **区间起点**当日市值（point-in-time，不含未来信息，也不随日期漂）；
  · 新股/次新股 → 上市日距**区间起点**的天数（回测当时视角）；
  · ST/*ST → akshare **当前**名称表（数据源无历史名单 → 前视偏差，**必须显式声明**）；
  · 板块 → 代码前缀（与时间无关）。
「每项剔除了多少只」必须回传并写进报告 —— 否则读者无从判断这个净值跑在什么样本上。

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

# 特征 → 中文名（预览与报告共用一份，别各处手写）
FEATURE_LABELS = {
    "chinext": "创业板", "star": "科创板", "bse": "北交所",
    "st": "ST", "star_st": "*ST", "ipo": "新股", "sub_new": "次新股",
    "micro": "微盘股",
}

# **只能用「当前状态」判定**的特征：数据源不提供历史 ST 名单，
# 无法还原「某个历史时点它是不是 ST」→ 用今天的状态筛整段历史 = 前视偏差。
# 这类特征必须在预览与报告里**显式声明**，不能只写一句笼统的「幸存者偏差」。
_FORWARD_LOOKING = ("st", "star_st")

# 默认阈值（可在 spec 里覆盖，便于以后调参而不用改代码）
IPO_DAYS = 90                 # 上市 ≤90 自然日 = 新股
SUB_NEW_DAYS = 365            # 90 < 上市 ≤365 自然日 = 次新股
MICRO_CAP = 3_000_000_000     # 总市值 ≤30 亿 = 微盘股

_CACHE_TTL = 3600             # 秒；范围不变时的复用窗口
_CACHE_MAX = 8                # 最多保留多少种范围（预览与回测不会互相挤掉）
_cache: dict = {}             # key -> (codes, base_count, at, detail)


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


def _as_of_date(spec: dict) -> dt.date:
    """「距今多少天」类剔除的基准日 = **区间起点**（回测当时视角）。

    ⚠️ 以前用的是区间**结束日**（`as_of` → `end` → 今天）→「新股/次新股」
    是按**未来**视角判的：区间早期上市的次新股，到区间末日已上市超过一年
    → 不算次新股 → 它新股期那段行情照样进池被交易。
    改成区间起点，才是「回测当时它算不算新股」。

    缺省链：`spec.as_of` → `spec.start` → 今天。
    """
    for key in ("as_of", "start"):
        v = _parse_date(spec.get(key))
        if v:
            return v
    return dt.date.today()


def spec_from_request(req, universe_mode: str | None = None) -> dict:
    """请求 → 股票池层 spec。**spec 构造的唯一入口**（路由层不许自己造）。

    为什么放在这一层：范围规则（判定基准日、剔除特征、阈值）全属股票池层语义，
    路由层只该「转发请求」。以前路由层自己造 spec —— 预览一份、实跑一份 ——
    改口径时两处都要跟着改，容易漏（漏一处 → 界面与实跑口径不一致 → 静默失真）。
    现在：**改范围口径只动本文件**。

    `req` 走鸭子类型：既接受 pydantic 请求对象，也接受 dict（预览接口的查询参数）。
    **本层不 import 路由层** —— 否则形成反向依赖。

    `as_of` 固定 = **区间起点**：「距今多少天」类剔除（新股/次新股）与微盘市值
    都按**回测当时视角**判定，不再用结束日/今天 —— 那是未来信息，
    还会让同一个区间每天跑出不同的池子。

    阈值（`ipo_days` / `sub_new_days` / `micro_cap`）**有才带**，
    不传就用本文件顶部的默认值。
    """
    def _get(key, default=None):
        if isinstance(req, dict):
            return req.get(key, default)
        return getattr(req, key, default)

    spec = {
        "universe_mode": universe_mode or _get("universe_mode") or "interval_data",
        "start": _get("start"),
        "end": _get("end"),
        "as_of": _get("start"),                 # 判定基准日 = 区间起点
        "exclude": list(_get("exclude") or []),
    }
    for key in ("ipo_days", "sub_new_days", "micro_cap"):
        v = _get(key)
        if v is not None:
            spec[key] = v
    return spec


def _apply_exclusions(base: list[str], spec: dict) -> tuple[list[str], dict[str, int], dict]:
    """基池剔除 exclude 里的特征。各特征独立计算、取并集；数据缺失时静默降级。

    返回 (剩余代码, 每项剔除只数)。**只数必须回传** —— 否则读者无法判断
    「勾了剔除」到底剔掉了什么（界面曾长期显示「剔除后 = 基池数」，而实跑
    却剔掉了一千多只，就是因为这个数从来没落地过）。

    判定口径（关键）：
      · 微盘股 → **区间起点**的总市值（point-in-time），不是「今天的市值」；
      · 新股/次新股 → 上市日距**区间起点**的天数；
      · ST/*ST → akshare 的**当前**名称表（无历史名单 → 前视偏差，须声明）。
    """
    ex = set(spec.get("exclude") or [])
    if not ex:
        return list(base), {}, {}

    ipo_days = int(spec.get("ipo_days") or IPO_DAYS)
    sub_days = int(spec.get("sub_new_days") or SUB_NEW_DAYS)
    micro_cap = float(spec.get("micro_cap") or MICRO_CAP)
    as_of_d = _as_of_date(spec)

    drop: set = set()
    dropped: dict[str, int] = {}

    def _hit(feature: str, codes) -> None:
        if codes:
            drop.update(codes)
            dropped[feature] = dropped.get(feature, 0) + len(codes)

    # 板块：纯代码前缀，零成本（与时间无关）
    for feat in ("chinext", "star", "bse"):
        if feat in ex:
            _hit(feat, [c for c in base if _board_key(c) == feat])

    # ST / *ST：名称（akshare 全市场表，24h 缓存）—— **当前**名单，无法还原历史
    if ex & {"st", "star_st"}:
        names = data.load_names(base)
        hit_st: list[str] = []
        hit_star_st: list[str] = []
        for code in base:
            nm = names.get(code) or ""
            if "*ST" in nm:
                if "star_st" in ex:
                    hit_star_st.append(code)
            elif "ST" in nm and "st" in ex:
                hit_st.append(code)
        _hit("st", hit_st)
        _hit("star_st", hit_star_st)

    # 新股 / 次新股：上市日距**区间起点**多少天
    if ex & {"ipo", "sub_new"}:
        first = data.code_first_dates()
        hit_ipo: list[str] = []
        hit_sub: list[str] = []
        for code in base:
            list_d = _parse_date(first.get(code))
            if list_d is None:
                continue
            days = (as_of_d - list_d).days
            if "ipo" in ex and days <= ipo_days:
                hit_ipo.append(code)
            elif "sub_new" in ex and ipo_days < days <= sub_days:
                hit_sub.append(code)
        _hit("ipo", hit_ipo)
        _hit("sub_new", hit_sub)

    # 微盘股：**区间起点**的总市值（point-in-time）。
    # 不用「今天的市值」：那是前视偏差（后来跌成微盘的票连同它过去那段大涨
    # 一起被整段剔除），而且市值天天变 → 同一区间两次跑结果不同。
    if "micro" in ex:
        caps, cap_meta = data.market_caps_at(base, as_of_d.isoformat())
        _hit("micro", [c for c in base
                       if caps.get(c) is not None and caps[c] <= micro_cap])

    extra = {"market_cap_meta": cap_meta} if "micro" in ex else {}
    return [c for c in base if c not in drop], dropped, extra


def build_detail(spec: dict, force: bool = False) -> tuple[list[str], dict]:
    """返回 (代码列表, 范围明细)。**股票池层的唯一入口**（命中缓存直接返回，
    force=True 强制重建）。

    spec 字段（全部可选）：
        universe_mode  interval_data / pool_snapshot / index_constituent
        start / end    区间（pool_snapshot 用；start 也是「距今」类剔除的基准日）
        as_of          判定基准日（缺省用 start，再缺省用今天）——「回测当时视角」
        exclude        排除特征键列表（见 CATEGORIES）
        ipo_days / sub_new_days / micro_cap   阈值覆盖

    明细含：基池/剔除后数量、**每项剔除只数**、判定基准日 as_of、
    以及哪些特征只能用「当前状态」判定（含前视偏差）。

    预览与回测报告**共用这一份**：以前预览只传 `exclude`、实跑还传
    `start/end/as_of` → 两个不同的缓存键 → 各算各的 → 界面显示「剔除后 = 基池」
    而实跑剔掉一千多只。现在两边用同一个 spec，数字必然一致。
    """
    key = _spec_key(spec)
    now = time.time()
    ent = _cache.get(key)
    if not force and ent and now - ent[2] < _CACHE_TTL:
        return list(ent[0]), dict(ent[3])

    base = data.list_universe_codes(
        universe_mode=spec.get("universe_mode") or "interval_data",
        start=spec.get("start"), end=spec.get("end"),
    )
    codes, dropped, extra = _apply_exclusions(base, spec)
    ex = sorted(set(spec.get("exclude") or []))
    as_of_d = _as_of_date(spec)
    detail = {
        "base_count": len(base),
        "filtered_count": len(codes),
        "excluded_count": len(base) - len(codes),
        "dropped": dropped,
        "exclude": ex,
        "as_of": as_of_d.isoformat(),
        # 只能用当前状态判定的特征 → 前视偏差，必须让人看见（不能只写「幸存者偏差」）
        "forward_looking": [f for f in _FORWARD_LOOKING if f in ex],
        # 上市日只能拿「库里最早日K」近似 —— 历史未拉全的老票会被误判成
        # 新股/次新股（剔除偏多）。误差方向必须写出来，不能让读者自己猜。
        "listing_date_source": "earliest_bar",
    }
    detail.update(extra or {})
    _cache[key] = (codes, len(base), now, detail)
    if len(_cache) > _CACHE_MAX:
        _cache.pop(min(_cache, key=lambda k: _cache[k][2]), None)  # 丢掉最旧的
    return list(codes), detail


def scope_note(detail: dict) -> str:
    """范围口径说明（一段话）—— 预览与回测报告**共用同一句**，别各处手写。

    必须点明四件事：
      ① 每项剔除了多少只（否则读者不知道「勾了剔除」到底做了什么）；
      ② 判定基准日 = 区间起点（微盘按该日市值、新股/次新股按该日起算）；
      ③ 哪些判定用的是「当前状态」→ 含前视偏差（数据源不提供历史 ST 名单）；
      ④ 两条数据限制：股本只有当前值、上市日只能拿「库里最早日K」近似。
    """
    detail = detail or {}
    dropped = detail.get("dropped") or {}
    as_of = detail.get("as_of") or "-"
    bits = [f"{FEATURE_LABELS.get(k, k)} {v}" for k, v in dropped.items() if v]
    detail_txt = "、".join(bits) if bits else "无"
    txt = (f"范围口径：基池 {detail.get('base_count')} 只 → 剔除后 "
           f"{detail.get('filtered_count')} 只（剔除明细：{detail_txt}）；"
           f"判定基准日 = 区间起点 {as_of}"
           f"（微盘按该日市值 ≤{int(MICRO_CAP / 1e8)} 亿、新股/次新股按该日起算上市天数）")
    fwd = [FEATURE_LABELS.get(k, k) for k in (detail.get("forward_looking") or [])]
    if fwd:
        txt += (f"；⚠️ {'、'.join(fwd)} 按**当前**名单判定（数据源不提供历史名单，"
                f"无法还原历史 → 含前视偏差）")
    if detail.get("listing_date_source") == "earliest_bar":
        txt += "；⚠️ 上市日以「库里最早日K」近似：历史未拉全的老票会被误判成新股/次新股（剔除偏多）"
    cap_meta = detail.get("market_cap_meta") or {}
    if cap_meta.get("shares_source") == "latest":
        txt += "；⚠️ 微盘判定用的**股本是当前值**（数据源不提供历史股本，只有价格回到区间起点）"
    return txt


def summary(spec: dict, force: bool = False) -> dict:
    """回测范围预览：基池数量 + 剔除后数量 + **每项剔除只数** + 口径说明。"""
    codes, detail = build_detail(spec, force=force)
    out = {
        "base_count": detail["base_count"],
        "filtered_count": detail["filtered_count"],
        "excluded_count": detail["excluded_count"],
        "dropped": detail["dropped"],
        "as_of": detail["as_of"],
        "forward_looking": detail["forward_looking"],
        "listing_date_source": detail.get("listing_date_source"),
        "market_cap_meta": detail.get("market_cap_meta") or {},
        "note": scope_note(detail),
    }
    return out


def invalidate() -> None:
    """清空缓存（例如下载新数据后希望下次重建）。"""
    _cache.clear()
