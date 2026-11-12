---
description: 分支规范：命名、起分支的时机、一类改动一个分支、合并后清理、与 git-safety 的分工
alwaysApply: true
enabled: true
---

# 分支规范

> 本规则管「**分支怎么起、怎么命名、什么时候删**」。
> 「提交 / 推送要不要授权、推送前的影响面自检」在 `git-safety.md` —— 两者不重复。

## 命名
- 格式 `<类型>/<短描述>`，类型白名单：`feat` `fix` `refactor` `docs` `chore` `perf` `test`。
- 短描述用 **kebab-case**、不超过 6 个词，说清「改什么」；不写编号、不写日期。
- 禁止：工具名 / 机器名前缀（`codex/`、`claude/`）、无类型前缀（`dev`）、中文、空格、下划线。
  历史遗留的 `codex/settings-download-fixes`、`dev` 这类在清理时一并收掉，别再新增。
- 例：`fix/backtest-limit-up-roll`、`docs/structure-rules-sync`、`feat/rps-pool-score`。

## 一类改动 = 一个分支
- **一次独立任务 = 一个分支**：文档 / 规则体系的任务走 `docs/xxx`，代码任务走 `fix|feat|refactor/xxx`。
  不要把「顺手改的文档」和「功能修复」混在一个分支里 —— 一个 PR 装两件事，审的人看不出重点。
- **必要配套除外**：某次改动**必然**牵连的说明同步（改了行为就要改说明、规则里明确要求同步的那些文件）
  跟该改动同支。理由：配套同步脱离代码后，文档描述的是**尚不存在**的功能，合回去反而更糟。
  例：改了 `launcher/*.cs` 就要同步 `README.md` 的启动说明 —— 它属于代码改动的一部分。
- 一个分支只做一件事；中途发现另一件事 → 另开分支，不要顺手带上。
- 分支从**当前主线**（`origin/main`）切出，不要从别的功能分支切 —— 叠罗汉会让 base 对不上。

## 起分支的时机
- **动手改之前**就建分支，不要在 `main` / `dev` 上直接改。
- 本地创建即可；**推送需要明确指令**（见 `git-safety.md`）。

## 生命周期：合并完就删
- 分支合并进 main 后：**本地分支和远端分支都要删**
  （`git branch -d <name>` / `git push origin --delete <name>`）。
- 留着已合并的分支**不占历史**（git 已经存了全部内容），只会让 `git branch -a` 刷屏、
  让人分不清哪个是活的 —— 本项目已经堆了十几个。
- 定期清一遍，两条命令就够：
  ```bash
  git branch --merged origin/main        # 已合并但还在的本地分支
  git branch -r --merged origin/main      # 已合并但还在的远端分支
  ```
  逐个确认后删掉。**删远端分支前先确认没有别人的 PR 挂在上面**。
- 长期不动、且内容已被别的分支覆盖的僵尸分支 → 人工确认后删，不要凭名字猜。
- ⚠️ **删分支不丢东西**：内容已在 main 里。唯一例外是「还没合并就想放弃」—— 那要先确认内容不要了。

## 不做的事
- 不在 `main` / `dev` 上直接堆提交（`main` 受保护，见 `git-safety.md`）。
- 不用 `git push --force` / `--force-with-lease` 覆盖远程历史（见 `git-safety.md`）。
- 不把本地配置、运行期数据、`venv`、`node_modules` 提交进任何分支
  （`.gitignore` 已覆盖，别用 `-f` 绕过）。
- 不为了「备份」而留着已合并的分支。
- 不把同一件事拆成多个分支来回切 —— 那是没想清楚边界。

## 与 git-safety.md 的分工
| 规则文件 | 管什么 |
|---|---|
| `branch-convention.md`（本文） | 分支的命名、粒度、起停、清理 |
| `git-safety.md` | 提交 / 推送的授权、推送前影响面自检、禁止推 main |
