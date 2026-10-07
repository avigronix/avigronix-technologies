# Load .env before importing any app module: routers.shop, rate_limit and
# database read their settings (ADMIN_API_KEY, MONGODB_URL, ...) at import
# time, so loading it later meant values set in .env were silently ignored.
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, PlainTextResponse, FileResponse, JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from routers import pages
from routers.shop import router as shop_manage, cleanup_orphaned_uploads
from render_utils import render_page, get_site_url, make_templates
from rate_limit import limiter
import os
import html
import asyncio
import logging
from contextlib import asynccontextmanager
logger = logging.getLogger("avigronix")
logging.basicConfig(level=logging.INFO)

from database import db, client as mongo_client

from urllib.parse import urlparse
from pydantic import BaseModel, EmailStr, Field
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import smtplib


UPLOAD_CLEANUP_INTERVAL_SECONDS = 6 * 60 * 60


async def _periodic_upload_cleanup():
    """Remove abandoned preview uploads at startup and every few hours."""
    while True:
        try:
            await cleanup_orphaned_uploads()
        except Exception as e:
            logger.warning("Orphaned-upload cleanup skipped (%s)", e)
        await asyncio.sleep(UPLOAD_CLEANUP_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Unique index on subdomain closes the registration race the app-level
    # availability check alone can't (two concurrent signups for the same
    # name); duplicate inserts are caught and turned into the normal
    # 'subdomain already taken' response in routers/shop.py.
    #
    # Must never crash app startup: if the collection already has duplicate
    # subdomains (or the DB is briefly unreachable), index creation fails —
    # log a clear warning and keep running with the existing app-level
    # availability check as the fallback, instead of taking the whole site
    # down.
    try:
        await db.shops.create_index("subdomain", unique=True)
    except Exception as e:
        logger.warning(
            "Could not create unique index on shops.subdomain (%s). "
            "The app will keep running, but the subdomain-race protection "
            "from this index is not active until the underlying data/DB "
            "issue is fixed and the index is created.",
            e,
        )

    cleanup_task = asyncio.create_task(_periodic_upload_cleanup())

    yield

    cleanup_task.cancel()

    # Shutdown: release the MongoDB connection pool cleanly. There was no
    # shutdown handler before this (confirmed: no @app.on_event("shutdown")
    # or client.close() anywhere in the pre-existing code), so this isn't
    # migrating prior behavior — it's closing a gap that lifespan's
    # post-yield half now makes the natural place to fix.
    mongo_client.close()


# Interactive API docs list every endpoint (including the admin ones), so
# they're off unless explicitly enabled, e.g. on a developer machine.
API_DOCS_ENABLED = os.environ.get("ENABLE_API_DOCS", "").strip().lower() == "true"

app = FastAPI(
    title="AVIGRONIX TECHNOLOGIES",
    description="Transform Your Business With Our Digital Solutions",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if API_DOCS_ENABLED else None,
    redoc_url="/redoc" if API_DOCS_ENABLED else None,
    openapi_url="/openapi.json" if API_DOCS_ENABLED else None,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)




SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")


def send_email(to: str, subject: str, body: str) -> bool:
    """
    Common reusable function:
    - to: receiver email
    - subject: mail subject
    - body: HTML/string body
    """
    try:
        # Strip CR/LF from anything that becomes a header value, so a
        # crafted field can't inject extra headers (e.g. Bcc) into the mail.
        to = to.replace("\r", " ").replace("\n", " ")
        subject = subject.replace("\r", " ").replace("\n", " ")

        msg = MIMEMultipart()
        msg["From"] = SMTP_USER
        msg["To"] = to
        msg["Subject"] = subject

        # body as HTML
        msg.attach(MIMEText(body, "html"))

        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg)
        server.quit()

        return True
    except Exception as e:
        logger.error("Email Error: %s", e)
        return False
    
class ContactForm(BaseModel):
    full_name: str = Field(..., max_length=200)
    email: EmailStr
    phone: str = Field(..., max_length=20)
    company: str | None = Field(None, max_length=200)
    service: str = Field(..., max_length=100)
    message: str = Field(..., max_length=5000)
    website: str | None = Field(None, max_length=200)  # honeypot: real users never see/fill this field

def extract_subdomain(hostname: str) -> str:
    """Extract subdomain safely"""
    if not hostname:
        return ""

    hostname = hostname.split(":")[0]  # remove port if present

    # Localhost root — no subdomain
    if hostname in ["localhost", "127.0.0.1"]:
        return ""

    # subdomain.localhost
    if hostname.endswith(".localhost"):
        return hostname.split(".")[0]

    # ngrok
    if ".ngrok-free.app" in hostname:
        return hostname.split(".")[0]

    # Production domain
    if hostname.endswith(".avigronix.com"):
        parts = hostname.split(".")
        if len(parts) == 3:  
            sub = parts[0]
            if sub not in ["www", "avigronix"]:
                return sub

    return ""


def _is_allowed_cors_origin(origin: str) -> bool:
    """Single allowlist shared by the preflight (OPTIONS) and real-response
    CORS branches below, so they can never drift apart again: local dev
    origins only, for now."""
    return "localhost" in origin or "127.0.0.1" in origin


@app.middleware("http")
async def shop_subdomain_middleware(request: Request, call_next):
    origin = request.headers.get("origin")
    host = request.headers.get("host", "")

    # Extract subdomain
    origin_sub = extract_subdomain(urlparse(origin).hostname if origin else "")
    host_sub = extract_subdomain(host)
    subdomain = origin_sub or host_sub

    logger.debug("Host=%s | Origin=%s | Subdomain=%s", host, origin, subdomain)

    request.state.subdomain = subdomain
    request.state.shop = None
    request.state.isAvailableSubDomain = False

    # Allow OPTIONS (CORS Preflight) — uses the exact same allowlist as the
    # real-response branch further down, so a preflight can never promise
    # access that the actual response won't also grant.
    if request.method == "OPTIONS":
        response = await call_next(request)
        if origin and _is_allowed_cors_origin(origin):
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, OPTIONS"
        return response

    # Subdomain exists → fetch shop
    if subdomain:
        shop = await db.shops.find_one({
            "subdomain": subdomain,
            "shop_status": "active"
        })

        if not shop:
            return templates.TemplateResponse(
                request,
                "not_found.html",
                {
                    "subdomain": subdomain,
                    "msg": f"Shop '{subdomain}' not found"
                }
            )

        # Attach in request.state
        request.state.shop = shop
        request.state.isAvailableSubDomain = True

    # Call the next route
    response = await call_next(request)

    # Add CORS headers for local development only
    if origin and _is_allowed_cors_origin(origin):
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"

    return response


@app.middleware("http")
async def trailing_slash_redirect_middleware(request: Request, call_next):
    """Canonical URLs have no trailing slash (/about, not /about/). Starlette's
    built-in redirect_slashes answers /about/ with a 307 to an absolute
    http:// URL (the app sees plain http behind Cloudflare), which Cloudflare
    then 301s to https — two hops, the first one temporary. Answer with a
    single permanent redirect instead, using a relative Location so the
    browser keeps whatever scheme and host it actually requested."""
    path = request.url.path
    if request.method in ("GET", "HEAD") and path != "/" and path.endswith("/"):
        # Collapse leading slashes too: a Location of "//evil.com" would be a
        # protocol-relative URL, i.e. an open redirect to another host.
        target = "/" + path.strip("/")
        if request.url.query:
            target += "?" + request.url.query
        return Response(status_code=301, headers={"Location": target})
    return await call_next(request)


CSP = (
    "default-src 'self'; "
    # 'unsafe-eval' was only ever needed by the cdn.tailwindcss.com "play CDN"
    # script's runtime JIT compiler — both are gone now that Tailwind is
    # compiled ahead of time (see WEBSITE_AUDIT_REPORT.md). Nothing else in
    # the codebase calls eval()/new Function()/string-based setTimeout
    # (checked), and htmx executes swapped-in <script> tags by re-inserting
    # real script elements, not eval, so it doesn't need this either.
    "script-src 'self' 'unsafe-inline' "
    "https://cdnjs.cloudflare.com https://unpkg.com "
    "https://www.googletagmanager.com https://static.cloudflareinsights.com; "
    "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com; "
    "img-src 'self' data: https:; "
    "connect-src 'self' https://www.google-analytics.com https://www.googletagmanager.com "
    "https://cloudflareinsights.com; "
    "frame-ancestors 'none'"
)


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = CSP
    if request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "public, max-age=86400"
    return response


# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Templates
templates = make_templates()

# Include routers
app.include_router(pages.router)
app.include_router(shop_manage)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return templates.TemplateResponse(request, "error_404.html", status_code=404)
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled server error: %s", exc)
    return templates.TemplateResponse(request, "error_500.html", status_code=500)


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    # A request on a shop subdomain gets that shop's public page
    if getattr(request.state, "isAvailableSubDomain", False):
        return templates.TemplateResponse(
            request,
            "public_shop.html",
            {"shop_data": request.state.shop}
        )

    # Otherwise show normal homepage
    return render_page(request, templates, "index.html")

UPLOAD_SUBFOLDERS = {"logos", "banners", "bank_qr", "payment_qr", "shop_qr"}


@app.get("/uploads/{sub_folder}/{filename}")
async def get_image(sub_folder: str, filename: str):
    # Only serve regular files from the known upload folders. Without these
    # checks, "/uploads/%2e%2e/main.py" resolved to uploads/../main.py and
    # served the app's own source code (the app runs from app/).
    if sub_folder not in UPLOAD_SUBFOLDERS or filename in ("", ".", "..") or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=404, detail="Image not found")

    base = os.path.realpath(os.path.join("uploads", sub_folder))
    file_path = os.path.realpath(os.path.join(base, filename))
    if os.path.dirname(file_path) != base or not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="Image not found")

    return FileResponse(file_path)

@app.get("/favicon.ico", include_in_schema=False)
@limiter.exempt
async def favicon():
    """Browsers request this at the site root by convention, independent of
    any <link rel="icon"> tag — served here so that no longer 404s."""
    response = FileResponse(os.path.join("static", "images", "favicon.ico"))
    response.headers["Cache-Control"] = "public, max-age=86400"
    return response

@app.get("/health", include_in_schema=False)
@limiter.exempt
async def health():
    """Uptime-monitor endpoint: 200 when the app and MongoDB are reachable,
    503 when the database isn't. Never exposes error details."""
    try:
        await db.command("ping")
    except Exception as e:
        logger.warning("Health check: database unreachable (%s)", e)
        return JSONResponse({"status": "degraded", "database": "unreachable"}, status_code=503)
    return {"status": "ok", "database": "ok"}

@app.get("/google217adffd4029d326.html", include_in_schema=False, response_class=PlainTextResponse)
def google_site_verification():
    return "google-site-verification: google217adffd4029d326.html"

@app.get("/sitemap.xml", include_in_schema=False)
def sitemap():
    from blog_content import list_posts

    site = get_site_url()
    static_pages = [
        ("/", "weekly", "1.0"),
        ("/about", "monthly", "0.8"),
        ("/services", "weekly", "0.9"),
        ("/projects", "weekly", "0.8"),
        ("/team", "monthly", "0.6"),
        ("/blog", "weekly", "0.7"),
        ("/faq", "monthly", "0.5"),
        ("/contact", "monthly", "0.7"),
        ("/privacy-policy", "yearly", "0.3"),
        ("/terms-and-conditions", "yearly", "0.3"),
    ]

    urls = [
        f"<url><loc>{site}{path}</loc><changefreq>{freq}</changefreq><priority>{priority}</priority></url>"
        for path, freq, priority in static_pages
    ]
    for post in list_posts():
        # lastmod only where a real date exists: the post's own updated date,
        # or its publish date if it has never been revised.
        lastmod = post.get("iso_updated") or post["iso_date"]
        urls.append(
            f"<url><loc>{site}/blog/{post['slug']}</loc><lastmod>{lastmod}</lastmod>"
            f"<changefreq>monthly</changefreq><priority>0.6</priority></url>"
        )

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(urls)
        + "\n</urlset>\n"
    )
    return Response(content=xml, media_type="application/xml")

@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots():
    site = get_site_url()
    content = f"""
User-agent: *
Allow: /
Disallow: /shop/register
Disallow: /shop/preview
Disallow: /shop/register-business
Disallow: /shop/api/
Disallow: /health

Sitemap: {site}/sitemap.xml
"""
    return content.strip()

@app.post("/api/contact")
@limiter.limit("5/10minutes")
async def send_contact_message(request: Request, data: ContactForm):

    # Honeypot: bots fill every field including hidden ones; real visitors
    # never see this field, so a non-empty value means spam. Pretend success
    # without actually sending mail, so the bot doesn't learn to look elsewhere.
    if data.website:
        return {"status": 200, "msg": "Message sent successfully"}

    # Escape every user-supplied value before it goes into an HTML email
    # body — these fields are attacker-controlled free text, and the mail
    # is rendered as HTML by the recipient's mail client.
    safe_full_name = html.escape(data.full_name)
    safe_email = html.escape(data.email)
    safe_phone = html.escape(data.phone)
    safe_company = html.escape(data.company) if data.company else "Not Provided"
    safe_service = html.escape(data.service)
    safe_message = html.escape(data.message)

    # -----------------------------
    # 1. ADMIN MAIL
    # -----------------------------
    admin_subject = f"📬 New Contact Inquiry from {data.full_name}"

    admin_body = f"""
    <div style="font-family: Arial, sans-serif; padding: 20px; color: #333;">
        <h2 style="color: #0F62FE;">New Contact Form Submission</h2>
        <p>You have received a new inquiry from your website.</p>
        <hr style="border:none;border-top:1px solid #eee;margin:20px 0;" />

        <p><strong>Name:</strong> {safe_full_name}</p>
        <p><strong>Email:</strong> {safe_email}</p>
        <p><strong>Phone:</strong> {safe_phone}</p>
        <p><strong>Company:</strong> {safe_company}</p>
        <p><strong>Service Interested:</strong> {safe_service}</p>

        <p style="margin-top:15px;"><strong>Message:</strong></p>
        <div style="background:#f7f7f7;padding:12px;border-radius:6px;">
            {safe_message}
        </div>

        <br>
        <p style="font-size: 13px; color: #777;">
            Sent automatically from AVIGRONIX TECHNOLOGIES contact form.
        </p>
    </div>
    """

    ok1 = send_email(
        to="avigronix@gmail.com",
        subject=admin_subject,
        body=admin_body
    )

    if not ok1:
        raise HTTPException(500, "Failed to send admin email")


    # -----------------------------
    # 2. USER ACKNOWLEDGMENT MAIL
    # -----------------------------
    # `to` here is whatever email address the caller typed in — since the
    # app's own SMTP account sends this, the body must be fixed, server-
    # controlled text only (an escaped name is the one exception), never a
    # reflection of the caller's own message/phone/service. Otherwise this
    # endpoint could be used to relay attacker-authored HTML to arbitrary
    # third-party addresses under our sending reputation.
    user_subject = "Thank You for Contacting AVIGRONIX TECHNOLOGIES"

    user_body = f"""
    <div style="font-family: Arial, sans-serif; padding: 20px; color: #333;">
        <h2 style="color:#0F62FE;">We Received Your Message, {safe_full_name}!</h2>

        <p>Thank you for reaching out to us. Our team will get back to you within 24 hours.</p>

        <br>
        <p style="color:#666;font-size:14px;">
            You can contact us anytime at
            <a href="mailto:avigronix@gmail.com">avigronix@gmail.com</a>.
            <br><br>
            — AVIGRONIX TECHNOLOGIES
        </p>
    </div>
    """

    ok2 = send_email(
        to=data.email,
        subject=user_subject,
        body=user_body
    )

    if not ok2:
        raise HTTPException(500, "Failed to send acknowledgment email")

    return {"status": 200, "msg": "Message sent successfully"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)