"""Decides vacancy status from evidence. The LLM never decides this; page text can't talk its way to 'Active'."""
import datetime as dt
from dataclasses import dataclass

from app.ai.schemas import ExtractedJob
from app.core.utils import domain_of, normalize_text, same_site, utcnow
from app.models import VacancyStatus
from app.research.fetcher import FetchResult
from app.research.parsing import ParsedPage


@dataclass
class Verification:
    status: VacancyStatus
    evidence: list[dict]


def _ev(signal: str, ok: bool, detail: str) -> dict:
    return {"signal": signal, "ok": ok, "detail": detail}


def _valid_through(node: dict) -> dt.date | None:
    v = node.get("validThrough")
    try:
        return dt.date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def verify_posting(*, fetch: FetchResult, page: ParsedPage | None, job: ExtractedJob, official_domains: set[str],
                   previously_known: bool = False) -> Verification:
    ev: list[dict] = []
    if fetch.status_code in (404, 410):
        ev.append(_ev("http_status", True, f"Posting URL returned HTTP {fetch.status_code}"))
        if previously_known:
            return Verification(VacancyStatus.CLOSED_VERIFIED, ev)
        return Verification(VacancyStatus.FOUND_UNVERIFIED, ev)
    if not fetch.ok or page is None:
        ev.append(_ev("fetch", False, fetch.error or f"HTTP {fetch.status_code}"))
        return Verification(VacancyStatus.FOUND_UNVERIFIED, ev)

    ev.append(_ev("http_status", True, "Posting page loaded (HTTP 200)"))
    ld = page.json_ld_jobs[0] if page.json_ld_jobs else None
    if page.closed_markers:
        ev.append(_ev("closed_notice", True, "Page states the position is closed or no longer accepting applications"))
        return Verification(VacancyStatus.CLOSED_VERIFIED, ev)
    through = _valid_through(ld) if ld else None
    if through and through < utcnow().date():
        ev.append(_ev("valid_through", True, f"Posting expired on {through.isoformat()}"))
        return Verification(VacancyStatus.CLOSED_VERIFIED, ev)

    page_text = normalize_text(f"{page.h1} {page.title} {page.text}")
    title_ok = normalize_text(job.title) in page_text
    ev.append(_ev("title_on_page", title_ok, "Job title appears on the page" if title_ok else "Job title not found on the page"))
    official = any(same_site(fetch.final_url, d) for d in official_domains)
    ev.append(_ev("official_source", official, f"Hosted on {domain_of(fetch.final_url)}" + (" (company domain)" if official else " (not a company domain)")))
    ev.append(_ev("apply_option", page.has_apply_signal, "Page offers an apply link/button" if page.has_apply_signal else "No apply link or button found"))
    ld_ok = bool(ld) and (through is None or through >= utcnow().date())
    ev.append(_ev("structured_data", ld_ok, "Valid JobPosting data (not expired)" if ld_ok else "No valid JobPosting data"))

    supporting = sum(e["ok"] for e in ev if e["signal"] in ("official_source", "apply_option", "structured_data"))
    if title_ok and supporting >= 2:
        return Verification(VacancyStatus.ACTIVE_VERIFIED, ev)
    ev.append(_ev("verdict", False, "Not enough independent signals to confirm the vacancy is open"))
    return Verification(VacancyStatus.FOUND_UNVERIFIED, ev)
