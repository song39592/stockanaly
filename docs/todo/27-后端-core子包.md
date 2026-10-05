# 27 · 后端：`core/` 子包（基础设施层归位）—— **源码搬家第一步**

> 状态：**已完成**（2026-10-05）　|　优先级：高　|　收益 ★★★★☆ / 风险 ★★★☆☆
>
> **回填实际改动**
> 新增 `backend_fastapi/core/`（含 `__init__.py` 说明这一层的定位），
> **14 个**模块用 `git mv` 迁入（保住历史）：
> `config` `crypto` `dpapi` `envfile` `storage` `db` `instance_lock` `logutil`
> `periods` `tdx_reader` `share_service` `price_store` `price_service` `kline_service`
>
> - **比清单多一个 `crypto`**：`db` 与 `price_store` 都依赖它，且它只依赖
>   `config`/`dpapi`/`envfile`，是标准的底部基础设施。留在外层会让 core 反过来依赖顶层。
> - core 内部 **17 处** import 改为相对导入（`from . import config` / `from .periods import ...`）。
> - 跨层依赖只剩 **1 条**：`core/price_service.py` 的 `from market_service import _ak`。
>   `market_service` 是叶子模块（不依赖任何项目模块），所以**不成环**，本项按「纯搬家」保留。
>   ⚠️ **第 28 项把 market_service 搬进 `features/market/` 时必须一并处理这条**。
>   顺带发现 `_ak` 其实是个**通用**的 akshare 调用包装（带超时与降级），
>   被 `price_service` 从盘面页借走属于放错位置，届时应下沉到 core。

> **踩坑点 1（BASE_DIR）—— 本项最大的雷，已按「只改一处」处理**
> `config.py` 移进 `core/` 后 `Path(__file__).parent` 变成 `backend_fastapi/core/`。
> 真正会静默出错的**不止 `BASE_DIR`**：`ENV_PATH` 在文件顶部**直接**用
> `Path(__file__).resolve().parent / ".env"` 取值、并不经过 `BASE_DIR` ——
> 漏改它 → `.env` 找不到 → LLM 配置全丢且没有任何报错。
> 修法：新增 `PROG_DIR = Path(__file__).resolve().parent.parent`，
> `ENV_PATH` 与 `BASE_DIR` **都由它派生**，「多退一级」全文件只出现一次。

> **⭐ 兼容转发用 `sys.modules` 别名，而不是待办建议的 `from core.x import *`**
> 待办原本建议转发写成 `from core.config import *` + 显式补私有名。**实测这个做法是错的**：
> `import *` 只是**复制**一份名字，而测试里大量使用
> `patch.object(config, "DATA_DIR", ...)` / `patch.object(storage, "ENV_PATH", ...)`
> （见 `test_settings_download.py`）—— 补丁打在**副本**上，实现模块仍读真实值，
> **补丁静默失效**。实测后果：52 个测试里 **26 个失败**，且 `update_data_dir` 去改**真实的 `.env`**。
> 改成别名后 52 个全过：
> ```python
> import sys
> from core import config as _impl
> sys.modules[__name__] = _impl
> ```
> 附带好处：`storage.py` 作为脚本的入口仍能用（启动器依赖），且**下划线私有名也自动可见**
> （`price_store._shard_years`、`crypto._key` 这 4 处跨模块引用不再需要手工补清单）。

> **⛔ 期间的一次真实事故（已修复，记录以免复发）**
> 上述 `import *` 版本跑测试时，`test_settings_download` 的
> `patch.object(storage, "ENV_PATH", 临时文件)` 无效，
> `update_data_dir` 于是把**临时目录路径写进了真实 `.env`**
> （含 `envfile.rewrite` 自动生成的 `.env.bak`），
> 致 `DATA_DIR` 一度指向一个**已被测试清理掉的** Temp 目录。
> 已把两个文件的 `DATA_DIR` 恢复为 `E:\stockanaly-data` 并复验；
> 改成别名后重跑测试，`.env` 哈希不再变化。
> **教训**：凡是要动 `.env` 写入路径的改动，跑测试前先备份 `.env`。

> **踩坑点 3（启动器把 storage.py 当脚本跑）**
> `backend_fastapi/storage.py` 保留同名入口，`__main__` 分支显式调 `_impl.main()`。
> 已实测：`python storage.py --set-data-dir-base64 <b64>` 返回 `ok:true`/退出码 0；
> 非法路径返回 `ok:false`/退出码 1（不崩）。

> **踩坑点 2（main.py 动态挂载）**
> `ROUTE_MODULES` 里 11 项全是 `*_routes`，**不含任何 core 模块**，本项未触及。

## 问题
`backend_fastapi/` 是**扁平**结构，约 87 个 .py 在同一层，基础设施、功能实现、路由混在一起。
本项先把**最底层**归位：被所有模块依赖、自己几乎不依赖别人的那批。

## 为什么先搬 core
它是依赖图的**底部**：搬完后各 feature 仍通过原模块名 import（有兼容转发），
而 core 内部互相引用可以一次性理顺 —— 这是风险最低的切入口。

## 建议目标
```
backend_fastapi/
├─ core/
│  ├─ __init__.py
│  ├─ config.py       ← 配置/凭据加载（含 DATA_DIR 解析、LLM 密钥 DPAPI 密封）
│  ├─ storage.py      ← 数据目录与各子目录常量（第 21 项后由它统一定义）
│  ├─ db.py           ← SQLite 连接（含 bars 年度分片）
│  ├─ periods.py      ← 周期合样（day/week/month 桶）
│  ├─ kline_service.py← 取数 + 周期合样出口
│  ├─ price_store.py  ← 行情存储与指纹校验
│  ├─ price_service.py
│  ├─ share_service.py
│  ├─ tdx_reader.py
│  └─ logutil.py      ← 第 22 项新增
```
（清单以执行时实际 `ls` 为准；`dpapi.py` / `envfile.py`（密钥密封与 .env 原子改写）、`instance_lock.py` 也属基础设施，一并归入 core。）

## 建议做法（**兼容转发**，可分批、可回滚）
1. `git mv` 模块进 `core/`（**必须用 git mv**，保住历史）。
2. 修正 core 内部的互相 import（改成相对导入或 `core.xxx`）。
3. **在旧路径留一行兼容转发**，例如 `backend_fastapi/config.py`：
   ```python
   from core.config import *          # 兼容：旧 import 路径保留，待全部迁移后删除
   from core.config import ENV_PATH, DATA_DIR, llm_ready, ...   # 下划线/非公共名要显式列出
   ```
   ⚠️ `from x import *` **不会**导出下划线开头的名字，必须按需显式补充（这是最容易踩的坑）。
4. 全部模块都改成 `import core.xxx` 后，再删除转发文件。

## 注意（踩坑点）
1. **`config.py` 的 `BASE_DIR` 语义会变**：现在 `BASE_DIR = Path(__file__).parent`（= `backend_fastapi/`），
   移进 `core/` 后变成 `backend_fastapi/core/` —— **必须显式改成上上级**，否则 `.env`、`LEGACY_DATA_DIR`、
   `DEFAULT_DATA_DIR`（`PARENT_DIR / "stockanaly-data"`）全都会指错，数据盘直接找不到。这是本项**最大的雷**。
2. `main.py` 里 `importlib.import_module(module_name)` 动态挂载路由（ROUTE_MODULES 是「模块名字符串」）——
   路由模块若移动，要确保仍在可导入路径上（本项不动路由，但要确认 core 不影响它）。
3. 启动器把 `storage.py` 当**脚本**跑（`storage.py --set-data-dir-base64`，见 StockPoolLauncher.cs:1748，路径写死为
   `backend_fastapi/storage.py`）—— 移动后**必须**留一个同名入口脚本，或等第 35 项一起改启动器。
4. `tdx_reader` / `price_store` 是最底层（连 db 都依赖它），先搬它们再搬上层。

## 验收（2026-10-05 实测）
- [x] `core/` 建立，基础设施模块已迁入；core 内部 import 全部修正 ✅ 14 个模块 `git mv`；
      17 处相对导入；跨层依赖仅剩 `price_service → market_service._ak` 一条（不成环）
- [x] 旧路径兼容转发存在，未迁移的模块仍能 import ✅
      14 个转发全部 `sys.modules["x"] is core.x`（别名，非副本）
- [x] **重点：`DATA_DIR` 解析结果与改动前一致** ✅
      搬家前先落**基线**（57 项：全部路径常量 / 分区目录 / 库常量 / `.env` 读取结果 /
      跨模块私有名 / 各模块公开名清单），搬家后逐项对照 —— **零差异**。
      `DATA_DIR = E:\stockanaly-data`、`BASE_DIR = ...\backend_fastapi`、
      `ENV_PATH = ...\backend_fastapi\.env`（存在）均一致
- [x] `python storage.py --set-data-dir-base64 <base64>` 仍能工作 ✅
      合法值 → `ok:true` + 退出码 0；非法路径 → `ok:false` + 退出码 1（不崩）
- [x] 后端启动：`/health` 的 `_module_errors` 为空 ✅ `ok=True`、`integrity.ok=True`、
      11 个路由模块全部挂载、stderr 无任何 import 告警
- [x] 个股页 / 盘面页 / 下载页冒烟正常 ✅ 报价、估值(POST)、外围环境、大盘资金、板块β、
      股票池、任务列表、TDX 状态、数据目录、完整性 —— 全部 `ok=true`
- [x] **单元测试全绿** ✅ `python -m unittest discover` → **52 tests OK**
      （中途曾因 `import *` 转发方案导致 26 个失败，见上方事故记录）
- [x] 真实 `.env` 未被测试污染 ✅ 测试前后哈希一致
