"""
Runtime 模块 - 实时控制模型

定义浏览器实时控制相关的枚举、请求和响应模型。
"""

from sqlmodel import SQLModel, Field
from enum import Enum
from typing import Any, Dict, Optional
import time
from app.config import settings


class BrowserStatusEnum(str, Enum):
    """浏览器状态枚举"""

    RUNNING = "running"  # 正常运行中
    PAUSED = "paused"  # 人工操作中，自动任务暂停
    IDLE = "idle"  # 闲置状态
    STOPPED = "stopped"  # 已停止
    ERROR = "error"  # 错误状态


class OperationPriority(str, Enum):
    """操作优先级"""

    LOW = "low"  # 低优先级，可被中断
    NORMAL = "normal"  # 普通优先级
    HIGH = "high"  # 高优先级
    CRITICAL = "critical"  # 关键优先级，不可中断


class BrowserStatus(SQLModel):
    """浏览器状态信息"""

    mid: int
    browser_id: int
    status: BrowserStatusEnum = Field(default=BrowserStatusEnum.RUNNING)
    active_connections: int = Field(default=0, description="活跃连接数")
    last_activity: int
    last_heartbeat: int
    is_manual_mode: bool = Field(default=False, description="是否处于人工操作模式")
    current_operation_priority: OperationPriority = Field(
        default=OperationPriority.NORMAL
    )


class LiveControlCommand(SQLModel):
    """实时控制命令"""

    type: str
    params: dict
    timestamp: int
    priority: OperationPriority = Field(default=OperationPriority.NORMAL)
    require_manual_mode: bool = Field(default=False, description="是否需要手动模式")
    interrupt_automation: bool = Field(default=True, description="是否中断自动化任务")


class HeartbeatRequest(SQLModel):
    """心跳请求"""

    client_id: str= Field(..., description="客户端唯一 ID,前端自己生成来提供")
    timestamp: int
    page_id: str = Field(...,description="页面唯一 ID（用于更新 WebRTC 活跃时间）")


class HeartbeatResponse(SQLModel):
    """心跳响应"""

    success: bool
    server_timestamp: int
    next_heartbeat_interval: int
    status: str
    active_clients: int = Field(default=0, description="活跃客户端数量")


class ManualOperationRequest(SQLModel):
    """人工操作请求"""

    operation_type: str
    priority: OperationPriority = Field(default=OperationPriority.HIGH)
    reason: str | None = Field(None, description="操作原因")
    estimated_duration: int | None = Field(None, description="预计持续时间（秒）")


class AutomationResumeRequest(SQLModel):
    """自动化恢复请求"""

    force: bool = Field(default=False, description="是否强制恢复")
    reason: str | None = Field(None, description="恢复原因")


class BrowserCleanupPolicy(SQLModel):
    """浏览器清理策略"""

    max_idle_time: int = Field(default_factory=lambda: settings.browser_session_max_idle_time, description="最大闲置时间（秒）")
    max_no_heartbeat_time: int = Field(default_factory=lambda: settings.browser_session_max_no_heartbeat_time, description="最大无心跳时间（秒）")
    cleanup_interval: int = Field(default_factory=lambda: settings.browser_session_cleanup_interval, description="清理检查间隔（秒）")


class SessionLifecycleState(str, Enum):
    """会话生命周期状态"""

    INITIALIZING = "initializing"
    ACTIVE = "active"
    IDLE = "idle"
    SUSPENDING = "suspending"
    TERMINATING = "terminating"
    TERMINATED = "terminated"


class BrowserSessionStatus(SQLModel):
    """浏览器会话状态响应"""

    session_exists: bool = Field(description="会话是否存在")
    browser_running: bool = Field(description="浏览器是否正在运行")
    lifecycle_state: SessionLifecycleState = Field(description="会话生命周期状态")
    last_heartbeat: int = Field(description="最后心跳时间")
    active_connections: int = Field(default=0, description="活跃连接数")
    video_streaming: bool = Field(default=False, description="是否正在视频流（已废弃）")
    manual_mode: bool = Field(default=False, description="是否为手动模式")
    created_at: int = Field(description="会话创建时间")
    expires_at: int | None = Field(None, description="会话过期时间")


class CreateSessionRequest(SQLModel):
    """创建会话请求"""

    # 注意：auto_cleanup, cleanup_policy 和 expiration_time 已移至系统配置
    # 前端不再能控制这些选项，它们由管理员在系统配置中统一管理


class CreateSessionResponse(SQLModel):
    """创建会话响应"""

    success: bool
    session_id: str
    browser_started: bool
    created_at: int
    expires_at: int | None = Field(None, description="会话过期时间")
    message: str | None = Field(None, description="详细信息")


class CloseSessionResponse(SQLModel):
    """关闭会话响应"""

    success: bool = Field(description="关闭是否成功")
    session_id: str = Field(description="会话ID")
    browser_id: int = Field(description="浏览器实例ID")
    mid: int = Field(description="用户ID")
    closed_at: int = Field(description="关闭时间戳")
    message: str = Field(description="响应消息")

    @property
    def browser_id_str(self) -> str:
        return str(self.browser_id)

    @property
    def mid_str(self) -> str:
        return str(self.mid)


class BrowserIdParam(SQLModel):
    """包含browser_id的基础请求模型"""

    browser_id: str = Field(description="浏览器实例ID")



class ScreenshotRequest(SQLModel):
    """截图请求"""

    browser_id: str = Field(description="浏览器实例ID")
    quality: int = Field(default=80, ge=1, le=100, description="图片质量 (1-100)")


class BrowserStatusRequest(SQLModel):
    """浏览器状态请求"""

    browser_id: str = Field(description="浏览器实例ID")


class NavigateRequest(SQLModel):
    """导航请求"""

    url: str = Field(description="目标URL")


class JavaScriptExecuteRequest(SQLModel):
    """JavaScript执行请求"""

    browser_id: str = Field(description="浏览器实例ID")
    code: str = Field(description="JavaScript代码")


class BrowserClickRequest(SQLModel):
    """浏览器点击请求"""

    browser_id: str = Field(description="浏览器实例ID")
    x: float = Field(description="X坐标相对位置 (0.0-1.0)")
    y: float = Field(description="Y坐标相对位置 (0.0-1.0)")
    button: str = Field(default="left", description="鼠标按钮: left, middle, right")
    double: bool = Field(default=False, description="是否双击")
    wait_after: int = Field(default=0, description="点击后等待时间(毫秒)")


class JavaScriptExecuteWithParamsRequest(SQLModel):
    """带参数的JavaScript执行请求"""

    browser_id: str = Field(description="浏览器实例ID")
    code: str = Field(description="JavaScript代码")
    timeout: int = Field(default=5000, description="执行超时时间(毫秒)")


class ForceReleaseRequest(SQLModel):
    """强制释放浏览器请求"""

    browser_id: str = Field(description="浏览器实例ID")


class HeartbeatWithBrowserIdRequest(HeartbeatRequest):
    """包含browser_id的心跳请求"""

    browser_id: str = Field(description="浏览器实例ID")


class CreateSessionWithBrowserIdRequest(CreateSessionRequest):
    """包含browser_id的创建会话请求"""

    browser_id: str = Field(description="浏览器实例ID")


class ManualOperationWithBrowserIdRequest(ManualOperationRequest):
    """包含browser_id的人工操作请求"""

    browser_id: str = Field(description="浏览器实例ID")


class AutomationResumeWithBrowserIdRequest(AutomationResumeRequest):
    """包含browser_id的自动化恢复请求"""

    browser_id: str = Field(description="浏览器实例ID")


class BrowserCleanupPolicyRequest(SQLModel):
    """设置清理策略请求"""

    browser_id: str = Field(description="浏览器实例ID")
    policy: BrowserCleanupPolicy = Field(description="清理策略配置")


class LiveControlCommandWithBrowserId(LiveControlCommand):
    """包含browser_id的实时控制命令"""

    browser_id: str = Field(description="浏览器实例ID")



class BrowserOperationRequest(SQLModel):
    """浏览器操作请求"""

    browser_id: str = Field(description="浏览器实例ID")


# ============ 响应模型 ============

class ManualOperationResponse(SQLModel):
    """人工操作响应"""

    success: bool = Field(description="操作是否成功")
    operation_id: str = Field(description="操作ID")
    browser_id: int = Field(description="浏览器实例ID")
    operation_type: str = Field(description="操作类型")
    priority: str = Field(description="操作优先级")
    started_at: int = Field(description="开始时间戳")
    message: str = Field(description="响应消息")

    @property
    def browser_id_str(self) -> str:
        return str(self.browser_id)


class AutomationResumeResponse(SQLModel):
    """自动化恢复响应"""

    success: bool = Field(description="恢复是否成功")
    browser_id: int = Field(description="浏览器实例ID")
    resumed_at: int = Field(description="恢复时间戳")
    operation_id: str | None = Field(None, description="关联的操作ID")
    message: str = Field(description="响应消息")

    @property
    def browser_id_str(self) -> str:
        return str(self.browser_id)


class OperationStatusResponse(SQLModel):
    """操作状态响应"""

    browser_id: int = Field(description="浏览器实例ID")
    is_manual_mode: bool = Field(description="是否为手动操作模式")
    active_connections: int = Field(description="活跃连接数")
    last_activity: int = Field(description="最后活动时间")
    current_operation: dict = Field(description="当前操作信息")
    priority: str = Field(description="当前优先级")

    @property
    def browser_id_str(self) -> str:
        return str(self.browser_id)





class BrowserInfoResponse(SQLModel):
    """浏览器信息响应"""

    browser_id: int = Field(description="浏览器实例ID")
    pages: list = Field(description="页面列表")
    plugins: list = Field(description="插件状态")
    connections: int = Field(description="连接数")
    manual_operation: dict = Field(description="手动操作状态")
    session_info: dict = Field(description="会话信息")

    @property
    def browser_id_str(self) -> str:
        return str(self.browser_id)



class SystemStatisticsResponse(SQLModel):
    """系统统计响应"""

    total_sessions: int = Field(description="总会话数")
    active_sessions: int = Field(description="活跃会话数")
    idle_sessions: int = Field(description="闲置会话数")
    total_active_connections: int = Field(description="总活跃连接数")
    manual_mode_sessions: int = Field(description="手动模式会话数")
    video_streaming_sessions: int = Field(description="视频流会话数（已废弃）")
    uptime: int = Field(description="系统运行时间（秒）")
    timestamp: int = Field(description="统计时间戳")


class CleanupPolicyResponse(SQLModel):
    """清理策略响应"""

    success: bool = Field(description="设置是否成功")
    browser_id: int = Field(description="浏览器实例ID")
    message: str = Field(description="响应消息")
    policy: BrowserCleanupPolicy = Field(description="设置的清理策略")

    @property
    def browser_id_str(self) -> str:
        return str(self.browser_id)


class SystemCleanupResponse(SQLModel):
    """系统清理响应"""

    success: bool = Field(description="清理是否成功")
    message: str = Field(description="响应消息")
    cleaned_sessions: int = Field(description="清理的会话数")
    cleaned_resources: int = Field(description="清理的资源数")
    statistics: SystemStatisticsResponse = Field(description="清理后的统计信息")


class ForceReleaseResponse(SQLModel):
    """强制释放响应"""

    success: bool = Field(description="释放是否成功")
    browser_id: int = Field(description="浏览器实例ID")
    message: str = Field(description="响应消息")
    released_at: int = Field(description="释放时间戳")

    @property
    def browser_id_str(self) -> str:
        return str(self.browser_id)


class SystemHealthCheckResponse(SQLModel):
    """系统健康检查响应"""

    status: str = Field(description="整体状态: healthy, degraded, unhealthy")
    timestamp: int = Field(description="检查时间戳")
    checks: dict = Field(description="各组件检查结果")
    uptime: int | None = Field(None, description="系统运行时间（秒）")
    error: str | None = Field(None, description="错误信息（如果有）")


class EmptyRequest(SQLModel):
    """空请求模型，用于不需要参数的请求"""

    pass


# LiveService 返回的具体模型


class BrowserInfoData(SQLModel):
    """浏览器信息数据"""

    browser_context: dict = Field(description="浏览器上下文信息")
    plugins: dict = Field(description="插件信息")
    session: dict = Field(description="会话信息")



class ManualOperationResult(SQLModel):
    """手动操作结果"""

    success: bool = Field(description="是否成功")
    message: str = Field(description="操作消息")
    status: str = Field(description="操作状态")
    priority: str = Field(description="优先级")
    start_time: int = Field(description="开始时间")


class AutomationResult(SQLModel):
    """自动化恢复结果"""

    success: bool = Field(description="是否成功")
    message: str = Field(description="操作消息")
    status: str = Field(description="操作状态")
    resume_time: int = Field(description="恢复时间")


class OperationStatusData(SQLModel):
    """操作状态数据"""

    status: str = Field(description="状态")
    is_manual_mode: bool = Field(description="是否手动模式")
    current_priority: str = Field(description="当前优先级")
    active_connections: int = Field(description="活跃连接数")
    last_activity: int = Field(description="最后活动时间")
    last_heartbeat: int = Field(description="最后心跳时间")
    manual_operation_duration: int = Field(description="手动操作持续时间")
    heartbeat_clients: list = Field(description="心跳客户端列表")





class SessionStatisticsData(SQLModel):
    """会话统计数据"""

    total_sessions: int = Field(description="总会话数")
    status_distribution: Dict[str, int] = Field(
        default_factory=dict, description="状态分布"
    )
    manual_mode_sessions: int = Field(description="手动模式会话数")
    total_active_connections: int = Field(description="总活跃连接数")
    total_heartbeat_clients: int = Field(description="总心跳客户端数")
    session_timeout: int = Field(description="会话超时时间")
    heartbeat_interval: int = Field(description="心跳间隔")
    cleanup_interval: int = Field(description="清理间隔")


class CreateSessionData(SQLModel):
    """创建会话数据"""

    success: bool = Field(description="是否成功")
    session_id: str = Field(description="会话ID")
    browser_started: bool = Field(description="浏览器是否启动")
    created_at: int = Field(description="创建时间")
    expires_at: int | None = Field(None, description="过期时间")
    message: str | None = Field(None, description="消息")
    error: str | None = Field(None, description="错误信息")


class BrowserSessionStatusData(SQLModel):
    """浏览器会话状态数据"""

    session_exists: bool = Field(description="会话是否存在")
    browser_running: bool = Field(description="浏览器是否运行")
    lifecycle_state: SessionLifecycleState = Field(description="生命周期状态")
    last_heartbeat: int = Field(description="最后心跳时间")
    active_connections: int = Field(description="活跃连接数")
    video_streaming: bool = Field(description="是否视频流中")
    manual_mode: bool = Field(description="是否手动模式")
    created_at: int = Field(description="创建时间")
    expires_at: int | None = Field(None, description="过期时间")
    status: str = Field(description="状态")
    cleanup_policy: BrowserCleanupPolicy = Field(default_factory=BrowserCleanupPolicy, description="清理策略")
    message: str = Field(description="状态消息")
    screen_width: int = Field(description="屏幕宽度")
    screen_height: int = Field(description="屏幕高度")
    viewport_width: int = Field(description="视口宽度")
    viewport_height: int = Field(description="视口高度")

class JavaScriptExecutionResult(SQLModel):
    """JavaScript执行结果"""

    success: bool = Field(description="执行是否成功")
    result: Any = Field(None, description="执行结果")
    error: str | None = Field(None, description="错误信息")
    execution_time: int = Field(description="执行时间(毫秒)")
    risks: list | None = Field(None, description="安全风险列表")


class ModelInfo(SQLModel):
    """模型信息"""

    status: str = Field(description="模型状态")
    device: str = Field(description="运行设备")
    model_dir: str = Field(description="模型目录")


class ChineseClickPredictionDetail(SQLModel):
    """中文点击预测详情"""

    coordinates: list = Field(description="预测坐标列表")
    bounding_boxes: list = Field(description="检测到的边界框")
    matches: list = Field(description="匹配结果")
    ans_boxes: list = Field(description="答案框")
    question_boxes: list = Field(description="问题框")
    debug_info: dict = Field(description="调试信息")


__all__ = [
    "BrowserStatusEnum",
    "OperationPriority",
    "BrowserStatus",
    "LiveControlCommand",
    "HeartbeatRequest",
    "HeartbeatResponse",
    "ManualOperationRequest",
    "AutomationResumeRequest",
    "BrowserCleanupPolicy",
    "SessionLifecycleState",
    "BrowserSessionStatus",
    "CreateSessionRequest",
    "CreateSessionResponse",
    "CloseSessionResponse",
    "BrowserIdParam",
    "ScreenshotRequest",
    "BrowserStatusRequest",
    "NavigateRequest",
    "JavaScriptExecuteRequest",
    "BrowserClickRequest",
    "JavaScriptExecuteWithParamsRequest",
    "ForceReleaseRequest",
    "HeartbeatWithBrowserIdRequest",
    "CreateSessionWithBrowserIdRequest",
    "ManualOperationWithBrowserIdRequest",
    "AutomationResumeWithBrowserIdRequest",
    "BrowserCleanupPolicyRequest",
    "LiveControlCommandWithBrowserId",
    "BrowserOperationRequest",
    "ManualOperationResponse",
    "AutomationResumeResponse",
    "OperationStatusResponse",
    "BrowserInfoResponse",
    "SystemStatisticsResponse",
    "CleanupPolicyResponse",
    "SystemCleanupResponse",
    "ForceReleaseResponse",
    "SystemHealthCheckResponse",
    "EmptyRequest",
    "BrowserInfoData",
    "ManualOperationResult",
    "AutomationResult",
    "OperationStatusData",
    "SessionStatisticsData",
    "CreateSessionData",
    "BrowserSessionStatusData",
    "JavaScriptExecutionResult",
    "ModelInfo",
    "ChineseClickPredictionDetail",
]
