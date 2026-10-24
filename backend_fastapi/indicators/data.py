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

import kline_service
import numpy as np
import pandas as pd
import price_store          # 仅用于读股本（share_capital），K 线本身走 kline_service
from periods import normalize

_MIN_ROWS = 2  # 少于此行数视为数据不足，技术指标无法计算
_OHLCV = ("open", "high", "low", "close", "volume")


def _norm_date(d) -> str | None:
    if d is None:
        return None
    if isinstance(d, dt.date):
        return d.strftime("%Y-%m-%d")
    return str(d)[:10]


@lru_cache(maxsize=256)
def _load(code: str, start: str | None, end: str | None, adjust: str,
          period: str = "day") -> pd.DataFrame | None:
    """缓存层：同一 (code, 区间, 口径, 周期) 只解析一次。

    ⚠️ 取数与周期合样**不在本文件做**——统一走 `kline_service.get_bars`，
    保证「用户看到的 K 线」与「指标计算的输入」是同一个序列（详见 kline_service 文档）。
    本文件的缓存只是为了避免重复裁剪列。

    返回 None 表示无数据；否则返回索引为 date、列为 OHLCV 的 DataFrame。
    """
    df = kline_service.get_bars(code, start=start, end=end, adjust=adjust,
                                period=period)
    if df is None or df.empty:
        return None
    # amount 一并带出：get_vwap（成交均价）需要它；get_ohlcv 仍只暴露 OHLCV 五列。
    cols = [c for c in ("open", "high", "low", "close", "volume", "amount") if c in df.columns]
    return df[cols]


def get_ohlcv(code: str, start: str | dt.date | None = None,
              end: str | dt.date | None = None, adjust: str = "qfq",
              period: str = "day") -> pd.DataFrame:
    """获取某只股票的行情序列（指标包唯一取数函数）。

    参数：
      code    股票代码（如 "600000" / "920000"）
      start   起始日（含），缺省取全部
      end     结束日（含），缺省取全部
      adjust  复权口径，技术指标默认 "qfq"（前复权）；回测用 "hfq" 需调用方显式传入
      period  K 线周期 day / week / month（周线 / 月线读取时现算，不落库）

    ⚠️ 周期必须与 K 线展示的周期一致：周线蜡烛要配周线 MA（MA5 = 5 周），
    拿日线指标去对齐周线日期是错的。

    返回：
      DataFrame，索引为日期字符串，列 open/high/low/close/volume，升序。

    异常：
      RuntimeError  数据不足或无数据时抛出，由调用方（compute）转成接口错误。
    """
    df = _load(code, _norm_date(start), _norm_date(end), adjust, normalize(period))
    if df is None or len(df) < _MIN_ROWS:
        raise RuntimeError(f"行情数据不足: {code} (adjust={adjust})")
    return df[[c for c in _OHLCV if c in df.columns]]


def _raw(code: str, start=None, end=None, adjust: str = "qfq",
         period: str = "day") -> pd.DataFrame:
    """带 amount 的完整行情（内部用），校验同 get_ohlcv。"""
    df = _load(code, _norm_date(start), _norm_date(end), adjust, normalize(period))
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


def get_turnover(code: str, start=None, end=None, adjust: str = "qfq",
                 period: str = "day") -> pd.Series:
    """换手率序列（**小数**：0.0123 = 1.23%），索引同 get_ohlcv。

    口径：成交量(手) × 100 ÷ 流通股本(股)，并按 [0,1] 截断
    （极端行情 / 股本变动会让比值失真，>1 表示当天理论上换手一遍以上）。
    周线 / 月线的 volume 是整周期合计，因此得到的就是**周期换手率**——
    与「周期成交量 ÷ 流通股本」一致，不需额外处理。
    """
    df = _raw(code, start, end, adjust, period=period)
    shares = get_float_shares(code, str(df.index[-1])[:10])
    if not shares or shares <= 0:
        raise RuntimeError(f"流通股本无效: {code}")
    series = df["volume"].astype(float) * 100.0 / shares
    return series.clip(lower=0.0, upper=1.0)


def get_vwap(code: str, start=None, end=None, adjust: str = "qfq",
             period: str = "day") -> pd.Series:
    """当日（或当期）成交均价（元/股）：成交额 ÷ 成交量。

    成交额缺失或为 0 时回落到经典近似 (H+L+C)/3（筹码分布用它做三角形分布的峰值位）。
    """
    df = _raw(code, start, end, adjust, period=period)
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
    gray: np.ndarray | None = None   # 锁仓/折旧分布（与 pct 同尺度，分母=全部筹码100%）；三态公式(如 ema_lock)非空，其余为 None


def code_of(df: pd.DataFrame) -> str:
    """取 DataFrame 对应的股票代码（由 base.compute 挂在 df.attrs 上）。"""
    code = str(df.attrs.get("code") or "").strip()
    if not code:
        raise RuntimeError("取不到股票代码：指标只能在 compute() 调用链中使用")
    return code


def _chip_frames(code: str, formula_id: str | None, days: int | None,
                 bins: int, adjust: str) -> ChipFrames:
    # 延迟导入：chipformulas.core.data 反过来要 import 本模块（取行情 /
    # 换手率），模块级互导会形成环；放在函数里，双方模块都已加载完毕，环自然断开。
    #
    # ⚠️ 本函数**不再自带缓存**（原来挂着 lru_cache(maxsize=8)）：筹码矩阵的缓存
    # 已下沉到 `compute_matrix` —— 它是矩阵的唯一入口，右侧筹码峰窗口也走它。
    # 缓存只挂在指标侧时，「开着 COST/SCR/WIN 三个指标 + 看右侧筹码峰」会把同一只
    # 票的矩阵**独立算两遍**（实测 tri_decay 250 天 × 80 箱的循环不便宜）。
    # 两层缓存还会让同一份矩阵在内存里存两份。
    import chipformulas as chip_formulas
    res = chip_formulas.compute_matrix(code, formula_id=formula_id, days=days,
                                       bins=bins, adjust=adjust)
    return ChipFrames(dates=list(res.dates), close=res.close,
                      centers=res.centers, pct=res.pct, gray=res.gray)


def get_chip_frames(code: str, formula_id: str | None = None,
                    days: int | None = None, bins: int = 80,
                    adjust: str = "qfq") -> ChipFrames:
    """取筹码分布矩阵（带缓存）。**只读**：调用方不得修改返回的数组。

    缓存键是 (code, formula_id, days, bins, adjust)，但**缓存本体在
    `chip_formulas.compute_matrix` 里**（筹码矩阵的唯一入口），本函数只做
    参数规范化与 `ChipFrames` 包装。故同一只票上叠加多个筹码类指标
    （获利比例 / 平均成本 / 集中度）只会真正算一次，右侧筹码峰窗口也共用这一份。
    """
    return _chip_frames(code, formula_id, days, int(bins), adjust)


def chip_rows_for(dates: list[str], frames: ChipFrames, field: str = "pct") -> np.ndarray:
    """把筹码矩阵按 `dates` 对齐：缺失的日期填 NaN 行。

    `field` 指定取哪个通道（默认 "pct" 全部筹码；"gray"/"red" 取三态叠层分布）。
    """
    mat = getattr(frames, field, None)
    nbin = frames.pct.shape[1]
    if mat is None:
        return np.full((len(dates), nbin), np.nan)
    out = np.full((len(dates), mat.shape[1]), np.nan)
    lookup = {d: i for i, d in enumerate(frames.dates)}
    for i, day in enumerate(dates):
        j = lookup.get(str(day)[:10])
        if j is not None:
            out[i] = mat[j]
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


# --------------------------------------------------------------------------- #
# RPS 横截面（全市场收盘价面板）已抽到 indicators/rps_data.py：
#   该面板的构建 / 复权 / 缓存是 RPS 相对强度指标的中间变量，放在通用 data 入口
#   里既模糊分层也让 data.py 膨胀；现由 rps_data 自行 import price_store。
# 指标层请 `from .rps_data import get_rps, warm, period_of`。
# --------------------------------------------------------------------------- #


def clear_cache() -> None:
    """测试或切换数据源后清空取数缓存。"""
    _load.cache_clear()
    # 筹码矩阵缓存已下沉到 chip_formulas.compute_matrix（唯一入口），这里转调；
    # 本模块不再单独持有缓存，故没有 _chip_frames.cache_clear() 可调。
    import chipformulas as chip_formulas
    chip_formulas.clear_matrix_cache()
    from .rps_data import clear_market_panel
    clear_market_panel()
