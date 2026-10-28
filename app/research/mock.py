"""Deterministic mock web so the whole pipeline runs without credentials or network access.

Hosts containing 'fail' time out (-> Search Incomplete); hosts containing 'nojobs' have an empty careers
page (-> No Matching Vacancy Found). Every 4th host (by hash) has one closed posting.
"""
import hashlib
import json
from urllib.parse import urlparse

from app.research.fetcher import FetchResult
from app.research.providers import SearchHit

JOBS = {
    "frontend-developer": ("Frontend Developer", ["React", "TypeScript", "CSS"], "1-3 years", (1, 3)),
    "backend-engineer": ("Backend Engineer", ["Python", "FastAPI", "PostgreSQL"], "3-5 years", (3, 5)),
    "data-analyst": ("Data Analyst", ["Python", "SQL", "Pandas"], "0-2 years", (0, 2)),
    "devops-engineer": ("DevOps Engineer", ["Docker", "Kubernetes", "AWS"], "3-6 years", (3, 6)),
}
LOCATIONS = ["Kochi", "Bengaluru", "Chennai", "Remote"]


def _h(host: str) -> int:
    return int(hashlib.sha256(host.encode()).hexdigest(), 16)


def _slugs_for(host: str) -> list[str]:
    if "nojobs" in host:
        return []
    keys = list(JOBS)
    skip = _h(host) % 4
    return [k for i, k in enumerate(keys) if i != skip or _h(host) % 2 == 0][:4]


def _closed_slug(host: str) -> str | None:
    return "devops-engineer" if _h(host) % 4 == 0 and "devops-engineer" in _slugs_for(host) else None


class MockFetcher:
    def fetch(self, url: str) -> FetchResult:
        p = urlparse(url)
        host = (p.hostname or "").removeprefix("www.")
        path = p.path.rstrip("/")
        if "fail" in host:
            return FetchResult(url, url, error="Timed out")
        if path == "/careers":
            links = "".join(f'<li><a href="/careers/jobs/{s}">{JOBS[s][0]}</a></li>' for s in _slugs_for(host))
            html = f"<html><head><title>Careers at {host}</title></head><body><h1>Open roles</h1><ul>{links}</ul></body></html>"
            return FetchResult(url, url, 200, html, "text/html")
        if path.startswith("/careers/jobs/"):
            slug = path.rsplit("/", 1)[1]
            if slug not in _slugs_for(host):
                return FetchResult(url, url, 404, "Not found", "text/html")
            title, skills, exp, _ = JOBS[slug]
            loc = LOCATIONS[_h(host + slug) % len(LOCATIONS)]
            closed = slug == _closed_slug(host)
            ld = {"@context": "https://schema.org", "@type": "JobPosting", "title": title,
                  "description": f"<p>We need a {title} skilled in {', '.join(skills)}. {exp} of experience.</p>",
                  "datePosted": "2026-10-01", "validThrough": "2027-03-31T00:00:00",
                  "jobLocation": {"address": {"addressLocality": loc}},
                  "skills": skills, "experienceRequirements": exp, "url": f"https://{host}/careers/jobs/{slug}"}
            body = ("<p>This position is no longer accepting applications.</p>" if closed else
                    f'<a class="btn" href="/careers/jobs/{slug}/apply">Apply now</a>')
            html = (f'<html><head><title>{title} - {host}</title>'
                    f'<script type="application/ld+json">{json.dumps(ld)}</script></head><body><h1>{title}</h1>'
                    f"<p>Location: {loc}</p><p>Required skills: {', '.join(skills)}</p>"
                    f"<p>Experience: {exp}</p>{body}</body></html>")
            return FetchResult(url, url, 200, html, "text/html")
        return FetchResult(url, url, 404, "Not found", "text/html")


class MockSearchProvider:
    def search(self, query: str, limit: int = 10) -> list[SearchHit]:
        host = next((w[5:] for w in query.split() if w.startswith("site:")), None)
        if not host or "fail" in host:
            return []
        return [SearchHit(f"https://{host}/careers/jobs/{s}", JOBS[s][0], "") for s in _slugs_for(host)][:limit]
