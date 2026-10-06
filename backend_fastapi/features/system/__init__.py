# -*- coding: utf-8 -*-
"""系统设置 / 完整性 / 修复（第 32 项）。

    routes.py     <- 原 system_routes.py   数据目录设置 / 存储信息 / 完整性 / 诊断包 / 重建
    integrity.py  <- 原 integrity.py       启动期数据完整性校验（含签名与封存）
    repair.py     <- 原 repair_digests.py  指纹修复（**独立 CLI 脚本**）

## 踩坑点的处置

1. **`main.py` 的 `_integrity_state` 仍是「启动时算一次并缓存」**：
   `main.py:28` 的 `import integrity` 走顶层别名转发，拿到的是**同一个模块对象**，
   所以 `ensure_signed()` / `summary()` 的结果与搬家前一致。
   `GET /api/system/integrity` 可重新校验，不依赖缓存。
2. **`/api/system/storage` 的路由前缀不能变** —— 启动器
   `StockPoolLauncher.cs:1535/1576/1589` 直接 `Probe` / `PostJson` 这个地址
   来判断后端是否就绪、并保存数据目录。`router = APIRouter(prefix="/api/system")`
   一个字都没改。
3. **离线改数据目录那条链路属 core（第 27 项），本项未触碰**：
   启动器 `Path.Combine(_root, "backend_fastapi", "storage.py")` +
   `--set-data-dir-base64`，走的是 `core/storage.py` 的转发（带 `__main__` 守卫）。
4. **`repair.py` 是独立 CLI，不是被 import 的模块** —— 全仓无人 `import repair_digests`。
   它原本的调用方式是 `python repair_digests.py [--apply]`（见自身 docstring）。
   为不断掉这条使用方式，顶层 `repair_digests.py` 转发**带 `__main__` 守卫**
   （与第 27 项 `storage.py` 同样的做法）。
"""
