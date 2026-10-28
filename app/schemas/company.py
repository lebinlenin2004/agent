import datetime as dt

from pydantic import BaseModel, Field, field_validator

from app.core.utils import domain_of
from app.schemas.common import ORM


def clean_url(v: str | None) -> str | None:
    if v is None or not v.strip():
        return None
    v = v.strip()
    if "://" not in v:
        v = "https://" + v
    if not v.lower().startswith(("http://", "https://")):
        raise ValueError("URL must start with http:// or https://")
    d = domain_of(v)
    if not d or "." not in d or " " in v:
        raise ValueError("Enter a valid web address, e.g. acme.com")
    return v[:1000]


def clean_stack(v: list[str] | None) -> list[str]:
    out, seen = [], set()
    for item in v or []:
        item = item.strip()
        if item and item.lower() not in seen:
            seen.add(item.lower())
            out.append(item[:50])
    if len(out) > 30:
        raise ValueError("At most 30 technologies per company")
    return out


class CompanyBase(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    location: str | None = Field(None, max_length=255)
    tech_stack: list[str] = []
    website: str | None = None
    careers_url: str | None = None

    _site = field_validator("website", "careers_url")(clean_url)
    _stack = field_validator("tech_stack")(clean_stack)

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("Name is required")
        return v


class CompanyCreate(CompanyBase):
    pass


class CompanyUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    location: str | None = Field(None, max_length=255)
    tech_stack: list[str] | None = None
    website: str | None = None
    careers_url: str | None = None

    _site = field_validator("website", "careers_url")(clean_url)

    @field_validator("tech_stack")
    @classmethod
    def _stack(cls, v):
        return None if v is None else clean_stack(v)


class CompanyOut(ORM):
    id: int
    name: str
    location: str | None
    tech_stack: list[str]
    website: str | None
    careers_url: str | None
    is_archived: bool
    last_searched_at: dt.datetime | None
    created_at: dt.datetime
    updated_at: dt.datetime


class ImportRowError(BaseModel):
    row: int
    message: str


class ImportReport(BaseModel):
    dry_run: bool
    total_rows: int
    created: int
    duplicates: list[ImportRowError]
    errors: list[ImportRowError]
    preview: list[dict]
