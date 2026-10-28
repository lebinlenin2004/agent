import time
from collections import defaultdict, deque

from fastapi import Request

from app.core.config import get_settings
from app.core.exceptions import RateLimitError

# In-memory sliding window: fine for one process. Use a Redis-backed limiter
# (or your gateway) when running several API replicas.
_hits: dict[str, deque] = defaultdict(deque)


def auth_rate_limit(request: Request) -> None:
    limit = get_settings().auth_rate_limit_per_minute
    key = f"{request.url.path}:{request.client.host if request.client else 'unknown'}"
    now = time.monotonic()
    q = _hits[key]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= limit:
        raise RateLimitError("Too many attempts. Wait a minute and try again.")
    q.append(now)


def reset_rate_limits() -> None:
    _hits.clear()
