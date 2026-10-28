from typing import Annotated, Literal

from fastapi import APIRouter, Depends

from app.api.deps import DB, CurrentUser, VacancyFilters
from app.services import export_service, report_service

router = APIRouter(prefix="/exports", tags=["exports"])
Filters = Annotated[VacancyFilters, Depends()]
Format = Literal["csv", "xlsx"]

VAC_COLS = ["Job title", "Company", "Location", "Skills", "Experience", "Salary", "Match score", "Status",
            "Application URL", "Posted", "Last verified", "Reviewed", "Verification notes"]
CO_COLS = ["company", "location", "total_vacancies", "active_verified", "found_unverified", "closed_verified",
           "best_match_score", "last_searched_at", "finding"]


@router.get("/vacancies")
def export_vacancies(db: DB, _: CurrentUser, f: Filters, format: Format = "csv"):
    """Exports exactly what the Vacancy Explorer shows for the same filters (max 50,000 rows)."""
    return export_service.to_response(report_service.vacancy_export_rows(db, f), VAC_COLS, format, "vacancies")


@router.get("/company-report")
def export_company_report(db: DB, _: CurrentUser, f: Filters, format: Format = "csv"):
    return export_service.to_response(report_service.company_report(db, f), CO_COLS, format, "company-report")
