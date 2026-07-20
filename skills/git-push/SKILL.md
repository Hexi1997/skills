---
name: git-push
description: Use when pushing commits to the remote (用户提到 "push"、"推一下"、"推到远程"、"git push"、"强推"、"force push"). Always resolves the push target to the CURRENT branch instead of master/main, sets upstream on first push, brakes on protected branches, and uses --force-with-lease for force pushes.
---

# git-push

把提交推到远端。核心约定只有一条，但它是默认行为里最容易出事的一条：

> **push 目标永远是「当前分支」，动态解析得到，命令里绝不出现字面量 `master` / `main`。**

## 步骤

```bash
# 1. 解析当前分支（唯一的真相源，不要凭记忆/凭仓库主分支名猜）
BRANCH=$(git rev-parse --abbrev-ref HEAD)

# 2. 受保护分支刹车：master / main 不直接推（见下节）
case "$BRANCH" in
  master|main) echo "⚠️ 当前在受保护分支 $BRANCH，先跟用户确认" ;;
esac

# 3. 有没有 upstream，决定用哪条 push
if git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' >/dev/null 2>&1; then
  git push                          # 已绑定过，直接推
else
  git push -u origin "$BRANCH"      # 首次推，顺带绑定远程分支
fi
```

`HEAD` 处于 detached 状态时 `$BRANCH` 会是 `HEAD` —— 这时**不要推**，告诉用户当前没在任何分支上。

## 受保护分支（master / main）

当前分支是 `master` 或 `main` 时**停下来**，不要执行 push。告诉用户：

- 你现在在受保护分支 `<branch>` 上；
- 建议改成：切个新分支再推（`git switch -c <new-branch> && git push -u origin <new-branch>`）。

只有用户明确表示「就是要推 master / 确认推」才执行。用户一开口说的那句「push」**不构成**对推 master 的同意 —— 他多半以为自己在功能分支上。

## 强推

需要强推时（典型：rebase / amend 之后远端历史被改写）：

```bash
git push --force-with-lease
```

- **一律 `--force-with-lease`，禁止裸 `--force`**：前者会在远端出现你没见过的新 commit 时拒绝推送，不会静默盖掉别人的工作。
- **强推前必须先跟用户确认**，哪怕用户已经说了「push」。
- 首次推 + 强推的组合：`git push -u --force-with-lease origin "$BRANCH"`。

## push 成功后

回一行结果，让用户知道推到哪了、下一步能干嘛：

```bash
git rev-parse --abbrev-ref '@{upstream}'    # 例：origin/feat-xxx
```

输出格式：`已推送 <branch> → <upstream>`，并附 PR 链接。链接优先从 push 的 stderr 里拿（GitHub 首次推送会直接打印 "Create a pull request" URL），否则自己拼：

```bash
git remote get-url origin \
  | sed -E 's#^git@([^:]+):#https://\1/#; s#\.git$##' \
  | xargs -I{} echo "{}/compare/$BRANCH?expand=1"
```

## push 被拒（non-fast-forward）

远端已有本地没有的 commit。**不要自作主张 pull / rebase / 强推。** 报告情况并给建议，等用户决定：

- 想保留远端提交 → `git pull --rebase` 后重推；
- 确认远端那些提交该被丢弃 → 走上面的强推流程（再确认一次）。

## Quick Reference

| 项 | 规则 |
|----|------|
| 推哪个分支 | `git rev-parse --abbrev-ref HEAD`，动态取 |
| 字面量分支名 | 命令里**不许**出现 `master` / `main` |
| master / main 上 | 停下 + 警告 + 等明确确认 |
| 首次 push | `git push -u origin "$BRANCH"` |
| 后续 push | `git push` |
| 强推 | `--force-with-lease`，先确认，禁止裸 `--force` |
| 被拒 | 只报告 + 给建议，不自动 pull/rebase/强推 |
| 成功后 | 输出 `<branch> → <upstream>` + PR 链接 |

## Common Mistakes

- **`git push origin master`** —— 最典型的事故。用户在 `feat-x` 上说了句「push」，结果推了个不存在/不相干的 master。永远先解析当前分支。
- **把仓库主分支名当 push 目标**：仓库主分支是 `master` 不代表这次要推 `master`。主分支名只在「判断要不要刹车」时用得上。
- **用 `-u` 覆盖已有 upstream**：分支已绑定时直接 `git push`，别无脑加 `-u origin "$BRANCH"` 去改绑定。
- **裸 `--force`** → 可能静默覆盖同事刚推的 commit。只用 `--force-with-lease`。
- **push 被拒就顺手 `git pull`** → 可能产生意料外的 merge commit，或把远端改动混进来。先问。
- **detached HEAD 时照推** → `$BRANCH` 是字面量 `HEAD`，推上去会建一个叫 `HEAD` 的远程分支。先检查。
