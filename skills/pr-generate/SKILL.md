---
name: pr-generate
description: Use when the user asks to summarize recent commits into a PR description or write a pull request title and body from git history (用户提到 "生成 PR"、"总结最近 commit"、"输出 PR"、"pr-generate"). Also when the PR may touch env vars or database/SQL that need highlighting. Supports two diff ranges - recent N commits, or the diff against a remote base branch (如 "跟 master 的 diff"、"对比 origin/develop"、"base=master").
---

# pr-generate

读取改动范围内的**实际 code diff**，归纳成一份 PR 描述，直接复制到剪贴板（不写临时文件）。
**不是**逐条罗列 commit message——而是看 diff 真正改了什么，按主题归纳；并把**环境变量新增/变更**和**数据库 / SQL 变更**顶到 PR 最显眼的位置。

## 两种模式（决定「怎么归纳」）

| 模式 | 触发条件 | 归纳依据 |
|------|----------|----------|
| **diff 模式**（默认） | 默认走这个 | 读完整 `git diff`，看代码真正改了什么 |
| **commit 模式** | **仅当用户显式要求**（如「根据 commits 生成 PR」「按 commit message 总结」「这次 diff 太大，用 commit 来写」） | 读 commit subject + body，不读完整 diff |

- **默认永远是 diff 模式**——它最准。只有当本次 diff 巨大、逐行读不现实，且**用户明确说**要按 commit 来生成时，才切到 commit 模式。
- **不要因为「diff 看起来很大」就擅自切模式**：除非用户明说，否则一律 diff 模式。
- 两种模式下，**env / SQL 的「显眼区块」扫描都不能跳**（见步骤 3）——commit message 经常漏掉这些上线必看项，所以即便 commit 模式也要单独扫这两类。

## 改动范围（决定「diff 哪一段」，与上面「两种模式」正交）

「两种模式」决定怎么归纳，「改动范围」决定 diff 哪一段，两者自由组合（生成正式 PR 时最常用：**diff 模式 + 基准分支范围**）。

| 范围 | 触发条件 | diff 范围 | commit 列表范围 |
|------|----------|-----------|-----------------|
| **commit 数 N**（默认） | 用户给 N，或只说「最近几个 commit」 | `HEAD~N..HEAD` | `HEAD~N..HEAD` |
| **远端基准分支 base** | 用户给出基准 / 目标分支（如「跟 master 的 diff」「对比 origin/develop」「base=master」「这个 PR 要并进 release」） | `origin/<base>...HEAD`（**三点** = merge-base..HEAD，正是 PR 真正改动） | `origin/<base>..HEAD`（**两点** = HEAD 上、base 没有的 commit） |

⚠️ **基准分支模式两个最容易错的点**：
- **先 `git fetch` 再 diff**：否则 `origin/<base>` 是本地旧引用，diff 不准。
- **diff 用三点 `...`、log 用两点 `..`**：三点 diff 自动以 merge-base 为基准，不会把 base 分支后续提交的改动也算进本 PR；log 两点只列本 PR 的 commit。**别两者都用同一种点号**（两点 diff 会混入 base 端改动）。

## 工作流程

1. **先确定改动范围**——拿到后设好下面 bash 全程要用的 `DIFF_RANGE` / `LOG_RANGE` 两个变量：

   - 用户给了**基准 / 目标分支**（master / origin/develop /「跟 xx 分支 diff」/ base=xxx）→ 基准分支模式：
     ```bash
     BASE=master          # 用户给的基准分支；去掉可能带的 origin/ 前缀
     REMOTE=origin        # 一般是 origin；用户指定别的就改
     git fetch "$REMOTE" "$BASE"            # 必须先 fetch，确保基准是最新的
     DIFF_RANGE="$REMOTE/$BASE...HEAD"      # 三点：PR 真正 diff（merge-base..HEAD）
     LOG_RANGE="$REMOTE/$BASE..HEAD"        # 两点：本 PR 的 commit 列表
     ```
   - 用户给了 **N**（或只说「最近几个 commit」并给了数）→ commit 数模式：
     ```bash
     N=<N>
     DIFF_RANGE="HEAD~$N..HEAD"
     LOG_RANGE="HEAD~$N..HEAD"
     ```
   - **两者都没给** → 先问用户：「按最近几个 commit（给 N），还是跟某个分支 diff（给分支名）？」拿到答案再继续，**不要自行默认 N、也不要自行假设基准分支**。

2. **收集改动**（范围一律用步骤 1 设好的 `$DIFF_RANGE` / `$LOG_RANGE`）：

   **diff 模式（默认）**——读完整 diff 作为归纳依据：
   ```bash
   # commit 列表（仅作追溯参考，不作为正文主体）
   git log $LOG_RANGE --pretty=format:"%h %s"
   # 变更文件总览：先看动了哪些模块
   git diff --stat $DIFF_RANGE
   # 完整 diff：归纳改动的真正依据
   git diff $DIFF_RANGE
   ```

   **commit 模式（用户显式要求时）**——不读完整 diff，改读 commit subject + 完整 body：
   ```bash
   # commit subject + 完整 body：归纳改动的依据
   git log $LOG_RANGE --pretty=format:"%h %s%n%b%n----"
   # 仍看文件总览，确认涉及哪些模块（轻量，不读 diff 内容）
   git diff --stat $DIFF_RANGE
   ```
   - commit 模式下，「改动概述」按 commit message 归纳；若某些 commit message 太单薄、说不清做了什么，可针对那几个文件补看局部 diff，但不必读整个大 diff。

3. **专项扫描「显眼区块」的来源**（无论改动多大、无论哪种模式 / 哪种范围，这一步都不能跳）：
   ```bash
   # 🔑 环境变量：.env* 文件改动 + 代码里新增的 process.env 读取
   git diff --name-status $DIFF_RANGE -- '.env*' '**/.env*'
   # 只对 .env* 与代码文件做定向 diff 抓新增 process.env（commit 模式也照跑，不读全量大 diff）
   git diff $DIFF_RANGE -- '.env*' '**/.env*' '*.ts' '*.js' | grep -nE '^\+.*process\.env\.[A-Z_][A-Z0-9_]*'
   # 🗄️ 数据库 / SQL：SQL 文件与 migration 目录
   git diff --name-status $DIFF_RANGE -- '*.sql' '**/migrations/**'
   ```
   - `.env` 若被 gitignore 没进 git，则以 `.env.example` 或代码中新增的 `process.env.XXX` 为准。
   - 提取每个新增/变更环境变量的**变量名、用途、必填还是可选、默认值**（看 diff 上下文推断）。
   - 提取数据库变更的**表 / 列 / 索引改动**和**迁移脚本路径**，能判断就标注**在哪个库执行**。

4. **生成内容**（diff 模式基于 diff 归纳、commit 模式基于 commit message 归纳；都不逐行复述 diff、不逐条复述 commit）：
   - **PR 标题**：英文，遵循 conventional commit 风格（如 `feat(scope): ...`），概括整批改动的主题。
   - **PR 正文**：套用下方模板。先放「⚠️ 部署须知」（仅当扫到 env / SQL 时保留），再放按模块/功能归纳的「改动概述」。
     - **diff 模式**：末尾可附 `## Commits` 短列表供追溯（用 `git log $LOG_RANGE --pretty=format:"%h %s"` 的结果）。
     - **commit 模式**：**省掉 `## Commits` 这一节**——正文本就是按 commit message 归纳的，再列一遍纯属重复。

5. **复制到剪贴板**：用 here-doc 把完整 PR 内容管道给下面的跨平台复制函数，不要落盘任何文件：
   ```bash
   pr_copy() {
     if   command -v pbcopy   >/dev/null 2>&1; then pbcopy                      # macOS
     elif command -v wl-copy  >/dev/null 2>&1; then wl-copy                     # Linux/Wayland
     elif command -v xclip    >/dev/null 2>&1; then xclip -selection clipboard  # Linux/X11
     elif command -v xsel     >/dev/null 2>&1; then xsel --clipboard --input    # Linux/X11
     elif command -v clip.exe >/dev/null 2>&1; then clip.exe                    # WSL
     elif command -v clip     >/dev/null 2>&1; then clip                        # Windows (Git Bash)
     else cat; echo "[no clipboard tool found — printed above]" >&2; fi
   }
   pr_copy <<'PR_EOF'
   <完整 PR 内容>
   PR_EOF
   ```
   若所有剪贴板命令都不存在，函数会回退为打印内容（用户仍能从对话预览里复制）。

6. 完成后向用户简单汇报：PR 标题 + 「已复制到剪贴板」，并在对话里贴出完整内容供预览。

## PR 内容模板

```markdown
# <英文 PR 标题>

<一两句概述本 PR 的主题与范围>

## ⚠️ 部署须知

> 仅在扫到对应改动时保留本节；两类都没有就整节删掉，别留空标题。

**🔑 环境变量**
- `NEW_ENV_VAR` — 用途说明；必填 / 可选（默认值 …）

**🗄️ 数据库 / SQL**
- 表结构：新增列 `xxx.col_a` / `col_b`、索引 …
- 迁移脚本：`server/db/migrations/<file>.sql`（在 <novel_local→test→prod> 执行）

## 改动概述
- **<模块 / 功能 A>**：从 diff 归纳这块实际做了什么
- **<模块 / 功能 B>**：…

## Commits
- `<short sha> <commit subject>`
- …
<!-- ↑ 仅 diff 模式保留；commit 模式整节删掉（正文已是按 commit message 归纳，无需重复） -->
```

## 注意

- **范围（N 或基准分支）由用户决定，绝不自行假设**：两者都没给就先问，不要默认一个 N、也不要默认 `master`。
- 基准分支模式务必：先 `git fetch`、diff 用三点 `origin/<base>...HEAD`、log 用两点 `origin/<base>..HEAD`。
- 标题用英文，正文说明用中文（除非用户另有要求）。
- **默认以 diff 为准归纳**，不是把 commit message 抄一遍；按模块/功能聚合，相关 commit 合并讲。仅当用户**显式要求**（如本次 diff 太大、让你按 commits 生成）才切到 commit 模式，用 commit message 归纳。
- **env 与 SQL 必须主动扫一遍并置顶**：这是 PR review 与上线时最容易踩坑、最该一眼看到的信息；没有就省略该节，有就放在改动概述之前。
- **绝不在 PR 里写出密钥 / 敏感值**：环境变量只列**变量名 + 用途**。`*_KEY` / `*_SECRET` / `*_TOKEN` / `PASSWORD` / `DATABASE_URL` 等的实际值、连接串里的口令一律不要粘进 PR——PR 会进 git / 平台，等于泄密。
- 保持简洁：「改动概述」抓主干，细节交给 diff 本身。
- **不写临时文件**，结果只进剪贴板 + 对话预览。
```
