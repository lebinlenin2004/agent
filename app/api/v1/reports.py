from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import DB, CurrentUser, VacancyFilters
from app.services import report_service as svc

router = APIRouter(prefix="/reports", tags=["reports"])
Filters = Annotated[VacancyFilters, Depends()]


@router.get("/dashboard")
def dashboard(db: DB, _: CurrentUser, days: int = Query(14, ge=7, le=90)):
    """Totals, recent runs and a per-day discovery trend for the dashboard cards and chart."""
    return svc.dashboard(db, days)


@router.get("/summary")
def summary(db: DB, _: CurrentUser, f: Filters):
    return svc.summary(db, f)


@router.get("/companies")
def company_findings(db: DB, _: CurrentUser, f: Filters):
    return {"items": svc.company_report(db, f)}
