# 自动化测试：现状盘点 + 规范化待办

## 现状（实测，2026-10-17）
- **已有 9 个测试文件**（unittest，标准库，零额外依赖）：
  `test_price_store.py`、`test_history_store.py`、`test_mentor_store.py`、`test_market_symbol.py`、
  `test_main.py`、`test_integrity.py`、`test_instance_lock.py`、`test_settings_download.py`、`test_support.py`
- **测试基建相当成熟**：`test_support.py` 提供
  - `isolate_crypto()`：mock 密钥，绝不碰用户真实 `.env`
  - `require_data_isolation()`：断言写入路径落在临时目录，防污染生产库
    （注释记录了 bug-04：测试夹具曾把脏柱写进用户 000002 生产库）
- **问题：有，但没有标准规范化**（用户原话）
  - 无 `pytest.ini` / `conftest.py` / 统一入口
  - 无 CI（全仓库无 `.github/workflows`，只有一个无关的 `agent_dsh/cordis.patch.yml`）
  - 测试只覆盖**数据/存储层**；`features/chip/`（筹码公式）、`indicators/`（指标公式）**完全无测试**
  - 测试文件散落在 `backend_fastapi/` 根目录，命名靠约定（`test_*.py`）

## 待办（规范化，按性价比排序）
- [ ] **统一入口**：确定用 `python -m unittest discover` 还是引入 pytest；加 `pytest.ini` 或 `Makefile`/`run_tests.ps1`，
      一条命令跑全部。
- [ ] **接入 CI**：加 `.github/workflows/tests.yml`，push/PR 自动跑现有 9 个测试（零新依赖，成本最低、杠杆最大）。
- [ ] **补公式层测试**（最高价值）：`features/chip/`、`indicators/` 关键公式的单测；
      尤其即将实现的**筹码双分布**，先把期望输出固化，防数值回归。
- [ ] **bat / 启动器集成测试**：把「LF-only bat 经 `NormalizeBatLineEndings` 规范化为 CRLF 后可正常执行」
      固化为 PowerShell 测试，防行尾问题回潮（本轮已手测两次，应沉淀）。
- [ ] **测试目录规范化**：是否把 `test_*.py` 收进 `tests/` 子目录、是否按模块分文件，需拍板（改动面小但要动 import）。
- [ ] （可选）C# 启动器：把 `NormalizeBatLineEndings` 等纯逻辑抽成可测方法，加轻量 harness。

## 原则
- 测试必须**隔离生产数据**（沿用 `require_data_isolation` 的教训），绝不写用户真实库。
- 新增测试不改变业务数值；数值正确性用「期望输出快照」方式固化。
