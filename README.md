# AVIGRONIX TECHNOLOGIES — Website

FastAPI + Jinja2 marketing site, with a "Launch Your Business" flow that lets
anyone register a shop (no login) and get a public storefront page.

See [WEBSITE_AUDIT_REPORT.md](WEBSITE_AUDIT_REPORT.md) for the full audit
history, findings, and fixes applied to this codebase.

## Requirements

- Python 3.10+
- A running MongoDB instance (defaults to `mongodb://localhost:27017`)

## Local setup / deployment

Always deploy from a **fresh virtual environment** installed from
`requirements.txt` — don't reuse an old venv or a machine's already-installed
packages, since the app relies on specific, currently-patched versions of
FastAPI/Starlette (see the audit report's dependency section for why: an
older Starlette breaks `TemplateResponse` calls across every page).

```bash
# 1. From the project root: create and activate a fresh venv
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set up environment variables
cp .env.example .env
# then edit .env — see "Environment variables" below

# 4. Run the app (from the app/ directory — paths in the code are relative to it)
cd app
uvicorn main:app --host 0.0.0.0 --port 8000
```

MongoDB must be reachable before the app starts — it creates a unique index
on `shops.subdomain` at startup (and logs a warning, without crashing, if it
can't).

### Environment variables

Set these in `.env` (loaded automatically via `python-dotenv`) or in your
host's environment/secrets manager:

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `SMTP_HOST` | yes | — | SMTP server for the contact form's emails |
| `SMTP_PORT` | yes | — | SMTP port (587 for Gmail) |
| `SMTP_USER` | yes | — | Sending mailbox address |
| `SMTP_PASS` | yes | — | SMTP password / app password. **Never commit this** — `.env` is gitignored; only `.env.example` (with placeholder values) is tracked. |
| `ADMIN_API_KEY` | **yes, for the admin endpoints** | — (unset = those endpoints always reject) | Shared secret required in the `X-Admin-Key` header to call `GET /shop/api/shops` and `PUT /shop/api/shop/{subdomain}/status`. Generate one with `python3 -c "import secrets; print(secrets.token_hex(32))"` and keep it out of version control, same as the SMTP password. |
| `MONGODB_URL` | no | `mongodb://localhost:27017` | MongoDB connection string |
| `DATABASE_NAME` | no | `shop_management` | MongoDB database name |
| `SITE_URL` | no | `https://avigronix.com` | Used to build the sitemap and canonical/OG URLs — set this to your actual deployed domain |
| `GA_MEASUREMENT_ID` | no | `G-QMZ7RMVX47` | Google Analytics 4 measurement ID. Analytics only actually fires once a visitor accepts the cookie-consent banner (Consent Mode v2 — see the audit report's I1 fix) |
| `ENABLE_API_DOCS` | no | off | Set to `true` to turn on FastAPI's `/docs`, `/redoc` and `/openapi.json` (e.g. on a developer machine). Leave unset in production — they list every endpoint, including the admin ones. |
| `RATE_LIMIT_STORAGE_URI` | only if you run more than one worker/instance | `memory://` | Where the rate limiter keeps its counters. See "Rate limiting and workers" below. |

### Reverse proxy / production notes

- Run under a process manager (systemd, supervisor, Docker restart policy)
  so the app restarts if it crashes — not `uvicorn main:app --reload`.
- If deploying behind Nginx/Cloudflare terminating TLS, forward
  `X-Forwarded-Proto` and run Uvicorn with `--proxy-headers` so the app can
  tell it's being served over HTTPS (this affects the HSTS header in
  `main.py`).
- `GET /health` returns `200 {"status":"ok"}` when the app and MongoDB are
  reachable and `503` when the database isn't — point your uptime monitor at
  it. It isn't rate limited, isn't in the sitemap, and is disallowed in
  `robots.txt`.
- Preview uploads that are never published are deleted automatically after
  24 hours (at startup and every 6 hours). Files referenced by any
  registered shop — active or deactivated — are never deleted, and if the
  database can't be read the cleanup deletes nothing. A visitor who leaves
  the preview page open for more than a day before clicking *Publish* would
  lose their uploaded images and need to upload them again.

### Uploaded images

Every uploaded image is decoded and re-encoded before it is saved, so EXIF
(camera, timestamps, **GPS location**), XMP, ICC profiles and PNG text
chunks never reach disk, and files that aren't real images are rejected.
Images uploaded before this existed can be cleaned with:

```bash
python scripts/strip_upload_metadata.py --dry-run   # show what would change
python scripts/strip_upload_metadata.py             # clean in place
```

It only rewrites files that contain metadata, keeps filenames (so shop
records still point at them) and modification times, and never touches
anything it can't read as an image. Run it from the project root with the
app's venv active; no database or admin key needed.

### Rate limiting and workers

The contact form (5 per 10 minutes per IP), shop preview and registration
(10 per 10 minutes each) and every other route (300/minute) are rate
limited. By default the counters live **in the app process's memory**,
which is only correct with **one** process:

- **One worker (default, recommended for this site's traffic):** the
  command above (`uvicorn main:app --host 0.0.0.0 --port 8000`) runs a
  single worker. Nothing to configure.
- **More than one worker or server** (`--workers 4`, Gunicorn, several
  containers behind a load balancer): every process would keep its own
  counters, multiplying the real limit by the number of processes. Point
  them all at a shared store instead:
  ```bash
  pip install redis
  RATE_LIMIT_STORAGE_URI=redis://localhost:6379
  ```
  `mongodb://…` (your existing MongoDB) also works with no extra package,
  but it makes a blocking database round-trip on every request, so Redis
  is the better choice if you scale out.

## Rebuilding the compiled Tailwind CSS

Tailwind is **not** loaded from a CDN — it's compiled ahead of time into
[`app/static/css/tailwind.min.css`](app/static/css/tailwind.min.css) using
the Tailwind v3 standalone CLI (no Node.js/npm project required). Whenever
you add, remove, or change a class in any file under `app/templates/`, you
must rebuild this file or your change won't show up.

```bash
# 1. Download the standalone CLI for your platform (one-time, or whenever
#    you want to bump the Tailwind version). Pick the right asset name from
#    https://github.com/tailwindlabs/tailwindcss/releases/tag/v3.4.17 —
#    e.g. tailwindcss-macos-arm64, tailwindcss-macos-x64,
#    tailwindcss-linux-x64, tailwindcss-windows-x64.exe
curl -sL -o tailwindcss \
  https://github.com/tailwindlabs/tailwindcss/releases/download/v3.4.17/tailwindcss-macos-arm64
chmod +x tailwindcss

# 2. Rebuild, from the project root (where tailwind.config.js lives)
./tailwindcss -c tailwind.config.js -i tailwind.input.css \
  -o app/static/css/tailwind.min.css --minify
```

`tailwind.config.js` reproduces the brand color palette, fonts, and the
`@tailwindcss/typography` plugin that the old CDN setup configured inline,
plus a `safelist` for the one place classes are built dynamically from
Python data (`blog.html`/`blog_detail.html`'s per-post color, driven by
`blog_content.py`) — if you ever add a *new* post color that isn't already
in that safelist, add it there too, or its classes will silently be missing
from the compiled CSS.

## Running the tests

```bash
pip install -r requirements-dev.txt   # once, inside your venv
pytest
```

That's the whole suite (pages, htmx partials, sitemap/robots, security
headers, the full shop flow, upload limits, admin auth, the contact form).
It is safe to run anywhere:

- **Never touches your real database.** By default it uses an in-memory
  MongoDB (mongomock-motor), so MongoDB doesn't even need to be running. To
  run against a real MongoDB instead (what CI does), point it at a server —
  the tests create and then drop a throwaway `avigronix_test_<random>`
  database there and refuse to run against any other name:
  ```bash
  TEST_MONGODB_URL=mongodb://localhost:27017 pytest
  ```
- **Never sends email.** SMTP is replaced by a recorder in every test.
- **Never writes to `app/uploads/`.** Tests run from a temporary directory
  with its own empty `uploads/`.

Three tests exercise the MongoDB-transaction path of shop registration and
only run against a replica set (they're reported as *skipped* with
mongomock or a standalone server, including CI's). The rollback path used
on standalone servers is tested everywhere.

### Continuous integration (GitHub Actions)

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs automatically on
every push and every pull request, as two jobs:

1. **Tests (pytest + MongoDB)** — installs `requirements.txt` +
   `requirements-dev.txt` and runs `pytest -v` against a throwaway MongoDB
   service container.
2. **Dependency audit (pip-audit)** — runs `pip-audit -r requirements.txt`
   and fails if any pinned production dependency has a known vulnerability.

To see results: open the repository on GitHub → **Actions** tab → pick the
run for your commit/branch → click a job to see its log (a failing test
shows its name and assertion there). The same status appears as a ✓/✗ next
to each commit and as checks at the bottom of every pull request. If you
want merges blocked until CI passes, enable it under **Settings → Branches →
Branch protection rules → Require status checks to pass** and select both
jobs.

### Manual checks that tests don't cover

- Open the shop flow in a real browser once: `/shop/register` → preview →
  publish, **leaving the optional Payment QR field empty** (the most common
  real-world case), then open the public shop page.
- The browser console for errors, especially any `Refused to load/execute`
  Content-Security-Policy violations, after touching `main.py`'s `CSP`
  constant or any `<script>`/`<link>` tag.
