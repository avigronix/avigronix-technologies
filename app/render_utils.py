import json

from fastapi import Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import Response


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

    response = templates.TemplateResponse(template_name, context)

    if is_htmx:
        tmpl = templates.env.get_template(template_name)
        title_block = tmpl.blocks.get("title")
        if title_block:
            block_ctx = tmpl.new_context(context)
            title_text = " ".join("".join(title_block(block_ctx)).split())
            response.headers["HX-Trigger"] = json.dumps({"pageTitleUpdate": title_text})

    return response
