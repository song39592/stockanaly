# -*- coding: utf-8 -*-
"""策略回测报告：回测跑完自动落盘，供后续分析。

原先回测结果只活在一次 HTTP 响应里，关掉页面就没了；想比较「上周的 scr90 和
这周的 macd 谁强」只能重跑一遍再拿眼睛比。本模块把每次回测写成一份 JSON 落在
数据盘，并在 index.json 里维护索引，于是后续分析有三种用法：

    1) 横向对比：所有报告的摘要都在 index.json 里，一眼筛出关心的
    2) 单次深挖：某份报告的 metrics / per_stock / 净值序列都在
    3) 批量统计：目录下所有 json 都能直接喂给 pandas / Excel

## 落盘位置与保留策略

    <data>/backtests/backtest-<策略>-<YYYYmmdd-HHMMSS>.json   完整报告
    <data>/backtests/index.json                              索引（每份一行摘要）

刻意**不放 logs/ 也不放 cache/**：
  - logs/   语义是日志，定时任务会清 *.log.* 与 dump-*.zip（cleanup._clean_dumps）；
  - cache/  清理策略是「全部可清」（定义上可重建）—— 报告丢了就没了。
所以单开 backtests/，**不参与任何自动清理**（报告是要长期留存的资产）。

## 报告里有什么（策略 + 覆盖范围这两块是必须的）

- strategy：策略 id / 名称 / **完整参数**（参数变了就不是同一次实验，事后无法
  从指标反推，所以必须原样记下）
- scope：**覆盖范围** —— 请求标的数 / 实际参与标的数 / 区间 / 交易日数 /
  初始资金 / 佣金 / 基准，外加「谁被跳过、为什么」
- metrics：收益、夏普、最大回撤等（stats.compute 的原始输出）
- per_stock：逐票汇总（全市场 5000+ 只会让文件大到不可读，故**只留前 500**，
  并在 per_stock_truncated 里如实标注截掉多少）
- data_errors：取数 / 基准异常
- env：Python 版本 + 数据目录，便于日后复现

**逐笔交易明细**（trades）：每笔含 code / name / 买卖日期 / 买卖价 / 收益率 /
股数 / 盈亏（字段口径逐条写在报告里，见 stats.trade_detail）。超过 MAX_TRADES
（2 万笔）会截断并用 `trades_truncated` 如实标注 —— 全市场高频策略能到十几万笔，
全量落盘会让报告膨胀到不可读。

## 写盘失败绝不影响回测结果

报告是「附加产物」，写盘失败（磁盘满、权限、无空间）**只记日志**，绝不能把一次
跑成功的回测变成 500 —— 那是净损失。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from typing import Any

import config
import logutil

logger = logutil.logger

SCHEMA = "backtest-report/1"
INDEX_NAME = "index.json"
MAX_PER_STOCK = 500          # 逐票汇总最多记多少行（超出只留统计口径）
MAX_TRADES = 20000           # 逐笔明细最多记多少笔（超出截断并标注 trades_truncated）
INDEX_MAX_ENTRIES = 500      # 索引最多留多少条（超出丢弃最旧的摘要行）


def _unique_path(directory, stem: str, suffix: str = ".json"):
    """在 `directory` 下找一个**还没被占用**的 `<stem><suffix>`。

    ⚠️ 为什么要这一步：文件名原本只带**秒级**时间戳，而同一策略连跑两次回测
    完全可能落在同一秒里（短区间回测只要几秒）—— 实测就是这么丢了
    一份报告：index.json 里有 3 条记录，目录里只剩 2 个文件，先写的那份
    被后写的**静默覆盖**了。所以这里显式去重，宁可多一个后缀也不丢数据。
    """
    candidate = directory / (stem + suffix)
    if not candidate.exists():
        return candidate
    for n in range(2, 1000):
        candidate = directory / ("%s-%d%s" % (stem, n, suffix))
        if not candidate.exists():
            return candidate
    # 理论上到不了这里（999 个同名文件）；仍兜底，避免 raise 把回测带崩
    return directory / ("%s-%d%s" % (stem, dt.datetime.now().strftime("%H%M%S%f"), suffix))


def _safe_name(text: Any) -> str:
    """把策略 id 里的路径分隔符换掉，别让文件名被带歪。"""
    out = "".join(c if (c.isalnum() or c in "-_") else "_" for c in str(text))
    return out[:40] or "na"


def _num(value: Any):
    """指标可能是 None / 非数，统一成可 JSON 化的数字或 None。"""
    return round(float(value), 6) if isinstance(value, (int, float)) else None


def _summarize(report: dict) -> dict:
    """从完整报告挑出**索引行**：只留横向对比时用得上的字段。"""
    strat = report.get("strategy") or {}
    scope = report.get("scope") or {}
    metrics = report.get("metrics") or {}
    return {
        "file": report.get("file"),
        "generated_at": report.get("generated_at"),
        "strategy_id": strat.get("id"),
        "strategy_name": strat.get("name"),
        "params": strat.get("params") or {},
        "start": scope.get("start"),
        "end": scope.get("end"),
        "trading_days": scope.get("trading_days"),
        "codes_count": scope.get("codes_count"),
        "requested": scope.get("requested"),
        "initial_capital": scope.get("initial_capital"),
        "commission": scope.get("commission"),
        "benchmark": scope.get("benchmark"),
        "total_return": _num(metrics.get("total_return")),
        "annual_return": _num(metrics.get("annual_return")),
        "max_drawdown": _num(metrics.get("max_drawdown")),
        "sharpe": _num(metrics.get("sharpe")),
        "win_rate": _num(metrics.get("win_rate")),
        "num_trades": _num(report.get("trades_total")),
    }


def _write_index(entry: dict) -> None:
    """把一行摘要并进 index.json（不存在则新建）。读-改-写，故整体 try。"""
    path = config.REPORTS_DIR / INDEX_NAME
    data = {"schema": "backtest-index/1", "reports": []}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and isinstance(loaded.get("reports"), list):
                data = loaded
        except Exception:                # 索引坏了就重建，不让它挡住写报告
            logger.warning("backtest index 读取失败，将重建：%s", path)
    data["reports"].append(entry)
    data["updated_at"] = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if len(data["reports"]) > INDEX_MAX_ENTRIES:
        data["reports"] = data["reports"][-INDEX_MAX_ENTRIES:]   # 0 即最旧
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(path))      # 原子替换：中途被杀不会留下半个索引


def build_report(payload: dict) -> dict:
    """从 `run_backtest` 的返回组装报告内容（纯函数，不碰磁盘，便于测试）。"""
    strat = payload.get("strategy") or {}
    scope = dict(payload.get("scope") or {})
    dates = payload.get("dates") or []
    metrics = payload.get("metrics") or {}
    errors = payload.get("data_errors") or []

    # 覆盖范围补三样原文没有的：实际交易日数、真正起止日、跳过的标的
    scope["trading_days"] = len(dates)
    scope["date_from"] = dates[0] if dates else None
    scope["date_to"] = dates[-1] if dates else None
    # ⚠️ `run_backtest` 的 scope.start/end 是**请求值**：不指定区间时它们是空，
    # 而实际跑的是「全部可用数据」。若照抄进报告，分析者会看到「区间为空」而
    # 不知道实际跑了 5 年。故保留请求值、另给实际值，并把 start/end 对齐到实际
    # 区间（否则报告里两个字段自相矛盾）。
    scope["requested_start"] = scope.get("start")
    scope["requested_end"] = scope.get("end")
    if not scope.get("start"):
        scope["start"] = scope["date_from"]
    if not scope.get("end"):
        scope["end"] = scope["date_to"]
    scope["range_is_effective"] = bool(scope.get("requested_start")) is False
    scope["skipped_count"] = len(errors)
    scope["skipped"] = list(errors[:50])
    scope["coverage_ratio"] = (
        round(scope.get("codes_count", 0) / scope["requested"], 4)
        if scope.get("requested") else None
    )

    per_stock = list(payload.get("per_stock") or [])
    truncated = max(0, len(per_stock) - MAX_PER_STOCK)

    # 逐笔交易明细：全市场一次回测动辄数千~上万笔，全量写进去报告可达数十 MB。
    # 与 per_stock 同策略：**超量就截断并如实标注**，让分析者知道「还有 N 笔没落盘」
    # （而不是误以为这就是全部）。要看全量得重跑并提高上限。
    all_trades = list(payload.get("trades") or [])
    if not all_trades:
        # trades 刻意不进 HTTP 响应（15 万笔 = 24 MB），只在这里从 backtest 取。
        try:
            from strategies.core import backtest as _bt
            all_trades = list(_bt.last_trades() or [])
        except Exception:                # noqa: BLE001 - 取不到就当没有明细
            all_trades = []
    trades_kept = all_trades[:MAX_TRADES]
    trades_dropped = max(0, len(all_trades) - MAX_TRADES)

    return {
        "schema": SCHEMA,
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "result_id": payload.get("result_id"),
        "strategy": {
            "id": strat.get("id"),
            "name": strat.get("name"),
            # 参数必须原样全记：参数变了就不是同一次实验，事后无法从指标反推
            "params": strat.get("params") or {},
        },
        "scope": scope,
        "metrics": metrics,
        # 净值序列：留着才能画图 / 算自定义指标，5000 只票的 equity 也就几万个点
        "dates": dates,
        "equity": payload.get("equity"),
        "equity_benchmark": payload.get("equity_benchmark"),
        "per_stock": per_stock[:MAX_PER_STOCK],
        "per_stock_total": len(per_stock),
        "per_stock_truncated": truncated,
        # 逐笔「一买一卖」明细。字段口径见 stats.trade_detail 的 docstring：
        #   buy_price/sell_price 原始收盘（未复权，便于核对）
        #   ret                   后复权收益率（消除除权跳空的假盈亏）
        #   shares                按等权资金带折算的股数（**非模拟成交**）
        #   pnl                   shares × 原始买价 × 后复权收益率（见 stats）
        "trades": trades_kept,
        "trades_total": len(all_trades),
        "trades_truncated": trades_dropped,
        "data_errors": list(errors[:50]),
        "env": {"python": "%d.%d.%d" % sys.version_info[:3],
                "data_dir": str(config.DATA_DIR)},
    }


def save_report(payload: dict) -> dict:
    """把 `run_backtest` 的返回写成一份报告。**任何失败都不抛**。

    返回 `{"ok": bool, "path": str|None, "entry": {...}}`：成功时 path 是报告
    绝对路径（前端可展示，方便用户直接去打开），失败时 ok=False / path=None，
    **回测结果本身照常返回** —— 报告是附加产物，写不进去不该让整次回测变成 500。
    """
    try:
        report = build_report(payload)
        config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        stem = "backtest-%s-%s" % (
            _safe_name(report["strategy"]["id"]),
            dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
        path = _unique_path(config.REPORTS_DIR, stem)
        report["file"] = path.name
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        entry = _summarize(report)
        try:
            _write_index(entry)
        except Exception as exc:           # 索引失败不影响报告本身已落盘
            logger.warning("回测报告索引写入失败（报告已保存）：%s", exc)
        logger.info("回测报告已保存：%s", path)
        return {"ok": True, "path": str(path), "entry": entry}
    except Exception as exc:               # noqa: BLE001 - 附加产物，失败只记日志
        logger.warning("回测报告保存失败（不影响回测结果）：%s", exc)
        return {"ok": False, "path": None, "entry": None, "error": str(exc)}


def list_reports(limit: int = 50) -> dict:
    """列出已保存的报告（读 index.json）；供前端「历史回测」用。"""
    try:
        path = config.REPORTS_DIR / INDEX_NAME
        if not path.exists():
            return {"ok": True, "reports": [], "dir": str(config.REPORTS_DIR)}
        data = json.loads(path.read_text(encoding="utf-8"))
        reports = data.get("reports") or []
        return {"ok": True, "reports": reports[-int(limit or 50):][::-1],
                "dir": str(config.REPORTS_DIR), "total": len(reports)}
    except Exception as exc:               # noqa: BLE001
        return {"ok": False, "reports": [], "error": str(exc)}