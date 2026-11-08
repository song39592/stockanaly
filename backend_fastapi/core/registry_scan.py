# -*- coding: utf-8 -*-
"""「扫目录 → 逐个导入校验 → 出报告」的**公共骨架**（指标 / 策略 / 筹码公式三处共用）。

## 为什么抽这一层

项目里有三套「动态注册」机制：

| 包 | 注册表 | id 与文件名 | 校验缓存 |
|---|---|---|---|
| `indicators/` | `base.REGISTRY` | **无关**（`macd.py` → `MACD`） | 无 |
| `strategies/` | `core.base.REGISTRY` | **必须相同** | mtime + JSON |
| `chipformulas/` | `core.base.FORMULAS` | **必须相同** | 无 |

三者的**形态**确实不同（强行统一会误伤 —— 例如把「id 必须等于文件名」套到指标上，
`MACD` 会被全判成非法），所以**判据不合并**；但**骨架完全一样**：

    扫目录 → 逐个强制重新导入 → 校验「恰好注册 1 个且 id 合法」→ 出同形状的报告

这段骨架以前各写一遍，于是「再加一种可注册的东西」要照抄一遍再改一遍，
抄错的地方（缓存键怎么算、id 怎么比、报告字段叫什么）**不会报错**，
只会给出前后矛盾的报告 —— 这类「静默不一致」正是最难查的一类 bug。

本模块只提供**机械动作**（遍历 / 强制导入 / 单注册校验 / 报告组装 / mtime 缓存），
**具体判据留给各包**（以回调传入）—— 形态差异因此得以保留，重复得以消除。

## 边界

- 只做机械动作，不含任何「什么是合法的指标 / 策略 / 公式」的判据；
- 不替调用方吞异常：导入失败等由各包的 validate 回调自行捕获并写进报告，
  本模块只负责把「已经登记的」和「已经导入的」对齐。
"""
from __future__ import annotations

import importlib
import json
import os
import pkgutil
import sys
from typing import Callable, Iterable

# 报告里「校验通过」的取值（各包统一用这个字符串，避免一处 ok 一处 passed）。
STATUS_OK = "ok"
STATUS_ERROR = "error"


def iter_module_names(directory: str, exclude: Iterable[str] = ()) -> list[str]:
    """列出目录下待扫描的**模块名**（不含子包与被排除项）。

    保持 `pkgutil` 的原始顺序（不额外排序）—— 报告与缓存文件的条目顺序因此稳定，
    不会因为排序规则变化而产生整文件 diff。
    """
    excl = set(exclude or ())
    names: list[str] = []
    for _finder, name, ispkg in pkgutil.iter_modules([directory]):
        if ispkg or name in excl:
            continue
        names.append(name)
    return names


def force_import(package: str, mod_name: str, registry: dict) -> set[str]:
    """强制重新执行某模块，返回**本次新注册**的 id 集合。

    流程：先清掉该模块上一次登记的条目 → 从 `sys.modules` 移除 → 重新 import。
    「新注册」= 重新导入前后注册表 key 的差集，所以「重复导入不会把自己算成新的」。

    导入异常**原样抛出**（由调用方捕获并写进报告）—— 这里吞掉会让「文件写错了」
    变成「该文件没注册」，两种完全不同的错误混成一种。
    """
    full = f"{package}.{mod_name}"
    if full in sys.modules:
        for rid in [k for k, v in registry.items()
                    if getattr(getattr(v, "func", None), "__module__", None) == full]:
            registry.pop(rid, None)
        del sys.modules[full]
    before = set(registry.keys())
    importlib.import_module(full)
    return set(registry.keys()) - before


def single_registration(new_ids, registry: dict, expected_id: str, *,
                         decorator: str, kind: str, id_label: str
                         ) -> tuple[str | None, str | None]:
    """校验「恰好注册 1 个，且 id 与期望值一致」，返回 `(id, error)`。

    - 不合格时**顺手把已登记的条目从注册表里摘掉**（校验不过的东西不许留在可选列表里）；
    - `error` 非空即表示不合格，`id` 为 None。

    文案里的 `decorator` / `kind` / `id_label` 由调用方给（三套机制的措辞不同：
    策略说「策略」、筹码公式说「公式」），**保持各包原有措辞不变** ——
    这些字符串会出现在用户看到的错误提示里，改动等于改产品文案。
    """
    ids = list(new_ids)
    if len(ids) != 1:
        for rid in ids:
            registry.pop(rid, None)
        return None, f"必须用 @{decorator} 恰好注册 1 个{kind}（实际 {len(ids)} 个）"
    rid = ids[0]
    if rid != expected_id:
        registry.pop(rid, None)
        return None, f"{id_label} id('{rid}') 必须等于文件名('{expected_id}')"
    return rid, None


class ModuleCache:
    """「文件名 + mtime」校验结果缓存（JSON 文件）。

    只服务「改了就重校验、没改就跳过重校验」这一种诉求：
    策略的冒烟测试要跑假行情回测，有缓存时只有改动文件才付这个代价。

    ⚠️ 缓存**只用于加速**，不用于判对错：命中缓存的条目仍会重新导入并复检
    「注册数量 + id 是否等于文件名」（见各包的 `_light_check`），
    所以缓存损坏 / 过期最坏结果是「多跑一次校验」，不会让坏文件蒙混过关。
    """

    def __init__(self, path: str):
        self.path = path

    def load(self) -> dict:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def save(self, data: dict) -> None:
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


def scan_modules(*, package: str, directory: str, exclude: Iterable[str],
                 registry: dict,
                 validate: Callable, light_check: Callable | None = None,
                 cache: ModuleCache | None = None, force: bool = False) -> dict:
    """扫目录 → 逐个校验 → 出 `{文件名: 结果}` 报告。

    参数：
      package     模块所在包名空间（如 `"strategies"`），用于 `force_import`。
      directory   存放待扫描 .py 的目录。
      exclude     排除的模块名（包入口、框架子目录等）。
      registry    注册表（被校验不过的条目会从中摘掉）。
      validate    完整校验回调 `(mod_name, filename) -> 结果dict`。
      light_check 轻量复检回调 `(mod_name, filename, cached) -> 结果dict`；
                  给了且命中缓存时用它替代完整校验（省掉冒烟测试）。
      cache       mtime 缓存（`ModuleCache`）；给了才会启用命中缓存的短路。
      force       True 时忽略缓存，全部走完整校验。

    报告里的字段由 `validate` / `light_check` 决定（本函数只负责遍历与缓存编排），
    这样各包可以保留自己的报告形状（策略带 `strategy_id`、公式带 `formula_id`）。
    """
    cache_data = cache.load() if cache is not None else {}
    results: dict[str, dict] = {}
    seen: set[str] = set()

    for name in iter_module_names(directory, exclude):
        filename = name + ".py"
        seen.add(filename)
        mtime = None
        try:
            mtime = os.path.getmtime(os.path.join(directory, filename))
        except Exception:
            pass

        cached = cache_data.get(filename)
        cached_ok = (cached is not None and cached.get("status") == STATUS_OK
                     and cached.get("mtime") == mtime)

        if (not force) and cached_ok and light_check is not None:
            rec = light_check(name, filename, cached)
        else:
            rec = validate(name, filename)
        results[filename] = rec
        if cache is not None:
            cache_data[filename] = rec

    if cache is not None:
        for fn in list(cache_data.keys()):        # 清理已删除文件的缓存
            if fn not in seen:
                cache_data.pop(fn, None)
        cache.save(cache_data)

    return results
