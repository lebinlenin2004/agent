import datetime as dt

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.ai.schemas import ExtractedJob, MatchResult
from app.api.deps import VacancyFilters
from app.core.exceptions import AppError, ConflictError, NotFoundError
from app.core.utils import domain_of, fingerprint, fit_url, normalize_text, normalize_url, to_index, utcnow
from app.models import Company, SourceType, Vacancy, VacancySource, VacancyStatus
from app.models.enums import STATUS_LABELS
from app.schemas.common import Page
from app.schemas.vacancy import (SourceOut, VacancyCreate, VacancyDetail, VacancyOut, VacancyUpdate)

SORTS = {
    "match_score": Vacancy.match_score, "title": Vacancy.title, "location": Vacancy.location,
    "status": Vacancy.status, "first_seen_at": Vacancy.first_seen_at, "last_verified_at": Vacancy.last_verified_at,
    "company": Company.name, "posted_date": Vacancy.posted_date,
}


def parse_statuses(values: list[str] | None) -> list[VacancyStatus]:
    try:
        return [VacancyStatus(v) for v in values or []]
    except ValueError:
        valid = ", ".join(s.value for s in VacancyStatus)
        raise AppError(f"Unknown status. Use one of: {valid}")


def apply_filters(stmt, f: VacancyFilters):
    """stmt must select from Vacancy joined to Company."""
    if not f.include_archived:
        stmt = stmt.where(Vacancy.is_archived.is_(False))
    if f.company_id:
        stmt = stmt.where(Vacancy.company_id == f.company_id)
    if f.q:
        like = f.q.lower()
        stmt = stmt.where(or_(func.lower(Vacancy.title).contains(like, autoescape=True),
                              func.lower(Company.name).contains(like, autoescape=True)))
    if f.location:
        stmt = stmt.where(func.lower(Vacancy.location).contains(f.location.lower(), autoescape=True))
    if f.stack:
        stmt = stmt.where(Vacancy.skills_index.contains("|" + normalize_text(f.stack) + "|", autoescape=True))
    if f.min_experience is not None:
        stmt = stmt.where(or_(Vacancy.experience_max_years.is_(None), Vacancy.experience_max_years >= f.min_experience))
    if f.max_experience is not None:
        stmt = stmt.where(or_(Vacancy.experience_min_years.is_(None), Vacancy.experience_min_years <= f.max_experience))
    statuses = parse_statuses(f.status)
    if statuses:
        stmt = stmt.where(Vacancy.status.in_(statuses))
    if f.min_score is not None:
        stmt = stmt.where(Vacancy.match_score >= f.min_score)
    if f.max_score is not None:
        stmt = stmt.where(Vacancy.match_score <= f.max_score)
    if f.date_from:
        stmt = stmt.where(Vacancy.first_seen_at >= dt.datetime.combine(f.date_from, dt.time.min, dt.timezone.utc))
    if f.date_to:
        stmt = stmt.where(Vacancy.first_seen_at < dt.datetime.combine(f.date_to + dt.timedelta(days=1), dt.time.min, dt.timezone.utc))
    if f.reviewed is not None:
        stmt = stmt.where(Vacancy.reviewed.is_(f.reviewed))
    return stmt


def to_out(v: Vacancy, detail: bool = False):
    base = dict(
        id=v.id, company_id=v.company_id, company_name=v.company.name if v.company else None, title=v.title,
        location=v.location, skills=v.skills or [], experience_text=v.experience_text,
        experience_min_years=v.experience_min_years, experience_max_years=v.experience_max_years,
        salary_text=v.salary_text, application_url=v.application_url, posted_date=v.posted_date,
        status=v.status, status_label=STATUS_LABELS[v.status], match_score=v.match_score,
        reviewed=v.reviewed, is_archived=v.is_archived, first_seen_at=v.first_seen_at,
        last_verified_at=v.last_verified_at)
    if not detail:
        return VacancyOut(**base)
    return VacancyDetail(**base, description=v.description, match_rationale=v.match_rationale,
                         matched_skills=v.matched_skills or [], evidence=v.evidence or [],
                         verification_notes=v.verification_notes, advisor_notes=v.advisor_notes,
                         sources=[SourceOut.model_validate(s) for s in v.sources])


def list_vacancies(db: Session, f: VacancyFilters, sort: str, desc: bool, page: int, page_size: int) -> Page:
    base = select(Vacancy).join(Company, Company.id == Vacancy.company_id)
    base = apply_filters(base, f)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    col = SORTS.get(sort)
    if col is None:
        raise AppError(f"Cannot sort by '{sort}'. Use one of: {', '.join(SORTS)}")
    order = col.desc().nulls_last() if desc else col.asc().nulls_last()
    rows = db.scalars(base.options(joinedload(Vacancy.company)).order_by(order, Vacancy.id)
                      .offset((page - 1) * page_size).limit(page_size)).all()
    return Page.build([to_out(v) for v in rows], total, page, page_size)


def get_vacancy(db: Session, vacancy_id: int) -> Vacancy:
    v = db.get(Vacancy, vacancy_id, options=[joinedload(Vacancy.company), joinedload(Vacancy.sources)])
    if not v:
        raise NotFoundError("Vacancy not found.")
    return v


def create_manual(db: Session, data: VacancyCreate, user_id: int) -> Vacancy:
    if not db.get(Company, data.company_id):
        raise NotFoundError("Company not found.")
    now = utcnow()
    url = data.source_url or data.application_url
    fp = fingerprint(data.company_id, data.title, data.location)
    norm = fit_url(normalize_url(url)) if url else None
    if db.scalar(select(Vacancy.id).where(or_(Vacancy.fingerprint == fp,
                                              Vacancy.normalized_url == norm if norm else False))):
        raise ConflictError("This vacancy is already recorded.")
    v = Vacancy(**data.model_dump(exclude={"source_url"}), skills_index=to_index(data.skills), fingerprint=fp,
                normalized_url=norm, status=VacancyStatus.FOUND_UNVERIFIED, first_seen_at=now, last_seen_at=now,
                created_by_id=user_id, evidence=[], matched_skills=[],
                verification_notes="Added manually; not yet verified against a source.")
    db.add(v)
    db.commit()
    return v


def update_vacancy(db: Session, v: Vacancy, data: VacancyUpdate) -> Vacancy:
    changes = data.model_dump(exclude_unset=True)
    for k, val in changes.items():
        setattr(v, k, val)
    if "skills" in changes:
        v.skills_index = to_index(v.skills)
    db.commit()
    return v


def upsert_discovered(db: Session, *, company: Company, job: ExtractedJob, page_url: str, final_url: str,
                      source_type: SourceType, official: bool, http_status: int | None,
                      content_hash: str | None, excerpt: str | None, status: VacancyStatus,
                      evidence: list[dict], match: MatchResult) -> tuple[Vacancy, bool]:
    """Insert or refresh a vacancy found by research. Duplicates are matched on canonical URL, then fingerprint."""
    now = utcnow()
    norm = fit_url(normalize_url(final_url or page_url))
    fp = fingerprint(company.id, job.title, job.location)
    v = db.scalar(select(Vacancy).where(or_(Vacancy.normalized_url == norm, Vacancy.fingerprint == fp)))
    created = v is None
    if created:
        v = Vacancy(company_id=company.id, title=job.title, fingerprint=fp, normalized_url=norm,
                    first_seen_at=now, last_seen_at=now)
        db.add(v)
    if not created and v.is_archived:
        v.last_seen_at = now  # respect an advisor's archive decision; don't resurrect
        db.flush()
        return v, False
    v.location = job.location or v.location
    v.skills = job.skills or v.skills or []
    v.skills_index = to_index(v.skills)
    v.experience_text = job.experience_text or v.experience_text
    v.experience_min_years = job.experience_min_years if job.experience_min_years is not None else v.experience_min_years
    v.experience_max_years = job.experience_max_years if job.experience_max_years is not None else v.experience_max_years
    v.salary_text = job.salary_text or v.salary_text
    v.description = job.description or v.description
    v.application_url = job.application_url or v.application_url or final_url or page_url
    v.posted_date = job.posted_date or v.posted_date
    v.status = status
    v.match_score, v.match_rationale, v.matched_skills = match.score, match.rationale, match.matched_skills
    v.evidence = evidence
    v.last_verified_at = now if status in (VacancyStatus.ACTIVE_VERIFIED, VacancyStatus.CLOSED_VERIFIED) else v.last_verified_at
    v.verification_notes = "; ".join(e["detail"] for e in evidence if e.get("detail"))[:2000]
    v.last_seen_at = now
    db.flush()
    src_url = final_url or page_url
    if not db.scalar(select(VacancySource.id).where(VacancySource.vacancy_id == v.id, VacancySource.url == src_url)):
        db.add(VacancySource(vacancy_id=v.id, url=src_url, source_type=source_type, domain=domain_of(src_url),
                             is_official=official, http_status=http_status, content_hash=content_hash,
                             fetched_at=now, excerpt=(excerpt or "")[:1000]))
    db.flush()
    return v, created
