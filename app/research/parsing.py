import json
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

from bs4 import BeautifulSoup

CLOSED_PATTERNS = [re.compile(p, re.I) for p in [
    r"no longer (accepting|available|open)", r"position (has been|is) (filled|closed)",
    r"(job|vacancy|posting|role|position) (is |has )?(closed|expired|filled)", r"this job has expired",
    r"applications? (are |is )?closed", r"we('| a)re not hiring for this",
]]
APPLY_RE = re.compile(r"\bapply\b", re.I)
JOB_LINK_RE = re.compile(r"(job|career|position|opening|vacanc|role|apply)", re.I)


@dataclass
class ParsedPage:
    title: str = ""
    h1: str = ""
    text: str = ""
    json_ld_jobs: list[dict] = field(default_factory=list)
    links: list[tuple[str, str]] = field(default_factory=list)  # (absolute href, anchor text)
    has_apply_signal: bool = False
    closed_markers: list[str] = field(default_factory=list)


def _json_ld_jobs(soup: BeautifulSoup) -> list[dict]:
    jobs = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (ValueError, TypeError):
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            node = stack.pop()
            if not isinstance(node, dict):
                continue
            stack.extend(node.get("@graph", []) if isinstance(node.get("@graph"), list) else [])
            t = node.get("@type")
            if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
                jobs.append(node)
    return jobs


def parse_page(html: str, base_url: str, max_text: int = 20000) -> ParsedPage:
    soup = BeautifulSoup(html or "", "html.parser")
    jobs = _json_ld_jobs(soup)  # read before we strip <script>
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        links.append((urljoin(base_url, href), " ".join(a.get_text(" ").split())[:200]))
    apply = any(APPLY_RE.search(a.get_text(" ")) or "apply" in a.get("href", "").lower() for a in soup.find_all("a")) \
        or any(APPLY_RE.search(b.get_text(" ")) for b in soup.find_all("button"))
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    h1 = soup.h1.get_text(" ", strip=True) if soup.h1 else ""
    for t in soup(["script", "style", "noscript", "template", "svg", "iframe"]):
        t.decompose()
    text = " ".join(soup.get_text(" ").split())[:max_text]
    closed = [p.pattern for p in CLOSED_PATTERNS if p.search(text)]
    return ParsedPage(title, h1, text, jobs, links, apply, closed)


def job_links(page: ParsedPage, domain_ok) -> list[str]:
    """Links on a listing page that look like individual postings, filtered by an allow-function."""
    out, seen = [], set()
    for href, text in page.links:
        if not domain_ok(href) or href in seen:
            continue
        if JOB_LINK_RE.search(href) or JOB_LINK_RE.search(text):
            seen.add(href)
            out.append(href)
    return out
