from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Request

from app.api.deps import DB, AdminUser, CurrentUser, Paging, VacancyFilters, WriteUser
from app.models import Vacancy
from app.schemas.common import Message, Page
from app.schemas.vacancy import VacancyCreate, VacancyDetail, VacancyOut, VacancyUpdate
from app.services import vacancy_service as svc
from app.tasks.dispatch import enqueue_recheck

router = APIRouter(prefix="/vacancies", tags=["vacancies"])
Filters = Annotated[VacancyFilters, Depends()]


@router.get("", response_model=Page[VacancyOut])
def list_vacancies(db: DB, _: CurrentUser, f: Filters, p: Paging, sort: str = "match_score",
                   order: Literal["asc", "desc"] = "desc"):
    return svc.list_vacancies(db, f, sort, order == "desc", p.page, p.page_size)


@router.post("", response_model=VacancyDetail, status_code=201)
def create_vacancy(data: VacancyCreate, db: DB, user: WriteUser):
    """Manual entries are always 'Found — Unverified'. Use /recheck to verify against the source."""
    return svc.to_out(svc.get_vacancy(db, svc.create_manual(db, data, user.id).id), detail=True)


@router.get("/{vacancy_id}", response_model=VacancyDetail)
def get_vacancy(vacancy_id: int, db: DB, _: CurrentUser):
    return svc.to_out(svc.get_vacancy(db, vacancy_id), detail=True)


@router.patch("/{vacancy_id}", response_model=VacancyDetail)
def update_vacancy(vacancy_id: int, data: VacancyUpdate, db: DB, _: WriteUser):
    v = svc.update_vacancy(db, svc.get_vacancy(db, vacancy_id), data)
    return svc.to_out(svc.get_vacancy(db, v.id), detail=True)


def _archive(db, vacancy_id: int, value: bool):
    v = svc.get_vacancy(db, vacancy_id)
    v.is_archived = value
    db.commit()
    return svc.to_out(v, detail=True)


@router.post("/{vacancy_id}/archive", response_model=VacancyDetail)
def archive(vacancy_id: int, db: DB, _: WriteUser):
    return _archive(db, vacancy_id, True)


@router.post("/{vacancy_id}/unarchive", response_model=VacancyDetail)
def unarchive(vacancy_id: int, db: DB, _: WriteUser):
    return _archive(db, vacancy_id, False)


@router.post("/{vacancy_id}/recheck", response_model=Message, status_code=202)
def recheck(vacancy_id: int, db: DB, _: WriteUser, background: BackgroundTasks, request: Request):
    svc.get_vacancy(db, vacancy_id)
    enqueue_recheck(vacancy_id, background, getattr(request.app.state, "session_factory", None))
    return Message(message="Recheck queued.")


@router.delete("/{vacancy_id}", response_model=Message)
def delete_vacancy(vacancy_id: int, db: DB, _: AdminUser):
    db.delete(svc.get_vacancy(db, vacancy_id))
    db.commit()
    return Message(message="Vacancy deleted.")
