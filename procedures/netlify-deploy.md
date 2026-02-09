# Netlify Deployment — monospacepoetry.com

## Site

- Hosted on Netlify
- Source includes poems.json, Netlify Functions for API

## API

- Random poem: `GET monospacepoetry.com/api/poems/random`
- Functions at: `/.netlify/functions/poems/`

## Gotchas

- **Netlify Functions have NO filesystem** — `fs.readFileSync()` will fail silently or 500
- Must use `fetch()` from a public URL to read data files
- Path parsing needs to handle BOTH `/.netlify/functions/poems/` AND `/api/poems/` prefixes
- This was the cause of the 500 error on Feb 6 — switching from `fs` to `fetch` fixed it

## Deploy

Check project repo for deploy method (likely auto-deploy from git push or Netlify CLI).
