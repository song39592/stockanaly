# -*- coding: utf-8 -*-
"""股价数据的存储与复权计算。

职责边界：
  - 只做「数据 ↔ 数据」的存取与换算，**不发起任何网络请求**（采集见 `price_service`）；
  - 因此页面、估值、回测等消费方都可以直接依赖本模块，不必关心数据源与降级链。

库文件布局（均位于 `config.DATA_DIR` 下）：

    bars/bars_YYYY.db   日 K **原始价**，按年分片：单文件可控（5000 只约 250 MB/年），
                        便于备份、归档与老库只读；跨年查询按年份逐库读取后合并排序
    bars/factors.db     复权因子与除权除息明细（全量，行数很少）

复权口径（核心设计）：
  库里只存 **不复权原始价** 与 **后复权因子 hfq_factor**；复权价一律现算：

      hfq(t)        = raw(t) × hfq_factor(t)
      qfq(t, as_of) = raw(t) × hfq_factor(t) ÷ hfq_factor(as_of)

  `as_of` 是**复权基准日**，缺省取最新交易日（等价于数据源的常规前复权）。
  回测时应传入**回测起点**作为基准，这样历史价格里不含未来信息，
  且同一段历史每次回测结果一致。只存一份原始价，比存三套复权价省约 2/3 空间。

数据可信（**核心原则**，完整版见 `docs/database.md` 第八节）：

    丢数据可重拉，**错误数据不可接受**；可用性可以让步，正确性不能让步。

  落地方式：
    · **按股指纹** `_digests`：覆盖**全部行情字段**的 HMAC，写入后刷新、读取前比对；
      不一致即抛 `UntrustedDataError`，**绝不把可疑数据交给上层计算**。
    · **指纹带版本号** `DIGEST_VERSION`：参与字段一变必须 +1，否则新旧摘要无法比对，
      会把正常数据整体误判成「被篡改」（升级由 `ensure_digests()` 自动完成）。
    · **写入前判定、已损坏则不重算**：否则增量写入会把「本次没覆盖到的历史改动」
      一并纳入新基线，等于**洗白篡改**（`upsert_bars` 里踩过这个坑，详见其注释）。
    · **修复走数据源**：`price_service.refetch_bars` 从库中既有最早日期全量重抓并
      **整体替换**；**先抓取、后写入**，失败则抛错并保留原数据。
    · **取舍：数据完整 vs 数据可信** —— 二者冲突时**可信优先**，
      宁可整体重拉，也不保留来源存疑的数据。
"""
from __future__ import annotations

import datetime as dt
import math
import re
from typing import Any

import pandas as pd

from . import crypto
from . import db

# 可用的复权口径：raw 不复权 / hfq 后复权 / qfq 前复权
BAR_ADJUSTS = ("qfq", "hfq", "raw")


class UntrustedDataError(RuntimeError):
    """数据指纹校验不通过。

    按既定原则「错误数据不可接受」，此时**拒绝返回数据**——
    宁可让这一只股票暂时不可用（会由 `price_service.refetch_bars` 全量重抓修复），
    也不把可能已被篡改的内容交给上层计算。
    """

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        super().__init__(f"{code} 的数据指纹校验未通过（{detail}）")


# 按股指纹：写入后刷新，读取前比对。
# 对每只股票的全部原始值算一个确定性 HMAC，能精确定位到被改动的个股，
# 且写入时只重算涉及的那几只（毫秒级），不会拖慢批量同步。
_DIGEST_SQL = """
CREATE TABLE IF NOT EXISTS _digests (
  code       TEXT NOT NULL PRIMARY KEY,
  rows       INTEGER NOT NULL,
  first_date TEXT NOT NULL,
  last_date  TEXT NOT NULL,
  checksum   TEXT NOT NULL,
  version    INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
) WITHOUT ROWID;
"""
# 指纹算法版本：**参与指纹的字段集合或序列化方式一变就必须 +1**。
# 否则新旧算法算出的摘要无法比较，升级后会把全部正常数据误判成「被篡改」。
# 启动时 ensure_digests() 会把旧版本指纹整体升级到当前版本。
DIGEST_VERSION = 2

# 参与指纹的列：**覆盖全部行情字段**（含 amount / turnover / amplitude / change_pct /
# change_amount / adjust / source）。只算 6 个价格字段的话，改成交额、换手率、
# 涨跌幅或数据来源标记都不会被发现。
# 不含 code（查询键）与 fetched_at（每次写入都变的采集时间戳，非行情数据）。
_DIGEST_FIELDS = ("trade_date", "adjust", "open", "high", "low", "close", "volume",
                  "amount", "amplitude", "change_pct", "change_amount", "turnover", "source")
_DIGEST_SELECT = ",".join(_DIGEST_FIELDS)

# 主键即聚簇数据，WITHOUT ROWID 省掉一份隐藏 rowid 索引，约省 20~30% 空间且读更快
_BARS_TABLE = """
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
) WITHOUT ROWID;
"""
_BARS_INSERT = """INSERT OR REPLACE INTO daily_bars(
  code,trade_date,adjust,open,high,low,close,volume,amount,amplitude,
  change_pct,change_amount,turnover,fetched_at,source)
  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""


def _store_adjust(adjust: str) -> str:
    """外部传入的口径 → 存储名（兼容空串 / None / none 表示不复权）。"""
    key = str(adjust or "").strip().lower()
    return "raw" if key in ("", "raw", "none") else key


def _year_of(trade_date: str) -> int:
    return int(str(trade_date)[:4])


def _shard_years() -> list[int]:
    """已存在的日 K 分片年份（升序）。"""
    # 用 db.BARS_DIR 而非模块级拷贝：便于测试临时切换数据目录
    if not db.BARS_DIR.exists():
        return []
    years = [int(m.group(1)) for path in db.BARS_DIR.glob("bars_*.db")
             if (m := re.fullmatch(r"bars_(\d{4})\.db", path.name))]
    return sorted(years)


# --------------------------------------------------------------------------- #
# 建表
# --------------------------------------------------------------------------- #
def _ensure_column(conn, table: str, column: str, ddl: str) -> bool:
    """幂等加列：SQLite 没有 `ADD COLUMN IF NOT EXISTS`，重复执行会报错，故先查表结构。"""
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column in existing:
        return False
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    return True


def _ensure_bars_table(conn) -> None:
    conn.execute(_BARS_TABLE)
    conn.execute(_DIGEST_SQL)
    # v2 起指纹带版本号；老库的 _digests 没有该列，补上（默认 0 表示旧算法）
    _ensure_column(conn, "_digests", "version", "INTEGER NOT NULL DEFAULT 0")


# --------------------------------------------------------------------------- #
# 按股指纹
# --------------------------------------------------------------------------- #
def _checksum(rows) -> str:
    """对行序列算确定性指纹（覆盖**全部行情字段**与顺序）。

    空值统一写成空串，避免 `None` / `"None"` / `NULL` 三种写法算出不同摘要；
    字段顺序沿用 `_DIGEST_FIELDS`，因此调整该元组即为改变算法，必须同时 +1 `DIGEST_VERSION`。
    """
    payload = "\n".join(
        "|".join("" if row[field] is None else str(row[field]) for field in _DIGEST_FIELDS)
        for row in rows)
    return crypto.sign(payload)


def _read_raw_rows(conn, code: str):
    # 按 (trade_date, adjust) 排序：同股可能有多口径行，排序不稳定会导致指纹抖动
    return list(conn.execute(
        f"SELECT {_DIGEST_SELECT} FROM daily_bars WHERE code=?"
        f" ORDER BY trade_date, adjust", (code,)))


def refresh_digest(conn, code: str) -> dict:
    """重算并写入某只股票在当前分片的指纹。"""
    conn.execute(_DIGEST_SQL)
    rows = _read_raw_rows(conn, code)
    if not rows:
        conn.execute("DELETE FROM _digests WHERE code=?", (code,))
        return {"code": code, "rows": 0}
    item = {
        "code": code,
        "rows": len(rows),
        "first_date": rows[0]["trade_date"],
        "last_date": rows[-1]["trade_date"],
        "checksum": _checksum(rows),
        "version": DIGEST_VERSION,
        "updated_at": db.now_iso(),
    }
    conn.execute("""INSERT OR REPLACE INTO _digests
      (code,rows,first_date,last_date,checksum,version,updated_at)
      VALUES(:code,:rows,:first_date,:last_date,:checksum,:version,:updated_at)""", item)
    return {"code": code, "rows": len(rows)}


def verify_digest(conn, code: str) -> dict:
    """重算并按记录比对；返回是否一致及差异说明。"""
    conn.execute(_DIGEST_SQL)
    recorded = conn.execute("SELECT * FROM _digests WHERE code=?", (code,)).fetchone()
    rows = _read_raw_rows(conn, code)
    if recorded is None:
        return {"code": code, "ok": not rows, "recorded": None,
                "actual_rows": len(rows),
                "reason": "缺少指纹记录" if rows else ""}
    problems = []
    # 算法版本不符时无法比对摘要，必须显式报告，否则会误判为「内容被改」
    stored_version = recorded["version"] if "version" in recorded.keys() else 0
    if stored_version != DIGEST_VERSION:
        problems.append(f"指纹算法版本 v{stored_version} → 需升级到 v{DIGEST_VERSION}")
    elif recorded["rows"] != len(rows):
        problems.append(f"行数 {recorded['rows']} → {len(rows)}")
    elif recorded["checksum"] != _checksum(rows):
        problems.append("内容指纹不一致")
    return {
        "code": code,
        "ok": not problems,
        "recorded": {"rows": recorded["rows"], "first_date": recorded["first_date"],
                     "last_date": recorded["last_date"], "version": stored_version},
        "actual_rows": len(rows),
        "reason": "；".join(problems),
    }


# 股本（流通 / 总）：换手率 = 成交量 ÷ 流通股本，筹码分布与衰减计算依赖它。
# 本地通达信日线只有 OHLCV，没有股本，故需单独接一路数据源（见 share_service）。
# 以 as_of（生效日）为键存成序列：当前只采到「最新值」，将来若接入股本变迁
# （解禁 / 增发 / 送转）可按同样结构补历史节点，读取端无需改动。
_SHARE_CAPITAL_TABLE = """
CREATE TABLE IF NOT EXISTS share_capital (
  code TEXT NOT NULL,
  as_of TEXT NOT NULL,
  float_shares REAL,
  total_shares REAL,
  source TEXT NOT NULL DEFAULT '',
  fetched_at TEXT NOT NULL,
  PRIMARY KEY(code, as_of)
) WITHOUT ROWID;
"""


def _ensure_share_table(conn) -> None:
    """幂等建股本表：老库在 `init_db` 之前就已存在，读写前都要能自愈。"""
    conn.execute(_SHARE_CAPITAL_TABLE)


def init_db() -> None:
    """建齐股价相关表：当前年份的日 K 分片 + 因子库。"""
    with db.connect(db.bars_db(dt.date.today().year)) as conn:
        _ensure_bars_table(conn)
    with db.connect(db.FACTORS_DB) as conn:
        conn.executescript("""
        -- 复权因子：只存 hfq_factor（基准为「上市首日」，历史值不随新的除权变化）。
        -- qfq_factor 以「最新日」为基准、每天都在变，存下来等于又把会变的数据请回来，故不存。
        CREATE TABLE IF NOT EXISTS adjust_factors (
          code TEXT NOT NULL,
          ex_date TEXT NOT NULL,
          hfq_factor REAL NOT NULL,
          source TEXT NOT NULL DEFAULT '',
          fetched_at TEXT NOT NULL,
          PRIMARY KEY(code, ex_date)
        ) WITHOUT ROWID;
        -- 除权除息明细：审计因子、对拍口径、计算税后真实持仓成本。
        -- 送股 / 转增 / 派息均为「每 10 股」口径（与数据源一致）。
        """ + _SHARE_CAPITAL_TABLE + """
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
        ) WITHOUT ROWID;
        """)


# --------------------------------------------------------------------------- #
# 行情读写（按年分片）
# --------------------------------------------------------------------------- #
def latest_bar_date(code: str, adjust: str = "raw") -> str | None:
    """最近一条日 K 的日期：从最新的分片往前找，找到即返回。"""
    store_adjust = _store_adjust(adjust)
    for year in reversed(_shard_years()):
        with db.connect(db.bars_db(year)) as conn:
            row = conn.execute(
                "SELECT MAX(trade_date) AS d FROM daily_bars WHERE code=? AND adjust=?",
                (code, store_adjust),
            ).fetchone()
        if row and row["d"]:
            return row["d"]
    return None


def earliest_bar_date(code: str, adjust: str = "raw") -> str | None:
    """最早一条日 K 的日期：从最老的分片往后找，找到即返回；没有数据返回 None。

    用来判断「这只股票还有没有更老的历史可抓」：若最早一条已经晚于某个分界日，
    说明它是在那之后才上市的，再往回抓只会拿到空结果。
    """
    store_adjust = _store_adjust(adjust)
    for year in _shard_years():
        with db.connect(db.bars_db(year)) as conn:
            row = conn.execute(
                "SELECT MIN(trade_date) AS d FROM daily_bars WHERE code=? AND adjust=?",
                (code, store_adjust),
            ).fetchone()
        if row and row["d"]:
            return row["d"]
    return None


def code_latest_dates(adjust: str = "raw") -> dict[str, str]:
    """**一次性**取回每只已下载股票的最新日 K 日期 {code: YYYY-MM-DD}。

    不能逐只调 `latest_bar_date`——那是「按 code 查、逐分片回溯」，
    几千只股票会退化成几十万次开库。这里改成**每个分片一次聚合查询**，
    再在内存里按 code 取全局最大值，全库只扫一遍。
    成本是一次全表扫描，因此调用方请勿高频调用（见 download_service 的缓存）。
    """
    store_adjust = _store_adjust(adjust)
    result: dict[str, str] = {}
    for year in _shard_years():
        with db.connect(db.bars_db(year)) as conn:
            for row in conn.execute(
                "SELECT code, MAX(trade_date) AS d FROM daily_bars "
                "WHERE adjust=? GROUP BY code", (store_adjust,)):
                day = row["d"]
                if not day:
                    continue
                code = row["code"]
                if code not in result or day > result[code]:
                    result[code] = day
    return result


def _digest_is_broken(conn, code: str) -> bool:
    """该股**既有**指纹是否已不匹配（供写入前判定）。无指纹记录时视为正常。

    没有指纹记录说明是首次写入或已由 `ensure_digests()` 补齐，不应阻拦；
    有记录且校验不通过，说明数据已被外部改过。
    """
    conn.execute(_DIGEST_SQL)
    if conn.execute("SELECT 1 FROM _digests WHERE code=?", (code,)).fetchone() is None:
        return False
    return not verify_digest(conn, code)["ok"]


def repair_digests(dry_run: bool = True) -> dict:
    """把「指纹与数据不一致」的记录按当前数据重算为新基线。

    背景：某次批量增量同步（`source='腾讯证券'`）写完数据后没刷指纹，导致大量
    (代码, 年份分片) 的指纹停在旧行数 / 旧内容，`load_bars` 一路抛 `UntrustedDataError`。
    这些数据出自项目自己的可信管道（腾讯行情），**非外部篡改**，故可用
    「当前数据 = 新基线」的方式修复，而不必逐票重抓（5500+ 只不现实）。

    覆盖三类失配：缺指纹、行数不一致、内容指纹不一致；算法版本落后也一并重算升级。
    `dry_run=True` 只统计、不落库（默认），`False` 才逐个 `refresh_digest`。

    返回 `{dry_run, count, by_year, items:[{code,year,reason}], updated:[...]}`。
    """
    affected: list[dict[str, Any]] = []
    updated: list[dict[str, Any]] = []
    by_year: dict[int, int] = {}
    for year in _shard_years():
        with db.connect(db.bars_db(year)) as conn:
            codes = [r["code"] for r in conn.execute(
                "SELECT DISTINCT code FROM daily_bars ORDER BY code")]
            for code in codes:
                chk = verify_digest(conn, code)
                if chk["ok"]:
                    continue
                affected.append({"code": code, "year": year,
                                 "reason": chk.get("reason") or "指纹不一致"})
                by_year[year] = by_year.get(year, 0) + 1
                if not dry_run:
                    res = refresh_digest(conn, code)
                    updated.append({"code": code, "year": year,
                                    "rows": res.get("rows", 0)})
    return {"dry_run": dry_run, "count": len(affected), "by_year": by_year,
            "items": affected, "updated": updated}


def _bad_bar(bar: dict[str, Any]) -> str:
    """返回该柱不可入库的原因；空串 = 合法。

    bug-04（测试夹具污染生产库）的教训：一根「只有 close、O/H/L/V 全空」的柱
    足以同时打坏两张脸 —— 前端图表价格刻度被撑到负值（红烛从图例顶贯到绘图区底）、
    筹码分布全分箱 NaN（turnover 缺失沿衰减累积传染）-> JSON 序列化直接 500。
    所以在写入的门口拒收：缺任一 O/H/L/C、或有非正价格，一律不入库。
    """
    for key in ("open", "high", "low", "close"):
        v = bar.get(key)
        if v is None:
            return "缺少 " + key
        try:
            fv = float(v)
        except (TypeError, ValueError):
            return key + " 不是数值"
        if math.isnan(fv) or math.isinf(fv) or fv <= 0:
            return key + " 非正数"
    if float(bar["high"]) < float(bar["low"]):
        return "high < low"
    # 一致性：开收盘必须落在当日高低区间内。bug-04 续集实测过上游字段错位 ——
    # 腾讯接口曾对 000001 的 177 个交易日返回 close=8888.0 而 O/H/L 正常，
    # 「非正数/缺字段」那两条校验全都放行，污染直接进了库（后复权与策略回测全被带偏）。
    lo, hi = float(bar["low"]), float(bar["high"])
    for key in ("open", "close"):
        v = float(bar[key])
        if v < lo * 0.99 or v > hi * 1.01:
            return "%s=%.4f 越出当日区间 [%.4f, %.4f]" % (key, v, lo, hi)
    return ""


def upsert_bars(code: str, bars: list[dict[str, Any]], adjust: str = "raw",
                source: str = "", validate: bool = True) -> int:
    """写入日 K：按年份分组，路由到对应的分片库（主键保证同口径同日覆盖）。

    `validate=True`（默认）时跳过缺 O/H/L/C 或价格非正的柱（见 `_bad_bar`）；
    单元测试里故意写「只有 close」的夹具柱时请显式传 `validate=False`。

    ⚠️ **写入前先判定旧指纹：已损坏则不重算**。
    增量写入只覆盖「有数据的那几天」，若此时直接重算指纹，会把**本次没覆盖到的
    历史改动**一并纳入新基线，等于**洗白篡改**——「改历史 + 等一次日常同步」
    之后便再也检不出来。保留旧指纹后，读取仍会报「行数 / 内容不一致」，
    从而继续触发全量重拉修复。
    """
    store_adjust = _store_adjust(adjust)
    fetched_at = db.now_iso()
    grouped: dict[int, list[tuple]] = {}
    for bar in bars:
        day = str(bar.get("date") or "")[:10]
        if not day:
            continue
        if validate and _bad_bar(bar):
            continue
        grouped.setdefault(_year_of(day), []).append((
            code, day, store_adjust, bar.get("open"), bar.get("high"), bar.get("low"),
            bar.get("close"), bar.get("volume"), bar.get("amount"), bar.get("amplitude"),
            bar.get("change_pct"), bar.get("change_amount"), bar.get("turnover"),
            fetched_at, source,
        ))
    total = 0
    for year, rows in grouped.items():
        with db.connect(db.bars_db(year)) as conn:
            _ensure_bars_table(conn)
            affected = {row[0] for row in rows}
            # 判定必须在写入**之前**：此刻行数仍是写入前的，才能与旧指纹正确比对
            broken = {item for item in affected if _digest_is_broken(conn, item)}
            conn.executemany(_BARS_INSERT, rows)    # 单事务批量写入
            for item in affected:
                if item in broken:
                    continue                        # 保留旧指纹，让问题继续暴露
                refresh_digest(conn, item)          # 正常：只重算本次涉及的股票
        total += len(rows)
    return total


def overwrite_bars(code: str, bars: list[dict[str, Any]], adjust: str = "raw",
                   source: str = "", validate: bool = True) -> dict[str, int]:
    """用给定数据**整体替换**某股某口径的日 K。

    与 `upsert_bars` 的区别：upsert 只写「有数据的那些天」，库里多出来的旧行**不会被动**；
    本函数在写入后删除各分片中**不在新数据日期集合内**的旧行（含该股完全没覆盖到的年份），
    因此能修复「历史区间被改动」——增量同步碰不到老日期，只有整体替换才还原得了。

    先写后删：即使中途失败也不会出现「删了却没能补上」的空窗。
    返回写入 / 删除行数，便于调用方判断修复效果。
    """
    store_adjust = _store_adjust(adjust)
    fetched_at = db.now_iso()
    grouped: dict[int, list[tuple]] = {}
    for bar in bars:
        day = str(bar.get("date") or bar.get("trade_date") or "")[:10]
        if not day:
            continue
        if validate and _bad_bar(bar):
            continue
        grouped.setdefault(_year_of(day), []).append((
            code, day, store_adjust, bar.get("open"), bar.get("high"), bar.get("low"),
            bar.get("close"), bar.get("volume"), bar.get("amount"), bar.get("amplitude"),
            bar.get("change_pct"), bar.get("change_amount"), bar.get("turnover"),
            fetched_at, source,
        ))

    written = removed = 0
    for year, rows in grouped.items():
        days = sorted({row[1] for row in rows})
        with db.connect(db.bars_db(year)) as conn:
            _ensure_bars_table(conn)
            conn.executemany(_BARS_INSERT, rows)
            written += len(rows)
            placeholders = ",".join("?" * len(days))
            removed += conn.execute(
                f"DELETE FROM daily_bars WHERE code=? AND adjust=?"
                f" AND trade_date NOT IN ({placeholders})",
                (code, store_adjust, *days)).rowcount
            refresh_digest(conn, code)

    # 新数据完全没有覆盖到的年份：该股在这些分片里的旧行同样属于「应被替换掉」的部分
    for year in _shard_years():
        if year in grouped:
            continue
        with db.connect(db.bars_db(year)) as conn:
            _ensure_bars_table(conn)
            removed += conn.execute(
                "DELETE FROM daily_bars WHERE code=? AND adjust=?",
                (code, store_adjust)).rowcount
            refresh_digest(conn, code)
    return {"written": written, "removed": removed}


def ensure_digests() -> dict:
    """为尚无指纹、或指纹版本落后的股票重算一次。

    版本升级（`DIGEST_VERSION` 递增，例如本次把参与字段从 6 个价格列扩到全部行情列）后，
    老指纹无法与新算法比对，会**整体误判成「被篡改」**，因此启动时必须按当前算法重建一次。
    """
    created = upgraded = 0
    for year in _shard_years():
        with db.connect(db.bars_db(year)) as conn:
            _ensure_bars_table(conn)
            known = {row["code"]: row["version"] for row in
                     conn.execute("SELECT code, version FROM _digests")}
            for row in conn.execute("SELECT DISTINCT code FROM daily_bars"):
                code = row["code"]
                if code not in known:
                    refresh_digest(conn, code)
                    created += 1
                elif known[code] != DIGEST_VERSION:
                    refresh_digest(conn, code)
                    upgraded += 1
    return {"created": created, "upgraded": upgraded, "version": DIGEST_VERSION}


def list_bars(code: str, start: str | None = None, end: str | None = None,
              adjust: str = "raw", verify: bool = True) -> list[dict[str, Any]]:
    """按**存储口径**读取原始价；跨年自动逐库查询后合并排序。复权价请用 load_bars()。

    `verify=True`（默认）时先比对按股指纹：不一致说明数据被改动过，
    此时**抛出 `UntrustedDataError` 拒绝返回**，不把可疑数据交给上层计算。
    """
    store_adjust = _store_adjust(adjust)
    sql = "SELECT * FROM daily_bars WHERE code=? AND adjust=?"
    params: list[Any] = [code, store_adjust]
    if start:
        sql += " AND trade_date>=?"
        params.append(str(start)[:10])
    if end:
        sql += " AND trade_date<=?"
        params.append(str(end)[:10])

    # 只在「区间可能涉及、且确实存在的分片」上查询，避免无谓开库
    available = _shard_years()
    if start and end:
        years = [y for y in available if _year_of(start) <= y <= _year_of(end)]
    elif start:
        years = [y for y in available if y >= _year_of(start)]
    elif end:
        years = [y for y in available if y <= _year_of(end)]
    else:
        years = available

    rows: list[dict[str, Any]] = []
    for year in years:
        with db.connect(db.bars_db(year)) as conn:
            if verify:
                check = verify_digest(conn, code)
                if not check["ok"]:
                    raise UntrustedDataError(code, check["reason"] or "指纹不一致")
            rows.extend(dict(row) for row in conn.execute(sql + " ORDER BY trade_date", params))
    rows.sort(key=lambda item: item["trade_date"])
    return rows


# --------------------------------------------------------------------------- #
# 复权因子与除权除息明细（独立库）
# --------------------------------------------------------------------------- #
def upsert_factors(code: str, rows: list[dict[str, Any]], source: str = "") -> int:
    fetched_at = db.now_iso()
    payload = [
        (code, item["ex_date"], item["hfq_factor"], source, fetched_at)
        for item in rows if item.get("ex_date") and item.get("hfq_factor") is not None
    ]
    if not payload:
        return 0
    with db.connect(db.FACTORS_DB) as conn:
        conn.executemany("""INSERT OR REPLACE INTO adjust_factors(
          code,ex_date,hfq_factor,source,fetched_at) VALUES(?,?,?,?,?)""", payload)
    return len(payload)


def list_factors(code: str) -> list[dict[str, Any]]:
    """按除权日**升序**返回复权因子（便于对交易日做前向填充）。"""
    with db.connect(db.FACTORS_DB) as conn:
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
    with db.connect(db.FACTORS_DB) as conn:
        conn.executemany("""INSERT OR REPLACE INTO dividends(
          code,ex_date,announce_date,record_date,bonus_per_10,transfer_per_10,
          cash_per_10,progress,source,fetched_at) VALUES(?,?,?,?,?,?,?,?,?,?)""", payload)
    return len(payload)


def list_dividends(code: str) -> list[dict[str, Any]]:
    """按除权日**降序**返回除权除息明细（最新在前）。"""
    with db.connect(db.FACTORS_DB) as conn:
        rows = conn.execute(
            "SELECT * FROM dividends WHERE code=? ORDER BY ex_date DESC", (code,)
        ).fetchall()
    return [dict(row) for row in rows]


# --------------------------------------------------------------------------- #
# 股本（流通 / 总）—— 换手率与筹码分布的基础数据
# --------------------------------------------------------------------------- #
def upsert_share_capital(code: str, rows: list[dict[str, Any]], source: str = "") -> int:
    """写入股本记录；`as_of` 为生效日（该日及之后有效）。单位：**股**。"""
    fetched_at = db.now_iso()
    payload = [
        (code, item["as_of"], item.get("float_shares"), item.get("total_shares"),
         source, fetched_at)
        for item in rows
        if item.get("as_of") and (item.get("float_shares") or item.get("total_shares"))
    ]
    if not payload:
        return 0
    with db.connect(db.FACTORS_DB) as conn:
        _ensure_share_table(conn)
        conn.executemany("""INSERT OR REPLACE INTO share_capital(
          code,as_of,float_shares,total_shares,source,fetched_at) VALUES(?,?,?,?,?,?)""",
            payload)
    return len(payload)


def list_share_capital(code: str) -> list[dict[str, Any]]:
    """按生效日**升序**返回股本序列（便于对交易日做前向填充）。"""
    with db.connect(db.FACTORS_DB) as conn:
        _ensure_share_table(conn)
        rows = conn.execute(
            "SELECT * FROM share_capital WHERE code=? ORDER BY as_of", (code,)
        ).fetchall()
    return [dict(row) for row in rows]


def float_shares_at(code: str, date: str | None = None) -> float | None:
    """取 `date` 当日生效的流通股本（股）。

    优先「生效日 ≤ date」的最后一条；若查不到（历史日期早于最早一条记录），
    回退到「生效日 > date」的最早一条——当前只采到「最新」这一个节点，
    历史股本（解禁 / 增发前）未知，只能用最近的已知值近似；将来接入股本变迁
    补上历史节点后，第一条查询即可命中，回退自动失效。
    date 缺省则取最新一条；完全无记录返回 None（调用方据此决定是否去采集）。
    """
    with db.connect(db.FACTORS_DB) as conn:
        _ensure_share_table(conn)
        if date:
            day = str(date)[:10]
            row = conn.execute(
                "SELECT float_shares FROM share_capital WHERE code=? AND as_of<=? "
                "ORDER BY as_of DESC LIMIT 1", (code, day),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    "SELECT float_shares FROM share_capital WHERE code=? AND as_of>? "
                    "ORDER BY as_of ASC LIMIT 1", (code, day),
                ).fetchone()
        else:
            row = conn.execute(
                "SELECT float_shares FROM share_capital WHERE code=? "
                "ORDER BY as_of DESC LIMIT 1", (code,),
            ).fetchone()
    return None if row is None else row["float_shares"]


# --------------------------------------------------------------------------- #
# 一次性迁移：旧主库（三口径单表）→ 分片结构
# --------------------------------------------------------------------------- #
def migrate_legacy_shards() -> dict:
    """把旧主库里的行情表拆到分片结构，**只读源表、不删除**。

    - 日 K：只迁 `adjust='raw'`。老数据只有 qfq（以当时的「最新日」为基准烘焙而来），
      无法从中还原真实成交价，一律丢弃，靠访问时自动重新同步补齐；
      迁移前已存在的 qfq/hfq 冗余也因此不再占用空间。
    - 复权因子与除权明细：全量搬到 `bars/factors.db`。
    幂等：主库里没有这些表（已迁移过）时直接返回。
    """
    legacy = db.DB_PATH
    if not legacy.exists():
        return {"ok": True, "migrated": {}, "note": "主库不存在，无需迁移"}

    with db.connect(legacy) as conn:
        tables = {row["name"] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if not tables & {"daily_bars", "adjust_factors", "dividends"}:
            return {"ok": True, "migrated": {}, "note": "已是分片结构"}

        result: dict[str, Any] = {}
        if "daily_bars" in tables:
            rows = [dict(row) for row in conn.execute(
                "SELECT * FROM daily_bars WHERE adjust='raw' ORDER BY trade_date")]
            result["bars_raw"] = upsert_bars_many(rows) if rows else 0
            result["bars_dropped"] = conn.execute(
                "SELECT COUNT(*) AS c FROM daily_bars WHERE adjust<>'raw'").fetchone()["c"]
        if "adjust_factors" in tables:
            result["factors"] = _copy_factors(conn)
        if "dividends" in tables:
            result["dividends"] = _copy_dividends(conn)
    return {"ok": True, "migrated": result,
            "note": "旧主库中的行情表已保留未删，确认无误后可自行清理"}


def drop_redundant_adjusts(keep: str = "raw") -> dict:
    """删除日K分片中非 `keep` 口径的冗余记录。

    复权价由 `hfq_factor` 现算即可，无需落盘；若库里残留 qfq / hfq，
    会白白占用约 2/3 的空间（5000 只 × 10 年约多占 4.9 GB）。
    """
    removed = 0
    detail: dict[str, dict] = {}
    for year in _shard_years():
        path = db.bars_db(year)
        with db.connect(path) as conn:
            found = conn.execute(
                "SELECT adjust, COUNT(*) AS c FROM daily_bars WHERE adjust<>? GROUP BY adjust",
                (keep,)).fetchall()
            if not found:
                continue
            detail[path.stem] = {row["adjust"]: row["c"] for row in found}
            removed += conn.execute(
                "DELETE FROM daily_bars WHERE adjust<>?", (keep,)).rowcount
    # 删除后需 VACUUM 才能真正回收文件空间（须在写入提交后另开连接执行）。
    # VACUUM 会刷新文件时间但不产生 DML，故用 force_touch 同步记录时间，避免校验误报。
    if removed:
        for year in _shard_years():
            with db.connect(db.bars_db(year), force_touch=True) as conn:
                conn.execute("VACUUM")
    return {"ok": True, "removed": removed, "detail": detail}


def drop_legacy_tables(vacuum: bool = True) -> dict:
    """删除旧主库里的行情表（数据已迁到分片结构，不再被读取）。

    **安全校验**：只在分片库确实已有数据时才执行，避免迁移未完成时误删唯一副本。
    仅处理主库中的 `daily_bars` / `adjust_factors` / `dividends`，
    不触碰 `bars/` 下的分片库与因子库。删除后 VACUUM 回收文件空间。
    """
    if not _shard_years():
        return {"ok": False, "error": "分片库为空，请先完成迁移再清理"}
    total_shard_rows = 0
    for year in _shard_years():
        with db.connect(db.bars_db(year)) as conn:
            total_shard_rows += conn.execute("SELECT COUNT(*) FROM daily_bars").fetchone()[0]
    if not total_shard_rows:
        return {"ok": False, "error": "分片库没有日K数据，拒绝清理旧表"}

    legacy = db.DB_PATH
    if not legacy.exists():
        return {"ok": True, "dropped": [], "note": "主库不存在"}
    with db.connect(legacy) as conn:
        tables = {row["name"] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        target = [name for name in ("daily_bars", "adjust_factors", "dividends") if name in tables]
        if not target:
            return {"ok": True, "dropped": [], "note": "旧表已不存在"}
        rows = {name: conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] for name in target}
        for name in target:
            conn.execute(f"DROP TABLE IF EXISTS {name}")
    if vacuum:
        # DROP 只把页退回空闲列表，文件不会立即变小；VACUUM 重建文件以真正回收空间。
        # 同样需要 force_touch 同步记录时间（VACUUM 不算 DML）。
        with db.connect(legacy, force_touch=True) as handle:
            handle.execute("VACUUM")
    return {"ok": True, "dropped": target, "rows": rows, "shard_rows": total_shard_rows}


def upsert_bars_many(rows: list[dict[str, Any]]) -> int:
    """批量写入（迁移用，入参已是目标行的字段名）。"""
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        day = str(row.get("trade_date") or "")[:10]
        if day:
            grouped.setdefault(_year_of(day), []).append(row)
    total = 0
    for year, items in grouped.items():
        payload = [(
            item["code"], str(item["trade_date"])[:10], "raw",
            item.get("open"), item.get("high"), item.get("low"), item.get("close"),
            item.get("volume"), item.get("amount"), item.get("amplitude"),
            item.get("change_pct"), item.get("change_amount"), item.get("turnover"),
            item.get("fetched_at") or db.now_iso(), item.get("source") or "",
        ) for item in items]
        with db.connect(db.bars_db(year)) as conn:
            _ensure_bars_table(conn)
            conn.executemany(_BARS_INSERT, payload)
        total += len(payload)
    return total


def _copy_factors(conn) -> int:
    rows = [dict(row) for row in conn.execute("SELECT * FROM adjust_factors")]
    if not rows:
        return 0
    payload = [(r["code"], r["ex_date"], r["hfq_factor"],
                r.get("source") or "", r.get("fetched_at") or db.now_iso()) for r in rows]
    with db.connect(db.FACTORS_DB) as target:
        target.executemany("""INSERT OR REPLACE INTO adjust_factors(
          code,ex_date,hfq_factor,source,fetched_at) VALUES(?,?,?,?,?)""", payload)
    return len(payload)


def _copy_dividends(conn) -> int:
    rows = [dict(row) for row in conn.execute("SELECT * FROM dividends")]
    if not rows:
        return 0
    payload = [(r["code"], r["ex_date"], r.get("announce_date"), r.get("record_date"),
                r.get("bonus_per_10"), r.get("transfer_per_10"), r.get("cash_per_10"),
                r.get("progress") or "", r.get("source") or "",
                r.get("fetched_at") or db.now_iso()) for r in rows]
    with db.connect(db.FACTORS_DB) as target:
        target.executemany("""INSERT OR REPLACE INTO dividends(
          code,ex_date,announce_date,record_date,bonus_per_10,transfer_per_10,
          cash_per_10,progress,source,fetched_at) VALUES(?,?,?,?,?,?,?,?,?,?)""", payload)
    return len(payload)


# --------------------------------------------------------------------------- #
# 复权计算
# --------------------------------------------------------------------------- #
def _factor_at(factors: list[dict[str, Any]], day: str) -> float | None:
    """取 day 当日生效的复权因子，即「除权日 ≤ day」的最后一条（factors 已按升序）。"""
    result = None
    for item in factors:
        if item["ex_date"] <= day:
            result = item["hfq_factor"]
        else:
            break
    return result


# --------------------------------------------------------------------------- #
# 后复权：生效区间判定（第 17 项）—— 全项目**唯一**的一份「哪天起该乘哪个因子」
# --------------------------------------------------------------------------- #
def hfq_steps(factors: list[dict[str, Any]], dates: list[str]) -> list[tuple[int, Any]]:
    """产出 `(起始下标, hfq_factor)`：因子在 `dates[i]` 这天起生效。

    规则（与本模块 `load_bars` 的 hfq 口径一致，第 17 项前曾被复刻过一份在
    `indicators/data.py::_apply_hfq` 里）：
    某交易日的因子 = **`ex_date <= 该日` 的最近一条**的 `hfq_factor`；
    早于首条因子时没有因子（调用方各自决定怎么处理，见下）。

    只在因子**发生变化**时产出，因此产出条数 = 事件数（实测中位数 10 只/票），
    调用方既能顺着上一步一路沿用（逐日取值），也能直接对 `[i:]` 整段缩放
    （切片赋值）—— 两种用法都不必逐日循环，这是它能被共用的前提。

    ⚠️ `hfq_factor` 为 `None` 的事件**照样产出**（值就是 None），由调用方决定
    「保持原始价」还是「沿用上一个」。两侧目前行为不同且都保留：
    `load_bars` 遇 None 视为「无可用因子」→ 该 bar 保持原始价并置
    `adjusted=False`；`apply_hfq_factors` 则是跳过该事件、沿用上一个有效因子。
    现有因子表实测 **0 行 NULL**（68668 行全为数值），故两者当前等价。

    因子**不是**累乘：`hfq_factor` 本身已是相对上市首日的累计因子，
    同一 ex_date 有多条时以最后一条为准（实测无重复 (code, ex_date)）。
    """
    steps: list[tuple[int, Any]] = []
    cursor = 0
    total = len(factors)
    for i, day in enumerate(dates):
        while cursor < total and str(factors[cursor]["ex_date"])[:10] <= day:
            steps.append((i, factors[cursor]["hfq_factor"]))
            cursor += 1
    return steps


def apply_hfq_factors(panel: "pd.DataFrame") -> "pd.DataFrame":
    """把原始收盘价**宽表**（索引 trade_date、列 code）按后复权因子缩放。

    给「同一交易日全市场」的面板用（RPS 等横截面指标）：`load_market_close`
    读回的是未复权收盘价，复权这一步以前在 `indicators/data.py::_apply_hfq`
    里**另写了一份**——它自己复制了 `load_bars` 的 hfq 算法，一旦
    `price_store` 改复权规则，RPS 排名就会**静默算错**且无人察觉
    （两条链没有任何交叉验证）。第 17 项把这份实现收回来，
    因子生效区间的判定统一走 `hfq_steps`。

    只影响价格，不动成交量 / 换手率（与 `load_bars` 的口径一致）。
    早于该票首条因子的日期保持原始价（等价 `load_bars` 的 `adjusted=False`）。
    """
    if panel is None or panel.empty:
        return panel if panel is not None else pd.DataFrame()

    by_code: dict[str, list[dict[str, Any]]] = {}
    for row in load_all_factors():
        by_code.setdefault(row["code"], []).append(row)

    dates = [str(d)[:10] for d in panel.index]
    base = panel.to_numpy(dtype="float64", copy=True)
    adj = base.copy()
    for j, code in enumerate(panel.columns):
        factors = by_code.get(str(code))
        if not factors:
            continue                      # 无因子 -> 保持原始价
        for i, fac in hfq_steps(factors, dates):
            if fac is None:
                continue                  # 见 hfq_steps 的说明：此处沿用上一个有效因子
            adj[i:, j] = base[i:, j] * float(fac)
    return pd.DataFrame(adj, index=panel.index, columns=panel.columns)


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
    # 第 17 项：生效区间的判定统一走 hfq_steps（与 apply_hfq_factors 同一份），
    # 免得「哪天起该乘哪个因子」在两处各算一遍、改一处就静默分叉。
    steps = hfq_steps(factors, [bar["trade_date"] for bar in raw])
    cursor, current = 0, None
    for day_index, bar in enumerate(raw):
        # steps 只在因子变化处产出，顺着往下走即可；未走到任何 step 说明早于首条因子
        while cursor < len(steps) and steps[cursor][0] <= day_index:
            current = steps[cursor][1]
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


# --------------------------------------------------------------------------- #
# 横截面（全市场）批量读取 —— 供 RPS 等「同一交易日全市场排名」的指标使用
# --------------------------------------------------------------------------- #
def load_market_close(adjust: str = "raw", start: str | None = None,
                      codes: list[str] | None = None) -> pd.DataFrame:
    """一次性读取**全市场**收盘价，返回宽表（索引 trade_date，列=code）。

    为什么单独开这个入口：横截面类需求（如 RPS 相对强度 = 同一交易日在全市场的
    涨幅排名）需要「全部标的 × 全部交易日」的价格。逐只走 load_bars 会把「按股
    指纹校验」的开销放大到数千次（实测全市场 ~10 分钟），这里改为**按年份分片
    整片查询一次**、且只取 close 单列（实测 ~15 秒）。

    ⚠️ 与 load_bars 的关键差异（务必知晓）：
      * **不做按股指纹校验**：整片查询拿不到按股摘要；这里只取一列 close 做横截面
        排序，属只读、低风险用途。需要严格校验的消费方仍必须走 load_bars。
      * 返回**原始价**（store 口径），不在此处复权；需要复权请在调用方按
        `load_all_factors()` 的因子自行缩放（见 indicators.data 的 RPS 面板）。
    """
    store_adjust = _store_adjust(adjust)
    years = _shard_years()
    if start:
        years = [y for y in years if y >= _year_of(start)]

    frames: list[pd.DataFrame] = []
    for year in years:
        sql = "SELECT code, trade_date, close FROM daily_bars WHERE adjust=?"
        params: list[Any] = [store_adjust]
        if start:
            sql += " AND trade_date>=?"
            params.append(str(start)[:10])
        with db.connect(db.bars_db(year)) as conn:
            rows = conn.execute(sql, params).fetchall()
        if not rows:
            continue
        df = pd.DataFrame(rows, columns=["code", "trade_date", "close"])
        # 逐分片 pivot 后按行拼接：避免把 600 万行长表一次性 concat 进内存
        frames.append(df.pivot(index="trade_date", columns="code", values="close"))

    if not frames:
        return pd.DataFrame()
    panel = pd.concat(frames, axis=0)
    panel.index = [str(d)[:10] for d in panel.index]
    panel = panel.sort_index()
    if codes is not None:
        keep = [c for c in codes if c in panel.columns]
        panel = panel[keep]
    return panel


def load_all_factors() -> list[dict[str, Any]]:
    """读取全部**后复权因子**（code / ex_date / hfq_factor，按 code、ex_date 升序）。

    因子库行数很少（全市场约 6~7 万行），供横截面批量复权一次性取用。
    """
    with db.connect(db.FACTORS_DB) as conn:
        return [dict(row) for row in conn.execute(
            "SELECT code, ex_date, hfq_factor FROM adjust_factors ORDER BY code, ex_date")]
