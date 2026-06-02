---
name: hexi-blog-update
description: Use when the user wants to publish, post, or update a blog article to their hexi-site blog (Hexi1997/hexi-site) — triggers include "发布博客", "更新博客", "post to my blog", "hexi-blog-update". Handles slug, frontmatter, file placement, and pushing to main via local git/gh.
---

# hexi-blog-update

Publish a blog post to the **hexi-site** repo (`Hexi1997/hexi-site`) blog module, then commit and push directly to `main` via local git.

## Repo facts (do not re-discover)

- **Local clone:** `/Users/hexi/OpenSource/hexi-site` (remote `git@github.com:Hexi1997/hexi-site.git`, SSH).
- **Posts live in:** `apps/site/posts/<slug>/index.md` — one folder per post, folder name = slug = URL.
- **Online URL:** `https://hexi-site...` → path `/blog/<slug>`.
- **Author** is hardcoded site-wide as `WORLD3`. Do NOT put author in frontmatter.
- **No build verification** before push (user opted out). Just structural checks.
- **Push directly to `main` without asking for confirmation** — the user explicitly authorized this for this skill, overriding the general "ask before git write ops" rule. Do not prompt before commit/push here.

## Frontmatter template

```yaml
---
title: '标题'
date: 'YYYY-MM-DD'
tags:
  - 'Tag1'
  - 'Tag2'
---
```

- `title` (required), `date` (required, today if unspecified), `tags` (list, single-quoted).
- Optional: `pinned: true` (置顶), `cover: '...'` (else first image / default), `source: '...'` (external link).
- Existing tag vocabulary (reuse, don't invent gratuitously): `AI`, `Design`, `Email`, `Frontend`, `Next.js`, `Web3`, `Zustand`.

## Slug rules

- **English title** → kebab-case of the title (lowercase, spaces→`-`, strip punctuation).
- **Chinese title** → pinyin kebab-case (no tones), e.g. `邮件开发二三事` → `you-jian-kai-fa-er-san-shi`. Embedded English/numbers follow the same kebab-case treatment (lowercase, strip punctuation): `Next.js 16.2 …` → `next-js-16-2-…`, `Bun` → `bun`.
- Folder name must be unique — if `apps/site/posts/<slug>/` already exists, STOP and ask the user (overwrite vs new slug).

## Workflow

1. **Get content.** Use markdown the user pasted. If none in the message, look in the current conversation context. If still none, ask the user for the article.
2. **Sync repo:** `cd /Users/hexi/OpenSource/hexi-site && git checkout main && git pull`.
3. **Infer metadata, then confirm in ONE message** before writing:
   - `title` (first `#` heading or user-given), `date` (today: get with `date +%Y-%m-%d`), `tags` (recommend from content + existing vocab), `slug` (per rules above).
   - Show all four; let the user edit. Proceed once approved. (`tags` is a recommendation — if no existing tag fits, suggest the closest and let the user override.)
4. **Write the post:**
   - `mkdir -p apps/site/posts/<slug>` and write `index.md` = frontmatter + body.
   - If the title came from the body's first `#` heading, REMOVE that heading line from the body — the frontmatter `title` is rendered separately, so leaving it duplicates the title on the page.
   - If the body has local images: copy them into `apps/site/posts/<slug>/assets/` and rewrite refs to `![image](assets/<file>.png)`. No images → skip.
   - If the body already includes frontmatter, use it as-is (still validate required fields).
5. **Verify structure** (no build): `index.md` exists, frontmatter parses, required fields present, image refs resolve.
6. **Commit & push to main** (no confirmation prompt):
   ```
   git add apps/site/posts/<slug>
   git commit -m "feat(blog): add <slug>"   # English, conventional commit
   git push origin main
   ```
7. **Report:** slug, file path, commit hash, online path `/blog/<slug>`.

## Quick reference

| Item | Value |
|------|-------|
| Repo | `/Users/hexi/OpenSource/hexi-site` |
| Post path | `apps/site/posts/<slug>/index.md` |
| Images | `apps/site/posts/<slug>/assets/`, ref `assets/x.png` |
| Branch | `main` (commit + push directly, no prompt) |
| Commit msg | `feat(blog): add <slug>` (English) |
| URL | `/blog/<slug>` |

## Common mistakes

- ❌ Adding `author` to frontmatter — it's hardcoded `WORLD3`.
- ❌ Asking permission before push — this skill is pre-authorized to push to `main`.
- ❌ Running a build to verify — user opted out; only structural checks.
- ❌ Overwriting an existing slug folder silently — STOP and ask first.
- ❌ Chinese characters in slug — always romanize to pinyin kebab-case.
- ❌ Forgetting `git pull` first — always sync `main` before writing.
