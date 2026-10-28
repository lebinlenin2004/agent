import math
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
    pages: int

    @classmethod
    def build(cls, items, total: int, page: int, page_size: int):
        return cls(items=items, total=total, page=page, page_size=page_size,
                   pages=max(1, math.ceil(total / page_size)))


class Message(BaseModel):
    message: str
