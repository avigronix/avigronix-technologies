"""Blog post content — a small hand-maintained store since there's no CMS.
Each entry is real, original content written by AVIGRONIX TECHNOLOGIES,
not per-client testimonials or claims about third parties.
"""

BLOG_POSTS = {
    "fastapi-best-practices": {
        "title": "Building Scalable APIs with FastAPI: Best Practices",
        "date": "June 12, 2026",
        "read_time": "6 min read",
        "icon": "fas fa-bolt",
        "color": "blue",
        "tags": ["FastAPI", "Python"],
        "excerpt": "Practical architecture patterns, authentication, and deployment strategies we use to build high-performance, scalable APIs with FastAPI.",
        "sections": [
            {
                "heading": None,
                "body": "FastAPI has become our default choice for backend APIs — it's fast, has "
                        "excellent async support, and its automatic OpenAPI docs save a lot of "
                        "back-and-forth with frontend teams. Here are the practices that matter "
                        "most once an API needs to handle real production traffic, not just a demo.",
            },
            {
                "heading": "Structure by domain, not by layer",
                "body": "A common early mistake is organizing code into flat models/, routes/, and "
                        "schemas/ folders. It works fine for a handful of endpoints, but stops scaling "
                        "once a project has ten resources with their own business logic. Grouping code "
                        "by domain (a users/ package with its own routes, schemas, and service "
                        "functions) keeps related logic together and makes it obvious where to add the "
                        "next feature.",
            },
            {
                "heading": "Keep Pydantic schemas separate from your database models",
                "body": "Whether you're on MongoDB or PostgreSQL, your API's request/response shape "
                        "and your storage shape have different concerns — an API schema should never "
                        "leak internal fields (hashed passwords, internal flags) just because they "
                        "exist on the stored document. We keep dedicated Pydantic schemas for what the "
                        "client sends and receives, separate from whatever represents the stored "
                        "record.",
            },
            {
                "heading": "Authentication: JWT with explicit scopes, not just a valid/invalid check",
                "body": "A bearer token proves who the caller is; it shouldn't automatically mean they "
                        "can do everything. We implement JWT authentication with role-based access "
                        "control (RBAC) at the dependency level, so an endpoint can require "
                        "\"admin\" or \"editor\" instead of just \"logged in\" — enforced consistently "
                        "via FastAPI's Depends() rather than scattered checks inside route bodies.",
            },
            {
                "heading": "Use async correctly — or not at all",
                "body": "async def only helps if everything inside it is actually non-blocking. A "
                        "synchronous database driver or a blocking HTTP call inside an async route can "
                        "quietly stall the entire event loop under load. We're deliberate about using "
                        "async-native drivers (like Motor for MongoDB) and running any unavoidable "
                        "blocking work in a thread pool rather than letting it block the loop.",
            },
            {
                "heading": "Deployment: run behind a process manager, not `uvicorn` alone",
                "body": "For production we run FastAPI behind Gunicorn with Uvicorn workers (or "
                        "Uvicorn's own multi-worker mode), fronted by Nginx for TLS termination and "
                        "static file serving. Health checks, structured logging, and graceful shutdown "
                        "handling matter as much as the application code once it's actually serving "
                        "traffic.",
            },
        ],
    },
    "cloud-migration-strategy": {
        "title": "Cloud Migration Strategy for Enterprises: A Complete Guide",
        "date": "May 28, 2026",
        "read_time": "8 min read",
        "icon": "fas fa-cloud",
        "color": "green",
        "tags": ["Cloud", "AWS", "GCP"],
        "excerpt": "A practical framework for planning and executing cloud migrations without the downtime and cost surprises that derail most projects.",
        "sections": [
            {
                "heading": None,
                "body": "Most cloud migrations don't fail because of the technology — they fail "
                        "because of planning gaps. Here's the approach we use when moving a business "
                        "from on-prem or a VPS setup onto AWS or GCP.",
            },
            {
                "heading": "Start with an honest inventory, not an assumption",
                "body": "Before touching infrastructure, we map what's actually running: services, "
                        "their dependencies, data volumes, and which ones are genuinely stateless "
                        "versus quietly relying on local disk or in-memory state. This step alone "
                        "usually surfaces the riskiest parts of a migration early, when they're cheap "
                        "to plan around.",
            },
            {
                "heading": "Pick a migration pattern per service, not one pattern for everything",
                "body": "Lift-and-shift (rehosting) is fastest but doesn't reduce operational "
                        "overhead. Re-platforming — for example, moving a self-managed database to a "
                        "managed one — buys reliability without a full rewrite. Full re-architecture "
                        "makes sense only for the services where the current design is actually the "
                        "bottleneck. Applying the same pattern to every service is usually a sign the "
                        "migration wasn't planned service-by-service.",
            },
            {
                "heading": "Plan the network and security boundary first",
                "body": "VPC layout, subnets, security groups/firewall rules, and how the new "
                        "environment talks to anything staying on-prem — these decisions are expensive "
                        "to change after the fact. We treat this as its own design phase, including "
                        "Nginx reverse-proxy and SSL configuration for anything public-facing.",
            },
            {
                "heading": "Migrate data with a cutover plan, not a one-shot copy",
                "body": "For anything with live write traffic, a single export/import step means "
                        "downtime and stale data. We plan a sync period (replication or scheduled "
                        "delta syncs) with a defined, tested cutover window, and always keep a rollback "
                        "path until the new environment has proven itself under real traffic.",
            },
            {
                "heading": "Observability from day one on the new environment",
                "body": "A migration isn't done when traffic moves — it's done when you can see, as "
                        "clearly as before, whether the system is healthy. We set up monitoring and "
                        "alerting (tools like Grafana for dashboards) before cutover, not after "
                        "something breaks.",
            },
        ],
    },
    "cybersecurity-best-practices": {
        "title": "Securing Web Applications: JWT, RBAC & Beyond",
        "date": "May 10, 2026",
        "read_time": "7 min read",
        "icon": "fas fa-shield-alt",
        "color": "orange",
        "tags": ["Security", "Backend"],
        "excerpt": "The security practices we build into every backend by default — authentication, access control, and safe handling of user-submitted data.",
        "sections": [
            {
                "heading": None,
                "body": "Security isn't a feature you bolt on before launch — most of it is decisions "
                        "made in how the backend is structured from the start. These are the defaults "
                        "we apply to every project.",
            },
            {
                "heading": "Authentication vs. authorization are two different problems",
                "body": "A valid JWT tells you who the request claims to be. It doesn't tell you what "
                        "they're allowed to do. We implement role-based access control (RBAC) as its "
                        "own layer — checked via dependency injection on every protected route — so "
                        "permission logic lives in one place instead of being re-implemented per "
                        "endpoint.",
            },
            {
                "heading": "Never trust client-supplied filenames or paths",
                "body": "File uploads are a common, underestimated attack surface. A client-controlled "
                        "filename used directly on disk can enable path traversal or silently overwrite "
                        "another user's file. We generate server-side filenames (UUID-based) and "
                        "validate file extensions against an explicit allow-list, never inferring "
                        "trust from what the client sent.",
            },
            {
                "heading": "Rate-limit anything that sends email, writes to a database, or costs money",
                "body": "Public-facing forms — contact forms, registration endpoints — are the first "
                        "thing bots target. Without limits, a single script can exhaust a mail "
                        "provider's sending quota or fill a database with junk. We apply per-IP rate "
                        "limits on state-changing endpoints as a default, not an afterthought.",
            },
            {
                "heading": "Secrets belong in environment variables, never in source",
                "body": "SMTP passwords, API keys, and database URLs should never be committed to a "
                        "repository, even privately. We load configuration from environment variables "
                        "(`.env` files kept out of version control) so credentials can rotate without "
                        "touching code.",
            },
            {
                "heading": "Set real security headers, not just HTTPS",
                "body": "TLS alone doesn't stop clickjacking, MIME-sniffing attacks, or a permissive "
                        "Content-Security-Policy from undermining everything else. We set "
                        "`X-Frame-Options`, `X-Content-Type-Options`, `Strict-Transport-Security`, and "
                        "a scoped CSP as standard middleware on every deployment.",
            },
        ],
    },
    "database-optimization": {
        "title": "Database Optimization for High-Traffic Applications",
        "date": "April 22, 2026",
        "read_time": "7 min read",
        "icon": "fas fa-database",
        "color": "purple",
        "tags": ["Database", "MongoDB", "PostgreSQL"],
        "excerpt": "How we approach query performance and schema design across MongoDB and PostgreSQL as an application's traffic grows.",
        "sections": [
            {
                "heading": None,
                "body": "Database performance problems rarely show up in development — they show up "
                        "once real traffic and real data volume hit. Here's what we check first.",
            },
            {
                "heading": "Index for your actual query patterns, not your schema",
                "body": "It's tempting to index every field that looks important. In practice, an "
                        "index only helps if it matches how the data is actually queried — filter "
                        "fields, sort fields, and lookup keys used together. On MongoDB we build "
                        "compound indexes around real query shapes; on PostgreSQL we check `EXPLAIN "
                        "ANALYZE` output before assuming an index is being used at all.",
            },
            {
                "heading": "Don't let pagination scan everything before the limit",
                "body": "Naive `OFFSET`-based pagination gets progressively slower as the offset "
                        "grows, because the database still has to scan and discard every earlier row. "
                        "For high-traffic listing endpoints, we use cursor-based pagination (a "
                        "reference to the last-seen document/row) instead, which stays fast regardless "
                        "of how deep a user pages.",
            },
            {
                "heading": "Cache what's expensive and doesn't change every request",
                "body": "Not every read needs to hit the primary database. We use Redis for data "
                        "that's read far more often than it changes — computed aggregates, "
                        "session/lookup data — with a deliberate invalidation strategy rather than a "
                        "blanket time-to-live that risks serving stale data where it matters.",
            },
            {
                "heading": "Watch for the N+1 query pattern",
                "body": "A list endpoint that triggers one query per item to fetch related data is the "
                        "single most common performance bug we see. On MongoDB this means designing "
                        "documents to embed what's read together; on relational databases it means "
                        "explicit joins or eager loading instead of lazy per-row lookups.",
            },
            {
                "heading": "Connection pooling matters more than query tuning, early on",
                "body": "Before optimizing individual queries, we make sure the application isn't "
                        "opening a new database connection per request — a surprisingly common cause "
                        "of latency under concurrent load. Proper pooling (and async-native drivers "
                        "that don't block the event loop) often fixes more than any single query "
                        "rewrite.",
            },
        ],
    },
}


def _iso_date(human_date: str) -> str:
    from datetime import datetime

    return datetime.strptime(human_date, "%B %d, %Y").strftime("%Y-%m-%d")


def get_post(slug: str) -> dict | None:
    post = BLOG_POSTS.get(slug)
    if post is None:
        return None
    return {**post, "iso_date": _iso_date(post["date"])}


def list_posts() -> list[dict]:
    return [{"slug": slug, **post, "iso_date": _iso_date(post["date"])} for slug, post in BLOG_POSTS.items()]
