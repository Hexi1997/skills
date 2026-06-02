---
name: pr-generate
description: Use when the user asks to summarize recent commits into a PR description or write a pull request title and body from git history (用户提到 "生成 PR"、"总结最近 commit"、"输出 PR"、"pr-generate").
---

# pr-generate

从最近 N 个 commit 生成一份 PR 描述，直接复制到剪贴板（不写临时文件）。

## 工作流程

1. **先询问 N**：除非用户已在请求里明确给出数字，否则必须先问用户「总结最近几个 commit？」拿到 N 后再继续。不要自己默认一个值。
2. **读取 commit**：
   ```bash
   git log -<N> --pretty=format:"%H%n%s%n%b%n---END---"
   ```
3. **生成内容**：
   - **PR 标题**：英文，遵循 conventional commit 风格（如 `feat(scope): ...`），概括这批 commit 的主题。
   - **PR 正文**：按 commit 逐条写（从新到旧），每条包含原始 commit message 标题 + 一句简洁中文说明这个 commit 做了什么。
   - 内容务必**简洁**，不要逐行复述 diff。
4. **复制到剪贴板**：用 here-doc 把完整 PR 内容管道给下面的跨平台复制函数，不要落盘任何文件。该函数按可用性自动选择 mac / linux / win 的剪贴板命令：
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
5. 完成后向用户简单汇报：PR 标题 + 「已复制到剪贴板」，并在对话里贴出完整内容供预览。

## PR 内容模板

```markdown
# <英文 PR 标题>

<一句话概述本 PR 主题>，包含以下 N 个 commit：

## Commits

### 1. `<commit message 标题>`
<一句简洁中文说明>

### 2. `<commit message 标题>`
<一句简洁中文说明>

...
```

## 注意

- N 由用户决定，绝不自行假设。
- 标题用英文，正文说明用中文（除非用户另有要求）。
- 保持简洁，重点是「这个 commit 做了什么」，不是完整 changelog。
- **不写临时文件**，结果只进剪贴板 + 对话预览。
