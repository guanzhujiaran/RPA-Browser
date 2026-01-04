"""
简化版模型文件
用于替代包含重复browser_id字段的模型

这些模型去除了browser_id字段，因为browser_id已经通过依赖注入获取
"""

from sqlmodel import SQLModel, Field
from .live_control_models import (
    HeartbeatRequest,
    CreateSessionRequest,
    ManualOperationRequest,
    AutomationResumeRequest,
    LiveControlCommand,
    VideoStreamParams,
    BrowserCleanupPolicy
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


class SimplifiedStartVideoStreamRequest(SQLModel):
    """简化版启动视频流请求（不包含browser_id）"""
    params: VideoStreamParams = Field(description="视频流参数")


class SimplifiedBrowserCleanupPolicyRequest(SQLModel):
    """简化版清理策略请求（不包含browser_id）"""
    policy: BrowserCleanupPolicy = Field(description="清理策略配置")


class SimplifiedScreenshotRequest(SQLModel):
    """简化版截图请求（不包含browser_id）"""
    quality: int = Field(default=80, ge=1, le=100, description="图片质量 (1-100)")
    full_page: bool = Field(default=True, description="是否截取整个页面（true）还是仅可视区域（false）")


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


class SimplifiedVideoStreamMjpegRequest(SQLModel):
    """简化版MJPEG视频流请求（不包含browser_id）"""
    pass


class SimplifiedForceReleaseRequest(SQLModel):
    """简化版强制释放浏览器请求（不包含browser_id）"""
    pass


class SimplifiedPausePluginsRequest(SQLModel):
    """简化版暂停插件请求（不包含browser_id）"""
    pass


class EmptyRequest(SQLModel):
    """空请求模型，用于不需要参数的接口"""
    pass