"""Per-company research: discover candidate pages -> fetch -> extract -> match -> verify -> store."""
import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.ai.extractor import extract_job
from app.ai.llm_client import LLMClient, get_llm_client
from app.ai.matcher import assess_match
from app.ai.sanitize import detect_injection
from app.ai.schemas import ExtractedJob
from app.core.config import Settings, get_settings
from app.core.utils import domain_of, normalize_url, same_site, utcnow
from app.models import Company, SourceType, Vacancy, VacancyStatus
from app.research.fetcher import Fetcher, FetchResult, PlaywrightFetcher, PoliteFetcher
from app.research.mock import MockFetcher, MockSearchProvider
from app.research.parsing import ParsedPage, job_links, parse_page
from app.research.providers import HttpSearchProvider, SearchProvider, SearchProviderError
from app.research.verification import verify_posting
from app.schemas.search import Criteria
from app.services import vacancy_service

log = logging.getLogger(__name__)


@dataclass
class Pipeline:
    fetcher: Fetcher
    provider: SearchProvider | None
    llm: LLMClient | None
    settings: Settings


def build_pipeline(settings: Settings | None = None) -> Pipeline:
    s = settings or get_settings()
    if s.search_provider == "mock":
        return Pipeline(MockFetcher(), MockSearchProvider(), get_llm_client(s), s)
    fetcher: Fetcher = PoliteFetcher(s)
    if s.use_playwright:
        fetcher = PlaywrightFetcher(fetcher)
    return Pipeline(fetcher, HttpSearchProvider(s), get_llm_client(s), s)


@dataclass
class Candidate:
    url: str
    source_type: SourceType
    official: bool


@dataclass
class CompanyOutcome:
    vacancies: list[tuple[Vacancy, list[dict]]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    sources_read: int = 0
    skipped_low_score: int = 0


def official_domains(company: Company) -> set[str]:
    return {d for d in (domain_of(company.website), domain_of(company.careers_url)) if d}


def _queries(company: Company, c: Criteria, domain: str | None) -> list[str]:
    roles = c.target_roles[:3] or c.technology_stacks[:2]
    loc = c.locations[0] if c.locations else ""
    scope = f"site:{domain}" if domain else company.name
    return [f"{scope} {r} {loc} jobs".replace("  ", " ") for r in roles]


def discover(company: Company, c: Criteria, p: Pipeline, cache: dict[str, FetchResult], out: CompanyOutcome) -> list[Candidate]:
    official = official_domains(company)
    portals = p.settings.portal_domains if "job_portals" in c.sources else set()
    cands: dict[str, Candidate] = {}

    def allowed(url: str) -> bool:
        return any(same_site(url, d) for d in official | portals)

    def add(url: str, st: SourceType):
        key = normalize_url(url)
        if key not in cands and allowed(url):
            cands[key] = Candidate(url, st, any(same_site(url, d) for d in official))

    if "company_sites" in c.sources:
        if company.careers_url:
            res = p.fetcher.fetch(company.careers_url)
            cache[company.careers_url] = res
            if res.ok:
                out.sources_read += 1
                page = parse_page(res.text, res.final_url)
                if page.json_ld_jobs:
                    add(company.careers_url, SourceType.COMPANY_CAREERS)
                for href in job_links(page, allowed):
                    add(href, SourceType.COMPANY_CAREERS)
            elif res.status_code in (404, 410):
                out.problems.append("Careers page not found (HTTP %s)" % res.status_code)
            else:
                out.problems.append(f"Careers page unavailable: {res.error or 'HTTP ' + str(res.status_code)}")
        else:
            out.problems.append("No careers page on record for this company")

    if p.provider and ("search_api" in c.sources or "job_portals" in c.sources):
        domain = next(iter(sorted(official)), None)
        for q in _queries(company, c, domain):
            try:
                hits = p.provider.search(q, limit=10)
                out.sources_read += 1
            except SearchProviderError as e:
                out.problems.append(str(e))
                break
            for h in hits:
                st = SourceType.SEARCH_API if any(same_site(h.url, d) for d in official) else SourceType.JOB_PORTAL
                add(h.url, st)

    return list(cands.values())[: p.settings.max_pages_per_company]


def research_company(db: Session, company: Company, c: Criteria, p: Pipeline) -> CompanyOutcome:
    out = CompanyOutcome()
    cache: dict[str, FetchResult] = {}
    cands = discover(company, c, p, cache, out)
    official = official_domains(company)

    for cand in cands:
        res = cache.get(cand.url) or p.fetcher.fetch(cand.url)
        if not res.ok:
            if res.status_code not in (404, 410):  # a dead link isn't a coverage problem
                out.problems.append(f"Couldn't read a posting page: {res.error or 'HTTP ' + str(res.status_code)}")
            continue
        page = parse_page(res.text, res.final_url)
        job = extract_job(page, res.final_url, p.llm)
        if job is None:
            continue
        match = assess_match(job, c, p.llm)
        if match.score < c.min_match_score:
            out.skipped_low_score += 1
            continue
        v = verify_posting(fetch=res, page=page, job=job, official_domains=official)
        evidence = v.evidence + [{"signal": "match", "ok": True, "detail": f"Match score {match.score} ({match.method})"}]
        flags = detect_injection(page.text)
        if flags:
            evidence.append({"signal": "prompt_injection_suspected", "ok": False,
                             "detail": "Page contains instruction-like text; it was ignored and did not affect status or score"})
        vac, _ = vacancy_service.upsert_discovered(
            db, company=company, job=job, page_url=cand.url, final_url=res.final_url, source_type=cand.source_type,
            official=cand.official, http_status=res.status_code, content_hash=res.content_hash,
            excerpt=page.text[:500], status=v.status, evidence=evidence, match=match)
        out.vacancies.append((vac, evidence))
    return out


def recheck_vacancy(db: Session, vacancy: Vacancy, p: Pipeline) -> Vacancy:
    """Re-verify a stored vacancy. A failed/blocked fetch never flips status: it only adds a note."""
    company = db.get(Company, vacancy.company_id)
    url = vacancy.sources[0].url if vacancy.sources else (vacancy.application_url or "")
    now = utcnow()
    if not url:
        vacancy.verification_notes = "Recheck skipped: no source URL on record."
        db.commit()
        return vacancy
    res = p.fetcher.fetch(url)
    page = parse_page(res.text, res.final_url) if res.ok else None
    if not res.ok and res.status_code not in (404, 410):
        vacancy.verification_notes = f"Recheck on {now:%Y-%m-%d} could not complete: {res.error or 'HTTP ' + str(res.status_code)}. Status unchanged."
        db.commit()
        return vacancy
    job = ExtractedJob(title=vacancy.title)
    v = verify_posting(fetch=res, page=page, job=job, official_domains=official_domains(company), previously_known=True)
    vacancy.status, vacancy.evidence = v.status, v.evidence
    vacancy.last_verified_at = now if v.status in (VacancyStatus.ACTIVE_VERIFIED, VacancyStatus.CLOSED_VERIFIED) else vacancy.last_verified_at
    vacancy.verification_notes = "; ".join(e["detail"] for e in v.evidence)[:2000]
    db.commit()
    return vacancy
