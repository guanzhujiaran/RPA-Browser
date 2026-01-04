from pydantic import computed_field
from datetime import datetime
from typing import Generic

from sqlmodel import SQLModel, Field

from app.models.response import DataT


class BaseSQLModel(SQLModel):
    created_at: datetime = Field(default_factory=datetime.now, nullable=False)
    updated_at: datetime = Field(
        default_factory=datetime.now,
        nullable=False,
        sa_column_kwargs={"onupdate": datetime.now},
    )


class BasePaginationReq(SQLModel):
    page: int = Field(default=1, nullable=False)
    per_page: int = Field(default=10, nullable=False)


class BasePaginationResp(SQLModel, Generic[DataT]):
    page: int = Field(default=1, nullable=False)
    per_page: int = Field(default=10, nullable=False)
    total: int = Field(default=0, nullable=False)
    items: list[DataT] = Field(default=[], nullable=False)

    @computed_field
    @property
    def pages(self) -> int:
        return self.total // self.per_page + (
            1 if self.total % self.per_page > 0 else 0
        )

    @computed_field
    @property
    def has_next(self) -> bool:
        return self.page < self.pages

    @computed_field
    @property
    def has_prev(self) -> bool:
        return self.page > 1

    @computed_field
    @property
    def next_page(self) -> int:
        return self.page + 1 if self.has_next else self.page

    @computed_field
    @property
    def prev_page(self) -> int:
        return self.page - 1 if self.has_prev else self.page


__all__ = ["BaseSQLModel"]
