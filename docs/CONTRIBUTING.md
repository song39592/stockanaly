# 开发规范（提交 / 分支 / 版本回退）

## 一、分支策略（个人开发者简易策略）

- `main`：稳定可用版本。每个 `tag` 代表一个可运行版本。**不直接在 main 上写新代码**。
- `dev`：日常开发主分支，绝大多数编码工作在 dev 完成。
- `feature/xxx`：新功能短分支（如 `feature/kline-indicator`、`feature/red-cow-dsh`），完成后合并回 dev 并删除该分支。

### 工作流

```
开发新功能： git checkout dev && git checkout -b feature/xxx
功能跑通：   合并回 dev（git checkout dev && git merge feature/xxx && git branch -d feature/xxx）
dev 验证：   整体系统可运行后合并到 main（git checkout main && git merge dev）
发版本：     main 上打 tag（git tag v0.1.0 && git push --tags）
```

## 二、提交规范（Conventional Commits）

提交信息格式：`<type>: <简短描述>`，中文描述即可。

| type | 含义 |
|---|---|
| `feat` | 新功能 |
| `fix` | 修复 bug |
| `docs` | 文档 |
| `refactor` | 重构（不改行为） |
| `chore` | 杂项（依赖、构建、配置） |
| `style` | 格式（不影响逻辑） |
| `test` | 测试 |

示例：

```bash
git commit -m "feat: 红色小牛接入 dsh，新增快照/调研两个 Tool"
git commit -m "fix: 修复行业乱码导致的分析错误"
git commit -m "docs: 更新启动说明"
```

## 三、版本号（语义化）

`v主版本.次版本.补丁版本`，例如 `v0.1.0`。

- 主版本：架构级大改 / 不兼容变更。
- 次版本：新增功能（向后兼容）。
- 补丁版本：bug 修复。

## 四、版本回退操作

```bash
# 查看历史
git log --oneline --graph --decorate

# 回到某个 tag 看代码（只读，游离 HEAD）
git checkout v0.1.0

# 撤销某次提交（生成一条反向提交，保留历史，推荐）
git revert <commit-id>

# 回退到某个提交（丢弃其后所有提交，危险，会改写历史）
git reset --hard <commit-id>      # 本地
git push --force-with-lease       # 若已推送

# 撤销未提交的改动
git checkout -- <file>            # 丢弃单个文件改动
git reset --hard HEAD             # 丢弃全部未提交改动

# 删除 tag
git tag -d v0.1.0                 # 本地
git push origin :refs/tags/v0.1.0 # 远程

# 查看某次提交改了什么
git show <commit-id>
```

## 五、备份策略

- 代码 + `.skill` 规则文件：Git 托管到 GitHub 私有仓库（`git push`）。
- 密钥：只存本机 `.env`，不进 Git；可另存到密码管理器。
- 前端股票池数据：浏览器 localStorage（页面内「导出备份」下载 JSON，定期留存）。
- 建议每次打完 tag 后 push，作为版本快照。
