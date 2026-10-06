# -*- coding: utf-8 -*-
"""基础设施层（第 27 项）。

这里放**依赖图的底部**：被其它模块依赖、自己几乎不依赖别人的那批东西。

    config.py         配置 / 凭据加载（DATA_DIR 解析、LLM 密钥 DPAPI 密封）
    crypto.py         哈希与 HMAC 签名、数据密钥密封
    dpapi.py          Windows DPAPI 封装
    envfile.py        .env 原子改写
    storage.py        数据目录与各子目录常量、一次性迁移
    db.py             SQLite 连接（含 bars 年度分片）
    instance_lock.py  单实例锁
    logutil.py        日志（stderr 双写 + <data>/logs/ 落盘）
    periods.py        周期合样（day/week/month 桶）
paramspec.py      参数规格 ParamSpec（指标 / 策略 / 筹码公式三处共用的唯一定义，第 13 项）
    tdx_reader.py     通达信日线 / 除权文件读取
    share_service.py  流通股本抓取
    price_store.py    行情存储与指纹校验
    price_service.py  行情采集（拉取 / 清洗 / 降级）
    kline_service.py  取数 + 周期合样出口

**为什么 `crypto` 也在 core**：待办清单里没列它，但 `db` 与 `price_store` 都依赖它，
且它只依赖 `config` / `dpapi` / `envfile` —— 是标准的底部基础设施，放外层会让 core 反过来
依赖顶层，故一并归入。

**内部互相引用用相对导入**（`from . import config`），不写死包名；
引用 core 之外的东西（如 `price_service` 里的 `from market_service import _ak`）
保持绝对导入 —— 那是**已知的反向依赖**，等第 28 项把 market_service 搬进
`features/market/` 时必须一并处理。

**旧路径的兼容转发**：`backend_fastapi/config.py` 等 14 个同名文件仍在原处，
内容是 `from core.xxx import *` + 私有名补齐，作用是让还没迁移的模块
（`main.py` / 各 `*_routes` / `test_*.py`）一行都不用改。
全部迁完后统一删除这批转发文件。
"""
