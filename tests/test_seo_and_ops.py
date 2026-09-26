"""SEO metadata, structured data, RSS, /health, and startup configuration."""
import ast
import html
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from conftest import APP_DIR

PAGES_WITH_OWN_DESCRIPTION = {
    "/": "index.html",
    "/about": "about.html",
    "/services": "services.html",
    "/team": "team.html",
    "/projects": "projects.html",
    "/blog": "blog.html",
    "/faq": "faq.html",
    "/contact": "contact.html",
    "/privacy-policy": "privacy_policy.html",
    "/terms-and-conditions": "terms.html",
    "/shop/register": "register_business.html",
}


def meta(html_text, attr, name):
    m = re.search(rf'<meta {attr}="{re.escape(name)}"\s+content="([^"]*)"', html_text)
    return html.unescape(m.group(1)) if m else None


def json_ld_blocks(html_text):
    return [json.loads(b) for b in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html_text, re.S)]


# --------------------------------------------------------------------------
# Meta description / canonical / Open Graph
# --------------------------------------------------------------------------
@pytest.mark.parametrize("path,template", PAGES_WITH_OWN_DESCRIPTION.items())
async def test_each_page_uses_its_own_meta_description(client, path, template):
    source = (APP_DIR / "templates" / template).read_text()
    own = re.search(r"{% block description %}(.*?){% endblock %}", source, re.S).group(1).strip()
    r = await client.get(path)
    desc = meta(r.text, "name", "description")
    assert desc == own
    assert meta(r.text, "property", "og:description") == own
    assert meta(r.text, "name", "twitter:description") == own


async def test_descriptions_are_unique(client):
    seen = {}
    for path in PAGES_WITH_OWN_DESCRIPTION:
        seen[path] = meta((await client.get(path)).text, "name", "description")
    assert len(set(seen.values())) == len(seen)


async def test_blog_post_description_is_its_excerpt(client):
    from blog_content import get_post

    r = await client.get("/blog/fastapi-best-practices")
    assert meta(r.text, "name", "description") == get_post("fastapi-best-practices")["excerpt"]


@pytest.mark.parametrize("path", ["/", "/about", "/blog/fastapi-best-practices", "/shop/register"])
async def test_og_url_equals_canonical_and_ignores_query_string(client, path):
    r = await client.get(path + "?utm_source=newsletter&ref=x")
    canonical = re.search(r'<link rel="canonical" href="([^"]*)"', r.text).group(1)
    assert canonical == "https://avigronix.com" + path
    assert meta(r.text, "property", "og:url") == canonical


async def test_og_title_matches_page_title(client):
    r = await client.get("/about")
    title = " ".join(re.search(r"<title>(.*?)</title>", r.text, re.S).group(1).split())
    assert meta(r.text, "property", "og:title") == title
    assert meta(r.text, "name", "twitter:title") == title


async def test_og_image_stays_png(client):
    r = await client.get("/")
    assert meta(r.text, "property", "og:image").endswith("/static/images/og-banner.png")


# --------------------------------------------------------------------------
# Structured data (JSON-LD)
# --------------------------------------------------------------------------
@pytest.mark.parametrize("path", list(PAGES_WITH_OWN_DESCRIPTION) + ["/blog/fastapi-best-practices"])
async def test_json_ld_is_well_formed(client, path):
    blocks = json_ld_blocks((await client.get(path)).text)
    assert blocks, "every page carries at least the Organization schema"
    for block in blocks:
        assert block["@context"] == "https://schema.org"
        assert "@type" in block
    assert any(b["@type"] == "Organization" for b in blocks)


@pytest.mark.parametrize("path", [p for p in PAGES_WITH_OWN_DESCRIPTION if p != "/"])
async def test_inner_pages_have_breadcrumbs(client, path):
    crumbs = [b for b in json_ld_blocks((await client.get(path)).text) if b["@type"] == "BreadcrumbList"]
    assert len(crumbs) == 1
    items = crumbs[0]["itemListElement"]
    assert [i["position"] for i in items] == list(range(1, len(items) + 1))
    assert items[0]["name"] == "Home"
    assert items[-1]["item"] == "https://avigronix.com" + path


async def test_home_has_no_breadcrumb(client):
    assert not [b for b in json_ld_blocks((await client.get("/")).text) if b["@type"] == "BreadcrumbList"]


async def test_blog_post_breadcrumb_is_home_blog_post(client):
    crumbs = [b for b in json_ld_blocks((await client.get("/blog/fastapi-best-practices")).text)
              if b["@type"] == "BreadcrumbList"][0]
    names = [i["name"] for i in crumbs["itemListElement"]]
    assert names == ["Home", "Blog", "Building Scalable APIs with FastAPI: Best Practices"]


async def test_services_page_has_a_service_schema_per_service_card(client):
    r = await client.get("/services")
    lists = [b for b in json_ld_blocks(r.text) if b["@type"] == "ItemList"]
    assert len(lists) == 1
    items = lists[0]["itemListElement"]
    card_titles = re.findall(r'<h3 class="text-2xl font-bold text-slate-900 mb-4">(.*?)</h3>', r.text)
    assert len(items) == 12
    for i, entry in enumerate(items, start=1):
        assert entry["@type"] == "ListItem" and entry["position"] == i
        service = entry["item"]
        assert service["@type"] == "Service"
        assert service["provider"]["name"] == "AVIGRONIX TECHNOLOGIES"
        assert service["name"] and service["description"]
    # the schema must describe exactly the service cards shown on the page
    assert [html.unescape(t) for t in card_titles] == [e["item"]["name"] for e in items]


# --------------------------------------------------------------------------
# RSS
# --------------------------------------------------------------------------
async def test_rss_feed(client):
    from blog_content import list_posts

    r = await client.get("/blog/rss.xml")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/rss+xml")
    root = ET.fromstring(r.content)
    assert root.tag == "rss" and root.get("version") == "2.0"
    channel = root.find("channel")
    items = channel.findall("item")
    assert len(items) == len(list_posts())
    links = {i.findtext("link") for i in items}
    for post in list_posts():
        assert f"https://avigronix.com/blog/{post['slug']}" in links
    for item in items:
        assert item.findtext("title") and item.findtext("pubDate") and item.findtext("description")
    dates = [i.findtext("pubDate") for i in items]
    assert dates[0].endswith("GMT") or dates[0].endswith("+0000")


async def test_rss_linked_in_head(client):
    r = await client.get("/")
    assert 'type="application/rss+xml"' in r.text
    assert "https://avigronix.com/blog/rss.xml" in r.text


async def test_rss_route_does_not_shadow_blog_posts(client):
    assert (await client.get("/blog/fastapi-best-practices")).status_code == 200


# --------------------------------------------------------------------------
# Health check
# --------------------------------------------------------------------------
async def test_health_ok(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "database": "ok"}


async def test_health_reports_database_down_without_details(client, monkeypatch):
    import main

    class DownDB:
        async def command(self, *a, **kw):
            raise ConnectionError("SECRET-HOST:27017 refused")

    monkeypatch.setattr(main, "db", DownDB())
    r = await client.get("/health")
    assert r.status_code == 503
    assert r.json()["database"] == "unreachable"
    assert "SECRET-HOST" not in r.text


async def test_health_not_in_sitemap_and_disallowed_in_robots(client):
    assert "/health" not in (await client.get("/sitemap.xml")).text
    assert "Disallow: /health" in (await client.get("/robots.txt")).text


async def test_health_is_not_rate_limited(client):
    for _ in range(310):
        r = await client.get("/health")
    assert r.status_code == 200


# --------------------------------------------------------------------------
# Assets / templates
# --------------------------------------------------------------------------
async def test_logo_served_as_webp_with_png_fallback(client):
    r = await client.get("/")
    assert '<source srcset="http://testserver/static/images/logo.webp" type="image/webp">' in r.text
    assert "static/images/logo.png" in r.text
    webp = await client.get("/static/images/logo.webp")
    assert webp.status_code == 200 and webp.content[:4] == b"RIFF" and webp.content[8:12] == b"WEBP"


def test_phone_placeholders_share_one_format():
    source = (APP_DIR / "templates" / "register_business.html").read_text()
    phones = {
        name: re.search(rf'name="{name}".*?placeholder="([^"]*)"', source, re.S).group(1)
        for name in ("contactNumber", "whatsapp")
    }
    assert len(set(phones.values())) == 1, phones
    assert re.fullmatch(r"\+\d{10,15}", phones["contactNumber"])


# --------------------------------------------------------------------------
# Configuration loading order
# --------------------------------------------------------------------------
def test_dotenv_loaded_before_app_modules_are_imported():
    """routers.shop / rate_limit / database read settings at import time, so
    load_dotenv() must run first or values in .env are silently ignored."""
    tree = ast.parse((APP_DIR / "main.py").read_text())
    load_line = min(
        n.lineno for n in ast.walk(tree)
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "load_dotenv"
    )
    app_modules = {"routers", "routers.shop", "routers.pages", "rate_limit", "database", "render_utils"}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module in app_modules:
            assert node.lineno > load_line, f"{node.module} imported before load_dotenv()"
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in app_modules or node.lineno > load_line
