---
name: git-commit-and-push
description: Use when the user wants to commit and push in one go (用户提到 "提交并推送"、"提交后推上去"、"commit 并 push"、"commit and push"、"/git-commit-and-push"). Covers what that single request does and does NOT authorize, and when to stop between the two halves.
---

# git-commit-and-push

把「生成规范 commit」和「推到当前分支」串成一条链。

> **commit 交给 `git-commit` skill，push 交给 `git-push` skill；本 skill 只管两者之间的交接、刹车和授权边界。**

那两个 skill 装了就直接调用，规则别在这里重写（会漂）。没装时用文末「兜底规则」。

## 流程

1. **看清改动** —— `git status --porcelain`，有暂存内容看 `git diff --staged`，否则看 `git diff`。
   工作区和暂存区都干净 → **停**，报告「没有可提交的改动」；**不要**因为用户说了 push 就空跑一次 push。
2. **提交** —— 走 `git-commit` skill。默认把当前**全部改动一次性提交为单个 commit**，不主动分批（用户明确要求拆分时才拆）。
3. **验收提交** —— commit 退出码非 0（典型：pre-commit / commit-msg hook 失败）→ **停在这里，不 push**，报告失败输出。
   不许 `--no-verify` 绕过，不许 amend 掩盖，不许"先推上去再说"。
4. **推送** —— 走 `git-push` skill：动态解析当前分支、比对 `@{upstream}` 分支名、受保护分支刹车。
5. **汇总一行** —— commit 标题 + short hash，`已推送 <branch> → <upstream>`，附 PR 链接。

## 「提交并推送」这一句授权了什么

= 同意**本次 commit** + **把它推到当前分支**。**不包括**下面任何一项：

| 不在授权范围 | 怎么办 |
|---|---|
| 推 `master` / `main` | 停，按 `git-push` 的受保护分支流程等明确确认 |
| upstream 分支名 ≠ 当前分支名时直推 | 不直推（会打到 master）；`git push -u origin "$BRANCH"` 推同名分支并改绑 |
| 强推（`--force-with-lease`） | 单独再确认一次，哪怕用户已经说了 push |
| push 被拒后 `git pull` / `rebase` / 强推 | 只报告 + 给建议，等用户决定 |
| 跳过 hook（`--no-verify`） | 不做 |
| amend / reset / 改写已有提交 | 不做 |
| 建 PR、合并、部署 | 不做，最多把 PR 链接贴出来 |

一条线：**这条链只新增一个 commit，并把它推到当前分支。任何会改写历史、或落到别人分支上的动作，都要再问一次。**

## 中途失败就地停

| 卡在哪 | 动作 |
|---|---|
| 没有改动 | 报告并结束，不 commit 不 push |
| commit 失败（hook / 空提交 / 冲突未解） | 报告原因，**不 push**；修完重新走流程（新 commit，不 amend） |
| push 被拒（non-fast-forward） | commit **保留在本地**，报告远端有新提交，给出 `git pull --rebase` 或强推两个选项让用户选 |
| 在 master/main 上 | commit 可以先做完（本地安全），push 停下等确认；同时建议 `git switch -c <new-branch>` |
| detached HEAD | 直接停，告诉用户当前不在任何分支上 |

push 失败**不要**回滚刚才的 commit —— 本地提交是有效成果，撤销它只会丢工作。

## Quick Reference

| 项 | 规则 |
|---|---|
| 提交粒度 | 默认全部改动一个 commit，不主动分批 |
| commit message | Conventional Commits，**英文**，subject < 72 字符，祈使句现在时 |
| 敏感文件 | `.env` / 密钥 / 凭据一律不提交 |
| commit 失败 | 不 push，不 `--no-verify` |
| push 目标 | 动态解析的当前分支；命令里不许出现字面量 `master` / `main` |
| master/main | commit 可做，push 停下等确认 |
| push 被拒 | 报告 + 给选项，不自动 pull/rebase/强推 |
| 成功输出 | `<commit 标题> <short-hash>` + `已推送 <branch> → <upstream>` + PR 链接 |

## Common Mistakes

- **commit 的 hook 挂了还接着 push** —— hook 失败说明这次提交根本没成，push 只会把上一次的旧提交推上去，用户以为新改动已上线。
- **没有改动也照跑一遍 push** —— 用户要的是"把这次改动推上去"，没改动就该报告，而不是推个空气。
- **把"提交并推送"当成推 master 的许可** —— 用户说这话时多半以为自己在功能分支上。分支名和 upstream 都要现查。
- **push 被拒就顺手 `git pull`** —— 可能拖进意外的 merge commit 或别人的改动。先问。
- **push 失败后 `git reset` 撤掉刚提交的 commit** —— 提交是好的，问题在远端；撤销纯属自伤。
- **为了"干净"把改动拆成好几个 commit** —— 除非用户要求，默认单 commit。

## 兜底规则（`git-commit` skill 未安装时）

只在调不到 `git-commit` skill 时用这份最小规则完成第 2 步：

```
<type>[optional scope]: <description>

[optional body]
```

- type：`feat` / `fix` / `docs` / `style` / `refactor` / `perf` / `test` / `build` / `ci` / `chore` / `revert`
- 破坏性变更：`type!:` 或加 `BREAKING CHANGE:` footer
- 从**实际 diff** 判断 type / scope，别照抄文件名
- 英文、祈使句、现在时（"add" 不是 "added"）、subject < 72 字符
- 多行用 heredoc：

```bash
git commit -m "$(cat <<'EOF'
<type>[scope]: <description>

<body>
EOF
)"
```

push 那半永远走 `git-push` skill；它不在时，最低限度也要：解析 `BRANCH=$(git rev-parse --abbrev-ref HEAD)`、比对 `@{upstream}` 分支名、名字不符或首次推就 `git push -u origin "$BRANCH"`。
