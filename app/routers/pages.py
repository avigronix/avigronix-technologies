from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from datetime import datetime, timezone
from email.utils import format_datetime
from xml.sax.saxutils import escape

from render_utils import render_page, get_site_url, make_templates
from blog_content import get_post, list_posts

router = APIRouter()
templates = make_templates()

@router.get("/about", response_class=HTMLResponse)
async def about(request: Request):
    return render_page(request, templates, "about.html")

@router.get("/services", response_class=HTMLResponse)
async def services(request: Request):
    return render_page(request, templates, "services.html")

@router.get("/team", response_class=HTMLResponse)
async def team(request: Request):
    return render_page(request, templates, "team.html")

@router.get("/contact", response_class=HTMLResponse)
async def contact(request: Request):
    return render_page(request, templates, "contact.html")

@router.get("/projects", response_class=HTMLResponse)
async def projects(request: Request):
    return render_page(request, templates, "projects.html")

@router.get("/blog", response_class=HTMLResponse)
async def blog(request: Request):
    return render_page(request, templates, "blog.html", {"posts": list_posts()})

# Must be registered before /blog/{slug}, which would otherwise match "rss.xml".
@router.get("/blog/rss.xml", include_in_schema=False)
async def blog_rss():
    site = get_site_url()
    posts = sorted(list_posts(), key=lambda p: p["iso_date"], reverse=True)

    def rfc822(iso_date: str) -> str:
        return format_datetime(datetime.strptime(iso_date, "%Y-%m-%d").replace(tzinfo=timezone.utc))

    items = []
    for post in posts:
        url = f"{site}/blog/{post['slug']}"
        categories = "".join(f"<category>{escape(tag)}</category>" for tag in post.get("tags", []))
        items.append(
            "<item>"
            f"<title>{escape(post['title'])}</title>"
            f"<link>{escape(url)}</link>"
            f"<guid isPermaLink=\"true\">{escape(url)}</guid>"
            f"<pubDate>{rfc822(post['iso_date'])}</pubDate>"
            f"<description>{escape(post['excerpt'])}</description>"
            f"{categories}"
            "</item>"
        )

    last_build = rfc822(posts[0]["iso_date"]) if posts else format_datetime(datetime.now(timezone.utc))
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">'
        "<channel>"
        "<title>AVIGRONIX TECHNOLOGIES Blog</title>"
        f"<link>{escape(site)}/blog</link>"
        f'<atom:link href="{escape(site)}/blog/rss.xml" rel="self" type="application/rss+xml"/>'
        "<description>Engineering notes from AVIGRONIX TECHNOLOGIES — practical guidance on FastAPI, "
        "cloud migration, backend security, and database performance.</description>"
        "<language>en</language>"
        f"<lastBuildDate>{last_build}</lastBuildDate>"
        + "".join(items)
        + "</channel></rss>\n"
    )
    return Response(content=xml, media_type="application/rss+xml")

@router.get("/blog/{slug}", response_class=HTMLResponse)
async def blog_detail(request: Request, slug: str):
    post = get_post(slug)
    if not post:
        raise HTTPException(status_code=404, detail="Blog post not found")
    all_posts = list_posts()
    related = [p for p in all_posts if p["slug"] != slug][:3]
    return render_page(request, templates, "blog_detail.html", {"slug": slug, "post": post, "related": related})

@router.get("/faq", response_class=HTMLResponse)
async def faq(request: Request):
    return render_page(request, templates, "faq.html")

@router.get("/privacy-policy", response_class=HTMLResponse)
async def privacy_policy(request: Request):
    return render_page(request, templates, "privacy_policy.html")

@router.get("/terms-and-conditions", response_class=HTMLResponse)
async def terms_conditions(request: Request):
    return render_page(request, templates, "terms.html")
