import datetime as dt

from pydantic import BaseModel, Field, field_validator

from app.ai.sanitize import clean_field


class ExtractedJob(BaseModel):
    is_job_posting: bool = True
    title: str = Field(min_length=1, max_length=300)
    location: str | None = None
    skills: list[str] = Field(default_factory=list)
    experience_text: str | None = None
    experience_min_years: float | None = Field(None, ge=0, le=60)
    experience_max_years: float | None = Field(None, ge=0, le=60)
    salary_text: str | None = None
    description: str | None = None
    application_url: str | None = None
    posted_date: dt.date | None = None

    @field_validator("title", mode="before")
    @classmethod
    def _title(cls, v):
        return clean_field(v, 300) or ""

    @field_validator("location", "experience_text", "salary_text", mode="before")
    @classmethod
    def _short(cls, v):
        return clean_field(v, 200)

    @field_validator("description", mode="before")
    @classmethod
    def _desc(cls, v):
        return clean_field(v, 8000)

    @field_validator("skills", mode="before")
    @classmethod
    def _skills(cls, v):
        out = []
        for s in (v or [])[:40]:
            s = clean_field(s, 60)
            if s and s.lower() not in [x.lower() for x in out]:
                out.append(s)
        return out

    @field_validator("application_url", mode="before")
    @classmethod
    def _url(cls, v):
        v = clean_field(v, 2000)
        return v if v and v.lower().startswith(("http://", "https://")) else None

    @field_validator("posted_date", mode="before")
    @classmethod
    def _date(cls, v):
        if isinstance(v, str):
            try:
                return dt.date.fromisoformat(v[:10])
            except ValueError:
                return None
        return v


class MatchResult(BaseModel):
    score: float = Field(ge=0, le=100)
    matched_skills: list[str] = []
    rationale: str = ""
    method: str = "rules"
    notes: list[str] = []
