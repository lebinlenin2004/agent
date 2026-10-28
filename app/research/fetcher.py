"""Polite, safe HTTP fetching: robots.txt, per-domain delay, timeouts, retries, SSRF guard, size cap."""
import hashlib
import ipaddress
import logging
import socket
import time
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

from app.core.config import Settings, get_settings
from app.core.utils import utcnow

log = logging.getLogger(__name__)
MAX_REDIRECTS = 3


@dataclass
class FetchResult:
    url: str
    final_url: str
    status_code: int | None = None
    text: str = ""
    content_type: str = ""
    error: str | None = None  # short, user-safe reason
    blocked_by_robots: bool = False

    @property
    def ok(self) -> bool:
        return self.status_code == 200 and not self.error

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8", "ignore")).hexdigest()


class Fetcher(Protocol):
    def fetch(self, url: str) -> FetchResult: ...


def is_safe_url(url: str, allow_private: bool = False) -> bool:
    """Reject non-http(s) schemes and hosts resolving to private/loopback/link-local addresses (SSRF)."""
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        return False
    if allow_private:
        return True
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return False
    return True  # note: DNS rebinding between this check and the request is not fully excluded


@dataclass
class _Resp:
    status_code: int
    headers: "httpx.Headers"
    text: str
    final_url: str


class PoliteFetcher:
    def __init__(self, settings: Settings | None = None, client: httpx.Client | None = None):
        self.s = settings or get_settings()
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(self.s.http_timeout_seconds), follow_redirects=False,
            headers={"User-Agent": self.s.user_agent, "Accept": "text/html,application/xhtml+xml"})
        self._robots: dict[str, RobotFileParser | None] = {}
        self._last_hit: dict[str, float] = {}

    # -- robots ---------------------------------------------------------
    def _robots_for(self, url: str) -> RobotFileParser | None:
        """None means 'could not determine, treat as disallowed' (robots 5xx / unreachable)."""
        p = urlparse(url)
        origin = f"{p.scheme}://{p.netloc}"
        if origin in self._robots:
            return self._robots[origin]
        rp = RobotFileParser()
        try:
            r = self._get(origin + "/robots.txt")
            if r.status_code == 200:
                rp.parse(r.text.splitlines())
            elif 400 <= r.status_code < 500:
                rp.parse([])  # no robots file: everything allowed
            else:
                rp = None
        except Exception:
            rp = None
        self._robots[origin] = rp
        return rp

    def _throttle(self, host: str):
        wait = self.s.per_domain_delay_seconds - (time.monotonic() - self._last_hit.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        self._last_hit[host] = time.monotonic()

    def _get(self, url: str, _hops: int = 0) -> "_Resp":
        if not is_safe_url(url, self.s.allow_private_network_fetch):
            raise PermissionError("blocked_url")
        self._throttle(urlparse(url).netloc)
        with self.client.stream("GET", url) as resp:
            if resp.is_redirect and resp.headers.get("location"):
                if _hops >= MAX_REDIRECTS:
                    raise PermissionError("too_many_redirects")
                return self._get(urljoin(url, resp.headers["location"]), _hops + 1)
            body = bytearray()
            for chunk in resp.iter_bytes():
                body += chunk
                if len(body) > self.s.max_response_bytes:
                    break
            text = bytes(body[: self.s.max_response_bytes]).decode(resp.encoding or "utf-8", errors="replace")
            return _Resp(resp.status_code, resp.headers, text, url)

    # -- public ---------------------------------------------------------
    def fetch(self, url: str) -> FetchResult:
        if not is_safe_url(url, self.s.allow_private_network_fetch):
            return FetchResult(url, url, error="URL not allowed")
        rp = self._robots_for(url)
        if rp is None or not rp.can_fetch(self.s.user_agent.split("/")[0], url):
            return FetchResult(url, url, error="Blocked by robots.txt", blocked_by_robots=True)
        last_err = "Request failed"
        for attempt in range(self.s.http_max_retries + 1):
            try:
                r = self._get(url)
                if r.status_code in (429, 500, 502, 503, 504) and attempt < self.s.http_max_retries:
                    try:
                        delay = min(float(r.headers.get("retry-after", "")), 10)
                    except ValueError:
                        delay = 2 ** attempt
                    time.sleep(delay)
                    last_err = f"HTTP {r.status_code}"
                    continue
                ctype = r.headers.get("content-type", "")
                if r.status_code == 200 and "html" not in ctype and "json" not in ctype and "text" not in ctype:
                    return FetchResult(url, r.final_url, r.status_code, error="Unsupported content type", content_type=ctype)
                if r.status_code >= 400:
                    return FetchResult(url, r.final_url, r.status_code, "", ctype, error=f"HTTP {r.status_code}")
                return FetchResult(url, r.final_url, r.status_code, r.text, ctype)
            except PermissionError as e:
                return FetchResult(url, url, error="URL not allowed" if "blocked" in str(e) else "Too many redirects")
            except httpx.TimeoutException:
                last_err = "Timed out"
            except httpx.HTTPError:
                last_err = "Connection error"
            time.sleep(min(2 ** attempt, 5))
        return FetchResult(url, url, error=last_err)


class PlaywrightFetcher:
    """Optional fallback for JavaScript-rendered career pages. Wraps another fetcher so robots, SSRF and
    throttling still apply: it only re-renders pages that the static fetch already got permission for."""

    def __init__(self, inner: PoliteFetcher):
        self.inner = inner

    def fetch(self, url: str) -> FetchResult:
        res = self.inner.fetch(url)
        if not res.ok or len(res.text) > 3000 and "enable javascript" not in res.text.lower():
            return res
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return res
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                page = browser.new_page(user_agent=self.inner.s.user_agent)
                page.goto(url, timeout=int(self.inner.s.http_timeout_seconds * 1000), wait_until="networkidle")
                html = page.content()[: self.inner.s.max_response_bytes]
                browser.close()
            return FetchResult(url, url, 200, html, "text/html")
        except Exception:
            log.warning("Playwright render failed for %s", urlparse(url).netloc)
            return res
