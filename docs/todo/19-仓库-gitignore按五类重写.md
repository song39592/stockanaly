# 19 · 仓库：`.gitignore` 按五类重写（源码 / 依赖 / 构建缓存 / 配置凭据 / 日志状态）

> 状态：待办　|　优先级：**最先做**　|　收益 ★★★★☆ / 风险 ★☆☆☆☆

## 问题
现有 `.gitignore` 是**按技术栈**堆的（Python / Node / OS / 编辑器），不是按你要求的**五类职责**组织的。
后续所有「搬家」都会产生新路径（`<data>/logs/`、`cache/`、新包目录），先把分类规则立好，才能避免把可重建产物误提交。

## 现状
根目录 `.gitignore`（54 行）已有内容，**已经覆盖了相当多**：
- 配置与凭据：`.env` / `.env.*` / `!.env.example` / `*.key` / `*.pem` / `secrets.*` / `*apikey*` ✅ 写得很好
- 构建缓存：`__pycache__/` `*.py[cod]` `venv/` `node_modules/` ✅
- 日志状态：`logs/` `*.log` `*.pid` `uvicorn.log` `backend_fastapi/data/` `backend_fastapi/chip_data/` ✅
- 策略校验缓存：`backend_fastapi/strategies/_validated.json` ✅

### 缺口 / 待办
1. **没有按五类分节**，读起来要猜。
2. `*.tmp` / `*.temp` / `*.cache` 有，但**没有** `cache/` 目录规则（后面要建 `<data>/cache/`）。
3. 没有 `*.db` / `*.sqlite*` 的显式规则（当前靠 `backend_fastapi/data/` 兜底；数据盘在仓库外所以不碍事，
   但源码树里若出现测试库会被误提交）。
4. 没有 `dump-*.zip`（第 23 项 dumplog 的产物）。
5. `frontend/theme-state.js` 已忽略 ✅，但 `agent_dsh` 下的运行期产物（如 `skills_library/mentor-*.skill`）只忽略了一部分。

## 建议做法
按五类重写 `.gitignore`，每类加注释标题（中文，与仓库风格一致）：

```
# ===== 1. 源码（全部跟踪，本文件不忽略）=====
# ===== 2. 依赖声明与锁文件（requirements*.txt / package*.json 跟踪；虚拟环境忽略）=====
venv/  .venv/  node_modules/
# ===== 3. 可重建的构建缓存 =====
__pycache__/  *.py[cod]  *.egg-info/  dist/  build/
backend_fastapi/strategies/_validated.json
# ===== 4. 配置与凭据（模板跟踪，真实值忽略）=====
.env  .env.*  !.env.example  *.key  *.pem  secrets.*  *apikey*
# ===== 5. 日志与持久化状态 =====
logs/  *.log  *.pid  cache/  dump-*.zip  instance.lock
```

## 注意（踩坑点）
1. **`!.env.example` 必须保留** —— 这是唯一允许提交的凭据模板，误删会让新机器无法初始化。
2. 不要加 `*.db` 的**全局**忽略：仓库里若有示例/测试用的 sqlite 需要显式例外；数据盘的 DB 在仓库外，不受影响。
3. `instance.lock` 现在落在数据盘根（`<data>/instance.lock`），仓库内没有；加上规则是为了防止以后有人把它生成到源码树。
4. 改完必须验证「**跟踪的文件一个都没减少**」—— 见验收。

## 验收
- [ ] 五类分节清晰，每节有中文注释
- [ ] `git status --porcelain` 与改动前**完全一致**（没有被新规则意外忽略掉已跟踪文件）
- [ ] `git ls-files | wc -l` 与改动前一致（当前 133）
- [ ] `git status --ignored` 里能看到 `venv/` / `__pycache__/` / `node_modules/` / `logs/`
- [ ] 新建 `<data>/logs/x.log` 后仓库仍干净（数据盘在仓库外，此项为确认性检查）
