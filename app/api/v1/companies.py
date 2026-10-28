from typing import Literal

from fastapi import APIRouter, File, Query, UploadFile

from app.api.deps import DB, AdminUser, CurrentUser, Paging, WriteUser
from app.core.exceptions import AppError
from app.schemas.common import Message, Page
from app.schemas.company import CompanyCreate, CompanyOut, CompanyUpdate, ImportReport
from app.services import company_service as svc

router = APIRouter(prefix="/companies", tags=["companies"])
MAX_UPLOAD = 5 * 1024 * 1024


@router.get("", response_model=Page[CompanyOut])
def list_companies(db: DB, _: CurrentUser, p: Paging, q: str | None = Query(None, max_length=100),
                   location: str | None = None, stack: str | None = None,
                   archived: Literal["no", "yes", "all"] = "no"):
    return svc.list_companies(db, q=q, location=location, stack=stack, archived=archived,
                              page=p.page, page_size=p.page_size)


@router.post("", response_model=CompanyOut, status_code=201)
def create_company(data: CompanyCreate, db: DB, user: WriteUser):
    return svc.create_company(db, data, user.id)


@router.post("/import", response_model=ImportReport)
async def import_companies(db: DB, user: WriteUser, file: UploadFile = File(...), dry_run: bool = False):
    """Upload CSV/XLSX. Use dry_run=true first to get the preview and row-level validation errors."""
    content = await file.read(MAX_UPLOAD + 1)
    if len(content) > MAX_UPLOAD:
        raise AppError("File is larger than 5 MB.")
    return svc.import_companies(db, file.filename or "", content, user.id, dry_run)


@router.get("/{company_id}", response_model=CompanyOut)
def get_company(company_id: int, db: DB, _: CurrentUser):
    return svc.get_company(db, company_id)


@router.patch("/{company_id}", response_model=CompanyOut)
def update_company(company_id: int, data: CompanyUpdate, db: DB, _: WriteUser):
    return svc.update_company(db, svc.get_company(db, company_id), data)


@router.post("/{company_id}/archive", response_model=CompanyOut)
def archive(company_id: int, db: DB, _: WriteUser):
    return svc.set_archived(db, svc.get_company(db, company_id), True)


@router.post("/{company_id}/unarchive", response_model=CompanyOut)
def unarchive(company_id: int, db: DB, _: WriteUser):
    return svc.set_archived(db, svc.get_company(db, company_id), False)


@router.delete("/{company_id}", response_model=Message)
def delete_company(company_id: int, db: DB, _: AdminUser):
    """Permanent delete (admin only). Removes the company's vacancies too; prefer archiving."""
    svc.delete_company(db, svc.get_company(db, company_id))
    return Message(message="Company deleted.")
