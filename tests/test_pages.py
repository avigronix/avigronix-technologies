"""Static pages, htmx partials, 404, sitemap/robots, security headers."""
import pytest

STATIC_PAGES = [
    "/",
    "/about",
    "/services",
    "/team",
    "/projects",
    "/blog",
    "/blog/fastapi-best-practices",
    "/faq",
    "/contact",
    "/privacy-policy",
    "/terms-and-conditions",
    "/shop/register",
]

HTMX_PAGES = [p for p in STATIC_PAGES if p != "/shop/register"]


@pytest.mark.parametrize("path", STATIC_PAGES)
async def test_static_page_full(client, path):
    r = await client.get(path)
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    # full page = rendered inside base.html (nav, footer, cookie banner)
    assert "<html" in r.text
    assert 'id="cookie-consent-banner"' in r.text


@pytest.mark.parametrize("path", HTMX_PAGES)
async def test_static_page_htmx_partial(client, path):
    r = await client.get(path, headers={"HX-Request": "true"})
    assert r.status_code == 200
    # partial = just the content block, no page shell
    assert "<html" not in r.text
    assert 'id="cookie-consent-banner"' not in r.text
    # the page title travels in the HX-Trigger header instead
    assert "pageTitleUpdate" in r.headers.get("hx-trigger", "")


async def test_404_page(client):
    r = await client.get("/definitely-not-a-page")
    assert r.status_code == 404
    assert "Page Not Found" in r.text
    assert '<link rel="icon" href="/favicon.ico" sizes="any">' in r.text  # error_404.html


async def test_500_page(app_running, monkeypatch):
    import httpx
    import main

    def boom(*a, **kw):
        raise RuntimeError("simulated unhandled error")

    monkeypatch.setattr(main, "render_page", boom)
    # Starlette's ServerErrorMiddleware sends the generated error_500.html
    # response and then re-raises the original exception (so a real server
    # still logs/reports it); the default client fixture would surface that
    # re-raise as a test failure even though the response was correct, so
    # this one test asks the ASGI transport not to propagate it.
    transport = httpx.ASGITransport(app=app_running, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        r = await client.get("/")
    assert r.status_code == 500
    assert "Something Went Wrong" in r.text or "went wrong" in r.text.lower()
    assert '<link rel="icon" href="/favicon.ico" sizes="any">' in r.text  # error_500.html
    assert "simulated unhandled error" not in r.text


async def test_unknown_blog_post_is_404(client):
    r = await client.get("/blog/no-such-post")
    assert r.status_code == 404


async def test_sitemap(client):
    from blog_content import list_posts

    r = await client.get("/sitemap.xml")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/xml")
    body = r.text
    assert body.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    for path in ["/", "/about", "/services", "/contact", "/blog", "/privacy-policy"]:
        assert f"<loc>https://avigronix.com{path}</loc>" in body
    for post in list_posts():
        assert f"<loc>https://avigronix.com/blog/{post['slug']}</loc>" in body
    # private/operational routes never appear in the sitemap
    for private in ["/shop/register", "/shop/preview", "/shop/api", "/health", "/favicon.ico"]:
        assert f"avigronix.com{private}<" not in body


async def test_robots(client):
    r = await client.get("/robots.txt")
    assert r.status_code == 200
    body = r.text
    assert "User-agent: *" in body
    for disallowed in ["/shop/register", "/shop/preview", "/shop/register-business", "/shop/api/"]:
        assert f"Disallow: {disallowed}" in body
    assert "Sitemap: https://avigronix.com/sitemap.xml" in body


@pytest.mark.parametrize("path", ["/", "/contact", "/shop/register", "/definitely-not-a-page", "/robots.txt"])
async def test_security_headers(client, path):
    r = await client.get(path)
    h = r.headers
    assert h["x-content-type-options"] == "nosniff"
    assert h["x-frame-options"] == "DENY"
    assert h["referrer-policy"] == "strict-origin-when-cross-origin"
    csp = h["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "unsafe-eval" not in csp
    assert "cdn.tailwindcss.com" not in csp


async def test_static_assets_cached(client):
    r = await client.get("/static/css/tailwind.min.css")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "public, max-age=86400"


async def test_favicon_ico_served_at_site_root(client):
    """Browsers request /favicon.ico at the root by convention, independent
    of any <link rel="icon"> tag — it must not 404."""
    r = await client.get("/favicon.ico")
    assert r.status_code == 200
    assert r.headers["content-type"] in ("image/vnd.microsoft.icon", "image/x-icon")
    assert r.headers["cache-control"] == "public, max-age=86400"
    assert r.content[:4] in (b"\x00\x00\x01\x00", b"\x00\x00\x02\x00")  # ICO magic bytes


async def test_favicon_ico_is_not_rate_limited(client):
    for _ in range(310):
        r = await client.get("/favicon.ico")
    assert r.status_code == 200


@pytest.mark.parametrize(
    "path,template",
    [
        ("/", "base.html (via index.html)"),
        ("/definitely-not-a-page", "error_404.html"),
        ("/shop/register", "register_business.html (via base.html)"),
    ],
)
async def test_favicon_ico_linked_in_head(client, path, template):
    r = await client.get(path)
    assert '<link rel="icon" href="/favicon.ico" sizes="any">' in r.text, template


# Every encoding tried against the old code (see WEBSITE_AUDIT_REPORT.md,
# G11). In production the app runs from app/, so "uploads/.." is app/ itself:
# the old route served any file directly inside it (main.py, database.py,
# and an app/.env if one existed). The test working directory plays app/.
TRAVERSAL_PREFIXES = [
    "/uploads/../",                 # literal (sent as-is, not normalised)
    "/uploads/%2e%2e/",             # percent-encoded dots
    "/uploads/%2E%2E/",             # upper-case encoding
    "/uploads/.%2e/",               # mixed
    "/uploads/%2e./",               # mixed
    "/uploads/%252e%252e/",         # double encoding
    "/uploads/..%5c",               # backslash
    "/uploads/..%255c",             # double-encoded backslash
    "/uploads/logos/../../",        # from inside a real sub-folder
    "/uploads/logos/%2e%2e/%2e%2e/",
    "/uploads/logos/..%2f..%2f",    # encoded slash
    "/uploads/..%2f",
    "/uploads/%2e%2e%2f",
]
TRAVERSAL_TARGETS = ["sentinel_secret.py", ".env", "main.py", "database.py", "requirements.txt", ".git%2fconfig"]
TRAVERSAL_EXTRA = [
    "/uploads/%2e%2e/..",           # directory targets (old code: 500)
    "/uploads/%2e%2e/templates",
    "/uploads/logos/..",
    "/uploads/logos/%2e%2e",
    "/uploads/logos/.",
    "/uploads/%2e%2e/%2e%2e%2f.git%2fconfig",
    "/uploads/logos%2f..%2f..%2fmain.py/x",
    "/uploads/LOGOS/real.png",      # folder names are matched exactly
]


def _plant_sentinels():
    from conftest import WORKDIR

    (WORKDIR / "sentinel_secret.py").write_text("import os  # TOP-SECRET-SOURCE")
    (WORKDIR / ".env").write_text("SMTP_PASS=TOP-SECRET-DOTENV")


async def raw_get(app, raw_path):
    """GET with the path passed to the app exactly as given.

    httpx normalises literal "../" segments before sending, but real
    clients (curl --path-as-is, raw sockets) don't and uvicorn passes them
    through, so literal variants are sent straight to the ASGI app."""
    from urllib.parse import unquote

    raw = raw_path.encode()
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET",
        "scheme": "http", "path": unquote(raw_path), "raw_path": raw, "query_string": b"",
        "root_path": "", "headers": [(b"host", b"testserver")],
        "client": ("127.0.0.1", 50000), "server": ("testserver", 80),
    }
    status, chunks = None, []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        nonlocal status
        if message["type"] == "http.response.start":
            status = message["status"]
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))

    await app(scope, receive, send)
    return status, b"".join(chunks).decode(errors="replace")


@pytest.mark.parametrize(
    "path", [p + t for p in TRAVERSAL_PREFIXES for t in TRAVERSAL_TARGETS] + TRAVERSAL_EXTRA
)
async def test_uploads_route_blocks_path_traversal(client, app_running, path):
    _plant_sentinels()
    status, body = await raw_get(app_running, path)
    assert status == 404
    assert "TOP-SECRET" not in body
    # and through a normal HTTP client too
    r = await client.get(path)
    assert r.status_code == 404
    assert "TOP-SECRET" not in r.text


async def test_raw_get_helper_really_sends_literal_dots(app_running):
    """Guard for the helper itself: the old vulnerable route must be reachable
    through it, otherwise the traversal tests above would prove nothing."""
    from conftest import UPLOADS

    (UPLOADS / "logos" / "x.png").write_bytes(b"png")
    status, _ = await raw_get(app_running, "/uploads/logos/../logos/x.png")
    assert status == 404  # ".." in the filename part is refused, not normalised away
    status, body = await raw_get(app_running, "/uploads/logos/x.png")
    assert status == 200 and body == "png"


@pytest.mark.parametrize(
    "path",
    [p.replace("/uploads/", "/static/", 1) + t
     for p in TRAVERSAL_PREFIXES if "logos" not in p for t in ("sentinel_secret.py", ".env")]
    + ["/static/css/../../.env", "/static/css/%2e%2e/%2e%2e/.env", "/static/css/..%2f..%2f.env"],
)
async def test_static_mount_blocks_path_traversal(client, app_running, path):
    _plant_sentinels()
    status, body = await raw_get(app_running, path)
    assert status == 404
    assert "TOP-SECRET" not in body
    r = await client.get(path)
    assert r.status_code == 404
    assert "TOP-SECRET" not in r.text


async def test_uploads_route_serves_real_upload(client):
    from conftest import UPLOADS, TINY_PNG

    (UPLOADS / "logos" / "abc.png").write_bytes(TINY_PNG)
    r = await client.get("/uploads/logos/abc.png")
    assert r.status_code == 200
    assert r.content == TINY_PNG
    # unknown sub-folder is not served even if the file exists
    (UPLOADS / "other").mkdir(exist_ok=True)
    (UPLOADS / "other" / "abc.png").write_bytes(TINY_PNG)
    assert (await client.get("/uploads/other/abc.png")).status_code == 404
