import datetime as dt

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class Company(TimestampMixin, Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    # website domain when known, otherwise "name|location": prevents duplicate records
    dedupe_key: Mapped[str] = mapped_column(String(400), unique=True)
    location: Mapped[str | None] = mapped_column(String(255), index=True)
    tech_stack: Mapped[list] = mapped_column(JSON, default=list)
    stack_index: Mapped[str] = mapped_column(Text, default="||")
    website: Mapped[str | None] = mapped_column(String(500))
    careers_url: Mapped[str | None] = mapped_column(String(1000))
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    last_searched_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    vacancies = relationship("Vacancy", back_populates="company", cascade="all, delete-orphan")
