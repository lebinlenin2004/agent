import os

os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ["SEARCH_PROVIDER"] = "mock"
os.environ["LLM_PROVIDER"] = "mock"
os.environ["PER_DOMAIN_DELAY_SECONDS"] = "0"
os.environ["AUTH_RATE_LIMIT_PER_MINUTE"] = "1000"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.ratelimit import reset_rate_limits
from app.core.security import hash_password
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import User, UserRole


@pytest.fixture()
def engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture()
def db(session_factory):
    s = session_factory()
    yield s
    s.close()


@pytest.fixture()
def client(session_factory):
    reset_rate_limits()

    def _get_db():
        s = session_factory()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _get_db
    app.state.session_factory = session_factory
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def make_user(db, email, role):
    u = User(email=email, full_name=email.split("@")[0], hashed_password=hash_password("correct-horse-1"), role=role)
    db.add(u)
    db.commit()
    return u


def auth_headers(client, email, password="correct-horse-1"):
    r = client.post("/api/v1/auth/login", data={"username": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture()
def admin(client, db):
    make_user(db, "admin@example.org", UserRole.ADMIN)
    return auth_headers(client, "admin@example.org")


@pytest.fixture()
def advisor(client, db):
    make_user(db, "advisor@example.org", UserRole.ADVISOR)
    return auth_headers(client, "advisor@example.org")


@pytest.fixture()
def viewer(client, db):
    make_user(db, "viewer@example.org", UserRole.VIEWER)
    return auth_headers(client, "viewer@example.org")
