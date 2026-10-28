from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select

from app.api.deps import DB, AdminUser, CurrentUser
from app.core.exceptions import NotFoundError
from app.core.ratelimit import auth_rate_limit
from app.core.security import create_access_token
from app.models import User
from app.schemas.user import Token, UserCreate, UserOut, UserRoleUpdate
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=201, dependencies=[Depends(auth_rate_limit)])
def register(data: UserCreate, db: DB):
    return auth_service.register(db, data)


@router.post("/login", response_model=Token, dependencies=[Depends(auth_rate_limit)])
def login(form: Annotated[OAuth2PasswordRequestForm, Depends()], db: DB):
    """OAuth2 password flow (Swagger 'Authorize' works). `username` is the email address."""
    user = auth_service.authenticate(db, form.username, form.password)
    return Token(access_token=create_access_token(user.id, user.role.value), user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser):
    return user


@router.get("/users", response_model=list[UserOut])
def list_users(_: AdminUser, db: DB):
    return db.scalars(select(User).order_by(User.email)).all()


@router.patch("/users/{user_id}/role", response_model=UserOut)
def set_role(user_id: int, body: UserRoleUpdate, _: AdminUser, db: DB):
    user = db.get(User, user_id)
    if not user:
        raise NotFoundError("User not found.")
    user.role = body.role
    db.commit()
    return user
