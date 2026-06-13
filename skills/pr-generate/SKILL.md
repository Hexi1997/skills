---
name: pr-generate
description: Use when the user asks to summarize recent commits into a PR description or write a pull request title and body from git history (用户提到 "生成 PR"、"总结最近 commit"、"输出 PR"、"pr-generate"). Also when the PR may touch env vars or database/SQL that need highlighting.
---

# pr-generate

读取最近 N 个 commit 的**实际 code diff**，归纳成一份 PR 描述，直接复制到剪贴板（不写临时文件）。
**不是**逐条罗列 commit message——而是看 diff 真正改了什么，按主题归纳；并把**环境变量新增/变更**和**数据库 / SQL 变更**顶到 PR 最显眼的位置。

## 工作流程

1. **先询问 N**：除非用户已在请求里明确给出数字，否则必须先问用户「总结最近几个 commit？」拿到 N 后再继续。不要自己默认一个值。

2. **收集这 N 个 commit 的改动**（范围统一用 `HEAD~<N>..HEAD`）：
   ```bash
   N=<N>
   # commit 列表（仅作追溯参考，不作为正文主体）
   git log -$N --pretty=format:"%h %s"
   # 变更文件总览：先看动了哪些模块
   git diff --stat HEAD~$N..HEAD
   # 完整 diff：归纳改动的真正依据
   git diff HEAD~$N..HEAD
   ```

3. **专项扫描「显眼区块」的来源**（无论改动多大，这一步都不能跳）：
   ```bash
   # 🔑 环境变量：.env* 文件改动 + 代码里新增的 process.env 读取
   git diff --name-status HEAD~$N..HEAD -- '.env*' '**/.env*'
   git diff HEAD~$N..HEAD | rg -n '^\+.*process\.env\.[A-Z_][A-Z0-9_]*'
   # 🗄️ 数据库 / SQL：SQL 文件与 migration 目录
   git diff --name-status HEAD~$N..HEAD -- '*.sql' '**/migrations/**'
   ```
   - `.env` 若被 gitignore 没进 git，则以 `.env.example` 或代码中新增的 `process.env.XXX` 为准。
   - 提取每个新增/变更环境变量的**变量名、用途、必填还是可选、默认值**（看 diff 上下文推断）。
   - 提取数据库变更的**表 / 列 / 索引改动**和**迁移脚本路径**，能判断就标注**在哪个库执行**。

4. **生成内容**（基于 diff 归纳，不逐行复述 diff、不逐条复述 commit）：
   - **PR 标题**：英文，遵循 conventional commit 风格（如 `feat(scope): ...`），概括整批改动的主题。
   - **PR 正文**：套用下方模板。先放「⚠️ 部署须知」（仅当扫到 env / SQL 时保留），再放按模块/功能归纳的「改动概述」，最后可附 commit 短列表供追溯。

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
```

## 注意

- N 由用户决定，绝不自行假设。
- 标题用英文，正文说明用中文（除非用户另有要求）。
- **以 diff 为准归纳**，不是把 commit message 抄一遍；按模块/功能聚合，相关 commit 合并讲。
- **env 与 SQL 必须主动扫一遍并置顶**：这是 PR review 与上线时最容易踩坑、最该一眼看到的信息；没有就省略该节，有就放在改动概述之前。
- **绝不在 PR 里写出密钥 / 敏感值**：环境变量只列**变量名 + 用途**。`*_KEY` / `*_SECRET` / `*_TOKEN` / `PASSWORD` / `DATABASE_URL` 等的实际值、连接串里的口令一律不要粘进 PR——PR 会进 git / 平台，等于泄密。
- 保持简洁：「改动概述」抓主干，细节交给 diff 本身。
- **不写临时文件**，结果只进剪贴板 + 对话预览。
```
