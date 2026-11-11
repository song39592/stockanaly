# -*- coding: utf-8 -*-
"""依赖完整性检查 —— **依赖校验的单一来源**。

`requirements.txt` 是依赖的**唯一真相**；「装齐了没有」也必须由它推导，
不能另抄一份 import 清单。

## 为什么抽成这个文件（2026-11-11 的教训）

过去「装齐没有」由**两份手抄的 import 清单**判断：
launcher 的 `CheckEnv()` 与 `install_env.ps1` 的 [3/4] 校验。
两份**都漏了 `pypinyin`**（11-05 才加进 requirements.txt，venv 却是 9-15 建的），
于是缺它时启动器判定「环境完整」→ 永远不触发安装 →
`_to_pinyin()` 静默降级返回空串 → 股票名称的**拼音搜索整片失效**，
而界面、后端、健康检查**全都正常**，没有任何一处会报错。

手抄的清单 = 影子，迟早会缺一块；真相只能有一份。

## 检查两件事

1. **装没装**：`importlib.metadata.distribution(<PyPI 包名>)`。

   ⚠️ **必须用 PyPI 包名，不能用 import 名**：
   `beautifulsoup4`→`bs4`、`python-dotenv`→`dotenv`、`python-multipart`→`multipart`
   三处「包名 ≠ import 名」，按 import 名查会把**已装的包误报成缺失**。

2. **版本符不符**：装的是 `pandas 3.0.5` 而声明的是 `pandas>=2.2,<3.0` —— 这类漂移
   会让「装齐了」的结论不可信（实测某台机器的 venv 里 pandas 越界到 3.0.5，
   而 pip install -r 之外没人会发现）。

   ⚠️ **不引入 `packaging`**（pip 用的是自己 vendored 的副本，实测**两个环境都没装**）——
   为一个辅助功能新增依赖，等于自己制造「装了 requirements 却用不了」的同类坑。
   这里手写**保守**比较：只认纯数字版本段 + `>= <= > < == !=`；
   `~=` / 通配 `==2.3.*` / 预发布后缀（rc、post、dev）/ epoch `1!` 一律**跳过不判**
   —— **宁可漏报，绝不误报**（误报会把好环境说成坏的，引导用户反复重装，越装越乱）。

## 分级：致命缺失 vs 可选缺失

依赖不是非黑即白，**缺了之后的代价差别很大**：

- **致命**：缺了后端根本起不来（fastapi / uvicorn / pandas …）→ 必须拦住并修。
- **可选**：缺了只是某个功能降级（`pypinyin` → 只有「股票名称拼音搜索」失效），
  **不该拦住启动** —— 离线设备上更不能因为一个可选库把整个程序卡死。

分级标记写在 **requirements.txt 的行尾注释**里（`# optional: …`），
不另开一份清单 —— 真相只能有一处，加依赖时顺手标上即可。

## 本脚本是**纯只读**的

不写任何文件、不落盘快照、不改环境 —— 它只回答「环境现在是什么状态」。

「缺了多久」不需要台账：**每次启动都查，缺了当场就被报出来**，
问题不会攒到没人发现的那天（本次就是这样攒了 5 天才被用户发现）。

## 用法

    python check_deps.py                # 人类可读；退出码 0=无缺失 / 1=有缺失 / 2=用法错
    python check_deps.py --json         # 机器可读 JSON（launcher 用这个）

⚠️ **退出码只反映「缺包」**：缺包 = 功能直接不可用，必须触发安装；
版本不符只作提示 —— 越界的版本多半还能跑，贸然降级可能打断正在运行的任务。

⚠️ 本脚本只回答「**装没装 / 版本对不对**」；「能不能真的 import」
（如 pandas 的 .pyd 被系统安全策略拦住）由 launcher 与 install_env.ps1
各自原有的 import 校验负责 —— 两件事分开报，错误文案才说得清。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

# requirements.txt 里的一行：包名 + 可选 extras + 可选版本约束
#   pandas>=2.2,<3.0   -> 包名 "pandas"、约束 ">=2.2,<3.0"
#   beautifulsoup4    -> 包名 "beautifulsoup4"、约束 ""
_REQ_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*(.*)$")
# 版本约束里的一段：比较运算符 + 版本
_CMP_RE = re.compile(r"^(>=|<=|==|!=|>|<)\s*(.+)$")
# 纯数字版本段（不含预发布后缀 / epoch）
_VER_RE = re.compile(r"^(\d+(?:\.\d+)*)$")
# 行尾注释里的 optional 标记 = 该依赖是**可选的**（缺失只让对应功能降级，不阻断启动）
_OPTIONAL_RE = re.compile(r"\boptional\b", re.IGNORECASE)

# requirements.txt 里以这些符号开头的是**选项行**（-r / --index-url / -e …），不是依赖
_OPTION_PREFIX = ("-", "--")

# 本文件同目录下的 requirements.txt —— launcher、install_env.ps1 与后端启动自检
# 都以它为唯一真相来源
DEFAULT_REQUIREMENTS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "requirements.txt")


def parse_requirements(path: str) -> list[tuple[str, str, bool]]:
    """从 requirements.txt 里取出 [(包名, 版本约束, 是否可选)]。

    去注释、去空行、去选项行、拆出版本约束 —— 约束要留着（用来比对），
    但比较由本模块自己做，不引第三方库。

    ⚠️ **行尾注释不能直接丢**：注释里的 `optional` 标记决定了这个依赖是
    「缺了就得拦住启动」还是「缺了只让某个功能降级」——
    这正是 2026-11-11 那个坑的教训（缺 pypinyin 只让拼音搜索失效，
    却因为被当成硬伤而拦住整个启动）。

    ⚠️ 用 **utf-8-sig** 打开：带 BOM 的文件，首行开头会多一个 U+FEFF，
    正则匹配不上 → 那一行被**静默跳过** → 该包永远检不出来
    （正是本脚本要消灭的那类漏检）。utf-8-sig 对有无 BOM 都能正常读。
    """
    names: list[tuple[str, str, bool]] = []
    seen: set[str] = set()
    with open(path, "r", encoding="utf-8-sig") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith(_OPTION_PREFIX):
                continue
            code, _, comment = line.partition("#")
            code = code.strip()
            if not code:
                continue
            # 形如 "pandas>=2.2,<3.0" -> 包名 "pandas"、约束 ">=2.2,<3.0"；
            # "pkg[extra]>=1" -> 包名 "pkg"；"beautifulsoup4" -> 约束为空
            m = _REQ_RE.match(code)
            if not m:
                continue
            name = m.group(1).strip()
            spec = (m.group(3) or "").strip()
            key = name.lower().replace("_", "-")
            if not name or key in seen:
                continue
            seen.add(key)
            names.append((name, spec, bool(_OPTIONAL_RE.search(comment))))
    return names


def _version_key(text: str):
    """'2.3.3' -> (2,3,3,0)；'2.3' -> (2,3,0,0)。

    带预发布后缀（rc/post/dev）/ epoch（1!）/ 非数字 -> None（**不判定**）。
    """
    m = _VER_RE.match((text or "").strip())
    if not m:
        return None
    parts = [int(x) for x in m.group(1).split(".")]
    parts += [0] * (4 - len(parts))      # 补齐到 4 段：2.3 == 2.3.0 == 2.3.0.0
    return tuple(parts[:4])


def satisfies(version: str, spec: str):
    """版本是否符合约束。返回 True / False / **None（看不懂，不判定）**。

    只认 `>= <= > < == !=` + 纯数字版本段；`~=` / 通配 `==2.3.*` /
    预发布后缀 / epoch 一律 None —— **宁可漏报，绝不误报**
    （误报会把好环境说成坏的，引导用户反复重装，越装越乱）。
    """
    if not spec:
        return None
    ver = _version_key(version)
    if ver is None:
        return None
    ok = True
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        m = _CMP_RE.match(part)
        if not m:
            return None
        op, target = m.group(1), m.group(2).strip()
        if target.endswith(".*"):
            return None
        tv = _version_key(target)
        if tv is None:
            return None
        r = (ver > tv) - (ver < tv)
        if op == ">=" and not r >= 0:
            ok = False
        elif op == "<=" and not r <= 0:
            ok = False
        elif op == ">" and not r > 0:
            ok = False
        elif op == "<" and not r < 0:
            ok = False
        elif op == "==" and not r == 0:
            ok = False
        elif op == "!=" and not r != 0:
            ok = False
    return ok


def inspect(names: list[tuple[str, str, bool]]) -> tuple[list[str], list[str], list[dict]]:
    """返回 (致命缺失, 可选缺失, 版本不符明细)。

    「致命」= 缺了后端起不来；「可选」= 缺了只让对应功能降级（requirements.txt
    里用 `# optional` 标注）。
    """
    from importlib.metadata import PackageNotFoundError, distribution

    missing: list[str] = []
    optional_missing: list[str] = []
    mismatch: list[dict] = []
    for name, spec, optional in names:
        try:
            dist = distribution(name)
        except PackageNotFoundError:
            (optional_missing if optional else missing).append(name)
            continue
        except Exception:  # noqa: BLE001 - 元数据损坏等异常一律当作「查不到」
            (optional_missing if optional else missing).append(name)
            continue
        if not spec:
            continue
        try:
            ver = dist.version
        except Exception:  # noqa: BLE001
            continue
        if satisfies(ver, spec) is False:
            mismatch.append({"name": name, "spec": spec, "installed": ver})
    return missing, optional_missing, mismatch


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="检查 requirements.txt 里的依赖是否装齐")
    default_req = DEFAULT_REQUIREMENTS
    ap.add_argument("--requirements", default=default_req, help="requirements.txt 路径")
    ap.add_argument("--json", action="store_true", help="输出 JSON（启动器用）")
    args = ap.parse_args(argv)

    try:
        names = parse_requirements(args.requirements)
    except Exception as exc:  # noqa: BLE001
        if args.json:
            print(json.dumps({"ok": False, "error": "读不到 %s：%s"
                              % (args.requirements, exc)}, ensure_ascii=False))
        else:
            print("读不到 %s：%s" % (args.requirements, exc))
        return 2

    if not names:
        if args.json:
            print(json.dumps({"ok": False, "error": "%s 里没解析出任何依赖"
                              % args.requirements}, ensure_ascii=False))
        else:
            print("%s 里没解析出任何依赖" % args.requirements)
        return 2

    missing, optional_missing, mismatch = inspect(names)

    if args.json:
        print(json.dumps({
            "ok": not missing,
            "checked": len(names),
            "missing": missing,
            "optional_missing": optional_missing,
            "mismatch": mismatch,
            "requirements": args.requirements,
        }, ensure_ascii=False))
    else:
        print("检查 %d 个依赖，装齐了 %d 个。" % (len(names), len(names) - len(missing) - len(optional_missing)))
        for m in missing:
            print("  缺：%s" % m)
        for m in optional_missing:
            print("  缺（可选）：%s" % m)
        for m in mismatch:
            print("  版本不符：%s 装了 %s（要求 %s）" % (m["name"], m["installed"], m["spec"]))
    # ⚠️ 退出码只反映「**致命缺包**」：
    #   致命缺包 = 后端起不来，必须拦住并触发安装；
    #   可选依赖缺失 = 只让某个功能降级（如缺 pypinyin 只废掉拼音搜索），
    #     **不该拦住启动** —— 离线设备上更不能因为一个可选库就把整个程序卡死；
    #   版本不符只作提示 —— 越界的版本多半还能跑，贸然降级可能打断正在运行的任务。
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
