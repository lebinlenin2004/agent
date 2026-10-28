import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.enums import RunStatus, VacancyStatus
from app.schemas.common import ORM

ExperienceLevel = Literal["entry", "junior", "mid", "senior"]
SourceName = Literal["company_sites", "job_portals", "search_api"]


def _clean(items: list[str]) -> list[str]:
    out = []
    for i in items:
        i = " ".join(i.split())[:80]
        if i and i not in out:
            out.append(i)
    return out


class Criteria(BaseModel):
    target_roles: list[str] = Field(default_factory=list, max_length=15)
    technology_stacks: list[str] = Field(default_factory=list, max_length=20)
    locations: list[str] = Field(default_factory=list, max_length=15)
    experience_levels: list[ExperienceLevel] = Field(default_factory=list)
    include_related_roles: bool = True
    sources: list[SourceName] = Field(default_factory=lambda: ["company_sites"])
    min_match_score: float = Field(40.0, ge=0, le=100)

    _c = field_validator("target_roles", "technology_stacks", "locations")(_clean)

    @model_validator(mode="after")
    def _need_something(self):
        if not self.target_roles and not self.technology_stacks:
            raise ValueError("Provide at least one target role or technology stack")
        if not self.sources:
            raise ValueError("Select at least one source")
        return self


class SearchCreate(Criteria):
    company_ids: list[int] = Field(min_length=1, max_length=500)


class SearchRunOut(ORM):
    id: int
    status: RunStatus
    total_companies: int
    processed_companies: int
    vacancies_found: int
    incomplete_companies: int
    progress_percent: int = 0
    criteria: dict
    started_at: dt.datetime | None
    finished_at: dt.datetime | None
    error_message: str | None
    retry_of_id: int | None
    created_at: dt.datetime


class SearchResultOut(BaseModel):
    id: int
    company_id: int
    company_name: str
    vacancy_id: int | None
    vacancy_title: str | None
    match_score: float | None
    outcome: VacancyStatus
    outcome_label: str
    evidence: list[dict]
    error: str | None


class SearchConfigIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    target_roles: list[str] = []
    technology_stacks: list[str] = []
    locations: list[str] = []
    experience_levels: list[ExperienceLevel] = []
    include_related_roles: bool = True
    sources: list[SourceName] = Field(default_factory=lambda: ["company_sites"])
    min_match_score: float = Field(40.0, ge=0, le=100)


class SearchConfigOut(SearchConfigIn, ORM):
    id: int
    is_global: bool
