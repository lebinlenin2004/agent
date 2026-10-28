from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.exceptions import ConflictError, ForbiddenError, UnauthorizedError
from app.core.security import hash_password, verify_password
from app.models import User, UserRole
from app.schemas.user import UserCreate

_DUMMY_HASH = hash_password("not-a-real-password")  # equalises timing for unknown emails


def register(db: Session, data: UserCreate) -> User:
    if not get_settings().allow_registration:
        raise ForbiddenError("Registration is disabled. Ask an administrator for an account.")
    if db.scalar(select(User).where(User.email == data.email)):
        raise ConflictError("An account with this email already exists.")
    user = User(email=data.email, full_name=data.full_name.strip(), role=UserRole.ADVISOR,
                hashed_password=hash_password(data.password), preferences={})
    db.add(user)
    db.commit()
    return user


def authenticate(db: Session, email: str, password: str) -> User:
    user = db.scalar(select(User).where(User.email == email.lower().strip()))
    ok = verify_password(password, user.hashed_password if user else _DUMMY_HASH)
    if not user or not ok or not user.is_active:
        raise UnauthorizedError("Incorrect email or password.")
    return user
