import uuid

from sqlmodel import SQLModel, Field


class BrowserSessionBaseParams(SQLModel):
    browser_token: uuid.UUID


class BrowserSessionGetParams(SQLModel):
    browser_id: int


class BrowserSessionCreateParams(BrowserSessionGetParams):
    headless: bool = True


class BrowserSessionAllRemoveParams(BrowserSessionGetParams):
    force_close: bool = False


class BrowserSessionRemoveParams(BrowserSessionBaseParams, BrowserSessionGetParams):
    force_close: bool = False


class SessionCreateParams(BrowserSessionCreateParams, BrowserSessionBaseParams):
    ...


class SessionCloseResponse(SQLModel):
    """会话关闭响应数据类"""
    browser_token: uuid.UUID
    browser_id: int
    is_closed: bool
    feedback: str


class SessionAllCloseResponse(SQLModel):
    items: list[SessionCloseResponse] = Field(default_factory=list)

    @property
    def closed_count(self) -> int:
        return len([i for i in self.items if i.is_closed])


__all__ = [
    "BrowserSessionBaseParams",
    "BrowserSessionCreateParams",
    "SessionCreateParams",
    "SessionCloseResponse",
    "SessionAllCloseResponse",
    "BrowserSessionGetParams",
    "BrowserSessionAllRemoveParams",
    "BrowserSessionRemoveParams",
]
