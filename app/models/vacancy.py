import datetime as dt

from sqlalchemy import (JSON, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, enum_col
from app.models.enums import SourceType, VacancyStatus


class Vacancy(TimestampMixin, Base):
    __tablename__ = "vacancies"
    __table_args__ = (Index("ix_vacancies_company_status", "company_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    location: Mapped[str | None] = mapped_column(String(255), index=True)
    skills: Mapped[list] = mapped_column(JSON, default=list)
    skills_index: Mapped[str] = mapped_column(Text, default="||")
    experience_text: Mapped[str | None] = mapped_column(String(200))
    experience_min_years: Mapped[float | None] = mapped_column(Float)
    experience_max_years: Mapped[float | None] = mapped_column(Float)
    salary_text: Mapped[str | None] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    application_url: Mapped[str | None] = mapped_column(String(2000))
    posted_date: Mapped[dt.date | None] = mapped_column(Date)

    # Duplicate guards: canonical posting URL and a company+title+location hash
    normalized_url: Mapped[str | None] = mapped_column(String(1000), unique=True)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)

    status: Mapped[VacancyStatus] = mapped_column(
        enum_col(VacancyStatus), default=VacancyStatus.FOUND_UNVERIFIED, index=True)
    match_score: Mapped[float | None] = mapped_column(Float, index=True)
    match_rationale: Mapped[str | None] = mapped_column(Text)
    matched_skills: Mapped[list] = mapped_column(JSON, default=list)
    evidence: Mapped[list] = mapped_column(JSON, default=list)  # verification signals
    last_verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    verification_notes: Mapped[str | None] = mapped_column(Text)

    reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    advisor_notes: Mapped[str | None] = mapped_column(Text)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    first_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    company = relationship("Company", back_populates="vacancies")
    sources = relationship("VacancySource", back_populates="vacancy", cascade="all, delete-orphan")


class VacancySource(Base):
    __tablename__ = "vacancy_sources"
    __table_args__ = (UniqueConstraint("vacancy_id", "url"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    vacancy_id: Mapped[int] = mapped_column(ForeignKey("vacancies.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(String(2000))
    source_type: Mapped[SourceType] = mapped_column(enum_col(SourceType))
    domain: Mapped[str | None] = mapped_column(String(255))
    is_official: Mapped[bool] = mapped_column(Boolean, default=False)
    http_status: Mapped[int | None] = mapped_column(Integer)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    fetched_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    excerpt: Mapped[str | None] = mapped_column(Text)

    vacancy = relationship("Vacancy", back_populates="sources")
