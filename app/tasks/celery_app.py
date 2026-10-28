from celery import Celery

from app.core.config import get_settings

s = get_settings()
celery_app = Celery("vacancy_agent", broker=s.redis_url, backend=s.redis_url, include=["app.tasks.jobs"])
celery_app.conf.update(
    task_acks_late=True, task_time_limit=60 * 60, task_soft_time_limit=55 * 60,
    worker_prefetch_multiplier=1, task_serializer="json", result_expires=3600, timezone="UTC",
    beat_schedule={"recheck-stale-vacancies": {"task": "app.tasks.jobs.recheck_stale_vacancies", "schedule": 3600.0}},
)
