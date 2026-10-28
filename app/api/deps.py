import datetime as dt
from typing import Annotated

import jwt
from fastapi import Depends, Query
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.security import decode_access_token
from app.db.session import get_db
from app.models import User, UserRole

oauth2 = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")
DB = Annotated[Session, Depends(get_db)]


def get_current_user(token: Annotated[str, Depends(oauth2)], db: DB) -> User:
    try:
        payload = decode_access_token(token)
        user = db.get(User, int(payload["sub"]))
    except (jwt.PyJWTError, ValueError):
        raise UnauthorizedError("Your session is invalid or has expired. Please sign in again.")
    if not user or not user.is_active:
        raise UnauthorizedError("Your session is invalid or has expired. Please sign in again.")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_role(*roles: UserRole):
    def checker(user: CurrentUser) -> User:
        if user.role not in roles:
            raise ForbiddenError("You don't have permission to do this.")
        return user
    return checker


WriteUser = Annotated[User, Depends(require_role(UserRole.ADMIN, UserRole.ADVISOR))]
AdminUser = Annotated[User, Depends(require_role(UserRole.ADMIN))]


class PageParams:
    def __init__(self, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
        self.page, self.page_size = page, page_size

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


Paging = Annotated[PageParams, Depends()]


class VacancyFilters:
    """Shared by /vacancies, /reports and /exports so filters behave identically everywhere."""

    def __init__(
        self,
        company_id: int | None = None,
        q: str | None = Query(None, max_length=100, description="Title/company text search"),
        location: str | None = None,
        stack: str | None = Query(None, description="Technology, e.g. React"),
        min_experience: float | None = Query(None, ge=0),
        max_experience: float | None = Query(None, ge=0),
        status: list[str] | None = Query(None),
        min_score: float | None = Query(None, ge=0, le=100),
        max_score: float | None = Query(None, ge=0, le=100),
        date_from: dt.date | None = None,
        date_to: dt.date | None = None,
        reviewed: bool | None = None,
        include_archived: bool = False,
    ):
        self.company_id, self.q, self.location, self.stack = company_id, q, location, stack
        self.min_experience, self.max_experience = min_experience, max_experience
        self.status, self.min_score, self.max_score = status, min_score, max_score
        self.date_from, self.date_to = date_from, date_to
        self.reviewed, self.include_archived = reviewed, include_archived
