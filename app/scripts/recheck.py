"""Cron-friendly recheck (use when you don't run Celery beat):  python -m app.scripts.recheck [limit]"""
import sys

from app.db.session import SessionLocal
from app.services import research_service
from app.tasks.jobs import recheck_one, stale_vacancy_ids


def main() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    with SessionLocal() as db:
        ids = stale_vacancy_ids(db, limit)
    pipeline = research_service.build_pipeline()
    for vid in ids:
        recheck_one(vid, pipeline=pipeline)
    print(f"Rechecked {len(ids)} vacancies")


if __name__ == "__main__":
    main()
