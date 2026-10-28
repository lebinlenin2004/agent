import datetime as dt

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.utils import utcnow
from app.db.base import Base, TimestampMixin, enum_col
from app.models.enums import RunStatus, VacancyStatus


class SearchConfiguration(TimestampMixin, Base):
    """Saved research presets. One global row (is_global) holds admin defaults."""
    __tablename__ = "search_configurations"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    is_global: Mapped[bool] = mapped_column(Boolean, default=False)
    target_roles: Mapped[list] = mapped_column(JSON, default=list)
    technology_stacks: Mapped[list] = mapped_column(JSON, default=list)
    locations: Mapped[list] = mapped_column(JSON, default=list)
    experience_levels: Mapped[list] = mapped_column(JSON, default=list)
    include_related_roles: Mapped[bool] = mapped_column(Boolean, default=True)
    sources: Mapped[list] = mapped_column(JSON, default=lambda: ["company_sites"])
    min_match_score: Mapped[float] = mapped_column(Float, default=40.0)


class SearchRun(TimestampMixin, Base):
    __tablename__ = "search_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    requested_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    status: Mapped[RunStatus] = mapped_column(enum_col(RunStatus), default=RunStatus.QUEUED, index=True)
    criteria: Mapped[dict] = mapped_column(JSON)  # immutable snapshot of what was asked
    company_ids: Mapped[list] = mapped_column(JSON)
    total_companies: Mapped[int] = mapped_column(Integer, default=0)
    processed_companies: Mapped[int] = mapped_column(Integer, default=0)
    vacancies_found: Mapped[int] = mapped_column(Integer, default=0)
    incomplete_companies: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)  # sanitized, user-safe
    retry_of_id: Mapped[int | None] = mapped_column(ForeignKey("search_runs.id", ondelete="SET NULL"))

    results = relationship("SearchResult", back_populates="run", cascade="all, delete-orphan")


class SearchResult(Base):
    """One row per vacancy found, or one company-level outcome row (vacancy_id NULL)."""
    __tablename__ = "search_results"
    __table_args__ = (UniqueConstraint("search_run_id", "company_id", "vacancy_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    search_run_id: Mapped[int] = mapped_column(ForeignKey("search_runs.id", ondelete="CASCADE"), index=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    vacancy_id: Mapped[int | None] = mapped_column(ForeignKey("vacancies.id", ondelete="SET NULL"), index=True)
    outcome: Mapped[VacancyStatus] = mapped_column(enum_col(VacancyStatus))
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    run = relationship("SearchRun", back_populates="results")
    company = relationship("Company")
    vacancy = relationship("Vacancy")
