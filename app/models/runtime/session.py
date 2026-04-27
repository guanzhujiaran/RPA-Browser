"""
Runtime 模块 - 会话管理模型

定义浏览器会话相关的参数和响应模型。
"""

from sqlmodel import SQLModel, Field
from pydantic import computed_field

class BrowserSessionGetParams(SQLModel):
    mid: int
    browser_id: int


class BrowserSessionCreateParams(BrowserSessionGetParams):
    headless: bool = False


class BrowserSessionAllRemoveParams(BrowserSessionGetParams):
    force_close: bool = False


class BrowserSessionRemoveParams(BrowserSessionGetParams):
    force_close: bool = False


class SessionCreateParams(BrowserSessionCreateParams):
    mid: int
    browser_id: int
    @computed_field
    @property
    def mid_str(self) -> str:
        return str(self.mid) if self.mid else ""


class SessionCloseResponse(SQLModel):
    """会话关闭响应数据类"""

    mid: int
    browser_id: int
    is_closed: bool
    feedback: str

    @computed_field
    @property
    def mid_str(self) -> str:
        return str(self.mid) if self.mid else ""

    @computed_field
    @property
    def browser_id_str(self) -> str:
        return str(self.browser_id) if self.browser_id else ""


class SessionAllCloseResponse(SQLModel):
    items: list[SessionCloseResponse] = Field(default_factory=list)

    @computed_field
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
