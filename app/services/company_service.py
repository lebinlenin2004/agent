import io
import re

import pandas as pd
from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.exceptions import AppError, ConflictError, NotFoundError
from app.core.utils import domain_of, normalize_text, to_index
from app.models import Company
from app.schemas.common import Page
from app.schemas.company import (CompanyCreate, CompanyOut, CompanyUpdate, ImportReport, ImportRowError)

ALIASES = {
    "name": {"name", "company", "company_name"},
    "location": {"location", "city", "place"},
    "tech_stack": {"tech_stack", "stack", "technologies", "tech", "technology_stack"},
    "website": {"website", "url", "site", "web"},
    "careers_url": {"careers_url", "careers", "careers_page", "career_page", "careers_link"},
}
MAX_IMPORT_ROWS = 5000


def make_dedupe_key(name: str, location: str | None, website: str | None) -> str:
    return domain_of(website) or f"{normalize_text(name)}|{normalize_text(location)}"


def get_company(db: Session, company_id: int) -> Company:
    c = db.get(Company, company_id)
    if not c:
        raise NotFoundError("Company not found.")
    return c


def list_companies(db: Session, *, q, location, stack, archived, page: int, page_size: int) -> Page:
    stmt = select(Company)
    if archived == "no":
        stmt = stmt.where(Company.is_archived.is_(False))
    elif archived == "yes":
        stmt = stmt.where(Company.is_archived.is_(True))
    if q:
        like = q.lower()
        stmt = stmt.where(or_(func.lower(Company.name).contains(like, autoescape=True),
                              Company.stack_index.contains(normalize_text(q), autoescape=True)))
    if location:
        stmt = stmt.where(func.lower(Company.location).contains(location.lower(), autoescape=True))
    if stack:
        stmt = stmt.where(Company.stack_index.contains("|" + normalize_text(stack) + "|", autoescape=True))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(Company.name).offset((page - 1) * page_size).limit(page_size)).all()
    return Page.build([CompanyOut.model_validate(r) for r in rows], total, page, page_size)


def _assert_unique(db: Session, key: str, exclude_id: int | None = None):
    stmt = select(Company.id).where(Company.dedupe_key == key)
    if exclude_id:
        stmt = stmt.where(Company.id != exclude_id)
    if db.scalar(stmt):
        raise ConflictError("A company with this website or name and location already exists.")


def create_company(db: Session, data: CompanyCreate, user_id: int) -> Company:
    key = make_dedupe_key(data.name, data.location, data.website)
    _assert_unique(db, key)
    c = Company(**data.model_dump(), dedupe_key=key, stack_index=to_index(data.tech_stack), created_by_id=user_id)
    db.add(c)
    db.commit()
    return c


def update_company(db: Session, company: Company, data: CompanyUpdate) -> Company:
    for k, v in data.model_dump(exclude_unset=True).items():
        if k == "name" and v is None:
            continue
        setattr(company, k, v)
    company.stack_index = to_index(company.tech_stack)
    key = make_dedupe_key(company.name, company.location, company.website)
    _assert_unique(db, key, company.id)
    company.dedupe_key = key
    db.commit()
    return company


def set_archived(db: Session, company: Company, archived: bool) -> Company:
    company.is_archived = archived
    db.commit()
    return company


def delete_company(db: Session, company: Company) -> None:
    db.delete(company)
    db.commit()


# ---------------------------------------------------------------- imports
def _read_table(filename: str, content: bytes) -> pd.DataFrame:
    name = filename.lower()
    try:
        if name.endswith(".csv"):
            df = pd.read_csv(io.BytesIO(content), dtype=str, keep_default_na=False, encoding="utf-8-sig")
        elif name.endswith(".xlsx"):
            df = pd.read_excel(io.BytesIO(content), dtype=str, keep_default_na=False, engine="openpyxl")
        else:
            raise AppError("Upload a .csv or .xlsx file.")
    except AppError:
        raise
    except Exception:
        raise AppError("The file couldn't be read. Check that it is a valid CSV or XLSX export.")
    if len(df) > MAX_IMPORT_ROWS:
        raise AppError(f"Files are limited to {MAX_IMPORT_ROWS} rows. Split the file and upload it in parts.")
    return df


def _map_columns(df: pd.DataFrame) -> dict[str, str]:
    found = {}
    for col in df.columns:
        norm = re.sub(r"[^a-z0-9]+", "_", str(col).strip().lower()).strip("_")
        for target, names in ALIASES.items():
            if norm in names and target not in found:
                found[target] = col
    if "name" not in found:
        raise AppError("The file needs a 'name' (or 'company') column.")
    return found


def import_companies(db: Session, filename: str, content: bytes, user_id: int, dry_run: bool) -> ImportReport:
    df = _read_table(filename, content)
    cols = _map_columns(df)
    existing = set(db.scalars(select(Company.dedupe_key)).all())
    seen: set[str] = set()
    errors, dups, preview, to_add = [], [], [], []

    for i, row in enumerate(df.to_dict("records"), start=2):  # row 1 is the header
        raw = {t: str(row[c]).strip() for t, c in cols.items()}
        raw["tech_stack"] = [s for s in re.split(r"[;,|]", raw.get("tech_stack", "")) if s.strip()]
        try:
            data = CompanyCreate(**{k: (v or None) if k != "tech_stack" and k != "name" else v
                                    for k, v in raw.items()})
        except ValidationError as e:
            msg = "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg'].removeprefix('Value error, ')}"
                            for x in e.errors())
            errors.append(ImportRowError(row=i, message=msg))
            continue
        key = make_dedupe_key(data.name, data.location, data.website)
        if key in existing:
            dups.append(ImportRowError(row=i, message="Already in your company list"))
        elif key in seen:
            dups.append(ImportRowError(row=i, message="Duplicate of an earlier row in this file"))
        else:
            seen.add(key)
            to_add.append((data, key))
            if len(preview) < 10:
                preview.append({"row": i, **data.model_dump()})

    if not dry_run:
        for data, key in to_add:
            db.add(Company(**data.model_dump(), dedupe_key=key, stack_index=to_index(data.tech_stack),
                           created_by_id=user_id))
        db.commit()
    return ImportReport(dry_run=dry_run, total_rows=len(df), created=len(to_add), duplicates=dups,
                        errors=errors, preview=preview)
