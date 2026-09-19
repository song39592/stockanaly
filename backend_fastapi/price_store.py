# -*- coding: utf-8 -*-
"""股价数据的存储与复权计算。

职责边界：
  - 只做「数据 ↔ 数据」的存取与换算，**不发起任何网络请求**（采集见 `price_service`）；
  - 因此页面、估值、回测等消费方都可以直接依赖本模块，不必关心数据源与降级链。

复权口径（核心设计）：
  库里只存 **不复权原始价** 与 **后复权因子 hfq_factor**；复权价一律现算：

      hfq(t)        = raw(t) × hfq_factor(t)
      qfq(t, as_of) = raw(t) × hfq_factor(t) ÷ hfq_factor(as_of)

  `as_of` 是**复权基准日**，缺省取最新交易日（等价于数据源的常规前复权）。
  回测时应传入**回测起点**作为基准，这样历史价格里不含未来信息，且同一段历史每次回测结果一致
  （这正是"只存原始价 + 因子、不存前复权价"的原因）。
"""
from __future__ import annotations

from typing import Any

import db

# 可用的复权口径：raw 不复权 / hfq 后复权 / qfq 前复权
BAR_ADJUSTS = ("qfq", "hfq", "raw")


def _store_adjust(adjust: str) -> str:
    """外部传入的口径 → 存储名（兼容空串 / None / none 表示不复权）。"""
    key = str(adjust or "").strip().lower()
    return "raw" if key in ("", "raw", "none") else key


# --------------------------------------------------------------------------- #
# 建表
# --------------------------------------------------------------------------- #
def init_db() -> None:
    with db.connect() as conn:
        conn.executescript("""
        -- 行情：只存不复权原始价（唯一事实来源），复权价由 adjust_factors 现算
        CREATE TABLE IF NOT EXISTS daily_bars (
          code TEXT NOT NULL,
          trade_date TEXT NOT NULL,
          adjust TEXT NOT NULL DEFAULT 'raw',
          open REAL, high REAL, low REAL, close REAL,
          volume REAL, amount REAL, amplitude REAL,
          change_pct REAL, change_amount REAL, turnover REAL,
          fetched_at TEXT NOT NULL,
          source TEXT NOT NULL DEFAULT '',
          PRIMARY KEY(code, trade_date, adjust)
        );
        CREATE INDEX IF NOT EXISTS idx_daily_bars_code_date ON daily_bars(code, trade_date);
        -- 复权因子：只存 hfq_factor（基准为「上市首日」，历史值不随新的除权变化）。
        -- qfq_factor 以「最新日」为基准、每天都在变，存下来等于又把会变的数据请回来，故不存。
        CREATE TABLE IF NOT EXISTS adjust_factors (
          code TEXT NOT NULL,
          ex_date TEXT NOT NULL,
          hfq_factor REAL NOT NULL,
          source TEXT NOT NULL DEFAULT '',
          fetched_at TEXT NOT NULL,
          PRIMARY KEY(code, ex_date)
        );
        CREATE INDEX IF NOT EXISTS idx_adjust_factors_code ON adjust_factors(code, ex_date);
        -- 除权除息明细：审计因子、对拍口径、计算税后真实持仓成本。
        -- 送股 / 转增 / 派息均为「每 10 股」口径（与数据源一致）。
        CREATE TABLE IF NOT EXISTS dividends (
          code TEXT NOT NULL,
          ex_date TEXT NOT NULL,
          announce_date TEXT,
          record_date TEXT,
          bonus_per_10 REAL,
          transfer_per_10 REAL,
          cash_per_10 REAL,
          progress TEXT NOT NULL DEFAULT '',
          source TEXT NOT NULL DEFAULT '',
          fetched_at TEXT NOT NULL,
          PRIMARY KEY(code, ex_date)
        );
        CREATE INDEX IF NOT EXISTS idx_dividends_code ON dividends(code, ex_date);
        """)
        # 早期版本建的表没有 source 列，幂等补齐
        db.ensure_column(conn, "daily_bars", "source", "TEXT NOT NULL DEFAULT ''")


# --------------------------------------------------------------------------- #
# 行情读写
# --------------------------------------------------------------------------- #
def latest_bar_date(code: str, adjust: str = "raw") -> str | None:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT MAX(trade_date) AS d FROM daily_bars WHERE code=? AND adjust=?",
            (code, _store_adjust(adjust)),
        ).fetchone()
    return row["d"] if row and row["d"] else None


def upsert_bars(code: str, bars: list[dict[str, Any]], adjust: str = "raw",
                source: str = "") -> int:
    """写入日 K；主键 (code, trade_date, adjust) 保证同口径同日覆盖。"""
    store_adjust = _store_adjust(adjust)
    fetched_at = db.now_iso()
    rows = [(
        code, bar["date"], store_adjust, bar.get("open"), bar.get("high"), bar.get("low"),
        bar.get("close"), bar.get("volume"), bar.get("amount"), bar.get("amplitude"),
        bar.get("change_pct"), bar.get("change_amount"), bar.get("turnover"), fetched_at,
        source,
    ) for bar in bars]
    if not rows:
        return 0
    with db.connect() as conn:
        conn.executemany("""INSERT OR REPLACE INTO daily_bars(
          code,trade_date,adjust,open,high,low,close,volume,amount,amplitude,
          change_pct,change_amount,turnover,fetched_at,source)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
    return len(rows)


def list_bars(code: str, start: str | None = None, end: str | None = None,
              adjust: str = "raw") -> list[dict[str, Any]]:
    """按**存储口径**原样读取（不做换算）；复权价请用 load_bars()。"""
    sql = "SELECT * FROM daily_bars WHERE code=? AND adjust=?"
    params: list[Any] = [code, _store_adjust(adjust)]
    if start:
        sql += " AND trade_date>=?"
        params.append(start)
    if end:
        sql += " AND trade_date<=?"
        params.append(end)
    sql += " ORDER BY trade_date"
    with db.connect() as conn:
        return [dict(row) for row in conn.execute(sql, params)]


# --------------------------------------------------------------------------- #
# 复权因子与除权除息明细
# --------------------------------------------------------------------------- #
def upsert_factors(code: str, rows: list[dict[str, Any]], source: str = "") -> int:
    fetched_at = db.now_iso()
    payload = [
        (code, item["ex_date"], item["hfq_factor"], source, fetched_at)
        for item in rows if item.get("ex_date") and item.get("hfq_factor") is not None
    ]
    if not payload:
        return 0
    with db.connect() as conn:
        conn.executemany("""INSERT OR REPLACE INTO adjust_factors(
          code,ex_date,hfq_factor,source,fetched_at) VALUES(?,?,?,?,?)""", payload)
    return len(payload)


def list_factors(code: str) -> list[dict[str, Any]]:
    """按除权日**升序**返回复权因子（便于对交易日做前向填充）。"""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM adjust_factors WHERE code=? ORDER BY ex_date", (code,)
        ).fetchall()
    return [dict(row) for row in rows]


def upsert_dividends(code: str, rows: list[dict[str, Any]], source: str = "") -> int:
    fetched_at = db.now_iso()
    payload = [
        (code, item["ex_date"], item.get("announce_date") or None,
         item.get("record_date") or None, item.get("bonus_per_10"),
         item.get("transfer_per_10"), item.get("cash_per_10"),
         str(item.get("progress") or ""), source, fetched_at)
        for item in rows if item.get("ex_date")
    ]
    if not payload:
        return 0
    with db.connect() as conn:
        conn.executemany("""INSERT OR REPLACE INTO dividends(
          code,ex_date,announce_date,record_date,bonus_per_10,transfer_per_10,
          cash_per_10,progress,source,fetched_at) VALUES(?,?,?,?,?,?,?,?,?,?)""", payload)
    return len(payload)


def list_dividends(code: str) -> list[dict[str, Any]]:
    """按除权日**降序**返回除权除息明细（最新在前）。"""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM dividends WHERE code=? ORDER BY ex_date DESC", (code,)
        ).fetchall()
    return [dict(row) for row in rows]


# --------------------------------------------------------------------------- #
# 复权计算
# --------------------------------------------------------------------------- #
def _factor_at(factors: list[dict[str, Any]], day: str) -> float | None:
    """取 day 当日生效的复权因子，即「除权日 ≤ day」的最后一条（factors 已按升序）。

    单次调用场景够用；批量换算请用 `_adjust_series`，避免逐日重复扫描。
    """
    result = None
    for item in factors:
        if item["ex_date"] <= day:
            result = item["hfq_factor"]
        else:
            break
    return result


def load_bars(code: str, adjust: str = "qfq", start: str | None = None,
              end: str | None = None, as_of: str | None = None) -> list[dict[str, Any]]:
    """按口径读取日 K，复权价**现算**。

    参数：
      adjust  qfq（默认，展示用）/ hfq（回测用）/ raw（不复权）
      as_of   **复权基准日**，仅对 qfq 生效；缺省为区间内最后一个交易日。
              回测请传回测起点，使历史价格不含未来信息（滚动复权）。
    返回项附 `adjusted` 标记；缺少因子时退化为原始价并置 `adjusted=False`，
    避免在因子缺失时静默给出看似正确的复权价。
    """
    store_adjust = _store_adjust(adjust)
    raw = list_bars(code, start, end, "raw")
    if store_adjust == "raw" or not raw:
        return raw

    factors = list_factors(code)
    if not factors:
        return [{**bar, "adjust": store_adjust, "adjusted": False} for bar in raw]

    if store_adjust == "hfq":
        base_factor = 1.0                                   # 后复权基准 = 上市首日（因子 1.0）
    else:
        anchor = str(as_of or raw[-1]["trade_date"])[:10]
        base_factor = _factor_at(factors, anchor) or factors[-1]["hfq_factor"]
    if not base_factor:
        return [{**bar, "adjust": store_adjust, "adjusted": False} for bar in raw]

    out: list[dict[str, Any]] = []
    cursor, current = 0, None
    for bar in raw:
        # 因子按除权日升序，日期同时也升序，用指针前进即可，整体 O(bars + factors)
        day = bar["trade_date"]
        while cursor < len(factors) and factors[cursor]["ex_date"] <= day:
            current = factors[cursor]["hfq_factor"]
            cursor += 1
        if current is None:                                  # 早于首条因子（未上市/异常）
            out.append({**bar, "adjust": store_adjust, "adjusted": False})
            continue
        scale = current / base_factor
        out.append({
            **bar,
            "adjust": store_adjust,
            "adjusted": True,
            "factor": round(scale, 10),
            # 只有价格需要复权；成交量 / 成交额 / 换手率 / 涨跌幅不受复权影响
            **{key: (None if bar.get(key) is None else round(bar[key] * scale, 4))
               for key in ("open", "high", "low", "close")},
        })
    return out
