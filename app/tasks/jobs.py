import logging

from sqlalchemy import or_, select

from app.core.config import get_settings
from app.core.utils import utcnow
from app.db.session import SessionLocal
from app.models import Vacancy, VacancyStatus
from app.services import research_service, search_service
from app.tasks.celery_app import celery_app

log = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.jobs.run_search")
def run_search_task(run_id: int) -> None:
    search_service.execute_run(run_id)


def recheck_one(vacancy_id: int, session_factory=None, pipeline=None) -> None:
    db = (session_factory or SessionLocal)()
    try:
        v = db.get(Vacancy, vacancy_id)
        if v and not v.is_archived:
            research_service.recheck_vacancy(db, v, pipeline or research_service.build_pipeline())
    finally:
        db.close()


@celery_app.task(name="app.tasks.jobs.recheck_vacancy")
def recheck_vacancy_task(vacancy_id: int) -> None:
    recheck_one(vacancy_id)


def stale_vacancy_ids(db, limit: int = 200) -> list[int]:
    import datetime as dt
    cutoff = utcnow() - dt.timedelta(hours=get_settings().recheck_interval_hours)
    stmt = (select(Vacancy.id).where(
        Vacancy.is_archived.is_(False),
        Vacancy.status.in_([VacancyStatus.ACTIVE_VERIFIED, VacancyStatus.FOUND_UNVERIFIED]),
        or_(Vacancy.last_verified_at.is_(None), Vacancy.last_verified_at < cutoff)).order_by(Vacancy.last_verified_at.asc().nulls_first()).limit(limit))
    return list(db.scalars(stmt))


@celery_app.task(name="app.tasks.jobs.recheck_stale_vacancies")
def recheck_stale_vacancies(limit: int = 200) -> int:
    """Hourly (Celery beat). Fans out one task per stale vacancy; spreads load via the worker queue."""
    db = SessionLocal()
    try:
        ids = stale_vacancy_ids(db, limit)
    finally:
        db.close()
    for vid in ids:
        recheck_vacancy_task.delay(vid)
    return len(ids)
