import os

from slowapi import Limiter
from slowapi.util import get_remote_address

# Generous global default (protects against extreme flooding without blocking
# real multi-tab visitors or legitimate search-engine crawlers); specific
# state-changing endpoints (contact form, shop registration) apply their own
# much stricter limits via @limiter.limit(...) on the route itself.
#
# Note: in-memory storage, so limits are per-process. If this is ever run with
# multiple uvicorn workers or behind a load balancer, each process/instance
# tracks its own counters (a distributed attack could multiply the effective
# limit by the number of processes) — fine for a single-process deployment,
# but worth moving to a Redis storage backend if that changes.
#
# RATE_LIMIT_STORAGE_URI (optional) switches to a shared backend, e.g.
# "redis://host:6379" (needs `pip install redis`) — see README.
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["300/minute"],
    storage_uri=os.environ.get("RATE_LIMIT_STORAGE_URI", "memory://"),
)
