from fastapi import APIRouter, HTTPException, Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse

from render_utils import render_page
from blog_content import get_post, list_posts

router = APIRouter()
templates = Jinja2Templates(directory="templates")

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
