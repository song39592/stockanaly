# -*- coding: utf-8 -*-
"""指标**数值基准**（golden values）—— 防回归的数值自检。

## 为什么需要这一层

`scan.scan()` 只验三件事：元数据完整、签名对得上、**能不能跑通**。
它**不验算出来的数对不对** —— 于是这条回归路径完全没人管：

    改一处共享计算内核（如成本线口径）
        → 若干指标的数值全变
        → scan 照样全绿（能跑通、结构没变）
        → 没人发现，直到实盘出现假信号

这正是「加指标时顺手改了别的东西」最常见的翻车方式：**改一个、坏一片，且无声**。

## 基准是什么

把每个指标在**确定性合成行情**（`scan.stub_df()`，300 根、正弦扰动、无随机）
上的输出拍平成一份快照 `_golden.json`；测试时重算并逐项比对：

- 每个序列存 **全量数值的 sha1**（任何一位数变了都会被发现）；
- 另存一份**抽样**（每 15 根取一个）用于**人眼定位**差异出在哪一段。

之所以不直接存全量数值：300 根 × 30 条序列的完整数组会让基准文件膨胀到 100KB 级，
而 git 里每天都要看它一眼。哈希保证「不漏」，抽样保证「能查」。

## 什么时候必须重新生成基准

- 新增 / 删除指标；
- 改了任何指标的**计算逻辑**（哪怕只是改了参数默认值）；
- 改了 `stub_df()` 的合成数据本身。

    python -m indicators.golden --update

## 与 `scan.py` 的分工

| | scan.py | golden.py |
|---|---|---|
| 验什么 | 能不能跑通、结构对不对 | **算出来的数对不对** |
| 失败后果 | 指标被移出 REGISTRY（不可用） | 测试失败（数值回归） |
| 是否改状态 | 是（剔除指标） | 否（只读） |

两者都跑、都用同一份 `stub_df()`，但**互不替代**：前者抓「写错了」，后者抓「算错了」。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

from . import base
from .scan import DATA_DEP_MARKERS, stub_df

# 基准快照文件（与本模块同目录，纳入版本控制）。
GOLDEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_golden.json")

# 抽样步长：每 N 根取一个值，用于差异定位。
SAMPLE_STRIDE = 15

# 快照结构版本：改动快照**格式**时 +1（老基准会被判为不兼容，要求重新生成）。
SNAPSHOT_VERSION = 1


def _round(v):
    """数值归一：float → round(10 位)；None / NaN / Inf → None。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:                                   # NaN
        return None
    if f in (float("inf"), float("-inf")):
        return None
    return round(f, 10)


def _digest(values) -> str:
    """一串数值的指纹（sha1 前 16 位，够用且短）。"""
    blob = json.dumps(values, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def _sample(values) -> list:
    """按固定步长抽样（用于人眼定位差异）。"""
    return [values[i] for i in range(0, len(values), SAMPLE_STRIDE)]


def snapshot() -> dict:
    """现算一遍全部指标，返回可序列化的快照结构。"""
    df = stub_df()
    out = {
        "version": SNAPSHOT_VERSION,
        # 合成行情本身的指纹：stub_df 一改，这里的 sha1 立刻变，
        # 提醒「基准要重新生成」，而不是让所有指标的数值莫名漂移。
        "stub": {
            "days": int(len(df)),
            "sha1": _digest([_round(v) for v in df["close"].tolist()]),
        },
        "indicators": {},
    }
    for iid, meta in sorted(base.REGISTRY.items()):
        rec: dict = {"panel": meta.panel}
        defaults = {p.name: p.default for p in meta.params}
        try:
            result = meta.func(df, **defaults)
        except Exception as exc:                   # noqa: BLE001 - 自检要把失败带出去
            msg = f"{type(exc).__name__}: {exc}"
            if any(m in msg for m in DATA_DEP_MARKERS):
                # 依赖行情 / 全市场数据的指标：合成数据跑不动属正常，不算失败。
                rec["status"] = "skipped"
                rec["note"] = msg
            else:
                rec["status"] = "error"
                rec["error"] = msg
            out["indicators"][iid] = rec
            continue
        try:
            series = base._normalize_series(result, df)
        except Exception as exc:                   # noqa: BLE001
            rec["status"] = "error"
            rec["error"] = f"序列归一化失败：{type(exc).__name__}: {exc}"
            out["indicators"][iid] = rec
            continue
        rec["status"] = "ok"
        rec["series"] = []
        for s in series:
            vals = [_round(v) for v in (s.get("data") or [])]
            rec["series"].append({
                "name": s.get("name"),
                "kind": s.get("kind"),
                "n": len(vals),
                "sha1": _digest(vals),
                "sample": _sample(vals),
            })
        out["indicators"][iid] = rec
    return out


def load(path: str | None = None) -> dict:
    """读基准快照；文件不存在时抛错（提示先 --update）。"""
    p = path or GOLDEN_FILE
    if not os.path.exists(p):
        raise FileNotFoundError(
            f"数值基准文件不存在: {p}\n"
            f"请先运行：python -m indicators.golden --update")
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def save(snap: dict, path: str | None = None) -> str:
    """写基准快照，返回文件路径。"""
    p = path or GOLDEN_FILE
    with open(p, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")
    return p


def _diff_series(iid: str, sname, e: dict, a: dict) -> list[str]:
    out: list[str] = []
    tag = f"指标 {iid} / 序列 {sname!r}"
    if e.get("n") != a.get("n"):
        out.append(f"{tag}：长度 {e.get('n')} -> {a.get('n')}")
    if e.get("sha1") != a.get("sha1"):
        out.append(f"{tag}：数值指纹变化 {e.get('sha1')} -> {a.get('sha1')}")
        es, as_ = e.get("sample") or [], a.get("sample") or []
        bad = [(i, x, y) for i, (x, y) in enumerate(zip(es, as_)) if x != y]
        if bad:
            detail = "; ".join(f"第{i}个抽样点 {x} -> {y}" for i, x, y in bad[:5])
            out.append(f"{tag}：抽样差异 {len(bad)} 处 —— {detail}")
        else:
            out.append(f"{tag}：差异出现在抽样点之外（数值确实变了，需 --update 查看新值）")
    return out


def _diff_indicator(iid: str, e: dict, a: dict) -> list[str]:
    out: list[str] = []
    if e.get("status") != a.get("status"):
        out.append(f"指标 {iid}：状态 {e.get('status')} -> {a.get('status')}"
                   f"（{a.get('error') or a.get('note') or ''}）")
        return out
    if e.get("panel") != a.get("panel"):
        out.append(f"指标 {iid}：panel {e.get('panel')} -> {a.get('panel')}")
    if e.get("status") != "ok":
        return out
    es = {s.get("name"): s for s in (e.get("series") or [])}
    as_ = {s.get("name"): s for s in (a.get("series") or [])}
    for nm in sorted(set(es) - set(as_), key=lambda x: str(x)):
        out.append(f"指标 {iid}：序列 {nm!r} 不见了")
    for nm in sorted(set(as_) - set(es), key=lambda x: str(x)):
        out.append(f"指标 {iid}：新增序列 {nm!r}（基准未登记）")
    for nm in sorted(set(es) & set(as_), key=lambda x: str(x)):
        out.extend(_diff_series(iid, nm, es[nm], as_[nm]))
    return out


def diff(expected: dict, actual: dict) -> list[str]:
    """比对基准与现算结果，返回差异描述列表（空 = 完全一致）。"""
    problems: list[str] = []
    if expected.get("version") != actual.get("version"):
        problems.append(f"基准结构版本不同：{expected.get('version')} -> {actual.get('version')}")
    if expected.get("stub", {}).get("sha1") != actual.get("stub", {}).get("sha1"):
        problems.append("合成行情指纹变了（stub_df 被改动）→ 基准需重新生成")
    e_ind = expected.get("indicators", {}) or {}
    a_ind = actual.get("indicators", {}) or {}
    for iid in sorted(set(e_ind) - set(a_ind)):
        problems.append(f"指标 {iid} 不见了（校验失败被剔除，或已被删除）")
    for iid in sorted(set(a_ind) - set(e_ind)):
        problems.append(f"新增指标 {iid}：未登记数值基准 → 需重新生成基准")
    for iid in sorted(set(e_ind) & set(a_ind)):
        problems.extend(_diff_indicator(iid, e_ind[iid], a_ind[iid]))
    return problems


def main(argv: list[str] | None = None) -> int:
    """命令行入口：`python -m indicators.golden [--update]`。"""
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--update" in argv:
        snap = snapshot()
        path = save(snap)
        n_ok = sum(1 for v in snap["indicators"].values() if v.get("status") == "ok")
        n_skip = sum(1 for v in snap["indicators"].values() if v.get("status") == "skipped")
        print(f"已更新数值基准：{path}")
        print(f"  指标 {len(snap['indicators'])} 个（可算 {n_ok} / 跳过 {n_skip}）")
        return 0

    try:
        expected = load()
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    problems = diff(expected, snapshot())
    if problems:
        print(f"=== 指标数值与基准不一致（{len(problems)} 处）===", file=sys.stderr)
        for p in problems:
            print("  - " + p, file=sys.stderr)
        print("\n若这是**有意**的算法变更，请运行：python -m indicators.golden --update",
              file=sys.stderr)
        return 1
    print("=== 指标数值与基准完全一致 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
