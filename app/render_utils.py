import html
import json
import logging
import os
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import Response


DEFAULT_SITE_URL = "https://avigronix.com"
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0"}


def get_site_url() -> str:
    """The public origin every canonical, sitemap, Open Graph and JSON-LD URL
    is built from — read from SITE_URL in one place so they can't disagree.

    Normalized so a slightly-off value can't leak bad URLs into production
    HTML: surrounding whitespace and trailing slashes are dropped (otherwise
    canonicals become "https://avigronix.com//about"), and http:// is upgraded
    to https:// for any real domain — only local development hosts may stay
    on plain http."""
    raw = os.environ.get("SITE_URL", "").strip().rstrip("/")
    if not raw:
        return DEFAULT_SITE_URL
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        logging.getLogger("avigronix").warning("Ignoring invalid SITE_URL %r", raw)
        return DEFAULT_SITE_URL
    if parts.scheme == "http" and parts.hostname not in _LOCAL_HOSTS:
        logging.getLogger("avigronix").warning("SITE_URL %r uses http; serving https URLs instead", raw)
        raw = "https://" + raw[len("http://"):]
    return raw


def make_templates() -> Jinja2Templates:
    """Every router renders pages that extend base.html, so every router's
    template environment needs the same globals. Routers used to each create
    their own Jinja2Templates and only main.py's got site_url and
    ga_measurement_id — so every page served from routers/pages.py rendered
    an empty Google Analytics ID, a blank footer year, and ignored SITE_URL."""
    from datetime import datetime

    templates = Jinja2Templates(directory="templates")
    templates.env.globals["current_year"] = datetime.now().year
    templates.env.globals["site_url"] = get_site_url()
    templates.env.globals["ga_measurement_id"] = os.environ.get("GA_MEASUREMENT_ID", "G-QMZ7RMVX47")
    return templates


def render_page(request: Request, templates: Jinja2Templates, template_name: str, context: dict | None = None) -> Response:
    """Render a page template as a full document on direct/refresh visits, or as
    just the {% block content %} fragment on HTMX navigation requests (detected
    via the HX-Request header) so the navbar/footer stay mounted and only the
    content swaps.

    Also keeps the browser tab title in sync on HTMX swaps: since the partial
    response doesn't include <title>, the page's own {% block title %} text is
    extracted server-side and sent back via the HX-Trigger response header, and
    a small listener in base.html applies it to document.title.
    """
    context = dict(context or {})
    is_htmx = request.headers.get("HX-Request") == "true"
    context["base_template"] = "partial_base.html" if is_htmx else "base.html"
    context["request"] = request

    response = templates.TemplateResponse(request, template_name, context)

    if is_htmx:
        tmpl = templates.env.get_template(template_name)
        title_block = tmpl.blocks.get("title")
        if title_block:
            block_ctx = tmpl.new_context(context)
            # The block renders autoescaped HTML ("&amp;"), but document.title
            # is set as plain text, so unescape it before sending.
            title_text = html.unescape(" ".join("".join(title_block(block_ctx)).split()))
            response.headers["HX-Trigger"] = json.dumps({"pageTitleUpdate": title_text})

    return response
