from fastapi import APIRouter

from app.api.v1 import auth, companies, exports, reports, searches, settings, vacancies

router = APIRouter()
for module in (auth, companies, searches, vacancies, reports, exports, settings):
    router.include_router(module.router)
