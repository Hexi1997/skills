---
name: new-worktree
description: Use when starting work on a new branch in a multi-worktree repo and need to create the git worktree (用户提到 "新开 worktree"、"建 worktree"、"开个 worktree 做 X"、"切个分支并行开发"、"new worktree"). Enforces fetch-first, branch off latest origin/master, and sibling-of-repo-root dir named after the branch.
---

# new-worktree

为一个**新分支**创建 git worktree，固定遵守三条约定（这些是默认行为里最容易漏掉的）：

1. **先更新远端**：`git fetch origin`（不是"可选"，是第一步）。
2. **基于最新的 `origin/master` 切分支**（不是当前 HEAD）。
3. **目录平级于 repo 根（主工作树），目录名 = 分支名**：从 repo 根执行 `git worktree add ../<branch>`。

> 不要用原生 worktree 工具（如 `EnterWorktree`）来建——它自行决定目录位置与 base，无法保证上面三条。这里直接用 `git`。

## 步骤

```bash
# 1. 定位主工作树根（哪怕当前在某个子 worktree 里 / repo 根是嵌套目录）
ROOT=$(git worktree list --porcelain | sed -n '1s/^worktree //p')
cd "$ROOT"

# 2. 更新远端（强制，第一步）
git fetch origin

# 3. 确定 base：优先 origin/master，没有就退回远端默认分支
BASE=origin/master
git rev-parse --verify --quiet "$BASE" >/dev/null \
  || BASE=$(git symbolic-ref --quiet refs/remotes/origin/HEAD | sed 's#^refs/remotes/##')

# 4. 建 worktree：新分支 <branch>，目录平级于 repo 根、按分支名命名
BRANCH=feat-xxx            # ← 换成实际分支名
git worktree add -b "$BRANCH" "../$BRANCH" "$BASE"

# 5. 进入并核对
cd "../$BRANCH"
git worktree list
git log --oneline -1      # 应指向 origin/master 的最新提交
```

完成后告诉用户最终目录绝对路径（`pwd`）。

## 分支已存在的情况

- 本地已有同名分支：去掉 `-b`，直接 `git worktree add "../$BRANCH" "$BRANCH"`。
- 远端已有同名分支（想继续它）：`git worktree add "../$BRANCH" -b "$BRANCH" "origin/$BRANCH"`。
- 仅当是**全新分支**才用步骤 4 的 `-b ... "$BASE"`。

## Quick Reference

| 项 | 规则 |
|----|------|
| 更新远端 | `git fetch origin`，第一步，强制 |
| base | `origin/master`（缺失才退回 `origin/HEAD`） |
| 目录位置 | repo 根的平级目录（`../`） |
| 目录名 | = 分支名 |
| 建法 | `git worktree add`，**不用**原生 worktree 工具 |

## Common Mistakes

- **把 fetch 当可选 / 跳过** → base 是过期的 origin/master。永远先 `git fetch origin`。
- **基于当前 HEAD 切** → 带进当前分支的未合并改动。必须显式传 `"$BASE"`。
- **目录算错层级**：repo 根可能嵌套（如 `whale/novel/novel`）。用 `git worktree list` 取主工作树根，再 `../<branch>`，不要凭目录名猜。
- **用原生 EnterWorktree** → 目录位置/base 不可控，违反约定。
