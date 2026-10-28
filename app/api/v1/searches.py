from fastapi import APIRouter, BackgroundTasks, Request

from app.api.deps import DB, CurrentUser, Paging, WriteUser
from app.models import RunStatus
from app.schemas.common import Page
from app.schemas.search import SearchCreate, SearchResultOut, SearchRunOut
from app.services import search_service as svc
from app.tasks.dispatch import enqueue_search

router = APIRouter(prefix="/searches", tags=["searches"])


def _sf(request: Request):
    return getattr(request.app.state, "session_factory", None)


@router.post("", response_model=SearchRunOut, status_code=202)
def start_search(data: SearchCreate, db: DB, user: WriteUser, background: BackgroundTasks, request: Request):
    """Queues a research run and returns immediately. Poll GET /searches/{id} for progress."""
    run = svc.create_run(db, data, user)
    enqueue_search(run.id, background, _sf(request))
    return svc.run_out(run)


@router.get("", response_model=Page[SearchRunOut])
def history(db: DB, _: CurrentUser, p: Paging, status: RunStatus | None = None):
    return svc.list_runs(db, p.page, p.page_size, status)


@router.get("/{run_id}", response_model=SearchRunOut)
def progress(run_id: int, db: DB, _: CurrentUser):
    return svc.run_out(svc.get_run(db, run_id))


@router.get("/{run_id}/results", response_model=Page[SearchResultOut])
def results(run_id: int, db: DB, _: CurrentUser, p: Paging):
    return svc.list_results(db, run_id, p.page, p.page_size)


@router.post("/{run_id}/retry", response_model=SearchRunOut, status_code=202)
def retry(run_id: int, db: DB, user: WriteUser, background: BackgroundTasks, request: Request):
    run = svc.retry_run(db, svc.get_run(db, run_id), user)
    enqueue_search(run.id, background, _sf(request))
    return svc.run_out(run)


@router.post("/{run_id}/cancel", response_model=SearchRunOut)
def cancel(run_id: int, db: DB, _: WriteUser):
    return svc.run_out(svc.cancel_run(db, svc.get_run(db, run_id)))
