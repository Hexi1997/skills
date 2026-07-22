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

# 2. 受保护分支刹车：当前分支是 master / main（或 detached）时停下（见下节）
case "$BRANCH" in
  master|main) echo "⚠️ 当前在受保护分支 $BRANCH，先跟用户确认" ;;
  HEAD)        echo "⚠️ detached HEAD，当前不在任何分支上，不要推" ;;
esac

# 3. 解析 upstream，并【校验它的分支名和本地一致】再决定怎么推
UPSTREAM=$(git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null)  # 如 origin/master
UP_BRANCH=${UPSTREAM#*/}   # 去掉 remote 前缀 → master / feat-x

if [ -n "$UPSTREAM" ] && [ "$UP_BRANCH" = "$BRANCH" ]; then
  git push                          # upstream 同名 → 安全直推
else
  # 走这里的两种情况：
  #   ① 没 upstream（首次推）；
  #   ② upstream 分支名 ≠ 本地分支名（典型：从 master 切出，upstream 继承成了 origin/master）。
  #      这种【直推会打到 master！】所以绝不 git push，一律推「同名远程分支」并 -u 改绑。
  [ -n "$UPSTREAM" ] && echo "⚠️ 本地 $BRANCH 的 upstream 是 $UPSTREAM（名字不符）；直推会打到 $UP_BRANCH，改推 origin/$BRANCH 并改绑。"
  git push -u origin "$BRANCH"      # 推同名远程分支；首次推 / 改绑都用它
fi
```

`HEAD` 处于 detached 状态时 `$BRANCH` 会是 `HEAD` —— 这时**不要推**，告诉用户当前没在任何分支上。

## upstream 名字 ≠ 本地分支名（从 master 切出的最常见坑）

从 master `git switch -c feat-x` 切出来的分支，**upstream 常继承成 `origin/master`**（`branch.autoSetupMerge` 默认行为，且分支没单独推过时尤其如此）。后果：

- naive `git push` 会瞄准 **master**（受保护！）。`push.default=simple`（默认）下 git 会以"分支名不符"报错拒绝、并提示 `git push origin HEAD:master`——**千万别照它做**，那就是往 master 推；换成别的 `push.default` 甚至会静默打到 master。
- **只用「当前分支名是不是 master」做刹车不够**：分支名是 `feat-x`、刹车放行，upstream 却是 `origin/master`，一推就中招。

**正确姿势**：push 前解析 `@{upstream}` 的分支名，和当前分支名比对；**不一致（尤其指向 master/main）就绝不直推**，改 `git push -u origin <当前分支>` 推同名远程分支并改绑 upstream（顺带修正这次误配）。用户那句「push」**从不构成**对推 master 的同意。

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
| 校验 upstream | push 前比对 `@{upstream}` 分支名 vs 当前分支名 |
| 首次 push | `git push -u origin "$BRANCH"` |
| 后续 push | upstream 同名才 `git push` |
| upstream 名字≠分支名 | **不直推**（会打到别的分支/master）；`git push -u origin "$BRANCH"` 推同名分支并改绑 |
| 强推 | `--force-with-lease`，先确认，禁止裸 `--force` |
| 被拒 | 只报告 + 给建议，不自动 pull/rebase/强推 |
| 成功后 | 输出 `<branch> → <upstream>` + PR 链接 |

## Common Mistakes

- **`git push origin master`** —— 最典型的事故。用户在 `feat-x` 上说了句「push」，结果推了个不存在/不相干的 master。永远先解析当前分支。
- **把仓库主分支名当 push 目标**：仓库主分支是 `master` 不代表这次要推 `master`。主分支名只在「判断要不要刹车」时用得上。
- **upstream 指向别的分支名却照样直推**（最阴的坑）：从 master 切出的 `feat-x` 常 track `origin/master`，`git push` 会瞄 master。push 前必须比对 `@{upstream}` 分支名 vs 当前分支名，不一致就 `-u origin "$BRANCH"` 改绑到同名分支。
- **git 提示 `git push origin HEAD:master` 就照做**：upstream 名字不符时 git 会这么提示，那是往 master 推——别信，改推同名分支。
- **无脑 `-u` 覆盖【正确】的 upstream**：upstream 名字已和分支一致时直接 `git push`，别多此一举加 `-u` 去改绑定。
- **裸 `--force`** → 可能静默覆盖同事刚推的 commit。只用 `--force-with-lease`。
- **push 被拒就顺手 `git pull`** → 可能产生意料外的 merge commit，或把远端改动混进来。先问。
- **detached HEAD 时照推** → `$BRANCH` 是字面量 `HEAD`，推上去会建一个叫 `HEAD` 的远程分支。先检查。
