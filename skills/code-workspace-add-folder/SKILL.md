---
name: code-workspace-add-folder
description: Use when a git worktree or project directory should show up in a VS Code multi-root workspace file (用户提到 "加到 workspace"、"把这个分支/worktree 加进 code-workspace"、"添加到 novel.code-workspace"、"add folder to VS Code workspace"). Defaults to the current worktree root and ~/Desktop/novel.code-workspace.
---

# code-workspace-add-folder

把一个目录（默认 = **当前 git worktree 根**）写进 VS Code 的 `.code-workspace` 文件的 `folders` 数组。
默认目标文件：`~/Desktop/novel.code-workspace`。

## 用法

```bash
# 最常见：把当前所在的 worktree 加进 novel.code-workspace
node ~/.agents/skills/code-workspace-add-folder/add-folder.mjs

# 指定目录
node ~/.agents/skills/code-workspace-add-folder/add-folder.mjs /path/to/dir

# 指定别的 workspace 文件
node ~/.agents/skills/code-workspace-add-folder/add-folder.mjs -w ~/Desktop/other.code-workspace
```

在 novel 仓库的某个 worktree 里直接跑第一条即可——脚本用 `git rev-parse --show-toplevel` 取当前 worktree 根，
**不是** repo 主工作树根，所以在 `.claude/worktrees/<branch>` 里跑就是加这个分支的目录。

跑完把脚本输出的 `path:` 和绝对路径回报给用户；VS Code 里该 workspace 已打开的话，需要重新打开 workspace 才会出现新文件夹。

## 脚本行为

| 项 | 规则 |
|----|------|
| 默认目录 | `git rev-parse --show-toplevel`（当前 worktree 根） |
| 默认 workspace | `~/Desktop/novel.code-workspace` |
| 幂等 | 按**绝对路径**比对已有条目，已存在则原样退出、不写文件 |
| 写入形式 | 相对 workspace 文件所在目录的相对路径；`..` 超过 4 层（跨盘/跨顶层目录）时改用绝对路径 |
| 缩进 | tab（与 VS Code 自己保存的格式一致） |
| 带注释的 jsonc | **拒绝改写并退出 1**，提示手动加——JSON.parse + stringify 会吞掉注释 |

## Common Mistakes

- **手写 `folders` 条目时算错相对层级**：`novel` 的 worktree 在 `whale/novel/.claude/worktrees/<branch>`，
  相对 Desktop 是 `../whale/novel/.claude/worktrees/<branch>`。用脚本，别数 `../`。
- **在主工作树里跑却以为加的是分支目录**：脚本取的是当前目录所属的 worktree。先 `pwd` 确认。
- **直接用编辑工具改 workspace 文件**：容易破坏 tab 缩进、重复添加。脚本已做去重。
