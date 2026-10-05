# 27 · 后端：`core/` 子包（基础设施层归位）—— **源码搬家第一步**

> 状态：待办　|　优先级：高　|　收益 ★★★★☆ / 风险 ★★★☆☆　|　**依赖第 19 项（先立 .gitignore 规矩）**

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

## 验收
- [ ] `core/` 建立，基础设施模块已迁入；core 内部 import 全部修正
- [ ] 旧路径兼容转发存在，未迁移的模块仍能 import
- [ ] **重点**：`DATA_DIR` 解析结果与改动前一致（打印核对，仍是 `E:\stockanaly-data`）
- [ ] `python storage.py --set-data-dir-base64 <base64>` 仍能工作（启动器依赖）
- [ ] 后端启动：`/health` 的 `_module_errors` 为空
- [ ] 个股页 / 盘面页 / 下载页冒烟正常
