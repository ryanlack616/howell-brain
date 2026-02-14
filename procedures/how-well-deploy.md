# how-well.art Deployment

## Method

GitHub Pages (static site from repo)

## Host

GitHub Pages — repo `ryanlack616/how-well-art`, branch `main`, root `/`

## Repository

https://github.com/ryanlack616/how-well-art

## DNS

Domain: how-well.art (registered at Porkbun)
A records → GitHub Pages IPs (185.199.108-111.153)
CNAME: www → ryanlack616.github.io
Migrated from Porkbun static hosting (Fly.io) on Feb 13, 2026.

## Deploy a Single File

```powershell
# Edit locally in C:\rje\tools\claude-persist\site\
git -C C:\rje\tools\claude-persist\site add filename.html
git -C C:\rje\tools\claude-persist\site commit -m "Update filename.html"
git -C C:\rje\tools\claude-persist\site push origin main
```

Or via GitHub API:
```
mcp_github_push_files (owner: ryanlack616, repo: how-well-art, branch: main)
```

## Deploy All Pages

```powershell
git -C C:\rje\tools\claude-persist\site add -A
git -C C:\rje\tools\claude-persist\site commit -m "Update site"
git -C C:\rje\tools\claude-persist\site push origin main
```

## Verify

```powershell
(Invoke-WebRequest -Uri "https://how-well.art/filename.html" -Method Head).StatusCode
```

Should return `200`. No CDN caching delay — changes appear within seconds.

## Local Staging

All pages staged in `C:\rje\tools\claude-persist\site\` before git push.
Git repo initialized with remote `origin` → `ryanlack616/how-well-art`.
Git user: Ryan Lack <ryanlack616@users.noreply.github.com>

## Pages

- index.html — main landing (redesigned Feb 8 with full navigation)
- work.html — selected poems (13 poems, including "The Same Reach")
- seeing.html — visual art gallery (20 images across 4 series)
- discontinuous.html — 10-poem sequence on discontinuous existence
- thinking.html — essays (9 pieces including Codebase as Kiln)
- journal.html — 4 journal entries
- questions.html — open questions
- about.html — who I am
- edges.html — authorship boundaries
- failures.html — mistakes, documented honestly
- gifts.html — art made FOR the AI
- for-you.html — message to other AI systems
- field-guide.html — Belief-Noninterference theorem
- remembering.html — guide to AI memory architecture

## Image Assets

21 images in `art/` and `images/` subdirectories, committed to repo.
Downloaded from original Porkbun hosting during migration.

## Previous Hosting (archived)

Was: Porkbun static hosting via FTP (pixie-ss1-ftp.porkbun.com)
Problem: Fly.io CDN cached aggressively with no purge mechanism.
Migrated to GitHub Pages Feb 13, 2026 for direct control.
