# -*- coding: utf-8 -*-
"""筹码集中度 90（SCR90）**周级三档分析**：本地算筹码 → 按周取最集中的前 N → 分档。

与 `chip_service.py` 是**同一套结论形态、换了数据源**：
    · chip_service：导入行情软件导出的 xls（一批已被 SCR 条件选出的票）→ 多期合并 → 三档；
    · 本模块：**本地日线**自己算筹码分布 → 按周把 SCR90 升序排名取前 N → 三档。
即不再依赖外部导出文件，“谁在榜、谁离榜、是涨着走还是跌着走”全部由本地数据算出。

三档（沿用 chip_service 的命名与阈值语义）：
    第一档 磨主峰     最新一期在榜 ∩ 连续 WEEKS 期全勤
    第三档 启动型离榜 **上一期在榜、本期才离榜**，且区间涨幅 ≥ LAUNCH_THRESHOLD（**上涨离开**）
    第二档 向下破位   **上一期在榜、本期才离榜**，涨幅未达阈值（**下跌离开**）

离榜用**严格口径**（上一期在榜、本期不在）而不是「曾入选但最新不在」：
后者会把两三周前就走了的票一直留在榜里，越攒越多、淹没了真正「本周离开」的那几只。

为什么能算历史各期（关键）：
    筹码矩阵是**因果**的 —— 第 i 帧只由第 0..i 天的换手与价格推出，
    一次算完某只票就得到**整段** SCR90 序列，在每个周节点上取一次截面即可，
    5 期只需**一遍**全量计算（不是 5 遍）。取数区间在最早一期之前再往前多取
    约 1.5×window 个自然日养状态，这些帧只养状态、不参与排名。

为什么必须 offline（不联网）：
    筹码取数链路里藏着两个联网兜底 —— 锁仓比例走 akshare、缺流通股本走腾讯行情现采。
    单只票无感，全市场几千只会拖垮并触发限流。故批量一律 `offline=True`：
    锁仓系数取 1.0、缺股本即抛错计入「数据不足」。
    **代价**：这里的 SCR90 未做锁仓修正（结果 `lockup=false` 如实标注）。

与 chip_service 的差异（如实记录）：
    原系统还按「流通市值区间 + PE > 0」过滤，本地没有基本面数据，故**不做**这两项过滤。

性能：全市场 5585 只一遍约 13~25 分钟（视窗口长度），做成后台线程 + 进度 + 按周缓存。
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import threading
import time
import traceback
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import numpy as np
import pandas as pd

import chip_formulas
import chip_service
from strategies.core.data import list_universe_codes

PROCESSED_DIR = chip_service.PROCESSED_DIR
IO_WORKERS = 6                 # 与 strategies/core/data.py 同量级：再多线程只是在抢 SQLite 连接
DEFAULT_TOP = 100              # 每期取 SCR90 最小的 N 只（= 最集中）
DEFAULT_WEEKS = 5              # 连续几期在榜算「全勤」（对齐 chip_service.FULL_WEEKS）
DEFAULT_DAYS = 250             # 筹码窗口（交易日），同时决定预热长度
DEFAULT_BINS = 80
DEFAULT_CHG_DAYS = 30          # 离榜时看多少交易日的涨幅（对齐 chip_service 的「30日涨幅%」）
DEFAULT_LAUNCH = 10.0          # 涨幅 ≥ 该阈值视为「上涨离开/启动型」（对齐 LAUNCH_THRESHOLD）

_LOCK = threading.Lock()
_STATE: dict[str, Any] = {
    "state": "idle",           # idle / running / ready / error
    "week": None,
    "total": 0, "done": 0,
    "computed": 0, "skipped": 0,
    "skipped_reasons": {},
    "started_at": None, "finished_at": None, "elapsed": 0.0,
    "error": None,
}


# --------------------------------------------------------------------------- #
# 周节点与落盘路径
# --------------------------------------------------------------------------- #
def _as_date(value) -> dt.date:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value)[:10])


def _week_nodes(count: int = DEFAULT_WEEKS, now=None) -> list[dt.date]:
    """最近 count 期的数据节点（周一，由新到旧），沿用 chip_service 的周口径
    （周一~周四、周五收盘前 → 上一周；周五收盘后 / 周末 → 本周）。"""
    return [_as_date(w) for w in chip_service.expected_weeks(count, now=now)]


def _result_path(week: dt.date) -> str:
    return os.path.join(PROCESSED_DIR, f"scr90_rank_{week.strftime('%Y%m%d')}.json")


# --------------------------------------------------------------------------- #
# 单只票：矩阵 → 逐日 SCR90 序列 + 收盘价序列
# --------------------------------------------------------------------------- #
def _scr90_series(res) -> pd.Series:
    """筹码矩阵逐帧换算成 SCR90 序列（索引 = 交易日）。

    逐帧按累计筹码线性插值取 P5 / P95；某帧筹码全空时留 NaN（该日不参与排名）。
    """
    pct = res.pct
    centers = res.centers
    out = np.full(pct.shape[0], np.nan)
    for i in range(pct.shape[0]):
        row = pct[i]
        total = float(row.sum())
        if total <= 0:
            continue
        cum = np.cumsum(row) / total
        p5 = float(np.interp(0.05, cum, centers))
        p95 = float(np.interp(0.95, cum, centers))
        den = p95 + p5
        if den == 0:
            continue
        out[i] = (p95 - p5) / den
    return pd.Series(out, index=list(res.dates))


def _compute_one(code: str, start: str | None, end: str | None,
                 adjust: str, bins: int) -> dict:
    """单只票：算整段 SCR90 与收盘价序列。异常向上抛，由调用方计入「数据不足」。"""
    res = chip_formulas.compute_matrix(code, formula_id="tri_decay", start=start,
                                       end=end, adjust=adjust, bins=bins,
                                       offline=True)
    return {"code": code,
            "scr": _scr90_series(res),
            "close": pd.Series(np.asarray(res.close, dtype=float),
                               index=list(res.dates))}


def _load_names(codes: list[str]) -> dict[str, str]:
    """批量取名称（复用 strategies 里「一次拉全市场」的 24h 缓存）。

    名称是锦上添花：拿不到就让前端显示「—」，绝不影响分析本身。
    """
    try:
        from strategies.core.backtest import _load_names as remote
        return remote(codes) or {}
    except Exception:                                    # noqa: BLE001 - 名称缺失可降级
        return {}


# --------------------------------------------------------------------------- #
# 批量任务（后台线程）
# --------------------------------------------------------------------------- #
def _panel(series: dict[str, pd.Series]) -> pd.DataFrame:
    """{code: 序列} → 面板（行 = 交易日，列 = 代码）。"""
    return pd.DataFrame(series).sort_index()


def _week_top(panel: pd.DataFrame, monday: dt.date, top_n: int) -> list[tuple]:
    """某周节点上的截面：取该周内**每只票最后的有效值**，按 SCR90 升序取前 N。

    用 ffill 只在窗口内向前补：某票周一有值、周五停牌，也能取到周一的值；
    整周都没值（停牌 / 已退市）的票自然被 dropna 掉，不参与该期排名。
    """
    cutoff = monday + dt.timedelta(days=6)
    sub = panel.loc[monday.isoformat():cutoff.isoformat()]
    if sub.empty:
        return []
    last = sub.ffill().iloc[-1].dropna()
    if last.empty:
        return []
    return list(last.sort_values().items())[:top_n]


def _ret_at(close: pd.Series, monday: dt.date, days: int) -> tuple:
    """某票在该周节点的收盘价，以及往前 days 个交易日的涨幅（%）。"""
    cutoff = monday + dt.timedelta(days=6)
    s = close[close.index <= cutoff.isoformat()].dropna()
    if s.empty:
        return None, None
    last_v = float(s.iloc[-1])
    if len(s) > days and float(s.iloc[-1 - days]) > 0:
        return last_v, (last_v / float(s.iloc[-1 - days]) - 1.0) * 100.0
    return last_v, None


def _worker(week: dt.date, weeks: list[dt.date], codes: list[str], top_n: int,
            window: int, bins: int, chg_days: int, launch: float,
            fetch_start: str) -> None:
    started = time.time()
    skipped: dict[str, str] = {}
    scr: dict[str, pd.Series] = {}
    close: dict[str, pd.Series] = {}
    try:
        # 名称**先取**：只有一次网络请求（约 11~25s），放在重型计算之前 ——
        # 之后 6 线程跑满 CPU 时再取，akshare 很容易超时，而 _load_names 是
        # 「拿不到就算了」的静默降级，结果就是一整列名称全空、还查不出原因。
        # 失败也不影响榜单：随后可经 POST /api/chip/rank/names 单独补齐（不必重算）。
        names = _load_names(codes)
        with ThreadPoolExecutor(max_workers=IO_WORKERS) as pool:
            futures = {pool.submit(_compute_one, c, fetch_start, None,
                                   "qfq", bins): c for c in codes}
            for fu in as_completed(futures):
                code = futures[fu]
                try:
                    got = fu.result()
                except Exception as exc:                 # noqa: BLE001 - 单票失败不影响整体
                    skipped[code] = f"{type(exc).__name__}: {exc}"
                    with _LOCK:
                        _STATE["done"] += 1
                    continue
                if got["scr"].dropna().empty:
                    skipped[code] = "筹码分布为空"
                    with _LOCK:
                        _STATE["done"] += 1
                    continue
                scr[code] = got["scr"]
                close[code] = got["close"]
                with _LOCK:
                    _STATE["done"] += 1

        scr_panel = _panel(scr)

        # 各期榜单（由新到旧），并记录每只票「在榜了哪几期」
        snapshots: list[list[tuple]] = []                # 每期 [(code, scr90)]
        for wk in weeks:
            snapshots.append(_week_top(scr_panel, wk, top_n))
        # 由旧到新累积：这样 weeks_on[-1] 就是「最近一次在榜」的期
        on_weeks: dict[str, list[str]] = {}              # code -> 在榜的期（日期串，旧→新）
        for wk, snap in zip(reversed(weeks), reversed(snapshots)):
            for code, _v in snap:
                on_weeks.setdefault(code, []).append(wk.isoformat())

        latest = weeks[0].isoformat()
        latest_set = {c for c, _v in snapshots[0]}

        def item(code: str, scr90: float) -> dict:
            c, _r = _ret_at(close[code], weeks[0], chg_days)
            weeks_on = on_weeks.get(code, [])
            return {
                "code": code,
                "name": names.get(code) or "",
                "scr90": round(float(scr90), 6),
                "close": None if c is None else round(c, 4),
                "chg": None if _r is None else round(_r, 4),   # 区间涨幅(%)，chg_days 个交易日
                "weeks_on": len(weeks_on),
                "last_week": weeks_on[-1] if weeks_on else None,
                "as_of": latest,
            }

        # 第一档：在最新一期，且连续 WEEKS 期全勤
        stay = [item(c, v) for c, v in snapshots[0]
                if len(on_weeks.get(c, [])) == len(weeks)]
        # 离榜（严格口径）：**上一期在榜、本期才走** → 按涨幅分「上涨离开 / 下跌离开」
        left_up: list[dict] = []
        left_down: list[dict] = []
        prev_codes = [c for c, _v in snapshots[1]] if len(snapshots) > 1 else []
        for code in prev_codes:
            if code in latest_set:
                continue
            v = scr_panel.loc[:, code].dropna()
            if v.empty:
                continue
            row = item(code, float(v.iloc[-1]))
            (left_up if (row["chg"] is not None and row["chg"] >= launch)
             else left_down).append(row)
        left_up.sort(key=lambda r: -(r["chg"] or -1e9))
        left_down.sort(key=lambda r: r["chg"] if r["chg"] is not None else 1e9)

        current = [item(c, v) for c, v in snapshots[0]]
        reasons = dict(Counter(v.split(":")[0] for v in skipped.values()).most_common(5))
        payload = {
            "ok": True,
            "week": latest,
            "weeks": [w.isoformat() for w in weeks],
            "params": {"top_n": top_n, "weeks": len(weeks), "window": window,
                       "bins": bins, "chg_days": chg_days, "launch_threshold": launch},
            "total": len(codes),
            "computed": len(scr),
            "skipped": len(skipped),
            "skipped_reasons": reasons,
            "skipped_sample": dict(list(skipped.items())[:20]),
            # 名称取到几条（0 = 没取到，可点「重取名称」单独补齐，不必重算）
            "names_resolved": sum(1 for r in rows if r["name"]),
            "lockup": False,            # 批量不联网 → 未做锁仓修正，口径透明
            "formula": chip_formulas.base.DEFAULT_FORMULA_ID,
            "elapsed": round(time.time() - started, 1),
            "computed_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "current": current,                       # 最新一期前 N（升序）
            "tiers": {
                "stay": {"key": "tier1", "name": f"连续{len(weeks)}周在榜 · 磨主峰",
                         "desc": "最新一期在榜且各期全勤", "count": len(stay), "items": stay},
                "up": {"key": "tier3", "name": "上涨离榜 · 启动型",
                       "desc": f"上一期在榜、本期离榜，且近 {chg_days} 个交易日涨幅 ≥ {launch}%",
                       "count": len(left_up), "items": left_up},
                "down": {"key": "tier2", "name": "下跌离榜 · 破位",
                         "desc": f"上一期在榜、本期离榜，涨幅未达 {launch}%",
                         "count": len(left_down), "items": left_down},
            },
        }
        os.makedirs(PROCESSED_DIR, exist_ok=True)
        with open(_result_path(week), "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

        with _LOCK:
            _STATE.update(state="ready", computed=len(scr), skipped=len(skipped),
                          skipped_reasons=reasons,
                          finished_at=payload["computed_at"],
                          elapsed=payload["elapsed"], error=None)
    except Exception as exc:                             # noqa: BLE001 - 任务失败要能看出来
        with _LOCK:
            _STATE.update(state="error",
                          error=f"{type(exc).__name__}: {exc}",
                          detail=traceback.format_exc()[-1200:],
                          finished_at=dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                          elapsed=round(time.time() - started, 1))


def status() -> dict:
    """当前周的计算状态（前端轮询它显示进度）。"""
    week = _week_nodes(1)[0]
    week_text = week.strftime("%Y-%m-%d")
    with _LOCK:
        snap = json.loads(json.dumps(_STATE))           # 深拷贝，避免暴露内部可变对象
    snap["week"] = week_text
    if snap["state"] == "running":
        snap["percent"] = round(snap["done"] * 100.0 / max(1, snap["total"]), 1)
        return snap
    if os.path.exists(_result_path(week)):
        snap["state"] = "ready"
        return snap
    snap["state"] = "idle"
    return snap


def ensure(force: bool = False, top_n: int | None = None, weeks: int | None = None,
           window: int | None = None, bins: int | None = None,
           chg_days: int | None = None, launch: float | None = None) -> dict:
    """确保本周结果存在：已有则复用，没有就在后台线程开跑。

    force=True 强制重算（跨周自动重算，无需 force）。
    """
    top_n = int(top_n) if top_n else DEFAULT_TOP
    weeks = int(weeks) if weeks else DEFAULT_WEEKS
    window = int(window) if window else DEFAULT_DAYS
    bins = int(bins) if bins else DEFAULT_BINS
    chg_days = int(chg_days) if chg_days else DEFAULT_CHG_DAYS
    launch = float(launch) if launch is not None else DEFAULT_LAUNCH
    nodes = _week_nodes(weeks)
    week = nodes[0]

    with _LOCK:
        if _STATE["state"] == "running":
            return status()
        if not force and os.path.exists(_result_path(week)):
            _STATE.update(state="ready", week=week.strftime("%Y-%m-%d"))
            return status()
        _STATE.update(state="running", week=week.strftime("%Y-%m-%d"),
                      total=0, done=0, computed=0, skipped=0,
                      skipped_reasons={}, started_at=dt.datetime.now()
                      .strftime("%Y-%m-%d %H:%M:%S"),
                      finished_at=None, elapsed=0.0, error=None, detail=None)

    # 放锁外：要扫完所有年度分片才知道总只数，别让状态轮询被它堵住
    codes = list_universe_codes()
    with _LOCK:
        if _STATE["state"] == "running" and _STATE["total"] == 0:
            _STATE["total"] = len(codes)

    # 取数区间：最早一期之前再往前多取约 1.5×window 个自然日养状态
    fetch_start = (nodes[-1] - dt.timedelta(days=int(window * 1.5))).isoformat()

    threading.Thread(target=_worker,
                     args=(week, nodes, codes, top_n, window, bins,
                           chg_days, launch, fetch_start),
                     name="chip-scr90-rank", daemon=True).start()
    return status()


def result(limit: int | None = None) -> dict:
    """读本周分析结果：未算过返回 ok=false（前端据此提示先刷新）。"""
    week = _week_nodes(1)[0]
    path = _result_path(week)
    limit = int(limit) if limit else DEFAULT_TOP
    if not os.path.exists(path):
        return {"ok": False, "week": week.strftime("%Y-%m-%d"),
                "error": "本周尚未计算，请先点「刷新本周」"}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception as exc:                             # noqa: BLE001 - 坏文件按未算过处理
        return {"ok": False, "week": week.strftime("%Y-%m-%d"),
                "error": f"结果缓存损坏：{type(exc).__name__}: {exc}"}

    for i, it in enumerate(payload.get("current") or []):
        it["rank"] = i + 1
    payload["limit"] = limit
    payload["shown"] = min(limit, len(payload.get("current") or []))
    payload["current"] = (payload.get("current") or [])[:limit]
    payload["state"] = "ready"
    return payload


def fill_names() -> dict:
    """给**已落盘**的本周结果补齐名称，不必重跑十几分钟的重型计算。

    名称只是一次外部查询（akshare 全市场表，进程内缓存 24h）。把它与计算解耦后：
    榜单先出来，名称取不到也只影响一列显示，随时补一次即可。
    """
    week = _week_nodes(1)[0]
    path = _result_path(week)
    if not os.path.exists(path):
        return {"ok": False, "week": week.strftime("%Y-%m-%d"),
                "error": "本周尚未计算，请先点「刷新本周」"}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception as exc:                             # noqa: BLE001
        return {"ok": False, "week": week.strftime("%Y-%m-%d"),
                "error": f"结果缓存损坏：{type(exc).__name__}: {exc}"}

    groups = [payload.get("current") or []]
    for tier in (payload.get("tiers") or {}).values():
        groups.append(tier.get("items") or [])
    codes = sorted({it.get("code") for g in groups for it in g if it.get("code")})
    names = _load_names(codes)

    resolved = 0
    for g in groups:
        for it in g:
            nm = names.get(it.get("code")) or ""
            if nm:
                resolved += 1
            it["name"] = nm
    payload["names_resolved"] = resolved

    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
    except Exception as exc:                             # noqa: BLE001
        return {"ok": False, "week": week.strftime("%Y-%m-%d"),
                "error": f"写回失败：{type(exc).__name__}: {exc}"}
    return {"ok": True, "week": week.strftime("%Y-%m-%d"),
            "total": len(codes), "names_resolved": resolved}
