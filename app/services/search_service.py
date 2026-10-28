import logging
from collections.abc import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.core.exceptions import AppError, ConflictError, NotFoundError
from app.core.utils import utcnow
from app.db import session as db_session
from app.models import (Company, RunStatus, SearchResult, SearchRun, User, Vacancy, VacancyStatus)
from app.models.enums import STATUS_LABELS
from app.schemas.common import Page
from app.schemas.search import Criteria, SearchCreate, SearchResultOut, SearchRunOut
from app.services import research_service

log = logging.getLogger(__name__)


def run_out(run: SearchRun) -> SearchRunOut:
    pct = 100 if run.status in (RunStatus.COMPLETED, RunStatus.COMPLETED_WITH_ERRORS) else \
        int(100 * run.processed_companies / run.total_companies) if run.total_companies else 0
    out = SearchRunOut.model_validate(run)
    out.progress_percent = pct
    return out


def get_run(db: Session, run_id: int) -> SearchRun:
    run = db.get(SearchRun, run_id)
    if not run:
        raise NotFoundError("Search run not found.")
    return run


def create_run(db: Session, data: SearchCreate, user: User, retry_of: int | None = None) -> SearchRun:
    ids = list(dict.fromkeys(data.company_ids))
    found = db.scalars(select(Company.id).where(Company.id.in_(ids), Company.is_archived.is_(False))).all()
    missing = set(ids) - set(found)
    if missing:
        raise AppError("Some selected companies don't exist or are archived.", {"company_ids": sorted(missing)})
    criteria = data.model_dump(exclude={"company_ids"})
    run = SearchRun(requested_by_id=user.id, criteria=criteria, company_ids=ids, total_companies=len(ids),
                    status=RunStatus.QUEUED, retry_of_id=retry_of)
    db.add(run)
    db.commit()
    return run


def list_runs(db: Session, page: int, page_size: int, status: RunStatus | None = None) -> Page:
    stmt = select(SearchRun)
    if status:
        stmt = stmt.where(SearchRun.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(SearchRun.id.desc()).offset((page - 1) * page_size).limit(page_size)).all()
    return Page.build([run_out(r) for r in rows], total, page, page_size)


def list_results(db: Session, run_id: int, page: int, page_size: int) -> Page:
    get_run(db, run_id)
    stmt = select(SearchResult).where(SearchResult.search_run_id == run_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.options(joinedload(SearchResult.company), joinedload(SearchResult.vacancy))
                      .order_by(SearchResult.id).offset((page - 1) * page_size).limit(page_size)).all()
    items = [SearchResultOut(id=r.id, company_id=r.company_id, company_name=r.company.name, vacancy_id=r.vacancy_id,
                             vacancy_title=r.vacancy.title if r.vacancy else None,
                             match_score=r.vacancy.match_score if r.vacancy else None, outcome=r.outcome,
                             outcome_label=STATUS_LABELS[r.outcome], evidence=r.evidence or [], error=r.error)
             for r in rows]
    return Page.build(items, total, page, page_size)


def retry_run(db: Session, run: SearchRun, user: User) -> SearchRun:
    """Retry only the companies whose search was incomplete (not those with a clean 'no match')."""
    if run.status not in (RunStatus.FAILED, RunStatus.COMPLETED_WITH_ERRORS):
        raise ConflictError("Only failed or partly completed searches can be retried.")
    ids = db.scalars(select(SearchResult.company_id).where(
        SearchResult.search_run_id == run.id, SearchResult.outcome == VacancyStatus.SEARCH_INCOMPLETE).distinct()).all()
    ids = list(ids) or list(run.company_ids)
    data = SearchCreate(company_ids=ids, **run.criteria)
    return create_run(db, data, user, retry_of=run.id)


def cancel_run(db: Session, run: SearchRun) -> SearchRun:
    if run.status not in (RunStatus.QUEUED, RunStatus.RUNNING):
        raise ConflictError("This search has already finished.")
    run.status, run.finished_at = RunStatus.CANCELLED, utcnow()
    db.commit()
    return run


def execute_run(run_id: int, session_factory: Callable[[], Session] | None = None,
                pipeline: research_service.Pipeline | None = None) -> None:
    """Runs in a Celery worker or a FastAPI background task. Safe to call twice: only QUEUED runs start."""
    db = (session_factory or db_session.SessionLocal)()
    try:
        run = db.get(SearchRun, run_id)
        if not run or run.status != RunStatus.QUEUED:
            return
        run.status, run.started_at = RunStatus.RUNNING, utcnow()
        db.commit()
        try:
            criteria = Criteria(**run.criteria)
            pipeline = pipeline or research_service.build_pipeline()
        except Exception as e:
            log.exception("Search run %s could not start", run_id)
            run.status, run.finished_at = RunStatus.FAILED, utcnow()
            run.error_message = "Search is not configured correctly. Ask an administrator to check the research settings."
            db.commit()
            return

        for cid in run.company_ids:
            db.refresh(run)
            if run.status == RunStatus.CANCELLED:
                return
            company = db.get(Company, cid)
            if company is None:
                run.processed_companies += 1
                db.commit()
                continue
            try:
                out = research_service.research_company(db, company, criteria, pipeline)
                for vac, ev in out.vacancies:
                    db.add(SearchResult(search_run_id=run.id, company_id=cid, vacancy_id=vac.id, outcome=vac.status, evidence=ev))
                if out.problems or not out.vacancies:
                    if out.vacancies:
                        outcome = VacancyStatus.SEARCH_INCOMPLETE  # found some, but coverage was partial
                    elif out.problems or out.sources_read == 0:
                        outcome = VacancyStatus.SEARCH_INCOMPLETE
                    else:
                        outcome = VacancyStatus.NO_MATCH_FOUND
                    detail = (out.problems if outcome == VacancyStatus.SEARCH_INCOMPLETE else
                              [f"Read {out.sources_read} source(s); {out.skipped_low_score} posting(s) scored below the threshold"])
                    db.add(SearchResult(search_run_id=run.id, company_id=cid, vacancy_id=None, outcome=outcome,
                                        evidence=[{"signal": "coverage", "ok": outcome == VacancyStatus.NO_MATCH_FOUND, "detail": d} for d in detail],
                                        error="; ".join(out.problems)[:1000] or None))
                    if outcome == VacancyStatus.SEARCH_INCOMPLETE:
                        run.incomplete_companies += 1
                run.vacancies_found += len(out.vacancies)
                company.last_searched_at = utcnow()
            except Exception:
                db.rollback()
                log.exception("Research failed for company %s in run %s", cid, run_id)
                run = db.get(SearchRun, run_id)
                db.add(SearchResult(search_run_id=run.id, company_id=cid, outcome=VacancyStatus.SEARCH_INCOMPLETE,
                                    evidence=[], error="An unexpected error stopped this company's search."))
                run.incomplete_companies += 1
            run.processed_companies += 1
            db.commit()

        run.finished_at = utcnow()
        if run.total_companies and run.incomplete_companies >= run.total_companies:
            run.status = RunStatus.FAILED
            run.error_message = "None of the companies could be fully searched. Retry the incomplete companies."
        elif run.incomplete_companies:
            run.status = RunStatus.COMPLETED_WITH_ERRORS
        else:
            run.status = RunStatus.COMPLETED
        db.commit()
    finally:
        db.close()
