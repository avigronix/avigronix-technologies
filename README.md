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
| `GA_MEASUREMENT_ID` | no | (a placeholder ID) | Google Analytics 4 measurement ID. Analytics only actually fires once a visitor accepts the cookie-consent banner (Consent Mode v2 — see the audit report's I1 fix) |

### Reverse proxy / production notes

- Run behind a real process manager (Gunicorn+Uvicorn workers, or Uvicorn's
  own `--workers`), not `uvicorn main:app --reload` alone.
- If deploying behind Nginx/Cloudflare terminating TLS, forward
  `X-Forwarded-Proto` and run Uvicorn with `--proxy-headers` so the app can
  tell it's being served over HTTPS (this affects the HSTS header in
  `main.py`).
- The in-memory rate limiter (`slowapi`, see `rate_limit.py`) tracks
  counters per-process. If you ever run multiple workers or instances behind
  a load balancer, each one counts separately — move to a Redis-backed
  limiter storage if that matters for you.

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

## Testing checklist after any change

There's no automated test suite. At minimum, manually re-check:

- The shop flow end-to-end: `/shop/register` → preview → register → the
  public shop page (both `/shop/{subdomain}` and via the subdomain itself).
- The contact form (the honeypot field must stay empty for a real send).
- The custom 404 page and a couple of the static marketing pages.
- The browser console for errors, especially any `Refused to load/execute`
  Content-Security-Policy violations, after touching `main.py`'s `CSP`
  constant or any `<script>`/`<link>` tag.
