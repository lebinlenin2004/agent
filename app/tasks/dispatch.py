from fastapi import BackgroundTasks

from app.core.config import get_settings


def enqueue_search(run_id: int, background: BackgroundTasks, session_factory=None) -> None:
    if get_settings().celery_enabled:
        from app.tasks.jobs import run_search_task
        run_search_task.delay(run_id)
    else:
        from app.services.search_service import execute_run
        background.add_task(execute_run, run_id, session_factory)


def enqueue_recheck(vacancy_id: int, background: BackgroundTasks, session_factory=None) -> None:
    if get_settings().celery_enabled:
        from app.tasks.jobs import recheck_vacancy_task
        recheck_vacancy_task.delay(vacancy_id)
    else:
        from app.tasks.jobs import recheck_one
        background.add_task(recheck_one, vacancy_id, session_factory)
