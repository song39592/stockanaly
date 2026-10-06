# 19 · 仓库：`.gitignore` 按五类重写（源码 / 依赖 / 构建缓存 / 配置凭据 / 日志状态）

> 状态：**已完成**（2026-10-06）　|　优先级：最先做　|　收益 ★★★★☆ / 风险 ★☆☆☆☆

## ⭐ 本项挖出一个真实缺陷：gitignore **不支持行尾注释**

原文件里有 3 行写成了「规则 + 行尾注释」形式，**它们一直是失效的**：

```
.runtime/          # 本机 Node 运行时，可重新下载
cache/             # 将来的磁盘缓存目录
dump-*.zip         # dumplog 诊断包
```

gitignore 只认**行首**的 `#`；行尾的 `#` 被当作**字面量**，于是整行模式变成
「路径 + `#` + 文字」，匹配不到任何东西。已全部改成「注释写在上一行」。

`git check-ignore -v` 是唯一能发现这个的检查 —— 光看 `git status` 会以为一切正常：

- `.runtime/` 之所以**看起来**在生效，是因为被 `.git/info/exclude` 兜住了
  （实测 `check-ignore` 报的是 `.git/info/exclude:9:/.runtime/`）。一旦那条被清掉，
  `.runtime/` 就会突然开始暴露。
- 本项新加的 `cache/` 与 `dump-*.zip` 若带着行尾注释发出去，等于**没写**。

**教训**：改 `.gitignore` 后要跑行为验证（造临时文件 + `check-ignore -v` 看
命中的具体规则行号），只对比「跟踪文件数没变」会漏掉「规则没生效」这一类。

## 纠正了原文的两处误判

1. **`agent_dsh/skills_library/*.skill` 不该忽略。** 原文把它当「运行期产物」，
   实测这三个（`bft` / `general` / `short-trader-01`）是**手写的技能定义**、
   **已被 git 跟踪**，属第 1 类源码。给已跟踪文件加忽略规则不生效，还会误导后来人。
   真正的运行期产物是 `agent_dsh/.env` 与 `dsh.log`（已被前两类覆盖）。
2. **不加 `*.db` / `*.sqlite*` 全局忽略**（原文踩坑点 2 的判断是对的，实测确认）：
   仓库里唯一的 db 是 `backend_fastapi/data/mentor_lab.db`，已被
   `backend_fastapi/data/` 规则覆盖。已把「不加」的理由写进文件，
   防止以后被人「补全」。

## 实际改动

- 补两条**防御性规则**：`cache/`、`dump-*.zip`。它们的匹配目标当前**都不在仓库内**
  （数据盘在仓库外），加它们是为「以后有人把产物生成到源码树里」时能自动挡住
  —— 与既有的 `instance.lock` 同思路。
- 修 3 处失效的行尾注释（见上）。
- 更新过时注释：`chip_formulas` 已于第 34 项搬入 `features/chip/formulas/`；
  补上 `AsyncKit.cs`（第 18 项新增的公共件）。
- 精简 `theme-state.js` 那段绕口的说明，补 `!.env.example` 不可删的提示。
- 第 2 类补一条实测结论：**venv 有 9668 文件 / 230MB，若日后改目录名，
  venv 不要留在旧目录**（会让 venv 与源码分属两个目录，比现在更难维护）。

## 验收
- [x] 五类分节清晰，每节有中文注释
- [x] `git ls-files` 数 = **207**，与改动前**完全一致**（没有文件被意外忽略掉）
- [x] `git status --porcelain` 仅含 `.gitignore` 自身
- [x] `git status --ignored` 里 `venv/` `__pycache__/`(15) `node_modules/`
      `.env`(2) `*.log`(7) `_validated.json` 均正常
- [x] **行为验证**（造临时文件 + `check-ignore -v`）：`cache/`→命中
      `.gitignore:110`、`dump-*.zip`→命中 `:112`、`.runtime/`→命中 `:47`；
      `_probe.db` 确认**未被**忽略（符合「故意不加」的设计）
- [x] 全文件无行尾注释残留（`^\S` 且含 `#` 的行 = 0 处）

> ⚠️ **提交原则（用户明确）：生成物也要提交。**
> 判据是「**使用环境能否重建**」，而不是「是不是手写的」：
> - **必须提交**：源码、依赖声明与锁文件、配置模板、**以及运行必需的构建产物**
>   （`股票池追踪系统.exe` —— 使用环境不装编译器，拉下来就要能直接跑）。
> - **必须忽略**：可重建且不随版本走的东西 —— 构建缓存（`__pycache__`、`*.pyc`）、
>   虚拟环境（`venv/`）、依赖目录（`node_modules/`）、日志与运行期状态（`logs/`、`*.log`、`*.pid`）、
>   凭据真值（`.env`）。

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
