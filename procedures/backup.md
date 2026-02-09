# Backup Procedure

## Script
`C:\Users\PC\Desktop\backup.ps1`

## Usage

```powershell
# Back up everything (projects + persist + site + archive)
.\backup.ps1

# Just projects (still includes claude-persist automatically)
.\backup.ps1 -Projects

# Just the website
.\backup.ps1 -Site

# Just persist files
.\backup.ps1 -Persist
```

## Key behavior
**claude-persist is always included** regardless of which flag you use. It backs up with every run. This is by design — memory is never optional.

## What gets backed up

| Flag | Source | Compression |
|------|--------|-------------|
| `-Persist` | `C:\Users\PC\Desktop\claude-persist` | mx=9 (max) |
| `-Projects` | `C:\Users\PC\Desktop\projects` | mx=5 (balanced) |
| `-Site` | `C:\Users\PC\how-well-art` | mx=9 (max) |
| `-Archive` | Copies theorem 7z to desktop | n/a |

## Excludes
node_modules, .venv, __pycache__, .git, dist, .lake, .next

## Output
Files land on the Desktop as `*-backup-YYYY-MM-DD.7z`

## Gotchas
- Projects backup is ~680MB — takes a minute or two
- If backup.ps1 won't run, check execution policy: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`
