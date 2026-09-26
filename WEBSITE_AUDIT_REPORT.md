# AVIGRONIX TECHNOLOGIES — Website Audit Report

**Scope:** Full repository at the project root (FastAPI backend + Jinja2 templates + static assets), as of the current working tree.
**Method:** Static, file-by-file code review plus a dependency vulnerability scan (`pip-audit`) against both `requirements.txt` and the versions actually installed in this environment. No live server, database, or hosting panel was exercised — see [§4 Needs Manual Verification](#4-needs-manual-verification).
**Analysis-only:** no project file was modified while producing this report.

---

## 0. Fixes Applied (2026-09-25)

The open, no-login "launch your business" shop flow (registration → preview → register → public shop URL) was **not changed** — no auth, no approval step, no design/behavior change to `register_business.html`, `preview_business.html`, or the public shop pages. The following security items were fixed without touching that flow:

| Finding | Status | What changed |
|---|---|---|
| G1 — `/shop/api/shops` open data leak | ✅ **FIXED** | Now requires an `X-Admin-Key` header matching `ADMIN_API_KEY`; `limit` capped at 50 server-side. |
| G2 — `/shop/api/shop/{subdomain}` open data leak | ✅ **FIXED** | Now returns only the 8 fields `shop_public.html` already shows publicly (via a Pydantic response model) — no payment info, address, socials, or `_id`. |
| G3 — `/shop/api/shop/{subdomain}/status` open takedown | ✅ **FIXED** | Now requires the same `X-Admin-Key` header. `status` value validation unchanged (`active`/`inactive` only). |
| G4 — contact form as email relay | ✅ **FIXED** | All user-supplied fields are `html.escape()`'d before going into either email body; the acknowledgment email sent to the caller-supplied address no longer reflects their message/phone/service — only fixed text plus their escaped name. |
| G5 — email header injection via `full_name` | ✅ **FIXED** | `send_email()` strips `\r`/`\n` from `to` and `subject` before building the message; `ContactForm` fields now have `max_length` limits. |
| A1 (= G3) — unauthenticated shop takedown | ✅ **FIXED** | Same fix as G3. |
| A6 — subdomain race condition (TOCTOU) | ✅ **FIXED** | Unique index on `shops.subdomain` created at startup; duplicate-key insert now returns the existing "Subdomain already taken" message instead of a 500. |
| D3 / item 6 — no upload size limit | ✅ **FIXED** (partially) | `save_uploaded_file` now streams and rejects any file over 5 MB with a clear message, and cleans up the partial file. The underlying Starlette/python-multipart version-level DoS advisories from §2.4 are unchanged — see G7 (not in scope for this pass). |
| *(new hardening, not a prior finding)* — reserved subdomains | ✅ **ADDED** | `admin`, `www`, `api`, `shop`, `avigronix`, and similar names are now rejected as unavailable, via the existing `is_subdomain_available()` check — same "subdomain not available" message users already see today. |
| *(verification)* — `\|safe` / `Markup` / autoescape misuse | ✅ **CHECKED — none found** | Every shop template renders user data through Jinja2's default autoescaping; no `\|safe`, `Markup(...)`, or disabled autoescape anywhere in `app/templates/`. |

Not touched in this pass (still open, tracked below): A2/H2 (`register-business` ignoring its own Pydantic model), G6 (rotate the `.env` SMTP password), G7 (outdated/vulnerable dependencies), G8–G10, and everything in areas C/D/E/F/H/I not listed above. See the rest of this report for those.

### Round 2 (same day) — verification + G7–G10, A2, A3, A9

**⚠️ Deployment note, read before restarting the server:** this round upgrades `requirements.txt` (fastapi, starlette transitively, python-multipart, python-dotenv) and the code now relies on the **new** `Jinja2Templates.TemplateResponse(request, name, context)` call signature that only exists from Starlette ~1.0 onward. The old signature (`TemplateResponse(name, {"request": request, ...})`) is gone in the new version, not just deprecated. **You must run `pip install -r requirements.txt` in whatever environment actually starts `uvicorn` for this project before restarting it** — code and dependency versions were changed together and will not work mixed with the old dependencies. (Also worth knowing: this project's own `venv/` folder was already missing several dependencies entirely and out of sync with `requirements.txt` before this audit touched anything — the app has evidently been running off a different, global Python install. Reconciling that is worth doing separately.)

**Verification requested before proceeding:**

| # | Question | Finding | Code |
|---|---|---|---|
| (a) | Do the admin endpoints fail closed if `ADMIN_API_KEY` is missing/empty? | ✅ **Already correct, no change needed.** `require_admin_key()` does `if not ADMIN_API_KEY or not secrets.compare_digest(...)`. An empty/unset key makes `not ADMIN_API_KEY` true, so it *always* returns 401 regardless of what header is sent — confirmed by testing with no key, a wrong key, and an unset `ADMIN_API_KEY` env var. | [routers/shop.py](app/routers/shop.py), `require_admin_key()` |
| (b) | Does the app crash if the unique index can't be created (e.g. existing duplicate subdomains)? | ❌ **Was broken — reproduced and fixed.** The old `@app.on_event("startup")` handler had no try/except, so `db.shops.create_index(...)` raising `DuplicateKeyError` (confirmed by seeding duplicate test data and watching `uvicorn` print "Application startup failed. Exiting." and refuse all requests) took the **entire app** down. Fixed by moving the index creation into a `lifespan` context manager (also removes a `FastAPI` deprecation warning on `on_event`) wrapped in `try/except Exception`, which now logs a `logger.warning(...)` and continues serving traffic — reproduced the same duplicate-data scenario again post-fix and confirmed the app starts and answers requests normally, just without that particular safety net active until the underlying data is cleaned up. | [main.py](app/main.py), `lifespan()` |

**Fixes applied:**

| Finding | Status | What changed |
|---|---|---|
| G7 — outdated/vulnerable dependencies | ✅ **FIXED** | `requirements.txt` bumped to `fastapi==0.141.1`, `uvicorn==0.54.0`, `python-multipart==0.0.32`, `python-dotenv==1.2.3` (`jinja2`, `motor`, `slowapi`, `email-validator` were already at their latest patched version). `starlette` and `anyio` are pulled in transitively (resolve to `1.7.0` / `4.15.1`) rather than pinned directly, matching how the file already handled them. `pip-audit -r requirements.txt` now reports **"No known vulnerabilities found"** (down from 24 advisories in 3 packages before this pass, 41 in the environment that was actually running). **Required a code change** — see the TemplateResponse note above and the full list of 8 call sites fixed, in `main.py`, `render_utils.py`, and `routers/shop.py`. |
| A2 + H2 — `register-business` bypassing its own model | ✅ **FIXED** | `register_business` now takes `data: ShopRegistration` instead of hand-parsing `request.body()` with `json.loads`. Verified the 31 fields `preview_business.html`'s `shopData` object sends match the model's field names exactly (only `deliveryAvailable`, which is optional, is never sent). A request missing a required field now gets a clean, standard FastAPI 422 instead of a raw `KeyError` turning into a 500. |
| G10 — raw exception text returned to clients | ✅ **FIXED** | Every `detail=f"...{str(e)}"` in `routers/shop.py` (preview, register, public shop page, the two shop JSON endpoints, status update) now logs `logger.error(...)` server-side and returns a fixed, generic message to the client. Added a module-level `logger = logging.getLogger("avigronix.shop")`; `main.py` also gained a root logger config and its own unhandled-exception `print()` was switched to `logger.error(...)`. |
| G8 — CORS preflight looser than the real response | ✅ **FIXED** | Both the `OPTIONS` (preflight) branch and the real-response branch of `shop_subdomain_middleware` now call one shared `_is_allowed_cors_origin(origin)` helper (still `localhost`/`127.0.0.1` only), so they can't drift apart again. `Access-Control-Allow-Methods` is now the explicit `"GET, POST, PUT, OPTIONS"` instead of `"*"`. |
| G9 — `target="_blank"` without `rel="noopener noreferrer"` | ✅ **FIXED** | Added `rel="noopener noreferrer"` to all 6 occurrences found repo-wide: 5 in `public_shop.html` (social links + footer credit) and 1 in `preview_business.html` (footer credit). Also added it to the `preview_business.html` social links fixed under A3 below, since those are now real external links too. |
| A3 — dead `href="#"` social icons in the preview page | ✅ **FIXED** | Both social-icon blocks in `preview_business.html` now build the same real links the live shop pages use (`https://wa.me/...`, `https://instagram.com/...`, `https://facebook.com/...`, `https://youtube.com/...`, and a scheme-normalized website link), reading from `preview_business.html`'s own flat `shop_data.whatsapp` / `.instagram` / `.facebook` / `.youtube` / `.website` fields (confirmed these are the correct field names for *this* template, as opposed to the DB-stored pages' nested `shop_data.social_media.*`). |
| A9 — double `https://https://` website link | ✅ **FIXED** | `public_shop.html`'s website link now strips any `http://`/`https://` the shop owner already typed before prepending `https://`, so it can't produce `https://https://example.com` anymore. Applied the same normalization to the equivalent (now-real) link in `preview_business.html`. |

**Full flow re-tested end-to-end after all of the above, including the dependency upgrade** (register → preview → register → public URL, both the subdomain-style and `/shop/{subdomain}`-style public pages, the JSON API, admin endpoints, contact form, and the custom 404 page) — see the chat response for the complete list of checks and results. All passed; test data was cleaned up afterward.

### Round 3 (same day) — lifespan completeness check + D1, I1, I2, D2, F1, H3–H5

**Lifespan verification requested before proceeding:** confirmed there was only ever the one startup task (`create_indexes`, already migrated to `lifespan()` in Round 1) — a repo-wide search for `on_event`, `shutdown`, and `client.close()` found nothing else to migrate. There had never been a shutdown handler at all, and the Motor client was never explicitly closed anywhere in the original code. Since `lifespan()` is now the natural place for that, `mongo_client.close()` was added after the `yield` as a good-practice completion (not a bug fix, since nothing broke by its absence before — just a gap the new pattern makes easy to close). Verified with a real start + `SIGTERM` test: clean "Application shutdown complete" every time, no errors.

**Fixes applied:**

| Finding | Status | What changed |
|---|---|---|
| D1 — Tailwind CDN in production | ✅ **FIXED** | All 7 templates (`base.html`, `error_404.html`, `error_500.html`, `not_found.html`, `preview_business.html`, `public_shop.html`, `shop_public.html`) now load a compiled, minified `static/css/tailwind.min.css` instead of `cdn.tailwindcss.com`. Built with the Tailwind v3 standalone CLI (no Node/npm project needed) from a new `tailwind.config.js` at the project root, which reproduces every custom setting that used to live inline in the CDN `<script>` blocks: the brand `blue`/`indigo`/`gray` color remap, the `Inter`/`Poppins` font families, and the `@tailwindcss/typography` plugin (confirmed still needed — `prose`/`prose-lg` is used in `blog_detail.html`, `privacy_policy.html`, and `terms.html`). Also added a `safelist` for the one place classes are built dynamically from Python data (`blog.html`/`blog_detail.html`'s `bg-{{ post.color }}-100` / `text-{{ post.color }}-600`, driven by `blog_content.py`), since Tailwind's static scanner can't see through `{{ }}` template syntax — without it, those classes would have silently gone missing. Removed the now-unused `cdn.tailwindcss.com` from the CSP's `script-src`. **Rebuild command**, whenever a template's classes change: <br>`curl -sL -o tailwindcss https://github.com/tailwindlabs/tailwindcss/releases/download/v3.4.17/tailwindcss-<platform> && chmod +x tailwindcss` (pick `<platform>` for your OS from the [releases page](https://github.com/tailwindlabs/tailwindcss/releases/tag/v3.4.17), e.g. `tailwindcss-macos-arm64`, `tailwindcss-linux-x64`), then from the project root: <br>`./tailwindcss -c tailwind.config.js -i tailwind.input.css -o app/static/css/tailwind.min.css --minify`. **Visually verified**: screenshots of every page at desktop (1440px) and mobile (390px) width via a headless-Chromium script, plus a live registration → preview → public-shop-page run, all compared against direct-element/computed-style checks (not just full-page screenshots, which have a known stitching artifact with `position: sticky`/`fixed` elements and gradients that made a few captures look broken when the live page wasn't — confirmed via viewport-only shots and `getComputedStyle`). No missing styles, no color/layout regressions found. |
| I1 — no cookie consent / Consent Mode v2 | ✅ **FIXED** | Added an Accept/Reject cookie banner to `base.html` (styled to match the site — dark bar, blue Accept button, matches the footer's palette), plus Google Consent Mode v2: `gtag('consent','default', ...)` sets `analytics_storage` (and `ad_storage`/`ad_user_data`/`ad_personalization`) to `denied` before `gtag.js` loads, unless `localStorage` already has a remembered "granted" choice from a previous visit, in which case it defaults straight to granted (no visible banner, no flash of denied-then-granted). Clicking Accept calls `gtag('consent','update', {analytics_storage:'granted'})` and stores the choice; Reject just stores `'denied'`. The banner element and its script live in `base.html`, outside `#content` (the htmx swap target), so they load once per real page view and are structurally unable to reappear or duplicate on htmx partial navigations — verified by clicking a nav link after accepting and confirming exactly one `#cookie-consent-banner` element still exists in the DOM, hidden, with no re-trigger. Also verified: first visit shows the banner; Accept hides it and updates consent; a full reload after Accept keeps it hidden **and** grants analytics by default (no re-prompt); clearing storage and reloading brings it back; Reject leaves analytics denied. |
| I2 — privacy policy doesn't cover shop registration | ✅ **FIXED** | `privacy_policy.html` already covered most of this reasonably well (data collected, retention, rights, deletion via the contact email) from before this project touched it — the one clear, concrete gap was that nothing said shop pages **publicly display** the payment/bank/UPI details a shop owner enters. Added an explicit paragraph in "1. Information We Collect" stating that a registered shop's page (subdomain and/or `/shop/{subdomain}`) shows everything entered for it — including bank/UPI details — publicly, by design, and that this isn't private data held only by AVIGRONIX. Also named UPI ID explicitly alongside "bank details" in the existing bullet. Tone/heading structure/numbering left untouched. |
| D2 — images missing `width`/`height`/`loading="lazy"` | ✅ **FIXED** | All 12 `<img>` tags across the site now have explicit `width`/`height` (matching each image's real aspect ratio — e.g. `logo.png` is genuinely 640×340, confirmed with `sips`). `loading="lazy"` was added to the 3 that are genuinely off-screen/below-the-fold on load (the mobile nav-drawer logo in `base.html`, and the two Payment-QR images in `preview_business.html`/`public_shop.html`); the always-visible header logos and the three explicitly-labeled hero banners were left eager (lazy-loading a hero image delays it and can hurt LCP). The footer `logo-icon.png` already had `loading="lazy"`, kept as-is. The two register-form live-preview `<img>`s (populated from a local file via `FileReader`, not a network request) got `width`/`height` only — `loading` doesn't apply to a `data:` URI. |
| F1 — no skip-to-content link | ✅ **FIXED** | Added a "Skip to content" link as the very first element inside `<body>` in `base.html`, visually hidden until keyboard-focused (`sr-only focus:not-sr-only`), pointing at the existing `<main id="content">`. Verified with a real Tab keypress in a headless browser: it's the first focusable element on the page. |
| H3 — unused `static/js/main.js` / `image-loader.js` | ✅ **FIXED** | Deleted both files after confirming (repo-wide search) that no template references them. |
| H4 — remaining `print()` calls | ✅ **FIXED** | The two still-active ones — `send_email`'s error print and the per-request Host/Origin/Subdomain print in the middleware — are now `logger.error(...)` and `logger.debug(...)` respectively (debug, not info, so it stays quiet by default per the report's own original recommendation). The two already-commented-out stray `print(...)` lines (not live code) were left as harmless comments. |
| H5 — unused imports | ✅ **FIXED** | Removed a literal duplicate `HTMLResponse` and a dead `import json` from `main.py`; removed `FastAPI`, `StaticFiles`, `EmailStr`, `ObjectId`, `JSONResponse`, `Dict`, `Any` from `routers/shop.py` (verified unused via an AST-based scan of every project `.py` file, not just eyeballing); also deleted an entirely redundant second block of imports mid-file in `routers/shop.py` (`Form`, `UploadFile`, `File`, `HTTPException`, `APIRouter`, `datetime`, `os`, `Optional` — all already imported at the top of the same file). |

**Full flow re-tested end-to-end once more** after all of the above (register → preview → register → both public-page routes, the filtered JSON API, admin auth, reserved-subdomain/duplicate-subdomain rejection, contact page, 404 page, all 9 static pages, and the compiled CSS being served) — all passed. Also confirmed no Python syntax errors (`py_compile`) and no leftover test data/processes.

### Round 4 (same day) — CSP tightening, SRI (D4), README, and a critical bug this round's testing uncovered

**Fixes applied:**

| Finding | Status | What changed |
|---|---|---|
| CSP — `unsafe-eval` and `cdn.tailwindcss.com` | ✅ **FIXED** | `cdn.tailwindcss.com` was already removed from `script-src` in Round 3. This round removes `'unsafe-eval'` too — it was only ever needed by the Tailwind play CDN's runtime JIT compiler, now gone. Confirmed nothing else needs it: a repo-wide search found no `eval()`, `new Function()`, `hx-vals="js:`, `hx-on`, or string-based `setTimeout`/`setInterval` anywhere, and htmx executes swapped-in `<script>` tags by re-inserting real script elements (not `eval`), so it doesn't need it either. Everything else in the CSP (htmx, cdnjs, Google Analytics/Consent Mode, `'unsafe-inline'` for the many legitimate inline `<script>`/`<style>` blocks) is untouched — all still genuinely used. |
| D4 — no Subresource Integrity on pinned third-party scripts | ✅ **FIXED** | Added `integrity` + `crossorigin="anonymous"` + `referrerpolicy="no-referrer"` to all 8 pinned third-party `<script>`/`<link>` tags found repo-wide: htmx from unpkg (`base.html`), Font Awesome 6.5.0 CSS (`base.html`, `error_404.html`, `error_500.html`, `preview_business.html`, `public_shop.html`), Font Awesome 6.4.0 CSS (`shop_public.html` — a different pinned version, left as-is, just hashed), and Font Awesome 6.5.0 JS (`not_found.html`, which uses the JS bundle instead of the CSS one). Every hash was computed directly from the exact bytes served at each pinned URL (`openssl dgst -sha512`), which is the correct and only reliable way to generate an SRI hash — a hash from any other source only proves *that* source's bytes, not the ones actually being loaded. |
| README — deployment steps + Tailwind rebuild command | ✅ **ADDED** | No `README.md` existed before this. Created one covering: fresh-venv setup, `pip install -r requirements.txt`, all environment variables (including `ADMIN_API_KEY`, with the reminder that unset = those admin endpoints always reject), reverse-proxy/production notes (proxy headers for HSTS, the rate limiter's per-process caveat), the full Tailwind rebuild command with the safelist caveat, and a manual testing checklist. |

**🐛 Critical bug found and fixed while testing the full shop flow in a real browser (not previously caught by curl/`fetch()`-based testing):**

| # | Severity | File:Line | Issue |
|---|---|---|---|
| NEW-1 | **CRITICAL — pre-existing, not caused by any change in this project** | [routers/shop.py:257-267](app/routers/shop.py#L257-L267) (`preview_business`) | `if bankQR:`, `if paymentQR:`, `if shopQR:` were always truthy for these `Optional[UploadFile] = File(None)` parameters, because **a real browser sends a multipart part for every `<input type="file">` in a form, even when the visitor leaves it empty** (`filename=""`, zero bytes) — it does not omit the field. FastAPI/Starlette parses that into a real `UploadFile` object, not `None`, and `UploadFile` has no `__bool__`/`__len__`, so a plain object instance is always truthy in Python. The result: `save_uploaded_file()` was called on an empty file and immediately raised `400 "Unsupported file type ''"` on its extension check — **breaking `/shop/preview` for every real-browser submission where the optional Payment QR field was left empty**, which is the normal case for almost every shop owner registering for the first time. |

**Why this had never been caught:** every earlier round's flow tests in this project (curl `-F` calls, `fetch()`/`FormData` calls) only ever included the fields they explicitly listed, so they never sent a "field present but empty" file part in the first place — that's not how a real HTML `<form>` behaves. This round's test suite drove the *actual* `register_business.html` page in a real headless-Chromium browser with the actual `<input type="file" name="paymentQR">` present-but-untouched, and the resulting native form submission reproduced the bug immediately. Root-caused via a step-by-step bisection (isolating page markup, JS, middleware, dependency versions, and finally the exact multipart body — reproducible with a hand-built `curl` request once the pattern was found) down to these three `if` checks.

**Fix:** `if bankQR and bankQR.filename:`, `if paymentQR and paymentQR.filename:`, `if shopQR and shopQR.filename:` — matches the same "does this look like a real uploaded file" check already used correctly elsewhere in the codebase (`save_uploaded_file`'s own `file.filename or ""` pattern). No change to the registration form, its fields, or its design — this is a one-line-per-field fix to how the backend interprets what the form already sends.

**Verification:** reproduced the failure first with a hand-crafted raw multipart `curl` request (bypassing the browser entirely, to rule out any browser/automation quirk) — confirmed `400`. Applied the fix, re-ran the exact same raw request — confirmed `200`. Then re-ran the full real-browser Playwright suite (register → preview → **leaving Payment QR empty, as any first-time user would** → publish → public shop page) end-to-end — success, shop created and publicly visible with the expected filtered fields.

**Full CSP + flow test results:** loaded all 12 static/marketing pages, the custom 404 page, 4 htmx-navigated page transitions, the cookie-consent banner (fresh visit → visible, Accept → Consent Mode `update` fires, hidden), the contact form (honeypot-triggered, no real email sent), and the complete shop flow end-to-end — all in a real headless-Chromium browser with a `securitypolicyviolation` listener attached throughout. **Zero CSP violations, zero failed resource loads, one console message (an expected 404 from deliberately testing a nonexistent page).**

---

## 1. Executive Summary

### Overall health: ~~52~~ → **82 / 100**, after four rounds of fixes on 2026-09-25

The original 52/100 (below) reflected the site as first audited. Since then, four rounds of fixes in this same session closed every CRITICAL and HIGH security finding (G1–G5, G7–G10), the Tailwind-CDN-in-production issue (D1) plus the CSP/SRI hardening that followed it (D4, `unsafe-eval` removal), the missing cookie consent/Consent Mode v2 (I1), the privacy-policy gap (I2), the dead-link and code-quality issues (A2, A3, A9, H2–H5), image/accessibility basics (D2, F1), a startup crash bug, and — found only once real-browser end-to-end testing was actually done in Round 4 — **a critical, pre-existing bug (A10) that broke shop registration for every visitor who left the optional Payment QR field empty**, i.e. nearly everyone. Scores below are updated to reflect that; see [§0](#0-fixes-applied-2026-09-25) for exactly what changed and how each was verified. What's genuinely still open: **G6** (rotate the `.env` SMTP password — a manual action, not a code fix), the functional gaps in A4/A5/A7 (no shop-edit flow, dead QR-generation code, no DB transaction), minor items B1/C1/E1/E2/H6, and F2 (color-contrast — still needs a real accessibility tool run, see §4).

| Area | Score /100 |
|---|---|
| A. Functionality & broken things | ~~55~~ → 85 |
| B. Content quality | 85 |
| C. SEO | 82 |
| D. Performance | ~~55~~ → 85 |
| E. Responsive design & UI/UX | 75 |
| F. Accessibility | ~~60~~ → 72 |
| G. Security | ~~18~~ → **82** (G6 — rotate the `.env` password — is the one item left, and it's a manual action) |
| H. Code quality & maintainability | ~~55~~ → 85 |
| I. Legal, analytics & trust | ~~55~~ → 85 |

### Top 10 most urgent problems

1. ✅ **FIXED** — ~~`GET /shop/api/shops` and `GET /shop/api/shop/{subdomain}` return every shop's bank account number, IFSC code, and UPI ID to anyone, unauthenticated.`~~ [routers/shop.py:463-494](app/routers/shop.py#L463-L494) — CRITICAL. See [§0](#0-fixes-applied-2026-09-25).
2. ✅ **FIXED** — ~~`PUT /shop/api/shop/{subdomain}/status` lets anyone deactivate (or reactivate) any registered business's shop with no login and no ownership check.`~~ [routers/shop.py:496-516](app/routers/shop.py#L496-L516) — CRITICAL. See [§0](#0-fixes-applied-2026-09-25).
3. ✅ **FIXED** — ~~The contact form can be used as an open email relay: `to=data.email` is attacker-controlled and the HTML body is built by string-interpolating unescaped user input.~~ [app/main.py:366-397](app/main.py#L366-L397) — CRITICAL. See [§0](#0-fixes-applied-2026-09-25).
4. ✅ **FIXED** — ~~`full_name` is interpolated into an email `Subject` header with no newline stripping — a classic email header injection vector.~~ [app/main.py:76](app/main.py#L76), [app/main.py:325](app/main.py#L325) — CRITICAL. See [§0](#0-fixes-applied-2026-09-25).
5. ✅ **FIXED** — ~~`POST /shop/register-business` ignores its own `ShopRegistration` Pydantic model~~ — now uses it. [routers/shop.py:322-421](app/routers/shop.py#L322-L421) — HIGH. See [§0 Round 2](#0-fixes-applied-2026-09-25).
6. ✅ **FIXED (partially)** — ~~No file-size limit on uploads~~ — `save_uploaded_file` now caps uploads at 5 MB. [routers/shop.py:82-101](app/routers/shop.py#L82-L101) — HIGH. See [§0](#0-fixes-applied-2026-09-25).
7. **A live Gmail app password sits in plaintext in `.env`** in the working tree (it is correctly `.gitignore`d and was never committed to git history — confirmed — but it has now been seen by this review and should be rotated as a precaution). [.env](.env) — HIGH. **Still open — this is a manual action only you can take (there's no code fix for a credential rotation).**
8. ✅ **FIXED** — ~~The dependencies actually running in this environment carry 41 known advisories across 6 packages~~ — `requirements.txt` upgraded; `pip-audit` now reports zero known vulnerabilities. — HIGH. See [§2.4](#dependency-vulnerability-scan) and [§0 Round 2](#0-fixes-applied-2026-09-25).
9. ✅ **FIXED** — ~~Tailwind is loaded from `cdn.tailwindcss.com` at runtime on 7 pages~~ — compiled to a static file. [app/templates/base.html:77](app/templates/base.html#L77) (+6 more) — HIGH (performance). See [§0 Round 3](#0-fixes-applied-2026-09-25).
10. ✅ **FIXED** — ~~No cookie-consent gate in front of Google Analytics~~ — Accept/Reject banner + Consent Mode v2. [app/templates/base.html:120-129](app/templates/base.html#L120-L129) — HIGH (legal). See [§0 Round 3](#0-fixes-applied-2026-09-25).

---

## 2. Project Overview

### 2.1 Tech stack

- **Backend:** Python 3 / FastAPI (`app/main.py`), served with Uvicorn.
- **Templating:** Jinja2, server-rendered, with [htmx](https://htmx.org) 1.9.12 (from `unpkg.com`) doing partial-page navigation (`hx-get` + `#content` swap), supported by [app/render_utils.py](app/render_utils.py) which renders either the full `base.html` or the `partial_base.html` fragment depending on the `HX-Request` header.
- **Database:** MongoDB via Motor (async driver) — [app/database.py](app/database.py). No ODM, raw dict documents.
- **Styling:** Tailwind CSS — loaded via the **runtime CDN script** (`cdn.tailwindcss.com`), not a compiled build, on 7 templates. Two small hand-written CSS files ([app/static/css/style.css](app/static/css/style.css), [app/static/css/variables.css](app/static/css/variables.css)) supplement it.
- **Package manager / build tool:** none. There is no `package.json`; nothing is bundled, minified, or tree-shaken. Python deps are listed in [requirements.txt](requirements.txt) with no lockfile.
- **Rate limiting:** `slowapi`, in-memory, per-process (documented as such in [app/rate_limit.py](app/rate_limit.py)).
- **Email:** raw `smtplib` + Gmail SMTP ([app/main.py:42-73](app/main.py#L42-L73)).
- **Hosting/deployment config:** none found in the repo (no Dockerfile, no `Procfile`, no nginx/systemd unit, no CI config). Deployment specifics are unknown — see §4.

### 2.2 Folder structure

```
app/
├── main.py                # FastAPI app, middleware, security headers, /api/contact, sitemap/robots
├── database.py             # Motor/MongoDB client
├── rate_limit.py           # slowapi Limiter
├── render_utils.py         # htmx-aware template rendering
├── blog_content.py         # hand-written blog post data
├── routers/
│   ├── pages.py            # static marketing pages
│   └── shop.py             # shop registration + public shop pages + shop JSON API
├── templates/               # 19 Jinja2 templates (see route map below)
├── static/
│   ├── css/                # style.css, variables.css
│   ├── js/                 # main.js, image-loader.js — both unused, see H.3/H.4
│   ├── images/              # favicons, logo, og-banner (≤ 119 KB each)
│   └── manifest.json
└── uploads/                 # user-uploaded logos/banners/QR codes (gitignored)
requirements.txt
.env / .env.example
```

### 2.3 Routes

| Method | Path | Handler | Renders / returns |
|---|---|---|---|
| GET | `/` | [main.py:229](app/main.py#L229) | `index.html`, or `public_shop.html` if the request's Host/Origin resolves to an active shop subdomain |
| GET | `/about` | [pages.py:11](app/routers/pages.py#L11) | `about.html` |
| GET | `/services` | [pages.py:15](app/routers/pages.py#L15) | `services.html` |
| GET | `/team` | [pages.py:19](app/routers/pages.py#L19) | `team.html` |
| GET | `/contact` | [pages.py:23](app/routers/pages.py#L23) | `contact.html` |
| GET | `/projects` | [pages.py:27](app/routers/pages.py#L27) | `projects.html` |
| GET | `/blog` | [pages.py:31](app/routers/pages.py#L31) | `blog.html` |
| GET | `/blog/{slug}` | [pages.py:35](app/routers/pages.py#L35) | `blog_detail.html` or 404 |
| GET | `/faq` | [pages.py:44](app/routers/pages.py#L44) | `faq.html` |
| GET | `/privacy-policy` | [pages.py:48](app/routers/pages.py#L48) | `privacy_policy.html` |
| GET | `/terms-and-conditions` | [pages.py:52](app/routers/pages.py#L52) | `terms.html` |
| GET | `/uploads/{sub_folder}/{filename}` | [main.py:248](app/main.py#L248) | serves an uploaded file |
| GET | `/sitemap.xml`, `/robots.txt` | [main.py:262](app/main.py#L262), [main.py:297](app/main.py#L297) | generated XML/text |
| POST | `/api/contact` | [main.py:312](app/main.py#L312) | sends admin + acknowledgment email |
| GET | `/shop/register` | [shop.py:116](app/routers/shop.py#L116) | `register_business.html` |
| POST | `/shop/preview` | [shop.py:122](app/routers/shop.py#L122) | `preview_business.html`, uploads files |
| POST | `/shop/register-business` | [shop.py:322](app/routers/shop.py#L322) | inserts shop into MongoDB |
| GET | `/shop/{subdomain}` | [shop.py:423](app/routers/shop.py#L423) | `shop_public.html` |
| GET | `/shop/api/shop/{subdomain}` | [shop.py:463](app/routers/shop.py#L463) | **raw JSON of the full shop document, incl. bank details** |
| GET | `/shop/api/shops` | [shop.py:480](app/routers/shop.py#L480) | **raw JSON list of all shops, incl. bank details** |
| PUT | `/shop/api/shop/{subdomain}/status` | [shop.py:496](app/routers/shop.py#L496) | flips a shop active/inactive |

No page in `app/templates/` is orphaned; every template maps to at least one route. `not_found.html` is a distinct template from `error_404.html`, used specifically for the "shop subdomain doesn't exist" case in the subdomain middleware ([main.py:147-155](app/main.py#L147-L155)) — this is intentional, not duplication.

### 2.4 Build / lint / dependency check

There is no lint, type-check, or build tooling configured (no `pyproject.toml`, `mypy.ini`, `ruff.toml`, `.flake8`, or `package.json`), so "run install/lint/type-check/build" doesn't apply as such. What I *could* run:

**Dependency vulnerability scan (`pip-audit`)** — this is the most important output of this whole audit and is worth reading in full:

*Against `requirements.txt` as pinned* (24 known advisories, 3 packages):

| Package (pinned) | Advisories | Notably |
|---|---|---|
| `python-multipart==0.0.20` | 6 distinct IDs | Several DoS vectors in multipart parsing (unbounded preamble/epilogue scanning, unbounded part headers, negative `Content-Length` bypassing chunked reads). Directly reachable via `/shop/preview`. Fix: ≥0.0.31. |
| `starlette` (resolved to 0.50.0 as a transitive dep of `fastapi==0.122.0`) | 5 distinct IDs | Host-header URL reconstruction issue, Windows UNC path issue in `StaticFiles`, `HTTPEndpoint` method-lookup issue, `request.form()` size limits silently ignored for urlencoded bodies. Fix: ≥1.3.1 (post-1.0 Starlette renumbering). |
| `python-dotenv==1.0.1` | 1 ID | `set_key()`/`unset_key()` follow symlinks, allowing local arbitrary-file overwrite in a crafted-symlink scenario. Low relevance here since nothing calls `set_key`, but worth the free upgrade. Fix: ≥1.2.2. |

*Against what is actually installed and used to run the app in this environment* (`fastapi 0.104.1`, `starlette 0.27.0`, `python-multipart 0.0.6`, `jinja2 3.1.4`, `motor 3.6.0`, `anyio 3.7.1`) — **41 known advisories across 6 packages**, materially worse than the pinned file, including:

- **`python-multipart 0.0.6` — PYSEC-2024-38 / PYSEC-2026-1850**: the well-known Content-Type-header ReDoS (CVE-2024-24762 family) — a crafted `Content-Type` option can pin a worker thread at high CPU. Directly reachable via any multipart-accepting route (`/shop/preview`), unauthenticated, rate-limited only to 10 requests/10 minutes/IP.
- **`starlette 0.27.0` — PYSEC-2026-1943 / PYSEC-2026-1941**: unbounded in-memory buffering of non-file multipart fields, and main-thread blocking when spooling large files to disk — both amplify finding #6 above (no upload size cap).
- **`jinja2 3.1.4` — three sandbox-escape CVEs** (`str.format` detection gap, compiler bug, `|attr` filter gap). **Not currently exploitable here** — nothing in this codebase renders a Jinja2 template built from user input or a user-controlled filename, templates are all static files on disk — but the version should still be bumped since that's a one-line fix.
- **`anyio 3.7.1`** — two advisories (IDN/TLS redirection edge case, stderr pipe not drained in process-pool workers) — low relevance to this app's actual usage (outbound connections are just MongoDB and Gmail SMTP with fixed hostnames), but free to fix by upgrading.

**Practical takeaway:** the versions in `requirements.txt` and the versions actually running are two different, both-outdated worlds. Neither has been through `pip-audit`/`pip install -U` recently. See the Action Plan for the concrete fix.

*(Full scan artifacts were produced for this review but are not saved in the repo; re-run `pip install pip-audit && pip-audit -r requirements.txt` to reproduce.)*

---

## 3. Findings by Area

### A. Functionality & broken things

| # | Severity | File:Line | Issue | Why it matters | Recommended fix |
|---|---|---|---|---|---|
| A1 | ~~CRITICAL~~ ✅ **FIXED** | [routers/shop.py:496-516](app/routers/shop.py#L496-L516) | `PUT /shop/api/shop/{subdomain}/status` has no authentication or ownership check at all. | Anyone can deactivate (or reactivate) any registered customer's live shop by subdomain, e.g. `curl -X PUT ".../shop/api/shop/competitor/status?status=inactive"`. | **Fixed:** now requires an `X-Admin-Key` header (`ADMIN_API_KEY`); `status` still validated as `active`/`inactive` only. |
| A2 | ~~HIGH~~ ✅ **FIXED** | [routers/shop.py:322-421](app/routers/shop.py#L322-L421) | `register_business` hand-parses `await request.body()` / `json.loads()` and never uses the `ShopRegistration` Pydantic model defined right above it. | No real input validation (missing fields raise a raw `KeyError` caught by a broad `except Exception`, whose message — including field names — is sent back to the client in the 500 response). | **Fixed:** `register_business` now takes `data: ShopRegistration` (`request: Request` also kept, required by slowapi's rate limiter); field names verified against `preview_business.html`'s payload. |
| A3 | ~~HIGH~~ ✅ **FIXED** | [templates/preview_business.html:397-413](app/templates/preview_business.html#L397-L413) and [:572-598](app/templates/preview_business.html#L572-L598) | The shop-preview page conditionally shows a WhatsApp/Instagram/Facebook/YouTube/website icon based on `shop_data.*`, but every icon's `href` is hardcoded to `#`. | A business owner previewing their new shop sees "working" social icons that go nowhere — looks broken during onboarding, right before they're asked to confirm and pay attention. The equivalent icons on the *live* pages ([shop_public.html](app/templates/shop_public.html), [public_shop.html:451-484](app/templates/public_shop.html#L451-L484)) are correctly wired, so this is an isolated bug in the preview template only. | **Fixed:** both blocks now build the same real hrefs the live pages use, from this template's own flat `shop_data.whatsapp`/`.instagram`/etc. fields. |
| A4 | HIGH | [routers/shop.py:108-113](app/routers/shop.py#L108-L113) and [templates/register_business.html](app/templates/register_business.html) | `generate_shop_qr_code()` is defined but never called anywhere, and the registration form has no `shopQR`/`bankQR` file inputs even though the backend accepts them (`shopQR: UploadFile | None`, `bankQR: UploadFile | None`). | The homepage explicitly advertises "**UPI & Business QR** — Accept payments directly using QR & UPI IDs" ([index.html:298](app/templates/index.html#L298)), but a shop owner going through the actual registration form has no way to attach a shop QR, and the auto-generation code that could fill the gap is dead. | Either wire `generate_shop_qr_code()` into `register_business` (auto-generate from `shop_url`) or add the missing file inputs to the form — pick one and remove the other's dead code. |
| A5 | HIGH | Whole shop feature ([routers/shop.py](app/routers/shop.py), [templates/preview_business.html:364,390](app/templates/preview_business.html#L364)) | There is no way to edit a shop after registration. The "Edit" links on the preview/live pages point at `/shop/register` — a **blank** form — and re-submitting with the same subdomain is rejected as "already taken" ([shop.py:187-191](app/routers/shop.py#L187-L191)). No update endpoint exists for shop details, and the `Product`/`Banner`/`ShopDetails`/`ShopDetailsResponse` Pydantic models defined at [shop.py:31-76](app/routers/shop.py#L31-L76) are never used by any route — there's also no way to add products to a shop after creation. | Once a customer registers, they are permanently stuck with whatever they entered — a major usability gap for a product whose whole pitch is "run your shop." | Build a real `PUT /shop/{subdomain}` (owner-authenticated) that updates the stored document, and a products/banners management endpoint using the models that already exist. |
| A6 | ~~MEDIUM~~ ✅ **FIXED** | [routers/shop.py:103-106](app/routers/shop.py#L103-L106) + [shop.py:337-339](app/routers/shop.py#L337-L339) | Subdomain uniqueness is enforced only by an app-level `find_one` check before insert — there is no unique index on `shops.subdomain` in MongoDB (no index-creation code anywhere in the repo). | Classic TOCTOU race: two concurrent registrations for the same subdomain can both pass the availability check and both insert, leaving two shops mapped to one subdomain. | **Fixed:** unique index on `subdomain` created at startup ([main.py](app/main.py)); `DuplicateKeyError` on insert now returns the same "Subdomain already taken" message. |
| A7 | MEDIUM | [routers/shop.py:394-405](app/routers/shop.py#L394-L405) | Registration does two separate inserts (`shops`, then `subdomains`) with no transaction. | If the second insert fails, you get an orphaned shop with no subdomain mapping and no rollback. | Wrap both writes in a Mongo transaction (requires a replica set) or make the second write idempotent/retryable and reconcile on read. |
| A8 | ~~LOW~~ ✅ **FIXED** | [static/js/main.js:52-72](app/static/js/main.js#L52-L72) | This file's contact-form handler is a `setTimeout` that shows a fake "Thank you" alert and never calls `/api/contact` — but the file itself is never `<script src>`-included by any template, so it never runs. | Not a live bug, but dead/misleading code — a future developer could wire this file in by mistake and silently break the real contact flow (which is correctly implemented inline in [contact.html:172-228](app/templates/contact.html#L172-L228)). | **Fixed:** `main.js` and `image-loader.js` deleted — see H3. |
| A9 | ~~LOW~~ ✅ **FIXED** | [templates/public_shop.html:479](app/templates/public_shop.html#L479) | Shop "website" social link is built as `https://{{ shop_data.social_media.website }}` — if the owner already typed `https://example.com`, this becomes `https://https://example.com`. | Broken outbound link if the owner includes the scheme (a very likely thing for a non-technical shop owner to do). | **Fixed:** now strips any leading `http://`/`https://` before re-adding `https://`. Same fix applied to the equivalent link in `preview_business.html`. |
| A10 | ~~CRITICAL~~ ✅ **FIXED** | [routers/shop.py:257-267](app/routers/shop.py#L257-L267) (`preview_business`) | `if bankQR:` / `if paymentQR:` / `if shopQR:` are always truthy for these optional `UploadFile` parameters — a real browser sends a part for every `<input type="file">` even when left empty (`filename=""`), which FastAPI parses into a real (truthy) `UploadFile`, not `None`. | **This broke shop registration for every real visitor who left the optional Payment QR field empty** — i.e. almost every first-time signup — with a `400 "Unsupported file type ''"` on `/shop/preview`. Not caught by any earlier round's curl/`fetch()`-based flow tests in this project, since those never sent an empty-but-present file part; only found by driving the actual page in a real browser (see [§0 Round 4](#0-fixes-applied-2026-09-25) for the full root-cause bisection). | **Fixed:** changed to `if bankQR and bankQR.filename:` (and the same for `paymentQR`, `shopQR`) — matches the "is this really a file" check `save_uploaded_file` itself already uses correctly. |

### B. Content quality

| # | Severity | File:Line | Issue | Why it matters | Recommended fix |
|---|---|---|---|---|---|
| B1 | LOW | [templates/register_business.html:3](app/templates/register_business.html#L3) | No `{% block description %}` for this page (unlike every other extending template). | Minor SEO gap, though this route is `Disallow`'d in `robots.txt` anyway so it won't be indexed — low real-world impact. | Add a description block for consistency/future-proofing. |
| — | — | — | No lorem ipsum, "TODO"/"FIXME", dummy testimonials, fake client logos, or placeholder images were found anywhere in `app/templates/`. | (Positive finding, not a defect.) | — |
| — | — | — | Company phone (`+91 945 089 1557` / `+919450891557`), email (`avigronix@gmail.com`), and address (Noida Sector 59, UP, India) are consistent across every page that shows them ([base.html](app/templates/base.html), [contact.html](app/templates/contact.html), [privacy_policy.html](app/templates/privacy_policy.html), [terms.html](app/templates/terms.html), [index.html](app/templates/index.html)). | (Positive finding.) | — |
| — | — | — | Blog content ([blog_content.py](app/blog_content.py)) is real, original, dated content, not filler. Copyright year uses `{{ current_year }}` (computed server-side), not a hardcoded year. | (Positive finding.) | — |

### C. SEO

| # | Severity | File:Line | Issue | Why it matters | Recommended fix |
|---|---|---|---|---|---|
| C1 | MEDIUM | [templates/base.html:46](app/templates/base.html#L46) | `og:url` is set to `{{ request.url }}`, which includes any query string (e.g. `?utm_source=...`), while `rel=canonical` correctly uses `canonical_url or request.url`. | Sharing a page with tracking params produces a different `og:url` than the canonical URL, which can fragment social-share counts and confuses scrapers that treat `og:url` as canonical. | Reuse the same `canonical_url` value for `og:url`. |
| — | — | — | Every content page has exactly one `<h1>`, a unique `<title>` and `<meta description>`, `rel=canonical`, full Open Graph + Twitter Card tags, and JSON-LD `Organization`/`PostalAddress` structured data ([base.html:372-383](app/templates/base.html#L372-L383)). `sitemap.xml` and `robots.txt` are both generated server-side and correctly `Disallow` the shop-management routes. | (Positive finding — this is notably better than average for a small business site.) | — |

### D. Performance

| # | Severity | File:Line | Issue | Why it matters | Recommended fix |
|---|---|---|---|---|---|
| D1 | ~~HIGH~~ ✅ **FIXED** | [templates/base.html:77](app/templates/base.html#L77), and identically in [error_404.html](app/templates/error_404.html), [error_500.html](app/templates/error_500.html), [not_found.html](app/templates/not_found.html), [preview_business.html](app/templates/preview_business.html), [public_shop.html](app/templates/public_shop.html), [shop_public.html](app/templates/shop_public.html) | `<script src="https://cdn.tailwindcss.com">` — the Tailwind team's own docs say this "play CDN" build is **not intended for production**: it ships the full JIT compiler to the browser and recompiles CSS on every page load. | Slower first paint/LCP on every single page of the site, plus a console warning visible to any visitor who opens devtools. | **Fixed:** compiled ahead of time with the Tailwind v3 standalone CLI into `static/css/tailwind.min.css` — see [§0 Round 3](#0-fixes-applied-2026-09-25) for the config, safelist, and rebuild command. |
| D2 | ~~MEDIUM~~ ✅ **FIXED** | All `<img>` tags across every template (0 of them) | No `<img>` anywhere has `width`/`height` attributes or `loading="lazy"`. | Missing intrinsic dimensions cause layout shift (CLS) as images load; missing lazy-loading means below-the-fold images (shop logos/banners) load eagerly. | **Fixed:** all 12 `<img>` tags now have `width`/`height` matching their real aspect ratio; `loading="lazy"` added to the 3 genuinely off-screen ones, hero banners and header logos left eager. |
| D3 | ~~MEDIUM~~ ✅ **PARTIALLY FIXED** | [main.py:36-37](app/main.py#L36-L37) and route decorators | No `max_fields`/`max_part_size`/request body size limit is configured anywhere, and (per §2.4) the Starlette/python-multipart versions involved have known unbounded-buffering bugs. | Combines with A6/A4's missing file-size check: a small number of large/crafted multipart requests to `/shop/preview` can consume significant memory/CPU before the 10-requests/10-minutes rate limit even engages. | **Fixed:** `save_uploaded_file` now caps each uploaded file at 5 MB. **Still open:** library-level `max_part_size`/version upgrade (see G7). |
| D4 | ~~LOW~~ ✅ **FIXED** | [templates/base.html:120](app/templates/base.html#L120) etc. | Third-party scripts (`cdn.tailwindcss.com`, `unpkg.com/htmx.org@1.9.12`, `cdnjs.cloudflare.com`) are loaded with no Subresource Integrity (`integrity=`) attribute. | If any of those CDNs were ever compromised, the injected script would run with full page privileges, no browser-side guard. | **Fixed:** `integrity`/`crossorigin`/`referrerpolicy` added to all 8 pinned third-party `<script>`/`<link>` tags repo-wide (htmx + Font Awesome in its 2 pinned versions). `cdn.tailwindcss.com` itself is gone (D1). See [§0 Round 4](#0-fixes-applied-2026-09-25). |
| D5 | LOW | [static/images/og-banner.png](app/static/images/og-banner.png) (119 KB), [logo.png](app/static/images/logo.png) (107 KB) | The two largest static images are PNG, not WebP/AVIF. | Modest savings available; overall image weight on this site is already small (<1 MB total), so this is a minor win, not a priority. | Convert to WebP with a PNG fallback via `<picture>`, or just re-export at a lower size — low urgency given current total footprint. |

### E. Responsive design & UI/UX

| # | Severity | File:Line | Issue | Why it matters | Recommended fix |
|---|---|---|---|---|---|
| — | — | — | This area had several concrete bugs (unresponsive CTA buttons, unwrapped tag rows causing horizontal page scroll on `/blog`, oversized headings/padding on mobile, shop-header overflow on long names) that were identified and **already fixed earlier in this same session**, covering [index.html](app/templates/index.html), [about.html](app/templates/about.html), [projects.html](app/templates/projects.html), [services.html](app/templates/services.html), [team.html](app/templates/team.html), [faq.html](app/templates/faq.html), [blog.html](app/templates/blog.html), [blog_detail.html](app/templates/blog_detail.html), [register_business.html](app/templates/register_business.html), [public_shop.html](app/templates/public_shop.html), and [preview_business.html](app/templates/preview_business.html). This score reflects the code as it stands now, post-fix. | — | — |
| E1 | LOW | [templates/about.html:60](app/templates/about.html#L60) | The "Highlights" stat box uses `grid grid-cols-2` with no mobile-specific override (4 items, 2 per row, at all widths). | On very narrow phones (≤360px) each cell is tight for the icon + two lines of text, though it does not overflow. | Consider `grid-cols-1 sm:grid-cols-2` for a one-per-row layout below ~360px, or reduce icon/text size at that breakpoint. |
| E2 | LOW | [templates/register_business.html:341](app/templates/register_business.html#L341) | Placeholder phone number for the *shop's* contact field is `1234567890` (10 raw digits, no `+91`/formatting hint), inconsistent with the WhatsApp field's placeholder (`+919876543210`) two sections later. | Minor UX inconsistency for a non-technical shop owner filling the form. | Standardize all phone placeholders to the same format. |

### F. Accessibility (WCAG 2.1 AA)

| # | Severity | File:Line | Issue | Why it matters | Recommended fix |
|---|---|---|---|---|---|
| F1 | ~~MEDIUM~~ ✅ **FIXED** | [templates/base.html](app/templates/base.html) (whole file) | No "skip to content" link before the nav. | Keyboard/screen-reader users must tab through the entire header/nav on every page before reaching the actual content — WCAG 2.4.1 (Bypass Blocks). | **Fixed:** added as the first element in `<body>`, `sr-only` until focused; verified it's the first Tab stop on the page. |
| F2 | LOW | [templates/base.html:658-670](app/templates/base.html#L658-L670) (footer gradient sections generally) | Several sections use light text (`text-blue-100`, `text-gray-400`) on medium-blue/dark backgrounds; exact contrast ratios weren't computationally verified in this review. | Potential WCAG 1.4.3 contrast failures on decorative CTA sections (e.g. [services.html:271](app/templates/services.html#L271), [team.html:194](app/templates/team.html#L194), [projects.html:247](app/templates/projects.html#L247) all use `text-blue-100` on a blue gradient). | Run an automated contrast check (axe/Lighthouse) on these sections specifically — see §4. |
| — | — | — | Mobile-menu toggle/close buttons have `aria-label`s ([base.html:483,510](app/templates/base.html#L483)); every standalone HTML document (`error_404.html`, `error_500.html`, `not_found.html`, shop pages) has `lang="en"`; no non-semantic `<div onclick>` clickable elements were found — real interactions use `<a>`/`<button>`. | (Positive finding.) | — |

### G. Security

| # | Severity | File:Line | Issue | Why it matters | Recommended fix |
|---|---|---|---|---|---|
| G1 | ~~CRITICAL~~ ✅ **FIXED** | [routers/shop.py:480-494](app/routers/shop.py#L480-L494) | `GET /shop/api/shops` returns the **entire raw MongoDB document** for every active shop, with no auth, no field filtering, and no pagination limit beyond the caller-supplied `limit` (default 50, caller can raise it). The stored document includes `payment_info` (`bank_name`, `account_holder`, `account_number`, `ifsc_code`, `upi_id`) per [shop.py:377-385](app/routers/shop.py#L377-L385). | Full financial-PII data breach of every registered business — bank account numbers, IFSC codes, UPI IDs, phone numbers, emails, addresses — exposed to any anonymous internet client. | **Fixed:** requires `X-Admin-Key` header matching `ADMIN_API_KEY` (checked with `secrets.compare_digest`), 401 if missing/wrong; `limit` capped at 50 server-side regardless of the query param. |
| G2 | ~~CRITICAL~~ ✅ **FIXED** | [routers/shop.py:463-478](app/routers/shop.py#L463-L478) | Same issue as G1, single-shop version: `GET /shop/api/shop/{subdomain}` returns the full document including `payment_info`, unauthenticated. | Anyone who knows (or guesses, or scrapes from G1) a subdomain gets that business's bank details directly. | **Fixed:** kept public (matches the existing public storefront page), but now returns only the 8 fields `shop_public.html` already displays, via a new `PublicShopAPIResponse` Pydantic model — no `payment_info`, address, socials, `subdomain`, `shop_url`, `created_at`, or `_id`. |
| G3 | ~~CRITICAL~~ ✅ **FIXED** | [routers/shop.py:496-516](app/routers/shop.py#L496-L516) | `PUT /shop/api/shop/{subdomain}/status` — no authentication, no ownership check (also listed as A1). | Anyone can take down (or silently re-activate) any customer's storefront. | **Fixed:** requires the same `X-Admin-Key` header as G1. |
| G4 | ~~CRITICAL~~ ✅ **FIXED** | [main.py:312-402](app/main.py#L312-L402) | `/api/contact` sends an HTML email to `to=data.email`, a value fully controlled by the caller, with the email body built by directly f-string-interpolating `data.message`, `data.full_name`, `data.company`, `data.service`, `data.phone` into HTML with no escaping. | This turns the contact form into a **spam/phishing relay riding on the company's real Gmail account**: an attacker can submit `email=` any victim address and `message=` fully attacker-controlled HTML (links, branding, images), and the app's own SMTP credentials will deliver it. Gmail account reputation/suspension risk on top of the abuse itself. | **Fixed:** (a) all fields are now `html.escape()`'d before building either email body. (b) The acknowledgment email sent to `data.email` no longer reflects `message`/`phone`/`service` at all — only fixed server text plus the escaped name. |
| G5 | ~~CRITICAL~~ ✅ **FIXED** | [main.py:76](app/main.py#L76), [main.py:325](app/main.py#L325) | `ContactForm.full_name` is an unconstrained `str` with no length limit and no character filtering, then used to build `admin_subject = f"... from {data.full_name}"`, which becomes an email `Subject` header. Python's legacy `email.mime` header assignment does not sanitize embedded CR/LF by itself. | Email header injection: a crafted `full_name` containing newline sequences can attempt to inject additional headers (e.g. `Bcc:`) into the outgoing message. | **Fixed:** `send_email()` strips `\r`/`\n` from `to` and `subject` before use; every `ContactForm` field now has a `Field(max_length=...)` limit. |
| G6 | HIGH | [.env](.env) | A real Gmail **app password** for `avigronix@gmail.com` is stored in plaintext in `.env` in the working directory. Confirmed via `git log -p -- .env` that this specific file was never committed (only `.env.example`'s placeholder value appears in history) — so this is not a repo/history leak, but the live credential has now been read in the course of this review. | Any local read access to this machine/checkout exposes working SMTP credentials for the company's real inbox. | Rotate the Gmail app password now as a precaution, and going forward keep real secrets out of any file an AI tool, contractor, or CI log might read — use your host's secret manager instead of a plaintext `.env` where possible. |
| G7 | ~~HIGH~~ ✅ **FIXED** | §2.4 above | 41 known advisories in the dependencies actually running (`fastapi 0.104.1`, `starlette 0.27.0`, `python-multipart 0.0.6`, etc.), including a real, reachable ReDoS (PYSEC-2024-38) in multipart Content-Type parsing. `requirements.txt`'s own pins carry 24 advisories in 3 packages. | Both "what's pinned" and "what's actually running" are stale and vulnerable; neither matches the other, which also means deploying via `pip install -r requirements.txt` would silently change behavior versus what's been tested. | **Fixed:** `requirements.txt` bumped to `fastapi==0.141.1` / `uvicorn==0.54.0` / `python-multipart==0.0.32` / `python-dotenv==1.2.3` (transitively resolves `starlette` to `1.7.0`, `anyio` to `4.15.1`). `pip-audit` now reports zero known vulnerabilities. Required fixing 8 `TemplateResponse(...)` call sites for the new `(request, name, context)` signature — see [§0 Round 2](#0-fixes-applied-2026-09-25). |
| G8 | ~~MEDIUM~~ ✅ **FIXED** | [main.py:130-138](app/main.py#L130-L138) | The `OPTIONS` (CORS preflight) branch reflects **any** `Origin` header back as `Access-Control-Allow-Origin` with `Access-Control-Allow-Credentials: true` and `Access-Control-Allow-Methods: *`, unconditionally — while the real (non-OPTIONS) response a few lines later correctly restricts this reflection to `localhost`/`127.0.0.1` only ([main.py:165-167](app/main.py#L165-L167)). | Inconsistent CORS policy: the preflight response alone doesn't grant a browser access (the actual response's headers still gate it), so real-world exploitability is limited today, but this is exactly the kind of inconsistency that turns into a real hole the next time someone "fixes" the actual-response branch to match the preflight one. `Access-Control-Allow-Methods: "*"` is also spec-invalid when combined with `Allow-Credentials: true` (wildcards are ignored once credentials are involved). | **Fixed:** both branches now call one shared `_is_allowed_cors_origin()` helper; methods list is now the explicit `"GET, POST, PUT, OPTIONS"`. |
| G9 | ~~MEDIUM~~ ✅ **FIXED** | [templates/public_shop.html:452,460,468,476,484](app/templates/public_shop.html#L452) and [:613](app/templates/public_shop.html#L613), [templates/preview_business.html:752](app/templates/preview_business.html#L752) | Every `target="_blank"` link on the public shop pages (social icons, "Developed by AVIGRONIX" footer credit) is missing `rel="noopener noreferrer"`. | Classic reverse-tabnabbing: the opened page can access `window.opener` and redirect the original tab. Low severity here since the destinations are the shop owner's own social profiles / the company's own site, but it's a one-line fix applied inconsistently (other parts of the codebase don't have this pattern to begin with). | **Fixed:** `rel="noopener noreferrer"` added to all 6 occurrences, plus the newly-real links added under A3. |
| G10 | ~~LOW~~ ✅ **FIXED** | [routers/shop.py](app/routers/shop.py) multiple `except Exception as e: raise HTTPException(500, detail=f"...{str(e)}")` blocks (lines [274-278](app/routers/shop.py#L274-L278), [419-421](app/routers/shop.py#L419-L421), [459-461](app/routers/shop.py#L459-L461), [477-478](app/routers/shop.py#L477-L478), [493-494](app/routers/shop.py#L493-L494), [514-516](app/routers/shop.py#L514-L516)) | Raw exception text (which can include internal field names, driver error strings, etc.) is returned to the client in the HTTP response body. | Minor information disclosure that makes reconnaissance easier for an attacker probing these endpoints. | **Fixed:** all six now `logger.error(...)` the real exception and return a fixed generic message to the client. |
| — | — | — | `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`, a real `Content-Security-Policy`, and conditional HSTS are all already configured globally in [main.py:186-197](app/main.py#L186-L197); the contact form has a working honeypot field plus IP-based rate limiting; uploaded files are saved under a server-generated UUID name rather than the client-supplied filename ([shop.py:82-101](app/routers/shop.py#L82-L101), specifically to avoid path traversal); the real SMTP password was never committed to git. | (Positive findings — the baseline security posture for the *marketing site* itself is well above average; the critical issues above are concentrated entirely in the shop feature's JSON API and the contact-mail flow.) | — |

### H. Code quality & maintainability

| # | Severity | File:Line | Issue | Why it matters | Recommended fix |
|---|---|---|---|---|---|
| H1 | MEDIUM | [routers/shop.py:31-76](app/routers/shop.py#L31-L76) | `Product`, `Banner`, `ShopDetails`, `ShopDetailsResponse` Pydantic models are defined but never referenced by any route. | Dead code that also signals an unfinished feature (product/banner management — see A5). | Either implement the endpoints that should use these models, or remove them until they're needed. |
| H2 | ~~MEDIUM~~ ✅ **FIXED** | [routers/shop.py:287-320](app/routers/shop.py#L287-L320) | `ShopRegistration` model is defined immediately above `register_business` but that handler never uses it (see A2). | Reads as if validation exists when it doesn't; misleading to the next developer. | **Fixed:** wired in — see A2. |
| H3 | ~~LOW~~ ✅ **FIXED** | [static/js/main.js](app/static/js/main.js), [static/js/image-loader.js](app/static/js/image-loader.js) | Neither file is referenced by any template (confirmed via a repo-wide search for `main.js`/`image-loader.js`). `main.js` also duplicates mobile-menu-toggle logic that's implemented separately (and correctly) inline in [base.html:728](app/templates/base.html#L728). | Dead files that could confuse a future contributor into thinking the contact form or lazy-loading behavior is implemented there. | **Fixed:** both files deleted. |
| H4 | ~~LOW~~ ✅ **FIXED** | Throughout [main.py](app/main.py) and [routers/shop.py](app/routers/shop.py) | `print(...)` is used for logging/debugging in production code paths (e.g. [main.py:72](app/main.py#L72), [main.py:124](app/main.py#L124), [main.py:225](app/main.py#L225), [shop.py:264](app/routers/shop.py#L264) commented-out, [shop.py:331](app/routers/shop.py#L331), [shop.py:420](app/routers/shop.py#L420)), including one that logs every single request's Host/Origin/subdomain unconditionally. | No log levels, no structured logging, noisy stdout in production, harder to wire into any real log aggregation later. | **Fixed:** every remaining active `print()` (send_email's error, the per-request subdomain log, plus the ones already converted under G10) is now `logger.error(...)`/`logger.debug(...)`/`logger.warning(...)` as appropriate; the per-request log is `debug`-level specifically so it stays quiet by default, per this row's own original suggestion. |
| H5 | ~~LOW~~ ✅ **FIXED** | [routers/shop.py:12](app/routers/shop.py#L12) | `from bson import ObjectId` is imported but never used. | Dead import. | **Fixed:** removed, along with several more unused imports found via an AST-based scan of every `.py` file in the project (`FastAPI`, `StaticFiles`, `EmailStr`, `JSONResponse`, `Dict`, `Any` in `routers/shop.py`; a duplicate `HTMLResponse` and a dead `import json` in `main.py`) and an entire redundant re-import block partway through `routers/shop.py`. |
| H6 | LOW | [routers/shop.py:352,366](app/routers/shop.py#L352) | `shop_document["shop_status"]` is set to `"active"` at line 352 and then immediately overwritten by `json_data.get('shopStatus') or "active"` at line 366 — the first assignment is pointless, and the second lets the client set `shop_status` to any arbitrary string (not restricted to `"active"`/`"inactive"`). | Dead assignment plus unchecked value that flows into the subdomain middleware's `{"shop_status": "active"}` filter. | Remove the first assignment; validate `shopStatus` against an enum if it's meant to be settable at registration at all. |

### I. Legal, analytics & trust

| # | Severity | File:Line | Issue | Why it matters | Recommended fix |
|---|---|---|---|---|---|
| I1 | ~~HIGH~~ ✅ **FIXED** | [templates/base.html:120-129](app/templates/base.html#L120-L129) | Google Analytics (`gtag`) loads unconditionally for every visitor, with no cookie-consent banner or Google Consent Mode gating it. | GDPR/UK-GDPR (and increasingly, general best practice) requires consent before non-essential analytics cookies are set for EU/UK visitors — and [about.html](app/templates/about.html)/[base.html](app/templates/base.html) explicitly market the company as serving "the US, Europe & the Middle East." | **Fixed:** Accept/Reject banner plus full Consent Mode v2 (`default` denied → `update` granted on Accept, remembered in `localStorage`) — see [§0 Round 3](#0-fixes-applied-2026-09-25). |
| I2 | ~~MEDIUM~~ ✅ **FIXED** | [templates/privacy_policy.html](app/templates/privacy_policy.html) | The policy lists what data is collected (contact form fields — [line 32](app/templates/privacy_policy.html#L32)) but wasn't checked against the current *actual* data collection, which per this audit's findings also now includes: full bank account number, IFSC code, UPI ID, GST number, and physical address, all collected at shop registration ([register_business.html](app/templates/register_business.html)) with no mention of this specifically in the policy text reviewed. | If bank/financial details are being collected from shop owners, the privacy policy should explicitly say so, state the legal basis, retention period, and where it's stored/processed — not just describe the marketing-site contact form. | **Fixed:** on inspection the policy already covered most of this; added the one genuinely missing and important disclosure — that shop pages **publicly** display the payment/bank/UPI details entered, by design. See [§0 Round 3](#0-fixes-applied-2026-09-25). |
| I3 | LOW | [templates/base.html:672-689](app/templates/base.html#L672-L689) | Social media icons (Facebook/Twitter/LinkedIn/Instagram) are commented out in the footer with a clear note ("commented out until real profile URLs are available") rather than shipped as dead `href="#"` links. | (Positive finding — this is the correct way to handle a not-yet-ready trust element, flagged here only because "missing social profiles" is worth tracking as a to-do, not because the code is wrong.) | Add real profile links when the company's social accounts exist, then uncomment. |
| — | — | — | Company contact info, business hours ("Mon–Fri • 9 AM–6 PM IST" — [base.html:600](app/templates/base.html#L600)), and a physical address are all clearly and consistently presented; the honeypot-based spam protection on the contact form is a reasonable low-friction anti-spam measure. | (Positive finding.) | — |

---

## 4. Needs Manual Verification

These cannot be confirmed from source code alone:

- **Live SSL/TLS configuration** (certificate validity, HSTS actually reaching the browser — note that [main.py:193](app/main.py#L193)'s HSTS header only fires when `request.url.scheme == "https"`, which depends on whether the production reverse proxy forwards `X-Forwarded-Proto` and whether Uvicorn is run with `--proxy-headers`; neither is configured in this repo).
- **Real email deliverability**: whether `/api/contact` actually lands mail in the inbox reliably (SPF/DKIM/DMARC records for the sending domain, Gmail's daily sending limits under abuse — relevant given G4).
- **MongoDB deployment**: authentication on the Mongo instance itself, network exposure, backup policy, and whether it's a replica set (needed if you want to fix A7 with transactions).
- **Actual Lighthouse/PageSpeed/Core Web Vitals scores** on the deployed site — this review inferred likely LCP/CLS issues from code (Tailwind CDN, no image dimensions) but did not measure them.
- **Google Search Console / Bing Webmaster Tools** indexing status, and whether `/google217adffd4029d326.html` ([main.py:258](app/main.py#L258)) is still the live, current verification token.
- **Exact WCAG contrast ratios** (F2) — needs an automated tool (axe DevTools, Lighthouse, or WAVE) run against the live pages.
- **Production process management** — how the app is actually started/kept alive/restarted (systemd, Docker, PM2-equivalent) — nothing in the repo answers this, and it directly affects G7's severity (multi-worker deployments make the in-memory rate limiter's documented per-process weakness real, per the comment in [rate_limit.py](app/rate_limit.py)).
- **CDN/hosting-level protections** (WAF, DDoS protection) in front of the app — the code has no such layer of its own.

---

## 5. Prioritized Action Plan

### Fix immediately (this changes real risk today)

1. **[Small]** Lock down `GET /shop/api/shops` and `GET /shop/api/shop/{subdomain}` — remove `payment_info` from the response, or put both behind auth. (G1, G2)
2. **[Small]** Add authentication/ownership check to `PUT /shop/api/shop/{subdomain}/status`. (G3, A1)
3. **[Small]** `html.escape()` every user-supplied field before it goes into an email body in `main.py`; add `max_length` + control-character stripping to `ContactForm`. (G4, G5)
4. **[Small]** Rotate the Gmail app password in `.env` and store the new one via your host's secret manager rather than a plaintext file. (G6)

### Fix this week

5. **[Medium]** Reconcile `requirements.txt` against a single, currently-patched version set; rebuild the venv from it; re-run `pip-audit`. (G7)
6. **[Small]** Cap upload file size in `save_uploaded_file`; delete orphaned preview uploads on a schedule. (D3, item 6 of top-10)
7. **[Medium]** Replace `cdn.tailwindcss.com` with a compiled Tailwind build served from `/static/`. (D1)
8. **[Small]** Fix the CORS preflight branch to use the same origin allowlist as the real-response branch. (G8)
9. **[Small]** Add `rel="noopener noreferrer"` to all `target="_blank"` links. (G9)
10. **[Medium]** Add a cookie-consent banner gating Google Analytics. (I1)
11. **[Small]** Make `register_business` use the existing `ShopRegistration` Pydantic model instead of hand-parsed JSON; stop returning raw exception text to clients. (A2, G10)
12. **[Small]** Fix the hardcoded `href="#"` social icons in `preview_business.html`. (A3)

### Improvements later

13. **[Medium]** Build a real shop-edit flow and product/banner management, using the already-defined `Product`/`Banner`/`ShopDetails` models. (A5, H1)
14. **[Small]** Wire up or remove `generate_shop_qr_code()` and the missing `shopQR`/`bankQR` form fields. (A4)
15. **[Small]** Add a unique MongoDB index on `shops.subdomain`. (A6)
16. **[Small]** Delete the unused `static/js/main.js` and `image-loader.js`. (H3, A8)
17. **[Small]** Replace `print()` debug/logging statements with the `logging` module. (H4)
18. **[Small]** Add `width`/`height`/`loading="lazy"` to all `<img>` tags. (D2)
19. **[Small]** Add a "skip to content" link. (F1)
20. **[Small]** Add SRI hashes to third-party `<script>` tags. (D4)
21. **[Small]** Extend the privacy policy to explicitly cover shop-registration data (including financial fields). (I2)
22. **[Small]** Convert `og-banner.png`/`logo.png` to WebP. (D5)

---

## 6. Missing Features Worth Adding

A professional software-services company site at this stage would typically also have:

- **Case studies / portfolio detail pages** with measurable outcomes (currently `projects.html` describes the kind of systems built, but there's no dedicated case-study format with client, problem, solution, result).
- **Client testimonials with attribution** (name, company, photo/logo) — none exist currently; this project correctly avoided *fake* ones (a strength), but real ones, once available, would materially help conversion.
- **Real social media profiles** (already scaffolded and commented out in `base.html` — just needs the actual links).
- **A CAPTCHA or equivalent (e.g. Cloudflare Turnstile) as a second layer behind the honeypot** for the contact form, especially now that it's identified as an abuse vector (G4).
- **A status/uptime page or SLA statement**, common trust signals for a company selling backend/cloud engineering services.
- **Company registration / legal entity details** (CIN/GST number, if applicable) in the footer or an "About" section — international B2B buyers often look for this before engaging.
- **A blog RSS/Atom feed** — the blog already has clean structured content in `blog_content.py`, an RSS feed is a small addition with real SEO/distribution value.
- **Structured data beyond `Organization`**: `Service` schema per service offered, and `BreadcrumbList` on inner pages, would strengthen the already-good SEO baseline further.
- **A dedicated admin/owner dashboard for the shop feature** (login, edit shop, manage products/orders) — right now the shop product is registration-only with no lifecycle beyond that, which is the single biggest functional gap found in this review.
