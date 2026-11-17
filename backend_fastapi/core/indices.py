# -*- coding: utf-8 -*-
"""指数清单与判定 —— 指数相关口径的**唯一实现处**。

## 为什么要有这一个文件

指数**不是股票**：代码带市场前缀（`sh000001` / `sz399001`），落在独立表
`index_bars`（独立库 `bars/indices.db`，见 `core/price_store.py`）。
这条**存储约定**决定了「哪些 code 是指数」必须只有一份判定，否则会出现
「一处当个股取（取不到 → 空图）、一处当指数取（有数据）」的静默分叉 ——
两处都不报错，只是图不一样。

消费方（都从这里取，不各写一份）：

| 消费方 | 用法 |
|---|---|
| `core/kline_service.py` | 取数分流：指数走 `index_bars`（无复权），个股走 `daily_bars` |
| `features/history/routes.py` | 路由校验：`/api/history/index/{code}` 只收指数代码 |
| `features/history/service.py` | 指数详情（K 线 + 名称 + 本地跨度） |
| 前端搜索联想 | 通过 `GET /api/history/indices` 拿清单（**前端不硬编码清单**） |

## 前端为什么只做「形态判定」

前端（C#）没有 Python 运行时，只能按形态判断「这是不是指数」
（`^[a-z]{2}[0-9]{6}$` —— 这是上面存储约定的镜像）。**名称与清单一律
从后端接口取**，不在 C# 里复制一份清单 —— 两份清单迟早漂移
（后端加了新指数、前端还在旧列表 → 用户输入代码却搜不到）。
"""
from __future__ import annotations

import re

# 指数代码形态：`市场前缀(2 位小写字母) + 6 位数字`。
# 个股代码是**裸 6 位数字**（无前缀）→ 两者天然不冲突。
_INDEX_CODE_RE = re.compile(r"^[a-z]{2}[0-9]{6}$")

# 常用指数清单（code → 中文名）。**清单本身不是数据源** ——
# 真实可用的指数以本地 `index_bars` 里的为准（见 `price_store.index_codes()`）；
# 这里只是给「友好名称」与「搜索联想」用。
COMMON_INDICES: tuple[tuple[str, str], ...] = (
    ("sh000001", "上证指数"),
    ("sz399001", "深证成指"),
    ("sh000300", "沪深300"),
    ("sh000016", "上证50"),
    ("sh000905", "中证500"),
    ("sh000852", "中证1000"),
    ("sh000985", "中证全指"),
    ("sz399006", "创业板指"),
    ("sh000688", "科创50"),
    ("bj899050", "北证50"),
    ("sz399330", "深证100"),
)

_NAMES = dict(COMMON_INDICES)


def normalize_code(code: object) -> str:
    """指数代码归一：去空白 + 转小写（`SH000001` → `sh000001`）。"""
    return str(code or "").strip().lower()


def is_index_code(code: object) -> bool:
    """这个 code 是指数吗？（形态判定，不查库）"""
    return bool(_INDEX_CODE_RE.match(normalize_code(code)))


def index_name(code: object) -> str:
    """指数中文名；清单里没有就返回空串（**不编造**）。"""
    return _NAMES.get(normalize_code(code), "")


def list_indices() -> list[dict[str, str]]:
    """常用指数清单（前端搜索联想用）：`[{code, name}, ...]`。"""
    return [{"code": c, "name": n} for c, n in COMMON_INDICES]
