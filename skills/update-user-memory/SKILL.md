---
name: update-user-memory
description: Use when the user wants to save, update, persist, or "remember" a global / user / AI / AGENT instruction or memory so it applies across all coding clients (Claude, Codex, Cursor). Triggers include "更新用户记忆", "保存全局指令", "记住…到全局 AGENT 指令", "save to global agent memory", "remember this globally". Assumes agent-root-cli has been set up once (init + link).
---

# Update User Memory

## Overview

`agent-root-cli` keeps a single source file (default `~/AGENTS.md`) and **symlinks** each client's instruction file to it:

```
~/.claude/CLAUDE.md  -> ~/AGENTS.md
~/.codex/AGENTS.md   -> ~/AGENTS.md
Cursor user rule      @~/AGENTS.md  (manual)
```

**Core principle: there is exactly ONE file to edit — the source.** Writing to the source propagates to every client automatically. Never write to `~/.claude/CLAUDE.md` or `~/.codex/AGENTS.md` directly.

## Workflow

Do these in order. Do not skip the prerequisite gate.

### 1. Capture the requirement

Extract the instruction to persist from the user's message. If it is vague (e.g. "更新一下记忆" with no content), ask what to save before doing anything else.

### 2. Prerequisite gate — STOP on failure

Run:

```bash
ls -la ~/.claude/CLAUDE.md && readlink ~/.claude/CLAUDE.md
```

The setup is valid ONLY if `~/.claude/CLAUDE.md` **is a symlink** AND its target file exists. Capture that target — it is the real source (this handles custom `--source` installs too).

If `~/.claude/CLAUDE.md` is missing, is a regular file (not a symlink), or its target does not exist: **STOP. Write nothing.** Tell the user the setup is incomplete and give them exactly:

```bash
npm install -g agent-root-cli && agent-root-cli init && agent-root-cli link
```

If `~/.claude/CLAUDE.md` already exists as a regular file with real content, warn the user first: `agent-root-cli link` deletes that file before creating the symlink, so they should copy its content into the source (`~/AGENTS.md`) — or back it up — before running `link`.

Wait for the user to confirm they ran it, then re-check. Do NOT create files yourself, and do NOT run the setup for them unless they ask.

### 3. Compose & categorize

Read the resolved source file. Phrase the requirement as one concise imperative bullet. Pick the best existing `## section` (e.g. `## Communication`, `## Engineering`); create a new section only if none fits. If a near-duplicate bullet already exists, say so and stop rather than adding a redundant line.

### 4. Confirm before writing

Show the user the exact bullet and the target section (a small diff). Wait for approval. Do not write first and ask later.

### 5. Write & report

`Edit` the resolved source file only. Then confirm: the change is now live for Claude and Codex via the symlinks, and for Cursor via its `@` user rule — no sync command needed.

## Quick Reference

| Step | Action |
|------|--------|
| Find source | `readlink ~/.claude/CLAUDE.md` (the symlink target IS the source) |
| Setup commands | `npm install -g agent-root-cli && agent-root-cli init && agent-root-cli link` |
| File to edit | The resolved source only (default `~/AGENTS.md`) |
| Propagation | Automatic via symlinks — no extra command |

## Common Mistakes

- **Writing to `~/.claude/CLAUDE.md` or `~/.codex/AGENTS.md`.** Those are symlinks; edit the source. Creating a regular file there breaks the single-source model.
- **Running `agent-root-cli sync`.** That command does not exist — the commands are `init` and `link`. No command is needed to propagate content; the symlinks already share one file.
- **Skipping the prerequisite gate.** Do not assume the setup is valid. If the symlink check fails, stop and guide the user.
- **Writing before confirming.** Always show the bullet + target section and get approval first.
- **Adding a duplicate bullet.** Check existing content; skip if already covered.
