import datetime as dt
from collections import Counter, defaultdict

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import VacancyFilters
from app.core.utils import utcnow
from app.models import Company, SearchRun, Vacancy, VacancyStatus
from app.models.enums import STATUS_LABELS
from app.services import search_service
from app.services.vacancy_service import apply_filters


def _filtered_vacancies(db: Session, f: VacancyFilters) -> list[Vacancy]:
    stmt = apply_filters(select(Vacancy).join(Company, Company.id == Vacancy.company_id), f)
    return list(db.scalars(stmt))


def _status_counts(vacs) -> dict[str, int]:
    c = Counter(v.status for v in vacs)
    return {s.value: c.get(s, 0) for s in (VacancyStatus.ACTIVE_VERIFIED, VacancyStatus.FOUND_UNVERIFIED,
                                           VacancyStatus.CLOSED_VERIFIED)}


def dashboard(db: Session, days: int = 14) -> dict:
    live = Vacancy.is_archived.is_(False)
    total = db.scalar(select(func.count()).select_from(Vacancy).where(live)) or 0
    by = dict(db.execute(select(Vacancy.status, func.count()).where(live).group_by(Vacancy.status)).all())
    cutoff = utcnow() - dt.timedelta(days=days - 1)
    cutoff = cutoff.replace(hour=0, minute=0, second=0, microsecond=0)
    seen = db.scalars(select(Vacancy.first_seen_at).where(live, Vacancy.first_seen_at >= cutoff)).all()
    buckets = Counter((d if d.tzinfo is None else d.astimezone(dt.timezone.utc)).date() for d in seen)
    trend = [{"date": (cutoff.date() + dt.timedelta(days=i)).isoformat(),
              "count": buckets.get(cutoff.date() + dt.timedelta(days=i), 0)} for i in range(days)]
    runs = db.scalars(select(SearchRun).order_by(SearchRun.id.desc()).limit(5)).all()
    return {
        "total_companies": db.scalar(select(func.count()).select_from(Company).where(Company.is_archived.is_(False))) or 0,
        "vacancies_discovered": total,
        "verified_active": by.get(VacancyStatus.ACTIVE_VERIFIED, 0),
        "pending_verification": by.get(VacancyStatus.FOUND_UNVERIFIED, 0),
        "closed_verified": by.get(VacancyStatus.CLOSED_VERIFIED, 0),
        "recent_runs": [search_service.run_out(r).model_dump(mode="json") for r in runs],
        "discovery_trend": trend,
    }


def summary(db: Session, f: VacancyFilters) -> dict:
    vacs = _filtered_vacancies(db, f)
    scores = [v.match_score for v in vacs if v.match_score is not None]
    return {"total_vacancies": len(vacs), "by_status": _status_counts(vacs),
            "companies_with_vacancies": len({v.company_id for v in vacs}),
            "average_match_score": round(sum(scores) / len(scores), 1) if scores else None}


def company_report(db: Session, f: VacancyFilters) -> list[dict]:
    vacs = _filtered_vacancies(db, f)
    groups: dict[int, list[Vacancy]] = defaultdict(list)
    for v in vacs:
        groups[v.company_id].append(v)
    stmt = select(Company).where(Company.is_archived.is_(False))
    if f.company_id:
        stmt = stmt.where(Company.id == f.company_id)
    rows = []
    for c in db.scalars(stmt.order_by(Company.name)):
        vs = groups.get(c.id, [])
        if not vs and (f.status or f.stack or f.location or f.min_score is not None or f.date_from or f.date_to):
            continue  # a filtered report lists only companies that have matching vacancies
        scores = [v.match_score for v in vs if v.match_score is not None]
        rows.append({"company_id": c.id, "company": c.name, "location": c.location, "total_vacancies": len(vs),
                     **_status_counts(vs), "best_match_score": max(scores) if scores else None,
                     "last_searched_at": c.last_searched_at.isoformat() if c.last_searched_at else None,
                     "finding": "Vacancies found" if vs else ("No matching vacancy found" if c.last_searched_at else "Not searched yet")})
    return rows


def vacancy_export_rows(db: Session, f: VacancyFilters, limit: int = 50000) -> list[dict]:
    stmt = apply_filters(select(Vacancy, Company.name).join(Company, Company.id == Vacancy.company_id), f)
    rows = db.execute(stmt.order_by(Vacancy.id).limit(limit)).all()
    return [{
        "Job title": v.title, "Company": name, "Location": v.location, "Skills": ", ".join(v.skills or []),
        "Experience": v.experience_text, "Salary": v.salary_text, "Match score": v.match_score,
        "Status": STATUS_LABELS[v.status], "Application URL": v.application_url,
        "Posted": v.posted_date.isoformat() if v.posted_date else None,
        "Last verified": v.last_verified_at.date().isoformat() if v.last_verified_at else None,
        "Reviewed": "Yes" if v.reviewed else "No", "Verification notes": v.verification_notes,
    } for v, name in rows]
