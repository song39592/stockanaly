# 32 · 后端：`features/system/`（系统设置 / 完整性 / 修复）

> 状态：**已完成**（2026-10-06）　|　优先级：低-中　|　收益 ★★☆☆☆ / 风险 ★★☆☆☆
> 依赖第 27 项（已完成）

## 回填实际改动

```
backend_fastapi/features/system/
├─ __init__.py
├─ routes.py     ← 原 system_routes.py   数据目录 / 存储信息 / 完整性 / 诊断包 / 重建（147 行）
├─ integrity.py  ← 原 integrity.py       启动期数据完整性校验（245 行）
└─ repair.py     ← 原 repair_digests.py  指纹修复（47 行，**独立 CLI**）
```

顶层保留 3 个**别名转发**（`sys.modules[__name__] = _impl`）：
`system_routes.py` / `integrity.py` / `repair_digests.py`。

### 函数体零改动 —— difflib 逐行核对过

三个文件共 **18 行差异，全部是 import 行**（`difflib` 对比 HEAD 版本，0 行非 import 改动）：

| 文件 | 改动 |
|---|---|
| `integrity.py` | `import crypto` / `import db` → `from core import ...`（第 27 项已搬）<br>L215 函数内 `import price_store` → `from core import price_store`（原注释「函数内导入避免模块级耦合」保留） |
| `routes.py` | `config` / `crypto` / `storage` / L138 函数内 `price_service` → `from core import ...`<br>`import integrity` → `from features.system import integrity`（同包）<br>`import dumplog` **不动**（`dumplog` 还在顶层） |
| `repair.py` | `import price_store` → `from core import price_store` |

## 踩坑点四条的处置

1. **`main.py` 的 `_integrity_state` 仍是「启动时算一次并缓存」** ——
   `main.py:28` 的模块级 `import integrity` 走顶层转发，拿到**同一个模块对象**。
   实测 `/health` 的 `integrity.ok=True`、`issues=[]`；
   `GET /api/system/integrity`（可重新校验，不依赖缓存）也返回 `ok=true, issues=[]`。
2. **`/api/system/storage` 前缀未变** —— 启动器 `StockPoolLauncher.cs:1535/1576/1589`
   直接 `Probe` / `PostJson` 这个地址判断后端是否就绪、并保存数据目录。
   `router = APIRouter(prefix="/api/system")` 一个字未改；实测 `root=E:\stockanaly-data`。
3. **离线改数据目录的联动仍正常** —— 启动器
   `Path.Combine(_root, "backend_fastapi", "storage.py")` + `--set-data-dir-base64`
   走的是第 27 项 `core/storage.py` 的转发（带 `__main__` 守卫）。
   实测该命令 `EXIT=0`、`ok=true`，`.env` 已从备份还原。
4. **数据目录设置功能正常** —— `POST /api/system/storage` 用当前值实测 `ok=true`
   （幂等，不改变任何配置；`.env` 事后比对确认值未变）。

## 本项额外发现：`repair_digests.py` 是独立 CLI，已保住那条用法

全仓**无人** `import repair_digests` —— 它原本是靠 `__main__` 跑的脚本，
其 docstring 记载了：

```
python backend_fastapi/repair_digests.py            # 干跑：只列失配清单，不落库
python backend_fastapi/repair_digests.py --apply    # 真正按当前数据重算指纹
```

若只做别名转发而不加 `__main__` 守卫，这条**已写进文档的使用方式会直接失效**。
所以 `repair_digests.py` 的转发**额外带 `__main__` 守卫**（与第 27 项 `storage.py` 同样做法）。
实测：干跑输出「失配 1 处 / `000001 @ 2026` 内容指纹不一致」、`--help` 的 prog 名仍是
`repair_digests.py`、`EXIT=0`。

## 问题
系统运维类模块混在扁平层。

## 建议目标
```
backend_fastapi/features/system/
├─ __init__.py
├─ routes.py    ← 原 system_routes.py（数据目录设置 / 存储信息 / 完整性）
├─ integrity.py ← 原 integrity.py（启动期数据完整性校验）
└─ repair.py    ← 原 repair_digests.py（指纹修复）
```

## 注意（踩坑点）
1. `main.py` 的 `_integrity_state` 是**启动时算一次并缓存**的，`GET /api/system/integrity` 可重新校验 ——
   移动后确认该接口仍可用（它是第 23 项 dumplog 的数据源之一）。
2. `/api/system/storage` 被启动器**直接调用**（StockPoolLauncher.cs:1745）判断后端是否就绪 ——
   路由前缀**不能变**。
3. 启动器通过 `storage.py --set-data-dir-base64` 离线改数据目录（:1748）—— 这属于 core（第 27 项），
   本项不要重复处理，但要确认联动仍正常。
4. 这一组是**运维工具**，用户不常用；搬运时优先级最低，但要保证「数据目录设置」功能不坏
   （坏了会导致用户无法改数据盘位置）。

## 建议做法
同第 27 项的**兼容转发**模式。

## 验收
- [x] `features/system/` 建立，三个模块迁入 ✅ 另加 `__init__.py` 说明踩坑点处置
- [x] 设置页：数据目录显示 / 修改 正常 ✅ `GET /api/system/storage` → `root=E:\stockanaly-data`；
      `POST` 同值 → `ok=true` 且 `.env` 值未变
- [x] `/api/system/storage` 与 `/api/system/integrity` 正常返回 ✅ 均 `ok=true`；
      `/health` 的 `integrity.ok=True`、`issues=[]`
- [x] `/health` 的 `_module_errors` 为空 ✅ `ok=True`、**11/11** 路由挂载、stderr **无任何告警**
- [x] **函数体零改动** ✅ difflib 对比 HEAD：18 行差异全是 import，0 行非 import
- [x] 踩坑点 1 的三方依赖都在 ✅ `main.py` / `dumplog.py` / `test_integrity.py` 全部导入成功；
      3 个转发与实现**是同一对象**；`integrity` 的 3 个跨模块可见私有名
      （`_all_targets` / `_latest_mtime` / `_parse_iso`）照常可见
- [x] `repair_digests` CLI 未断 ✅ 干跑 / `--help` / `EXIT=0` 三项都通过
- [x] 基线逐项对照 ✅ `STATE_DIR` / `ENV_PATH` / `DB_PATH` / `storage.STATE_DIR` /
      `integrity.ensure_signed()` / `router prefix` / **11 条路由清单** /
      **`integrity` 全部公开名（含私有名）** —— **全部一致**
- [x] 全仓 .py 三引号成对 + `ast.parse` **全部通过**；52 个单元测试全绿
- [x] HTTP 路径零变化 ✅ prefix 仍是 `/api/system`

## 本项排查出的一处**既有**误报（与搬家无关，已排除嫌疑）

核对基线时 `integrity.summary()` 曾报一条：

```
日K分片 bars_2021：文件修改时间明显晚于程序记录（相差 7 分钟），疑似被外部工具改动
```

**用 `git stash` 回到搬家前（HEAD = 第 31 项、本项零改动）跑同样的调用，稳定复现同一条**
（4/4 次，差 8 分钟）—— 所以**不是本项搬家造成的**。真实服务端流程下也正常：
`/health` 的 `integrity.ok=True`、`issues=[]`。

成因是 `integrity.py` **自己 docstring 里就写明**的那个陷阱：

> 计时必须早于打开库：打开 WAL 库（哪怕纯读查询）会创建 `-wal`，其 mtime 就是「此刻」；
> 若在连接之后再取值，会把「打开库」误判成「被外部写入」，造成只要库有年龄就必然误报。

合成复现里我在同进程先 `import main`（它导入期就开库并自己跑过一次校验）、
再重复调 `summary()`，于是 `-wal` 的 mtime 成了「此刻」，而 `_meta.last_write_at`
停在 12:09:31（第 31 项下载测试写库的时刻），差值随时间增长（7→8→9 分钟）。

排查过程中我自己的诊断脚本（`sqlite3.connect` 读 `_meta`）也触碰过一次 `-wal`，
已用 `PRAGMA wal_checkpoint(TRUNCATE)` 清理，**未删任何数据、未改任何表**。

**结论：属 `integrity` 的既有时效性敏感点，不在本项「纯搬家」范围内修**。
若要修，方向是让 `_latest_mtime` 忽略「本进程自己打开库」造成的 `-wal` 时间
（例如打开前先记录基线时间，或把 `-wal` 与本进程启动时间做比较）—— 属行为变更，需单独立项。

## 记录

- 2026-10-06 执行，位置为阶段 C 第 21 步（排在 31 之后）。
- 流程沿用第 29~31 项确立的做法：**所有 Python 文件生成与替换都走脚本 + 脚本内双校验**
  （三引号个数为偶 + `ast.parse` 通过）才落盘。
- ⚠️ **转发生成器踩了一次坑并当场抓到**：首版把「转发名」当成了「实现文件名」
  （`repair_digests` 的实现是 `repair.py`），生成出的转发 import 了一个不存在的名字。
  **`ast.parse` 查不出来**（那是运行期 `ImportError`），靠**导入自检**才暴露。
  已改为 `(转发名, 实现文件名)` 成对传入，并在生成前先确认实现文件存在。
  **教训：「语法校验」只覆盖语法，必须再配一个「导入自检」才算完整。**
- 本项 import 扫描比前几项多查了一层：先前 3 次扫描都只列了 `STALE` 里我预设的模块名，
  结果**漏掉了 `routes.py` 的 `crypto` 和 L138 函数内的 `price_service`**。
  改成「扫全部 import + 对照一份完整的扁平模块名清单」后一次性找出。
  **教训：扫描器的判据不该是我记得的那几个名字，应该是「所有项目内模块」的完整清单。**
