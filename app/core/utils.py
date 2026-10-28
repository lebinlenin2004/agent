import datetime as dt
import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

_TRACKING = re.compile(r"^(utm_|fbclid|gclid|ref$|source$|gh_src|lever-)", re.I)


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def ensure_aware(value: dt.datetime | None) -> dt.datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=dt.timezone.utc)
    return value


def normalize_url(url: str) -> str:
    """Canonical form used for duplicate detection (lowercase host, no www/fragment/tracking params)."""
    p = urlparse(url.strip())
    host = (p.hostname or "").lower().removeprefix("www.")
    port = f":{p.port}" if p.port and p.port not in (80, 443) else ""
    query = urlencode(sorted((k, v) for k, v in parse_qsl(p.query) if not _TRACKING.match(k)))
    path = re.sub(r"/+$", "", p.path) or ""
    return urlunparse(("https", host + port, path, "", query, ""))


def domain_of(url: str | None) -> str | None:
    if not url:
        return None
    host = (urlparse(url if "://" in url else "https://" + url).hostname or "").lower()
    return host.removeprefix("www.") or None


def same_site(host_or_url: str, domain: str | None) -> bool:
    d = domain_of(host_or_url)
    return bool(d and domain and (d == domain or d.endswith("." + domain)))


def normalize_text(s: str | None) -> str:
    return re.sub(r"[^a-z0-9+#.]+", " ", (s or "").lower()).strip()


def to_index(items: list[str] | None) -> str:
    """'|react|node.js|' - lets us filter JSON lists with portable LIKE queries."""
    return "|" + "|".join(sorted({normalize_text(i) for i in items or [] if i})) + "|"


def fingerprint(company_id: int, title: str, location: str | None) -> str:
    raw = f"{company_id}|{normalize_text(title)}|{normalize_text(location)}"
    return hashlib.sha256(raw.encode()).hexdigest()


def fit_url(url: str, limit: int = 1000) -> str:
    """Keep canonical URLs within the indexed column size; long ones get a hash suffix so they stay unique."""
    if len(url) <= limit:
        return url
    return url[: limit - 17] + "#" + hashlib.sha256(url.encode()).hexdigest()[:16]
