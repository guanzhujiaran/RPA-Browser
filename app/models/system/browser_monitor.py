"""
System 模块 - 浏览器监管（管理端）请求 / 响应模型

供审核员查看「运行中的浏览器实例」并处置（停止会话 / 通知 / 封号）：

- 列表：`BrowserMonitorItem`（会话 + 指纹 + 时间 + 标签页概览）
- 详情：`BrowserMonitorPageItem`（标签页只读，不提供任何写操作）
- 处置：`BrowserMonitorStopRequest`（强制停止会话）

口径：指纹 / 浏览器实例是用户私有资源，不对外公开；监管仅为治理目的，
审核员只读查看，不能代替用户操作浏览器。
"""

from sqlmodel import SQLModel, Field


class BrowserMonitorPageItem(SQLModel):
    """监管用标签页信息（只读）"""

    index: int = Field(description="页面索引（从 0 开始）")
    url: str = Field(default="", description="页面 URL")
    title: str = Field(default="", description="页面标题")


class BrowserMonitorItem(SQLModel):
    """监管列表项：运行中浏览器实例概览"""

    mid: int = Field(description="所属用户 mid")
    mid_str: str = Field(default="", description="所属用户 mid（字符串，避免精度丢失）")
    browser_id: int = Field(description="浏览器实例 ID")
    browser_id_str: str = Field(default="", description="浏览器实例 ID（字符串）")
    custom_name: str | None = Field(default=None, description="用户自定义名称，未命名时为 None")
    platform: str | None = Field(default=None, description="指纹操作系统平台")
    browser: str | None = Field(default=None, description="指纹浏览器类型")
    started_at: int = Field(default=0, description="会话启动时间（秒级时间戳）")
    last_activity_at: int = Field(default=0, description="最后操作时间（秒级时间戳）")
    page_count: int = Field(default=0, description="标签页数量")
    active_page_url: str = Field(default="", description="首个标签页 URL（概览）")
    active_page_title: str = Field(default="", description="首个标签页标题（概览）")
    webrtc_active_streams: int = Field(default=0, description="活跃直播流数量")
    is_closed: bool = Field(default=False, description="浏览器是否已关闭")


class BrowserMonitorListRequest(SQLModel):
    """监管列表请求"""

    mid: int | str | None = Field(default=None, description="按用户 mid 过滤")
    browser_id: int | str | None = Field(default=None, description="按浏览器实例 ID 过滤")
    page: int = Field(default=1, ge=1, description="页码")
    per_page: int = Field(default=20, ge=1, le=100, description="每页条数")


class BrowserMonitorListResponse(SQLModel):
    """监管列表响应"""

    total: int = Field(default=0, description="总条数（过滤后）")
    page: int = Field(default=1, description="当前页码")
    per_page: int = Field(default=20, description="每页条数")
    items: list[BrowserMonitorItem] = Field(default_factory=list, description="列表数据")


class BrowserMonitorPagesRequest(SQLModel):
    """监管标签页列表请求"""

    mid: int | str = Field(description="用户 mid")
    browser_id: int | str = Field(description="浏览器实例 ID")


class BrowserMonitorPagesResponse(SQLModel):
    """监管标签页列表响应（只读）"""

    mid: int = Field(description="用户 mid")
    mid_str: str = Field(default="", description="用户 mid（字符串）")
    browser_id: int = Field(description="浏览器实例 ID")
    browser_id_str: str = Field(default="", description="浏览器实例 ID（字符串）")
    total: int = Field(default=0, description="标签页数量")
    pages: list[BrowserMonitorPageItem] = Field(default_factory=list, description="标签页列表")


class BrowserMonitorStopRequest(SQLModel):
    """强制停止浏览器会话请求"""

    mid: int | str = Field(description="用户 mid")
    browser_id: int | str = Field(description="浏览器实例 ID")
    reason: str = Field(default="", description="处置原因（写入审计日志）")


class BrowserMonitorStopResponse(SQLModel):
    """强制停止浏览器会话响应"""

    mid: int = Field(description="用户 mid")
    browser_id: int = Field(description="浏览器实例 ID")
    closed: bool = Field(default=False, description="是否已关闭")
    message: str = Field(default="", description="结果说明")


__all__ = [
    "BrowserMonitorPageItem",
    "BrowserMonitorItem",
    "BrowserMonitorListRequest",
    "BrowserMonitorListResponse",
    "BrowserMonitorPagesRequest",
    "BrowserMonitorPagesResponse",
    "BrowserMonitorStopRequest",
    "BrowserMonitorStopResponse",
]
