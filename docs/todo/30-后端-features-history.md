# 30 · 后端：`features/history/`（股票池历史）

> 状态：**已完成**（2026-10-06）　|　优先级：中　|　收益 ★★★☆☆ / 风险 ★★☆☆☆
> 依赖第 27 项（已完成）

## 回填实际改动

```
backend_fastapi/features/history/
├─ __init__.py
├─ service.py   ← 原 history_service.py   （K线 / 入池出池轨迹 / 消息面）
├─ store.py     ← 原 history_store.py     （pool_snapshots 快照）
└─ routes.py    ← 原 history_routes.py
```

顶层保留 3 个**别名转发**（`sys.modules[__name__] = _impl`）：
`history_service.py` / `history_routes.py` / `history_store.py`。

### 只改了「因本次搬家而失效」的 import

| 文件 | 改动 |
|---|---|
| `service.py` | `import history_store` → `from features.history import store as history_store`；`kline_service` / `price_service` / `price_store` → `from core import ...`（第 27 项已搬） |
| `routes.py` | `import history_service` / `import history_store` → 包内引用；`from periods import normalize` → `from core.periods import normalize` |
| `store.py` | `import db` / `from db import ...` → `from core import db` / `from core.db import ...` |

`from collectors import get_announcements, get_news` **未动** —— `collectors` 还在顶层
（它要等第 31 项之后才有归属）。

**函数体一行未改。**

## 踩坑点三条的处置

1. ⭐ **`load_trusted_bars` 仍直接调 `price_store.load_bars`** —— 已用源码断言验证：
   - `price_store.load_bars` 在源码里 → **True**
   - `UntrustedDataError` 捕获在源码里 → **True**
   - `kline_service.get_bars` **不在**源码里 → **True**（没被"顺手改成走 kline_service"）

   「脏数据自动重抓」的能力完整保留。**待办建议的「把这条例外写进 kline_service 文档」未做** ——
   它属于文档而非本项代码改动，见下方「未做」。
2. **周/月合样仍走 `kline_service.resample`** —— 已断言 `kline_service.resample` 仍在
   `service.py` 源码里；实测周线 264 根、月线 62 根，正常。
3. **`latest_snapshot_codes()` 的调用方** —— `download_service.py:204/207` 用它挑下载范围。
   旧路径 `history_store` 保留别名转发，**该调用方一行未改**；
   实测 `/api/history/download/universe` 返回 `ok=true`（11.1s，真实联网取全市场代码）。

## ⚠️ 本项发现的一处层级倒置（**未修**，需独立决策）

`core/db.py` 的 `init_db()` 里有**函数内** `import history_store`（原注释：
「避免与 db 形成模块级循环依赖」）。本项之后它指向 `features.history.store` ——
也就是 **core 层反过来依赖 features 层**。

这是**既有状况**：`db.init_db()` 本来就要编排 `price_store` / `download_store` /
`history_store` 三家的建表，而那三者都在上层。本项**只做位置移动**，
`core/db.py` 一行未改（仍走顶层转发），把这个倒置记录下来留给后续。

要治有两条路：把「建表编排」从 `core/db.py` 挪到某个上层模块（`core` 就不再知道上层），
或把三家的 `init_db` 提成独立的迁移入口。**两者都会改动 `core/db.py` 的职责边界**，
不属于「纯搬家」，不应塞进本项。
## 踩坑（流程改进已生效）

第 29 项踩了三次 PowerShell here-string 吞掉 docstring 收尾 `"""` 的坑，
其中一次还造成**整文件字符被系统性替换**。本项一开始就换成**Python 脚本**做所有
文件生成与替换，并在脚本内**自带双校验**（`"""` 个数为偶 + `ast.parse` 通过）才落盘：

- `features/history/__init__.py`、3 个转发文件、3 处 import 替换 —— **全部一次通过，零事故**。
- 事后另跑一次**全仓 `.py` 扫描**（三引号成对 + `ast.parse`）：**全部通过**。

> 结论已被反复验证：**改 Python 一律走 Python 脚本 + 脚本内自带校验**，
> 不要用 PowerShell 的 `.Replace()` 或裸 here-string 写文件。

## 验收（2026-10-06 实测）

- [x] `features/history/` 建立，三个模块迁入 ✅ 另加 `__init__.py` 说明定位与逐条踩坑处置
- [x] 个股页：K 线、入池/出池轨迹、消息面 正常 ✅
      `/api/history/stock/600519` 返回 `ok=true`：`bars` 1252 根、`bars_raw` 1252 根、
      **`events` 73 项**（轨迹/消息面）、`pool` 字段存在
- [x] 周/月周期切换正常 ✅ 日线 1252 根 / **周线 264 根** / **月线 62 根**（`kline_service.resample` 链路通）
- [x] 股票池历史列表与快照正常 ✅ `/api/history/download/universe` `ok=true`（11.1s），
      这是 `latest_snapshot_codes()` 唯一的实际消费方；`history_store.init_db` 可见可调用
- [x] `/health` 的 `_module_errors` 为空 ✅ `ok=True`、`integrity.ok=True`、
      **11 个路由模块全部挂载**、stderr 无任何告警
- [x] 额外验证（别名转发正确性）✅ 11 个模块全部导入成功（含 `core.db`、`download_service`、
      `test_history_store`、`test_integrity`、`main`）；3 个转发与实现**是同一对象**；
      `load_trusted_bars` / `latest_snapshot_codes` / `init_db` 均可见
- [x] 踩坑点 1、2 用源码断言验证 ✅ 见上（`price_store.load_bars` 与 `UntrustedDataError`
      仍在、`kline_service.get_bars` 不在、`kline_service.resample` 仍在）
- [x] 单元测试 52 个全绿 ✅ 真实 `.env` 未被改动
- [x] HTTP 路径零变化 ✅ `router` 的 prefix 仍是 `/api/history`，
      启动器与网页端不需要改（`main.py` 按字符串 `"history_routes"` 引用转发）

## 未做（记录以免遗漏）

1. **待办建议的「把 `load_trusted_bars` 这条例外写进 `kline_service` 的文档」未做** ——
   本项只做位置移动，不改任何代码或文档措辞。`kline_service` 在第 27 项已搬进 `core/`，
   其文档应在那里补一句「`features.history.service.load_trusted_bars` 是**故意**绕过
   本模块的，因为它要靠 `price_store.UntrustedDataError` 实现脏数据自动重抓」。
   建议并入第 31 项或第 34 项一并处理。
2. **层级倒置**（`core/db.py` → `features/history/store.py`）未修，理由见上。

## 记录

- 2026-10-06 执行，位置为阶段 C 第 19 步（排在 29 之后）。