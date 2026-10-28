from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import DB, AdminUser, CurrentUser, WriteUser
from app.core.exceptions import NotFoundError
from app.models import SearchConfiguration
from app.schemas.common import Message
from app.schemas.search import SearchConfigIn, SearchConfigOut
from app.schemas.user import Preferences

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/preferences", response_model=Preferences)
def get_preferences(user: CurrentUser):
    return Preferences(**(user.preferences or {}))


@router.put("/preferences", response_model=Preferences)
def put_preferences(data: Preferences, user: CurrentUser, db: DB):
    user.preferences = data.model_dump()
    db.commit()
    return data


@router.get("/search-configurations", response_model=list[SearchConfigOut])
def list_configs(user: CurrentUser, db: DB):
    """Your saved presets plus the organisation default (if an admin set one)."""
    stmt = select(SearchConfiguration).where(
        (SearchConfiguration.user_id == user.id) | (SearchConfiguration.is_global.is_(True))).order_by(SearchConfiguration.name)
    return db.scalars(stmt).all()


@router.post("/search-configurations", response_model=SearchConfigOut, status_code=201)
def create_config(data: SearchConfigIn, user: WriteUser, db: DB):
    cfg = SearchConfiguration(user_id=user.id, is_global=False, **data.model_dump())
    db.add(cfg)
    db.commit()
    return cfg


def _own(db, user, config_id: int) -> SearchConfiguration:
    cfg = db.get(SearchConfiguration, config_id)
    if not cfg or cfg.is_global or cfg.user_id != user.id:
        raise NotFoundError("Saved search not found.")
    return cfg


@router.put("/search-configurations/{config_id}", response_model=SearchConfigOut)
def update_config(config_id: int, data: SearchConfigIn, user: WriteUser, db: DB):
    cfg = _own(db, user, config_id)
    for k, v in data.model_dump().items():
        setattr(cfg, k, v)
    db.commit()
    return cfg


@router.delete("/search-configurations/{config_id}", response_model=Message)
def delete_config(config_id: int, user: WriteUser, db: DB):
    db.delete(_own(db, user, config_id))
    db.commit()
    return Message(message="Saved search deleted.")


@router.get("/research", response_model=SearchConfigIn)
def get_research_defaults(_: CurrentUser, db: DB):
    cfg = db.scalar(select(SearchConfiguration).where(SearchConfiguration.is_global.is_(True)))
    return SearchConfigIn.model_validate(cfg, from_attributes=True) if cfg else SearchConfigIn(name="Organisation default")


@router.put("/research", response_model=SearchConfigIn)
def put_research_defaults(data: SearchConfigIn, _: AdminUser, db: DB):
    """Organisation-wide research defaults. Administrators only."""
    cfg = db.scalar(select(SearchConfiguration).where(SearchConfiguration.is_global.is_(True)))
    if not cfg:
        cfg = SearchConfiguration(is_global=True, user_id=None, **data.model_dump())
        db.add(cfg)
    else:
        for k, v in data.model_dump().items():
            setattr(cfg, k, v)
    db.commit()
    return data
