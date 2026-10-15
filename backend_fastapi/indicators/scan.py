# -*- coding: utf-8 -*-
"""指标校验（思路与 `features/chip/formulas/core/registry.py` 一致，**形态不同**）。

## 为什么形态不同

两边都是**动态扫目录 + 导入即注册**，但关键差别在 **id 与文件名的关系**：

- 筹码公式：**文件名即 id**，其 `registry.scan()` 强制 `id == 文件名`，不符即剔除；
- 指标：**id 与文件名无关**（`macd.py` → `MACD`、`chip_cost.py` → `COST`），
  `__init__.py` 没有也不应强制对应关系 —— 那边是大写缩写，这边是语义名。

故本模块**刻意不复制**「id 必须等于文件名」这条规矩（套用会把 `MACD` 全判成非法）。
校验对象是 `base.REGISTRY` 里**已注册**的指标：动态 import 已在 `__init__.py` 完成，
这里只负责「注册完了之后声明与实现对不对得上」，不重复 import、也不重复枚举目录。

## 校验什么

1. **元数据**：`name` / `category` 非空、`panel` 在 `VALID_PANELS` 内、
   `params` 是 `ParamSpec` 列表且每个元素 `type` 合法；
2. **函数签名**（`base.signature_problems`）：首参必须是 `df`，其余参数都要有默认值，
   且与 `ParamSpec` 声明**一一对应**——这是最常见也最难在运行期发现的错；
3. **合成数据自检**：用构造的 OHLCV 真跑一次，校验返回结构。

## 与 `/api/indicators/diagnose` 的关系（**刻意共用一份实现**）

`indicators_routes.py` 早就有一个「指标诊断」端点，它也在用合成 OHLCV 跑一遍。
本模块与它**共用 `check_io()` 这一个函数**，而不是各写一套 —— 否则同一件事
（"这个指标在合成数据下能不能跑通"）有两个实现、两套合成数据、两处判定规则，
改一处忘另一处就会给出矛盾结论。路由层那个端点是**按需诊断、不剔除**；
这里是**启动时 LOAD 校验、剔除不通过**，两者定位不同但底层判据必须一致。

## 数据依赖型失败：自动跳过，不误杀

不是所有指标都能用构造数据冒烟：

- 筹码类（`COST` / `SCR` / `WIN`）要经 `data.get_chip_frames(code)` 取
  **流通股本**与筹码矩阵；
- `RPS` 要取**全市场**收盘面板（`rps_data.get_rps`）。

对它们跑构造数据只会得到"数据缺失"。所以这里**对所有指标都尝试**，
但异常信息命中 `DATA_DEP_MARKERS`（缺行情 / 缺流通股本 / 取不到代码 / 全市场……）
时判为「需外部数据，跳过」，**不算失败、不剔除**。

这比"让每个指标手写 `smoke=True`"好：后者要求写指标的人自己知道依赖什么，
漏标就等于没校验，多标则白跑一遍 —— 两类错都会发生。

## 未通过的指标会被剔除

与筹码层一致：校验不过的**移出 `REGISTRY`** —— 不出现在 `/api/indicators` 的清单里，
也无法被 `compute` 调用。运行期不再做冗余兜底（否则就是"启动时坏、点一下才知道"）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from core.paramspec import ParamSpec

from . import base
from .base import VALID_PANELS, REGISTRY

# 合成行情的长度：必须够长 —— EMA 的周期选项含 250，60 根 K 线会让它整条是NaN，
# 那样"跑通了"也验不出任何东西。
SMOKE_DAYS = 300
_VALID_PARAM_TYPES = ("int", "float", "bool", "choice")

# 数据依赖型失败的特征词：命中即判为「需外部数据，跳过」而非失败。
# 覆盖三类来源：缺行情 / 缺流通股本（筹码类）/ 全市场面板（RPS）。
# 名单来自 `indicators_routes.py` 原有的诊断实现，收敛到包内一处、两处共用。
DATA_DEP_MARKERS = ("行情数据不足", "缺少流通股本", "取不到股票代码", "无行情数据",
                    "price_store", "get_ohlcv", "akshare", "全市场", "NoData", "market",
                    "no such table")


def stub_df(days: int = SMOKE_DAYS) -> pd.DataFrame:
    """合成行情：温和上涨 + 正弦扰动，**确定性**（不用随机，否则会时好时坏）。"""
    n = int(days)
    i = np.arange(n)
    close = 10.0 + 0.02 * i + 0.3 * np.sin(i / 7.0)
    df = pd.DataFrame({
        "open": close - 0.05,
        "high": close + 0.12,
        "low": close - 0.12,
        "close": close,
        "volume": 100000.0 + 1000.0 * np.sin(i / 5.0),
    }, index=pd.date_range("2023-01-02", periods=n, freq="D"))
    # ⚠️ **刻意不挂 `df.attrs["code"]`**（`/diagnose` 的合成数据同样不挂，保持一致）。
    # 筹码类指标经 `data.code_of(df)` 反查代码、拿不到就抛「取不到股票代码」——
    # 该串命中 `DATA_DEP_MARKERS`，于是被判为「需外部数据，跳过」，**不误判为失败**。
    #
    # 一旦挂上 code，它们就会真去取数，而 `RPS` 取的是**全市场**收盘面板：
    # 实测冷缓存下单这一个指标就要 **13.6 秒**，会把后端启动拖慢一个数量级。
    # 而"合成K 线 + 真实全市场面板"本身是半真半假的组合，测出来的东西价值有限；
    # 筹码类与 RPS 的正确性由真实数据端到端验证覆盖，不是这里该负责的。
    return df


def _meta_problems(meta: base.IndicatorMeta) -> list[str]:
    """元数据校验，返回问题列表（空 = 通过）。"""
    problems: list[str] = []
    if not str(meta.name).strip():
        problems.append("name 不能为空")
    if not str(meta.category).strip():
        problems.append("category 不能为空")
    if meta.panel not in VALID_PANELS:
        problems.append(f"panel 非法：{meta.panel!r}（允许 {VALID_PANELS}）")
    if not isinstance(meta.params, list):
        problems.append(f"params 必须是列表，实际 {type(meta.params).__name__}")
        return problems
    for p in meta.params:
        if not isinstance(p, ParamSpec):
            problems.append(f"params 元素必须是 ParamSpec，实际 {type(p).__name__}")
            continue
        if not str(p.name).strip():
            problems.append("ParamSpec.name 不能为空")
        if p.type not in _VALID_PARAM_TYPES:
            problems.append(f"ParamSpec.type 非法：{p.type!r}")
    return problems


def check_io(meta: "base.IndicatorMeta", df: pd.DataFrame | None = None) -> tuple[bool, str]:
    """合成数据自检：真跑一次指标并校验返回结构。返回 `(ok, note)`。

    **公共实现**：`/api/indicators/diagnose`（按需诊断）也转调本函数，
    保证两处对"能不能跑通"的判据完全一致。

    判据直接复用 `base._normalize_series` —— 它就是 `compute()` 出口用的那条归一化，
    于是自检验的就是**真实计算路径的产物**，而不是另写一套长度/类型检查。

    `ok=True` 且 note 标为"跳过"表示该指标依赖行情/全市场数据，合成数据跑不动属正常，
    **不算失败**（依据 `DATA_DEP_MARKERS`）。
    """
    if meta is None:
        return False, "未注册"
    frame = stub_df() if df is None else df
    defaults = {p.name: p.default for p in meta.params}
    try:
        result = meta.func(frame, **defaults)
    except Exception as exc:                     # noqa: BLE001 - 自检要把失败带出去
        msg = f"{type(exc).__name__}: {exc}"
        if any(m in msg for m in DATA_DEP_MARKERS):
            return True, "需行情/全市场数据，跳过校验"
        return False, msg
    if not isinstance(result, dict):
        return False, f"返回值必须是 dict，实际 {type(result).__name__}"
    series = base._normalize_series(result, frame)
    if not series:
        return False, "series 为空（或只有兼容格式的 lines 且为空）"
    for s in series:
        if not str(s.get("name") or "").strip():
            return False, "存在没有 name 的序列"
    return True, "OK"


def scan() -> dict:
    """校验全部已注册指标；不通过的移出 `REGISTRY`。

    返回 `{indicator_id: {"status": "ok"|"error"|"skipped", "error": ...}}`：

    - `ok`       元数据 + 签名 + 合成数据自检全过；
    - `skipped`  前两项过了，自检因**依赖行情/全市场数据**而跳过（不算失败）；
    - `error`    有真问题，**已从 `REGISTRY` 剔除**（不进 `/api/indicators` 清单，
                 也无法被 `compute` 调用）。

    注意顺序：先剔元数据/签名问题，再跑自检 —— 声明与实现对不上时，跑了也没意义。
    """
    report: dict[str, dict] = {}
    for iid, meta in list(REGISTRY.items()):
        problems = _meta_problems(meta) + base.signature_problems(meta)
        if problems:
            REGISTRY.pop(iid, None)
            report[iid] = {"status": "error", "checked": "meta+signature",
                           "error": "；".join(problems)}
            continue
        ok, note = check_io(meta)
        if not ok:
            REGISTRY.pop(iid, None)
            report[iid] = {"status": "error", "checked": "io", "error": note}
            continue
        report[iid] = {"status": "ok" if note == "OK" else "skipped",
                       "checked": "io", "error": None if note == "OK" else note}
    return report


def validation_report() -> dict:
    """校验状态汇总（供 /health 与排障展示）。"""
    results = scan()
    ok = [v for v in results.values() if v.get("status") == "ok"]
    skipped = [v for v in results.values() if v.get("status") == "skipped"]
    bad = [v for v in results.values() if v.get("status") == "error"]
    return {
        "total": len(results),
        "valid": len(ok) + len(skipped),     # 无真问题即算可用
        "checked": len(ok),                  # 其中真用合成数据跑通的
        "skipped": len(skipped),            # 因依赖外部数据而跳过的
        "invalid": len(bad),
        "indicators": results,
    }
