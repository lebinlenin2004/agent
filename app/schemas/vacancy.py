import datetime as dt

from pydantic import BaseModel, Field, field_validator

from app.models.enums import SourceType, VacancyStatus
from app.schemas.common import ORM


class VacancyBase(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    location: str | None = Field(None, max_length=255)
    skills: list[str] = Field(default_factory=list, max_length=40)
    experience_text: str | None = Field(None, max_length=200)
    experience_min_years: float | None = Field(None, ge=0, le=60)
    experience_max_years: float | None = Field(None, ge=0, le=60)
    salary_text: str | None = Field(None, max_length=200)
    description: str | None = Field(None, max_length=20000)
    application_url: str | None = Field(None, max_length=2000)
    posted_date: dt.date | None = None

    @field_validator("application_url")
    @classmethod
    def _http_only(cls, v):
        if v and not v.lower().startswith(("http://", "https://")):
            raise ValueError("Application URL must be an http(s) link")
        return v


class VacancyCreate(VacancyBase):
    """Manual entries are always 'Found — Unverified'. Only verification can change status."""
    company_id: int
    source_url: str | None = Field(None, max_length=2000)


class VacancyUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=300)
    location: str | None = None
    skills: list[str] | None = None
    experience_text: str | None = None
    salary_text: str | None = None
    description: str | None = Field(None, max_length=20000)
    reviewed: bool | None = None
    advisor_notes: str | None = Field(None, max_length=5000)


class SourceOut(ORM):
    id: int
    url: str
    source_type: SourceType
    domain: str | None
    is_official: bool
    http_status: int | None
    fetched_at: dt.datetime | None


class VacancyOut(ORM):
    id: int
    company_id: int
    company_name: str | None = None
    title: str
    location: str | None
    skills: list[str]
    experience_text: str | None
    experience_min_years: float | None
    experience_max_years: float | None
    salary_text: str | None
    application_url: str | None
    posted_date: dt.date | None
    status: VacancyStatus
    status_label: str
    match_score: float | None
    reviewed: bool
    is_archived: bool
    first_seen_at: dt.datetime
    last_verified_at: dt.datetime | None


class VacancyDetail(VacancyOut):
    description: str | None
    match_rationale: str | None
    matched_skills: list[str]
    evidence: list[dict]
    verification_notes: str | None
    advisor_notes: str | None
    sources: list[SourceOut]
