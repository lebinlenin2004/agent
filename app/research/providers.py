import logging
from dataclasses import dataclass
from typing import Protocol

import httpx

from app.core.config import Settings, get_settings

log = logging.getLogger(__name__)


@dataclass
class SearchHit:
    url: str
    title: str
    snippet: str


class SearchProvider(Protocol):
    def search(self, query: str, limit: int = 10) -> list[SearchHit]: ...


class SearchProviderError(Exception):
    pass


class HttpSearchProvider:
    """Generic JSON search API client: GET {SEARCH_API_URL}?q=...&num=... with a Bearer key.

    Accepts the common response shapes ({"results": [...]}, {"organic_results": [...]}, {"items": [...]}),
    each entry having url|link and title|name and snippet|description. Adapt `_parse` for your vendor.
    """

    def __init__(self, settings: Settings | None = None):
        self.s = settings or get_settings()
        if not self.s.search_api_url or not self.s.search_api_key:
            raise SearchProviderError("SEARCH_API_URL and SEARCH_API_KEY are required when SEARCH_PROVIDER=http")
        self.client = httpx.Client(timeout=self.s.http_timeout_seconds)

    def search(self, query: str, limit: int = 10) -> list[SearchHit]:
        last = None
        for attempt in range(self.s.http_max_retries + 1):
            try:
                r = self.client.get(self.s.search_api_url, params={"q": query, "num": limit},
                                    headers={"Authorization": f"Bearer {self.s.search_api_key.get_secret_value()}"})
                if r.status_code in (429, 500, 502, 503, 504):
                    last = f"HTTP {r.status_code}"
                    continue
                r.raise_for_status()
                return self._parse(r.json())[:limit]
            except (httpx.HTTPError, ValueError) as e:
                last = type(e).__name__  # never log the request (it carries the key)
        log.warning("Search API failed: %s", last)
        raise SearchProviderError("The search service is unavailable")

    @staticmethod
    def _parse(data: dict) -> list[SearchHit]:
        rows = data.get("results") or data.get("organic_results") or data.get("items") or []
        hits = []
        for r in rows:
            url = r.get("url") or r.get("link")
            if url:
                hits.append(SearchHit(url, str(r.get("title") or r.get("name") or "")[:300],
                                      str(r.get("snippet") or r.get("description") or "")[:500]))
        return hits
