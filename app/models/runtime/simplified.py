"""
Runtime 模块 - 简化版模型

定义去除了 browser_id 字段的简化请求模型。
"""

from sqlmodel import SQLModel, Field

from app.models.runtime.control import (
    HeartbeatRequest,
    CreateSessionRequest,
    ManualOperationRequest,
    AutomationResumeRequest,
    LiveControlCommand,
    BrowserCleanupPolicy,
    EmptyRequest,
)


class SimplifiedHeartbeatRequest(HeartbeatRequest):
    """简化版心跳请求（不包含browser_id）"""
    pass


class SimplifiedCreateSessionRequest(CreateSessionRequest):
    """简化版创建会话请求（不包含browser_id）"""
    pass


class SimplifiedManualOperationRequest(ManualOperationRequest):
    """简化版人工操作请求（不包含browser_id）"""
    pass


class SimplifiedAutomationResumeRequest(AutomationResumeRequest):
    """简化版自动化恢复请求（不包含browser_id）"""
    pass


class SimplifiedLiveControlCommand(LiveControlCommand):
    """简化版实时控制命令（不包含browser_id）"""
    pass


class SimplifiedBrowserCleanupPolicyRequest(SQLModel):
    """简化版清理策略请求（不包含browser_id）"""
    policy: BrowserCleanupPolicy = Field(description="清理策略配置")


class SimplifiedScreenshotRequest(SQLModel):
    """简化版截图请求（不包含browser_id）"""
    quality: int = Field(default=80, ge=1, le=100, description="图片质量 (1-100)")
    full_page: bool = Field(default=True, description="是否截取整个页面")


class SimplifiedNavigateRequest(SQLModel):
    """简化版导航请求（不包含browser_id）"""
    url: str = Field(description="目标URL")


class SimplifiedJavaScriptExecuteRequest(SQLModel):
    """简化版JavaScript执行请求（不包含browser_id）"""
    code: str = Field(description="JavaScript代码")


class SimplifiedBrowserClickRequest(SQLModel):
    """简化版浏览器点击请求（不包含browser_id）"""
    x: float = Field(description="X坐标相对位置 (0.0-1.0)")
    y: float = Field(description="Y坐标相对位置 (0.0-1.0)")
    button: str = Field(default="left", description="鼠标按钮: left, middle, right")
    double: bool = Field(default=False, description="是否双击")
    wait_after: int = Field(default=0, description="点击后等待时间(毫秒)")


class SimplifiedJavaScriptExecuteWithParamsRequest(SQLModel):
    """简化版带参数的JavaScript执行请求（不包含browser_id）"""
    code: str = Field(description="JavaScript代码")
    timeout: int = Field(default=5000, description="执行超时时间(毫秒)")


SimplifiedExecuteJSRequest = SimplifiedJavaScriptExecuteWithParamsRequest


from app.models.runtime.operations import ExecuteJsResponse as ExecuteJSResponse


class SimplifiedForceReleaseRequest(SQLModel):
    """简化版强制释放浏览器请求（不包含browser_id）"""
    pass


class SimplifiedPausePluginsRequest(SQLModel):
    """简化版暂停插件请求（不包含browser_id）"""
    pass


__all__ = [
    "SimplifiedHeartbeatRequest",
    "SimplifiedCreateSessionRequest",
    "SimplifiedManualOperationRequest",
    "SimplifiedAutomationResumeRequest",
    "SimplifiedLiveControlCommand",
    "SimplifiedBrowserCleanupPolicyRequest",
    "SimplifiedScreenshotRequest",
    "SimplifiedNavigateRequest",
    "SimplifiedJavaScriptExecuteRequest",
    "SimplifiedBrowserClickRequest",
    "SimplifiedJavaScriptExecuteWithParamsRequest",
    "SimplifiedExecuteJSRequest",
    "ExecuteJSResponse",
    "SimplifiedForceReleaseRequest",
    "SimplifiedPausePluginsRequest",
    "SimplifiedEmptyRequest",
]
