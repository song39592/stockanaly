# 32 · 后端：`features/system/`（系统设置 / 完整性 / 修复）

> 状态：待办　|　优先级：低-中　|　收益 ★★☆☆☆ / 风险 ★★☆☆☆　|　**依赖第 27 项**

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
- [ ] `features/system/` 建立，三个模块迁入
- [ ] 设置页：数据目录显示 / 修改 正常
- [ ] `/api/system/storage` 与 `/api/system/integrity` 正常返回
- [ ] `/health` 的 `_module_errors` 为空
