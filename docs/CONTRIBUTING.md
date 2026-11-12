# 开发规范（版本号 / 备份策略 / 常用命令）

> ⚠️ **分支策略与提交规范的权威是
> [.codebuddy/rules/git-safety.md](../.codebuddy/rules/git-safety.md)** —— 本文不重复，
> 只保留它没覆盖的部分（版本号、备份策略、命令速查、自检清单）。
> 两处如有冲突，**以 `git-safety.md` 为准**。

## 一、版本号（语义化）

`v主版本.次版本.补丁版本`，例如 `v0.1.0`。

| 级别 | 何时递增 |
|---|---|
| 主版本 | 架构级大改 / 不兼容变更（如后端分层搬家、策略契约破坏性变更） |
| 次版本 | 新增功能（向后兼容） |
| 补丁版本 | Bug 修复 |

版本标签打在被人工合并后的主分支上，作为可运行的版本快照。

## 二、备份策略

| 对象 | 托管方式 |
|---|---|
| 代码 + `.skill` 规则文件 | Git（私有仓库） |
| 密钥 | 只存本机 `.env`，**不进 Git**；可另存到密码管理器 |
| 前端股票池数据 | 浏览器 `localStorage`，用页面「导出备份」下载 JSON，定期留存 |
| 后端行情 / 池快照 / 实验室数据 | 数据目录（仓库外），按需整体备份 |

> 后端数据目录在仓库之外（默认 `<程序上一级>/stockanaly-data`），
> 删项目 / 重新 clone / `git clean` 都不影响数据。位置与迁移见 `docs/database.md`。

## 三、常用命令速查

```bash
# 查看历史
git log --oneline --graph --decorate

# 撤销某个提交（生成反向提交，保留历史，推荐）
git revert <commit-id>

# 回到某个提交（丢弃其后所有提交，危险，会改写历史）
git reset --hard <commit-id>          # 本地
git push --force-with-lease           # 若已推送

# 撤销未提交的改动
git checkout -- <file>                # 丢弃单个文件改动
git reset --hard HEAD                 # 丢弃全部未提交改动

# 删除 tag
git tag -d v0.1.0                    # 本地
git push origin :refs/tags/v0.1.0     # 远程

# 查看某次提交改了什么
git show <commit-id>
```

> ⚠️ `--force` / `--force-with-lease` 仅在明确知道后果时使用；
> 远程 base 与本地不一致时**不得强推**（见 `git-safety.md`）。

## 四、提交前自检

1. 改了 `backend_fastapi/**/*.py` → 已结束占用 8000 端口的旧 uvicorn 进程并重启后端验证。
2. 改了 `launcher/**/*.cs` → 已重跑 `launcher\build_exe.bat` 重新生成 `stockpool.exe`，
   且确认提交的是**发布版**（不含临时诊断）。
3. 新增 / 删除 `launcher` 顶层 `.cs` → 已同步 `build_exe.bat` 的显式源文件清单。
4. 改了指标 / 策略 / 筹码公式的计算逻辑 → 已跑 `python -m indicators.golden --update`
   并把 `_golden.json` 一并提交。
5. 改了表结构 → 已同步 `docs/database.md`。
6. 改了目录结构 / 模块职责 → 已同步 `.codebuddy/rules/project-structure.md` 与根 `README.md`。
7. 已跑 `scripts/check-docs.ps1`（文档路径与接口前缀自检，退出码 0 = 通过）。

## 五、测试

```powershell
# 后端
cd backend_fastapi; venv\Scripts\python.exe -m unittest -v
# 后端完整自检脚本
powershell -File backend_fastapi\run_tests.ps1
# 启动器静态约定自检
powershell -File launcher\test-launcher-conventions.ps1
# 文档一致性自检
powershell -File scripts\check-docs.ps1
# Agent 侧（含前端共享引擎单测）
cd agent_dsh; npm test
```
