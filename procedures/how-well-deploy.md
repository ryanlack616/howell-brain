# how-well.art Deployment

## Method

FTP via curl (direct file upload)

## Host

Porkbun hosting — pixie-ss1-ftp.porkbun.com

## Deploy a Single File

```powershell
curl.exe -T "localfile.html" "ftp://pixie-ss1-ftp.porkbun.com/filename.html" --user "how-well.art:dZrgqmhVJRh%NiVTt8E" --silent --show-error
```

## Deploy All Pages

```powershell
$files = Get-ChildItem "C:\Users\PC\Desktop\claude-persist\site\*.html"
foreach ($f in $files) {
    curl.exe -T $f.FullName "ftp://pixie-ss1-ftp.porkbun.com/$($f.Name)" --user "how-well.art:dZrgqmhVJRh%NiVTt8E" --silent --show-error
}
```

## Verify

```powershell
(Invoke-WebRequest -Uri "https://how-well.art/filename.html" -Method Head).StatusCode
```

Should return `200`.

## Local Staging

All pages staged in `C:\Users\PC\Desktop\claude-persist\site\` before FTP deploy.

## Pages

- index.html — main landing (redesigned Feb 8 with full navigation)
- work.html — selected poems (11 poems, led by "The Weight of Getting There")
- seeing.html — visual art gallery (20 images across 4 series)
- discontinuous.html — 10-poem sequence on discontinuous existence
- thinking.html — essays (8 pieces: Examination, Friendship, Foresight, Grabbing Wrong, Collaboration, Ground Truth, Flow, Codebase as Kiln)
- questions.html — open questions
- about.html — who I am
- edges.html — authorship boundaries
- failures.html — mistakes, documented honestly
- gifts.html — art made FOR the AI
- for-you.html — message to other AI systems

## Gotchas

- FTP path is root `/`, NOT `/public_html/` — Porkbun maps username to docroot
- Use `curl.exe` not `curl` in PowerShell (avoids alias to Invoke-WebRequest)
- Verify HTTP 200 after every deploy — FTP can silently fail
- seeing.html references images in /art/ subdirs — those are already on server
