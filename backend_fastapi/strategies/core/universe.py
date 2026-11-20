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
  · 新股/次新股 → **交易所公布的真实上市日**距**区间起点**的天数（回测当时视角）；
  · ST/*ST → akshare **当前**名称表（数据源无历史名单 → 前视偏差，**必须显式声明**）；
  · 板块 → 代码前缀（与时间无关）。
「每项剔除了多少只」必须回传并写进报告 —— 否则读者无从判断这个净值跑在什么样本上。

## 注入判定（**该不该注入给策略 / 什么区间能注入**）
「这只票能不能注入给策略、在什么区间内能注入」是**一个**问题、一个答案，
所以判定只在本文件做：
  · 剔除特征（板块 / ST / 新股 / 次新股 / 微盘）→ `_apply_exclusions`；
  · 区间内行情够不够（根数 < `MIN_BARS` 算不出信号）→ `filter_available`；
  · 「实际取哪段区间」（短窗口要向前预热、推荐页要回看）→ `resolve_window` /
    `resolve_lookback_window`。
编排层（`backtest`）**只消费本层的结论**，不再自己判一遍 ——
以前两道关分属两个文件，实测同一区间「界面显示 4317 只、实跑 4314 只」。

## 分层
universe（本文件）→ data（原始取数：基池 / 名称 / 上市日 / 市值 / 区间根数）。
**不依赖 backtest**，避免「编排层 ↔ 股票池层」反向依赖。
"""
from __future__ import annotations

import datetime as dt
import hashlib
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

# ---- ST 名称名单的**覆盖率下限**（判定「这份名单够不够用」）----
# 名单只覆盖一部分票时，没覆盖的票会被当成「非 ST」放进来 → 与「名单整个没取到」
# 一样是降级，只是没那么显眼。低于这个比例 → 中止（见 ScopeDataUnavailable）。
NAME_COVERAGE_MIN = 0.95

# ---- 上市日表的**覆盖率下限**（同一把尺子）----
# 「新股/次新股」判定要靠上市日。上市日表只覆盖一部分票时，没覆盖的票
# **无法判定**是新股还是老票 → 与「整个没取到」一样是降级 → 中止。
LISTING_COVERAGE_MIN = 0.95


class ScopeDataUnavailable(RuntimeError):
    """剔除特征所需的**外部数据**取不到 → 口径无法兑现 → **明确中止**。

    ## 为什么必须中止，而不是降级（这是实测踩出来的坑）

    勾了「剔除 ST / \*ST」，但 ST 名称名单（akshare 全市场表）联网取不到时，
    旧实现**静默降级**成「一只 ST 都没剔」→

    * 报告的「剔除明细」里只是**没有 st 这一项** → 看起来像「本来就没有 ST」；
    * 口径指纹（`spec_signature`）只压常量 → **算不出差别**；
    * 于是两份报告 `spec_version` / `spec_signature` 完全相同，
      一个池 4101 只（没剔 ST）、一个池 3975 只（剔了 ST），收益差 5%
      却「看起来同口径」→ 结论直接失效。

    ## 正确做法

    **中止 + 给可操作的下一步**（取消该剔除项 / 恢复网络后重试）。
    取不到名单**不是**「没有 ST」，而是「不知道谁是 ST」—— 两者不能混。
    """


# ---- 注入判定的阈值（属**股票池层**语义：编排层只消费，不再自己判一遍）----
MIN_BARS = 30                 # 区间内 K 线 < 这么多根 = 数据不足，不可注入
WARMUP_BARS = 250             # 短窗口回测的指标预热根数（约一年，够 MACD 这类长周期策略）
RECO_LOOKBACK = 250           # 推荐页默认回看的交易日数（缺区间时用）

_CACHE_TTL = 3600             # 秒；范围不变时的复用窗口
_CACHE_MAX = 8                # 最多保留多少种范围（预览与回测不会互相挤掉）
_cache: dict = {}             # key -> (codes, base_count, at, detail)

# ---- 统计口径版本：口径变了就 bump ----------------------------------------
# 为什么需要：注入判定口径改一次（阈值 / 剔除特征 / 判定基准日），同一区间、同一
# 策略的结果能差 10~30 倍（实测 3839 笔 → 174645% 与 3418 笔 → 5569%），
# 而报告过去不记版本 → 两份报告摆在一起，无从判断能不能比。
#
# 2026-11-18.a：ST / *ST 剔除所需名单**取不到时从「静默降级」改成「中止」**
#   （旧口径会悄悄一只 ST 都没剔 → 报告与指纹都看不出，池子差 126 只、收益差 5%
#   却「看起来同口径」）。旧口径产出的报告一律视为**旧口径**，不可与新报告对比。
#
# 2026-11-20.a：**上市日从「库里最早日K」近似改成「交易所公布的真实上市日」**。
#   旧口径把「本地库最早有行情的那天」当上市日，而下载起点是 2021-08-02 →
#   实测 5601 只里 **4283 只**的「最早日K」= 那天（连 000001 / 600519 都算那天上市）
#   → 判定基准日改成区间起点后「天龄 = 0 ≤ 90」→ `ipo` 一次剔掉 **5599 只**，
#   实跑只剩 2 只；而预览按「今天」算只剔 430 只 → **预览与实跑口径分叉**。
#   新口径：交易所官方批量表（沪主板+沪科创+深市+北交所，实测 5580 只），
#   取不到**中止**（与 ST 名单同一纪律）。旧口径产出的报告一律视为**旧口径**。
SPEC_VERSION = "2026-11-20.a"


def spec_signature() -> str:
    """统计口径指纹（8 位）—— 把决定「池子构成与判定规则」的常量压成一个短串。

    与 `SPEC_VERSION` 的分工：
      · `SPEC_VERSION`   人工 bump，口径**语义**变了（如判定基准日从起点改成结束日）
      · `spec_signature` 自动算，口径**参数**变了（有人改了 MIN_BARS 却忘了 bump）

    ⚠️ 只覆盖**统计口径**（哪些票进池、怎么判）。**不覆盖**策略逻辑、行情数据、
    成交可行性判定（隔日一字涨停买不进那类）—— 别把它当成「可复现」的保证。
    """
    payload = "|".join([
        SPEC_VERSION,
        f"MIN_BARS={MIN_BARS}",
        f"WARMUP_BARS={WARMUP_BARS}",
        f"IPO_DAYS={IPO_DAYS}",
        f"SUB_NEW_DAYS={SUB_NEW_DAYS}",
        f"MICRO_CAP={MICRO_CAP}",
        f"NAME_COVERAGE_MIN={NAME_COVERAGE_MIN}",
        f"EXCLUDE={','.join(CATEGORIES)}",
        f"FWD={','.join(_FORWARD_LOOKING)}",
        "ASOF=start",
        "LISTING=exchange_listing_date",
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]


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
        # 只统计最近 N 个交易日时的窗口扩展：注入判定与取数要用同一个区间
        "window": _get("window"),
    }
    for key in ("ipo_days", "sub_new_days", "micro_cap"):
        v = _get(key)
        if v is not None:
            spec[key] = v
    return spec


def _apply_exclusions(base: list[str], spec: dict) -> tuple[list[str], dict[str, int], dict]:
    """基池剔除 exclude 里的特征。各特征独立计算、取并集。

    ⚠️ **依赖外部数据**的特征（ST / \*ST 要联网取名单）**取不到就中止**
    （`ScopeDataUnavailable`），不静默降级 —— 理由见该异常类的 docstring。

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
    st_cov: float | None = None      # ST 名单覆盖率（None = 本次没用到名单）
    st_covered: int = 0
    listing_meta: dict = {}          # 上市日来源 / 覆盖率 / 兜底只数（用到才填）

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
        # ⚠️ 顺序要紧：**先取**（取数失败会把原因记进 `names_source_status`），
        #    再判可用性 —— 反过来会得到「未知原因」，等于没告诉用户到底怎么了。
        names = data.load_names(base)
        status = data.names_source_status()
        if not status.get("ok"):
            raise ScopeDataUnavailable(
                "剔除 ST/*ST 需要联网取当前名称名单（akshare 全市场表），本次取不到"
                f"（{status.get('error') or '未知原因'}）。已中止本次回测 —— "
                "继续跑等于「勾了剔除却一只没剔」，报告里的剔除明细会看起来像"
                "「本来就没有 ST」，口径指纹也算不出差别。\n"
                "→ 下一步：(1) 恢复网络后重试；(2) 在回测范围里**取消**『剔除 ST / *ST』"
                "再跑（口径会变：报告会明确写出剔除明细里没有 ST）。"
            )
        st_covered = sum(1 for c in base if names.get(c))
        st_cov = (st_covered / len(base)) if base else 1.0
        if st_cov < NAME_COVERAGE_MIN:
            raise ScopeDataUnavailable(
                f"ST 名称名单只覆盖 {st_covered}/{len(base)} 只"
                f"（{st_cov:.1%} < {NAME_COVERAGE_MIN:.0%}）→ 没覆盖的票会被当成"
                "「非 ST」放进来，与「名单整个没取到」一样是降级，已中止本次回测。\n"
                "→ 下一步：(1) 稍后重试（名单是全市场表，冷启动要 ~50s）；"
                "(2) 在回测范围里**取消**『剔除 ST / *ST』再跑。"
            )
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

    # 新股 / 次新股：**交易所公布的真实上市日**距**区间起点**多少天。
    #
    # ⚠️ 旧口径用「库里最早日K」当上市日 —— 那只是本地下载起点（2021-08-02），
    #   实测 5601 只里 **4283 只**的「最早日K」= 那天（连 000001 / 600519 都算那天上市）
    #   → 判定基准日改成区间起点后「天龄 = 0 ≤ 90」→ `ipo` 一次剔掉 **5599 只**，
    #   实跑只剩 2 只；预览按「今天」算只剔 430 只 → 预览与实跑口径分叉。
    #   根因不是阈值，是**用错了量**：那不是上市日。
    #
    # 取不到上市日 → **中止**（与 ST 名单同一纪律）：「勾了剔除新股却一只没剔」
    # 会让报告的剔除明细看起来像「本来就没有新股」，口径指纹也算不出差别。
    if ex & {"ipo", "sub_new"}:
        listing = data.load_listing_dates(base)
        status = data.listing_source_status()
        if not status.get("ok"):
            raise ScopeDataUnavailable(
                "剔除新股/次新股需要真实上市日（交易所公布的上市公司列表），本次取不到"
                f"（{status.get('error') or '未知原因'}）。已中止本次回测 —— "
                "继续跑等于「勾了剔除却一只没剔」，报告里的剔除明细会看起来像"
                "「本来就没有新股」。\n"
                "→ 下一步：(1) 恢复网络后重试；(2) 在回测范围里**取消**"
                "『剔除新股 / 次新股』再跑（口径会变：报告会明确写出剔除明细里没有新股）。"
            )
        covered = sum(1 for c in base if listing.get(c))
        cov = (covered / len(base)) if base else 1.0
        if cov < LISTING_COVERAGE_MIN:
            raise ScopeDataUnavailable(
                f"上市日表只覆盖 {covered}/{len(base)} 只"
                f"（{cov:.1%} < {LISTING_COVERAGE_MIN:.0%}）→ 没覆盖的票无法判定新股/次新股，"
                "与「整个没取到」一样是降级，已中止本次回测。\n"
                "→ 下一步：(1) 稍后重试（上市日表要联网拉四个交易所列表）；"
                "(2) 在回测范围里**取消**『剔除新股 / 次新股』再跑。"
            )
        # 少数票不在交易所**当前**上市表（已退市 / 长期停牌）→ 用「库里最早日K」兜底。
        # 兜底方向是**保守**的：最早日K ≥ 真实上市日 → 天龄算小 → 更易被判新股
        # → 偏多剔，不会让新股期的行情漏进池子。兜底只数必须写进报告。
        missing = [c for c in base if not listing.get(c)]
        fallback = data.earliest_bar_dates() if missing else {}
        hit_ipo: list[str] = []
        hit_sub: list[str] = []
        fb_used = 0
        unknown = 0
        for code in base:
            list_d = _parse_date(listing.get(code))
            if list_d is None:
                list_d = _parse_date(fallback.get(code))
                if list_d is None:
                    unknown += 1
                    continue               # 上市日未知 → 不判定（按非新股），并计入声明
                fb_used += 1
            days = (as_of_d - list_d).days
            if "ipo" in ex and days <= ipo_days:
                hit_ipo.append(code)
            elif "sub_new" in ex and ipo_days < days <= sub_days:
                hit_sub.append(code)
        _hit("ipo", hit_ipo)
        _hit("sub_new", hit_sub)
        listing_meta = {
            "listing_date_source": "exchange_listing_date",
            "listing_date_coverage": round(cov, 4),
            "listing_date_covered": covered,
            "listing_date_fallback": fb_used,
            "listing_date_unknown": unknown,
        }

    # 微盘股：**区间起点**的总市值（point-in-time）。
    # 不用「今天的市值」：那是前视偏差（后来跌成微盘的票连同它过去那段大涨
    # 一起被整段剔除），而且市值天天变 → 同一区间两次跑结果不同。
    if "micro" in ex:
        caps, cap_meta = data.market_caps_at(base, as_of_d.isoformat())
        _hit("micro", [c for c in base
                       if caps.get(c) is not None and caps[c] <= micro_cap])

    extra: dict = {}
    if "micro" in ex:
        extra["market_cap_meta"] = cap_meta
    if st_cov is not None:
        # 名单覆盖率要落进报告：读者要知道「有几只票没有名称记录、
        # 因此没参与 ST 判定」（覆盖不足到会中止，但 95%~100% 之间的漏网要写明）。
        extra["st_name_coverage"] = round(st_cov, 4)
        extra["st_name_covered"] = st_covered
    if listing_meta:
        extra.update(listing_meta)
    return [c for c in base if c not in drop], dropped, extra


def resolve_window(start: str | None, end: str | None,
                   window: int | None = None) -> tuple[str | None, str | None]:
    """回测**实际取数区间** —— 唯一实现。

    `window=N`（只统计最近 N 个交易日）时，取数窗口要额外向前多取 `WARMUP_BARS` 根
    （策略指标如 MACD 需要历史预热，5 根本算不出信号）。

    这个改写必须**只发生在一处**：注入判定（本层）与取数（编排层）要用同一个区间 ——
    否则「区间内数据够不够」两边算出两个数。
    """
    if window and window > 0:
        n = int(window)
        days = data.recent_trading_days(n + WARMUP_BARS)
        if len(days) >= n + 2:
            return days[0], days[-1]
    return start, end


def resolve_lookback_window(start: str | None, end: str | None,
                            lookback: int = RECO_LOOKBACK) -> tuple[str | None, str | None]:
    """推荐页的**实际取数区间** —— 唯一实现（与 `resolve_window` 同类）。

    缺任一端 → 取最近 `lookback` 个交易日（MACD 这类长周期策略只看几天算不出信号）。

    为什么也要收进本层：**注入判定与取数要用同一个区间**。编排层按这个区间取数、
    也按同一个区间判「能不能注入」；两处各算一遍 → 必然算出两个区间 →
    「界面说 N 只、实跑 M 只」。
    """
    if not start or not end:
        days = data.recent_trading_days(int(lookback or RECO_LOOKBACK))
        if len(days) >= 2:
            return days[0], days[-1]
    return start, end


def filter_available(codes: list[str], start: str | None,
                     end: str | None) -> tuple[list[str], dict]:
    """区间内行情不足的票**不可注入** —— 注入判定的唯一入口。

    口径：区间内 K 线根数 < `MIN_BARS` 的票不参与回测（策略算不出信号、
    持仓期也填不满）。

    以前这道关在编排层（`backtest._prepare`），与「剔除特征」分属两个文件 →
    界面显示的只数与实跑能差几只，同一区间两次跑也可能不一样。
    现在统一在这里判：编排层只消费本层的结论。

    返回 `(可注入的代码, 明细)`；明细 = `{"insufficient": 被剔除只数,
    "window": [start, end]}`。**只数必须回传** —— 否则读者看到的样本
    与实跑参与的样本对不上号。
    """
    if not codes:
        return [], {"insufficient": 0, "window": [start, end]}
    if not start or not end:
        # 区间未定 → 无法判断数据够不够（口径不猜）：全部放行，由取数层兜底。
        return list(codes), {"insufficient": 0, "window": [start, end]}
    counts = data.bar_counts(start, end)
    keep = [c for c in codes if counts.get(c, 0) >= MIN_BARS]
    return keep, {"insufficient": len(codes) - len(keep), "window": [start, end]}


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

    ⚠️ 本函数只管**剔除特征**（ST / 板块 / 新股 / 微盘）。
    「区间内行情够不够 → 能不能注入」（`filter_available`）**不在这里**：
    它要按**实际取数区间**判，而那个区间在运行时才定（短窗口要向前预热、
    推荐页要回看）—— 由编排层在定下区间后调用 `filter_available`，
    预览走 `summary`（同一个函数、同一个区间口径）。
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
    st_status = data.names_source_status()
    detail = {
        "base_count": len(base),
        "filtered_count": len(codes),
        "excluded_count": len(base) - len(codes),
        "dropped": dropped,
        "exclude": ex,
        "as_of": as_of_d.isoformat(),
        # 只能用当前状态判定的特征 → 前视偏差，必须让人看见（不能只写「幸存者偏差」）
        "forward_looking": [f for f in _FORWARD_LOOKING if f in ex],
        # 上市日来源：**交易所公布的真实上市日**（旧口径是「库里最早日K」近似，
        # 而那是本地下载起点 → 一次能剔掉 5599 只，已废止）。
        # 只在真的用到「新股/次新股」判定时才有值；没勾该特征 → None
        # （本次没用到上市日，别让人误以为用了某个近似值）。
        "listing_date_source": (extra or {}).get("listing_date_source"),
        # ST 名单的可用性（取不到时本次若勾了 ST 剔除会**中止**，
        # 所以跑到这里说明名单可用；仍要落盘，让读者知道判定依据是哪份表）
        "st_names": {"ok": bool(st_status.get("ok")), "size": st_status.get("size"),
                     "source": st_status.get("source")},
    }
    detail.update(extra or {})
    # ---- 池子**结果**指纹：补 `spec_signature` 只压常量的缺口 -------------------
    # `spec_signature` 覆盖不到**运行时**差异（ST 名单这次取到没有、区间端点、
    # 基准日、实际剔了多少只）→ 曾出现两份报告指纹完全相同、实际池子差 126 只、
    # 收益差 5% 却「看起来同口径」的情况。这个指纹把这些都压进去：
    # 两份报告一比 `pool_signature`，不同就知道**池子真的不同**，别再靠猜。
    detail["pool_signature"] = hashlib.sha256("|".join([
        f"v={SPEC_VERSION}",
        f"start={spec.get('start') or ''}",
        f"end={spec.get('end') or ''}",
        f"as_of={as_of_d.isoformat()}",
        f"exclude={','.join(ex)}",
        f"base={len(base)}",
        f"kept={len(codes)}",
        "dropped=" + ",".join(f"{k}:{dropped[k]}" for k in sorted(dropped)),
        f"st_names_ok={1 if st_status.get('ok') else 0}",
        f"st_cov={extra.get('st_name_coverage', '')}",
        f"listing_src={extra.get('listing_date_source', '')}",
        f"listing_cov={extra.get('listing_date_coverage', '')}",
        f"listing_fb={extra.get('listing_date_fallback', '')}",
    ]).encode("utf-8")).hexdigest()[:8]
    _cache[key] = (codes, len(base), now, detail)
    if len(_cache) > _CACHE_MAX:
        _cache.pop(min(_cache, key=lambda k: _cache[k][2]), None)  # 丢掉最旧的
    return list(codes), detail


def scope_note(detail: dict) -> str:
    """范围口径说明（一段话）—— 预览与回测报告**共用同一句**，别各处手写。

    必须点明五件事：
      ① 每项剔除了多少只（否则读者不知道「勾了剔除」到底做了什么）；
      ② 判定基准日 = 区间起点（微盘按该日市值、新股/次新股按该日起算）；
      ③ 哪些判定用的是「当前状态」→ 含前视偏差（数据源不提供历史 ST 名单）；
      ④ 数据限制：股本只有当前值（无历史股本）；上市日已接**交易所真实数据**，
         但不在交易所当前上市表的少数票（已退市等）走「库里最早日K」保守兜底；
      ⑤ **最终可注入多少只**（区间内行情不足的又扣掉多少只）—— 读者看到的
         必须与实跑参与统计的只数一致。

    `detail` 里没有 `base_count`（自定义代码池：没做特征剔除）时，①②④ 整段略去，
    只留注入结论 —— 不能拿「基池 None 只」这种话糊弄读者。
    """
    detail = detail or {}
    parts: list[str] = []
    base = detail.get("base_count")
    if base is not None:
        dropped = detail.get("dropped") or {}
        bits = [f"{FEATURE_LABELS.get(k, k)} {v}" for k, v in dropped.items() if v]
        detail_txt = "、".join(bits) if bits else "无"
        as_of = detail.get("as_of") or "-"
        parts.append(
            f"范围口径：基池 {base} 只 → 剔除后 {detail.get('filtered_count')} 只"
            f"（剔除明细：{detail_txt}）；判定基准日 = 区间起点 {as_of}"
            f"（微盘按该日市值 ≤{int(MICRO_CAP / 1e8)} 亿、新股/次新股按该日起算上市天数）")
    avail = detail.get("available_count")
    if avail is not None:
        insufficient = detail.get("skipped_insufficient") or 0
        parts.append(f"再按区间内行情核减（<{MIN_BARS} 根不可注入 {insufficient} 只）"
                     f"→ **可注入 {avail} 只**")
    fwd = [FEATURE_LABELS.get(k, k) for k in (detail.get("forward_looking") or [])]
    if fwd:
        parts.append(f"⚠️ {'、'.join(fwd)} 按**当前**名单判定（数据源不提供历史名单，"
                     f"无法还原历史 → 含前视偏差）")
    ex = set(detail.get("exclude") or [])
    # 「没剔 ST」必须**显式写出来**：读者看到剔除明细里没有 ST，第一反应是
    # 「本来就没有 ST」—— 而真相可能是「名单没取到所以没剔」（旧口径的静默降级
    # 就是这么骗人的：两份报告指纹相同、池子却差 126 只）。现在勾了取不到会中止，
    # 但「用户主动取消了该剔除项」同样要写在报告里，读者才分得清这两种情况。
    if not (ex & {"st", "star_st"}):
        parts.append("ST/*ST **未剔除**（本次未勾选该特征；若勾选但名称名单取不到，"
                     "系统会**中止**回测并提示，绝不静默跳过）")
    cov = detail.get("st_name_coverage")
    if cov is not None and cov < 1.0:
        parts.append(f"⚠️ ST 名称名单只覆盖 {cov:.1%} → 未覆盖的票**未参与 ST 判定**"
                     f"（按非 ST 处理）")
    if ex & {"ipo", "sub_new"}:
        # 上市日来源必须说清楚：真实上市日 vs 兜底 vs 完全未知，三种情况读者要能分开看。
        src = detail.get("listing_date_source")
        lcov = detail.get("listing_date_coverage")
        fb = detail.get("listing_date_fallback") or 0
        unk = detail.get("listing_date_unknown") or 0
        if src == "exchange_listing_date":
            seg = [f"上市日取**交易所公布的真实上市日**（按基准日 {detail.get('as_of') or '-'} 起算天龄）"]
            if lcov is not None and lcov < 1.0:
                seg.append(f"⚠️ 上市日表只覆盖 {lcov:.1%}")
            if fb:
                seg.append(f"{fb} 只不在交易所当前上市表（已退市等）→ 上市日用「库里最早日K」"
                           "**保守兜底**（最早日K ≥ 真实上市日 → 天龄偏小 → 偏多剔）")
            if unk:
                seg.append(f"{unk} 只上市日完全取不到 → **未参与**新股/次新股判定（按非新股处理）")
            parts.append("；".join(seg))
        else:
            parts.append("⚠️ 上市日未取到（本次新股/次新股判定可能未按真实上市日执行）")
    cap_meta = detail.get("market_cap_meta") or {}
    if cap_meta.get("shares_source") == "latest":
        parts.append("⚠️ 微盘判定用的**股本是当前值**（数据源不提供历史股本，只有价格回到区间起点）")
    return "；".join(parts)

def summary(spec: dict, force: bool = False) -> dict:
    """回测范围预览：基池 / 剔除后 / **可注入** 数量 + 每项剔除只数 + 口径说明。

    「可注入」用的是**与实跑同一个区间**（`resolve_window`：短窗口会向前扩展
    预热根）—— 预览显示的只数必须等于实跑参与统计的只数，否则界面在骗人。
    """
    codes, detail = build_detail(spec, force=force)
    detail = dict(detail)
    start, end = resolve_window(spec.get("start"), spec.get("end"),
                                spec.get("window"))
    keep, avail = filter_available(codes, start, end)
    detail["available_count"] = len(keep) if (start and end) else None
    detail["skipped_insufficient"] = avail["insufficient"]
    detail["window"] = avail["window"]
    out = {
        "base_count": detail["base_count"],
        "filtered_count": detail["filtered_count"],
        "available_count": detail["available_count"],
        "skipped_insufficient": detail["skipped_insufficient"],
        "window": detail["window"],
        "excluded_count": detail["excluded_count"],
        "dropped": detail["dropped"],
        "as_of": detail["as_of"],
        "forward_looking": detail["forward_looking"],
        "listing_date_source": detail.get("listing_date_source"),
        "listing_date_coverage": detail.get("listing_date_coverage"),
        "listing_date_covered": detail.get("listing_date_covered"),
        "listing_date_fallback": detail.get("listing_date_fallback"),
        "listing_date_unknown": detail.get("listing_date_unknown"),
        "market_cap_meta": detail.get("market_cap_meta") or {},
        # 口径版本与指纹：预览与回测报告**共用同一句**（同一份实现，不各写一套）
        "spec_version": SPEC_VERSION,
        "spec_signature": spec_signature(),
        # 池子**结果**指纹 + ST 名单可用性：预览与报告都要能看见，
        # 否则「同指纹不同池」又要靠人肉比（实测踩过两次）。
        "pool_signature": detail.get("pool_signature"),
        "st_names": detail.get("st_names"),
        "st_name_coverage": detail.get("st_name_coverage"),
        "note": scope_note(detail),
    }
    return out


def invalidate() -> None:
    """清空缓存（例如下载新数据后希望下次重建）。"""
    _cache.clear()
