# -*- coding: utf-8 -*-
"""指标计算上下文（CalcContext）—— 把「按代码 / 周期再取数」的需求**显式化**。

## 为什么有这个东西

原先 `base.compute` 把 code / period 挂在 `df.attrs` 上，指标函数「只收 df」却能
从 df 里挖出股票代码：

    df.attrs["code"] = code          # 指标函数只收 df，拿不到代码
    df.attrs["period"] = period      # 横截面类指标要按同一周期取全市场数据

契约是**假的**：单看函数签名（`def my_indicator(df, **params)`）无法得知它还需要
code / period；新人照文档写指标，运行期才发现缺东西；写单元测试时也必须伪造
`df.attrs` 才能跑起来。

现在改为**显式注入**：`base.compute` 构造一个 `CalcContext`，经关键字参数 `ctx=`
传给指标函数；需要它的指标在**签名里写出来**：

    def chip_cost(df: pd.DataFrame, *, ctx=None, formula: str = "tri_decay") -> dict:
        rows, frames = chip_rows_of(df, formula_id=formula, code=code_of(ctx))

## 约定

- `ctx` 是**注入参数**：由 `compute()` 传入，用户调不了（不进 ParamSpec），
  但**必须在签名里写出来** —— 依赖什么就写什么，契约一眼可见；
- `ctx=None` 表示「没有上下文」（合成数据自检 / 直接调函数）：需要 code 的指标
  据此抛「取不到股票代码」，被 `scan` 判为「需外部数据，跳过」，不算失败；
- 本模块**不依赖包内任何其它模块**（`base` / `data` 都从它这里取类型），
  故不会与 `indicators.data ↔ chipformulas.core.data` 的循环依赖撞车。
"""
from __future__ import annotations

import dataclasses as dc


@dc.dataclass(frozen=True)
class CalcContext:
    """一次指标计算的上下文（只读）。

    字段：
      code    股票代码（如 "600000"）。筹码类指标要按代码取筹码分布矩阵
              （`data.get_chip_frames(code)`），没有它就算不出筹码类指标。
      period  K 线周期（day / week / month）。横截面类指标（如 RPS）要按**同一**
              周期取全市场数据，否则周线蜡烛会配上日线的 RPS。
    """

    code: str = ""
    period: str = "day"
