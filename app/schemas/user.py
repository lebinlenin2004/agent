import datetime as dt
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.models.enums import UserRole
from app.schemas.common import ORM


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=10)

    @field_validator("password")
    @classmethod
    def _bcrypt_limit(cls, v: str) -> str:
        if len(v.encode()) > 72:
            raise ValueError("Password must be 72 bytes or fewer")
        return v

    @field_validator("email")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.lower()


class UserOut(ORM):
    id: int
    email: str
    full_name: str
    role: UserRole
    is_active: bool
    created_at: dt.datetime


class UserRoleUpdate(BaseModel):
    role: UserRole


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class Preferences(BaseModel):
    default_page_size: int = Field(20, ge=5, le=100)
    theme: Literal["light", "dark", "system"] = "light"
    notify_search_complete: bool = True
    notify_recheck_changes: bool = True
    notify_email: bool = False
