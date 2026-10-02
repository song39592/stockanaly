# -*- coding: utf-8 -*-
"""【指标包唯一数据入口】—— 指标所需行情数据只能从这里取。

约束（详见 ARCHITECTURE.md）：
  * 本文件是指标包内**唯一**允许 `import price_store`（以及将来可能的外部源）的地方。
  * 指标实现文件（trend / oscillator / volatility 等）只能 `from .data import get_ohlcv`，
    不得自行读取数据库、文件或调用 akshare / 网络。
  * 取数结果统一为 pandas.DataFrame，索引为日期字符串（YYYY-MM-DD），列固定为
    open / high / low / close / volume，按日期升序排列，前段缺失保留 NaN。

技术面指标当前只需要 OHLCV；若后续需要成交额、换手率等，在本文件扩展即可，
指标实现文件无需改动。

已扩展（筹码分布用）：
  * `get_float_shares(code, date)`  流通股本（股）——来自 share_service 采集、price_store 落盘；
  * `get_turnover(...)`             换手率序列（小数 0~1）= 成交量(股) ÷ 流通股本；
  * `get_vwap(...)`                 当日成交均价（成交额 ÷ 成交量，缺失时回落 (H+L+C)/3）。
之所以要单独接股本：本地通达信日线没有流通股本（见 tdx_reader），换手率无从计算，
而筹码分布的衰减完全由换手率驱动。
"""
from __future__ import annotations

import dataclasses as dc
import datetime as dt
from functools import lru_cache

import numpy as np
import pandas as pd
import price_store

_MIN_ROWS = 2  # 少于此行数视为数据不足，技术指标无法计算
_OHLCV = ("open", "high", "low", "close", "volume")


def _norm_date(d) -> str | None:
    if d is None:
        return None
    if isinstance(d, dt.date):
        return d.strftime("%Y-%m-%d")
    return str(d)[:10]


@lru_cache(maxsize=256)
def _load(code: str, start: str | None, end: str | None, adjust: str) -> pd.DataFrame | None:
    """缓存层：同一 (code, 区间, 口径) 只解析一次。

    返回 None 表示无数据；否则返回索引为 date、列为 OHLCV 的 DataFrame。
    """
    rows = price_store.load_bars(code, adjust=adjust, start=start, end=end)
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df = df.rename(columns={"trade_date": "date"}).set_index("date").sort_index()
    # amount 一并带出：get_vwap（成交均价）需要它；get_ohlcv 仍只暴露 OHLCV 五列。
    cols = [c for c in ("open", "high", "low", "close", "volume", "amount") if c in df.columns]
    return df[cols]


def get_ohlcv(code: str, start: str | dt.date | None = None,
              end: str | dt.date | None = None, adjust: str = "qfq") -> pd.DataFrame:
    """获取某只股票的行情序列（指标包唯一取数函数）。

    参数：
      code    股票代码（如 "600000" / "920000"）
      start   起始日（含），缺省取全部
      end     结束日（含），缺省取全部
      adjust  复权口径，技术指标默认 "qfq"（前复权）；回测用 "hfq" 需调用方显式传入

    返回：
      DataFrame，索引为日期字符串，列 open/high/low/close/volume，升序。

    异常：
      RuntimeError  数据不足或无数据时抛出，由调用方（compute）转成接口错误。
    """
    df = _load(code, _norm_date(start), _norm_date(end), adjust)
    if df is None or len(df) < _MIN_ROWS:
        raise RuntimeError(f"行情数据不足: {code} (adjust={adjust})")
    return df[[c for c in _OHLCV if c in df.columns]]


def _raw(code: str, start=None, end=None, adjust: str = "qfq") -> pd.DataFrame:
    """带 amount 的完整行情（内部用），校验同 get_ohlcv。"""
    df = _load(code, _norm_date(start), _norm_date(end), adjust)
    if df is None or len(df) < _MIN_ROWS:
        raise RuntimeError(f"行情数据不足: {code} (adjust={adjust})")
    return df


def get_float_shares(code: str, date: str | None = None,
                     auto_sync: bool = True) -> float:
    """取 `date` 当日生效的**流通股本（股）**。

    先读库（`price_store.share_capital`，按生效日前向填充）；库里没有且 auto_sync
    为真时，走 `price_service.sync_share_capital` 现采一次并落盘；仍取不到则抛错。

    异常：RuntimeError 无股本（调用方应转成「缺流通股本」类错误）。
    """
    shares = price_store.float_shares_at(code, date)
    if shares:
        return float(shares)
    if auto_sync:
        # 延迟导入：price_service 会带出 akshare，不想让指标包对它形成硬依赖
        import price_service
        price_service.sync_share_capital(code)
        shares = price_store.float_shares_at(code, date)
        if shares:
            return float(shares)
    raise RuntimeError(f"缺少流通股本: {code}（腾讯行情未返回市值，稍后重试）")


def get_turnover(code: str, start=None, end=None, adjust: str = "qfq") -> pd.Series:
    """换手率序列（**小数**：0.0123 = 1.23%），索引同 get_ohlcv。

    口径：成交量(手) × 100 ÷ 流通股本(股)，并按 [0,1] 截断
    （极端行情 / 股本变动会让比值失真，>1 表示当天理论上换手一遍以上）。
    """
    df = _raw(code, start, end, adjust)
    shares = get_float_shares(code, str(df.index[-1])[:10])
    if not shares or shares <= 0:
        raise RuntimeError(f"流通股本无效: {code}")
    series = df["volume"].astype(float) * 100.0 / shares
    return series.clip(lower=0.0, upper=1.0)


def get_vwap(code: str, start=None, end=None, adjust: str = "qfq") -> pd.Series:
    """当日成交均价（元/股）：成交额 ÷ 成交量。

    成交额缺失或为 0 时回落到经典近似 (H+L+C)/3（筹码分布用它做三角形分布的峰值位）。
    """
    df = _raw(code, start, end, adjust)
    vol = df["volume"].astype(float) * 100.0          # 手 → 股
    if "amount" in df.columns:
        amount = pd.to_numeric(df["amount"], errors="coerce")
        price = (amount / vol).where(vol > 0)
    else:
        price = pd.Series(float("nan"), index=df.index)
    approx = (df["high"].astype(float) + df["low"].astype(float)
              + df["close"].astype(float)) / 3.0
    price = price.where(price > 0, approx)
    return price.fillna(approx)


# --------------------------------------------------------------------------- #
# 筹码分布矩阵（供「筹码类指标」复用，不重复计算）
# --------------------------------------------------------------------------- #
@dc.dataclass
class ChipFrames:
    """某只股票的筹码分布矩阵：**按日期 × 价格分箱**。

    与指标体系的「按日期的序列」不同，这里是二维的；指标文件按需把它压成序列
    （如获利比例 = 每帧中成本低于当日收盘的分箱占比之和）。
    """
    dates: list[str]        # 与 get_ohlcv 的日期同序（同一窗口）
    close: np.ndarray       # 当日收盘价
    centers: np.ndarray     # 分箱中心价格
    pct: np.ndarray         # (天数, 分箱数)，每行合计 100


def code_of(df: pd.DataFrame) -> str:
    """取 DataFrame 对应的股票代码（由 base.compute 挂在 df.attrs 上）。"""
    code = str(df.attrs.get("code") or "").strip()
    if not code:
        raise RuntimeError("取不到股票代码：指标只能在 compute() 调用链中使用")
    return code


@lru_cache(maxsize=8)
def _chip_frames(code: str, formula_id: str | None, days: int | None,
                 bins: int, adjust: str) -> ChipFrames:
    # 延迟导入：chip_formulas.core.data 反过来要 import 本模块（取行情 / 换手率），
    # 模块级互导会形成环；放在函数里，双方模块都已加载完毕，环自然断开。
    import chip_formulas
    res = chip_formulas.compute_matrix(code, formula_id=formula_id, days=days,
                                       bins=bins, adjust=adjust)
    return ChipFrames(dates=list(res.dates), close=res.close,
                      centers=res.centers, pct=res.pct)


def get_chip_frames(code: str, formula_id: str | None = None,
                    days: int | None = None, bins: int = 80,
                    adjust: str = "qfq") -> ChipFrames:
    """取筹码分布矩阵（带缓存）。**只读**：调用方不得修改返回的数组。

    缓存按 (code, formula_id, days, bins, adjust) 计；同一只票上叠加多个筹码类指标
    （获利比例 / 平均成本 / 集中度）只会真正算一次。
    """
    return _chip_frames(code, formula_id, days, int(bins), adjust)


def chip_rows_for(dates: list[str], frames: ChipFrames) -> np.ndarray:
    """把筹码矩阵按 `dates` 对齐：缺失的日期填 NaN 行。"""
    lookup = {d: i for i, d in enumerate(frames.dates)}
    out = np.full((len(dates), frames.pct.shape[1]), np.nan)
    for i, day in enumerate(dates):
        j = lookup.get(str(day)[:10])
        if j is not None:
            out[i] = frames.pct[j]
    return out


def chip_rows_of(df: pd.DataFrame, formula_id: str | None = None,
                 bins: int = 80) -> tuple[np.ndarray, ChipFrames]:
    """指标侧一步到位：取本票的筹码矩阵，并按 `df` 的日期对齐。

    返回 (rows, frames)；rows 形状 (len(df), 分箱数)，缺失日期为 NaN 行。
    三个筹码类指标（获利比例 / 平均成本 / 集中度）共用同一份缓存，只算一次。
    """
    frames = get_chip_frames(code_of(df), formula_id=formula_id, bins=bins)
    rows = chip_rows_for([str(d)[:10] for d in df.index], frames)
    return rows, frames


def clear_cache() -> None:
    """测试或切换数据源后清空取数缓存。"""
    _load.cache_clear()
    _chip_frames.cache_clear()
