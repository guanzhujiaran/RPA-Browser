from sqlmodel import SQLModel, Field
from typing import Any


class RPAClickParams(SQLModel):
    """RPA点击操作参数"""
    selector: str  # CSS选择器
    timeout: int = Field(default=30000, description="超时时间(毫秒)")


class BrowserClickParams(SQLModel):
    """浏览器点击操作参数 - 支持相对坐标"""
    x: float = Field(..., ge=0, le=1, description="相对X坐标 (0-1)")
    y: float = Field(..., ge=0, le=1, description="相对Y坐标 (0-1)")
    button: str = Field(default="left", description="鼠标按钮: left, right, middle")
    double: bool = Field(default=False, description="是否双击")
    wait_after: int = Field(default=500, description="操作后等待时间(毫秒)")


class ClickResponse(SQLModel):
    """点击操作响应"""
    success: bool
    message: str
    coordinates: dict = Field(description="点击坐标信息")
    timestamp: int


class RPAFillParams(SQLModel):
    """RPA填充操作参数"""
    selector: str  # CSS选择器
    value: str  # 要填充的值
    timeout: int = Field(default=30000, description="超时时间(毫秒)")


class RPAScrollParams(SQLModel):
    """RPA滚动操作参数"""
    x: int = Field(default=0, description="水平滚动距离")
    y: int = Field(default=0, description="垂直滚动距离")
    behavior: str = Field(default="auto", description="滚动行为: auto, smooth")


class RPAScreenshotParams(SQLModel):
    """RPA截图操作参数"""
    full_page: bool = Field(default=False, description="是否全页截图")
    selector: str | None = Field(None, description="指定元素截图")
    type: str = Field(default="png", description="图片类型: png, jpeg")
    quality: int = Field(default=80, description="图片质量(仅jpeg)")


class RPAEvaluateParams(SQLModel):
    """RPA执行JavaScript参数"""
    script: str  # JavaScript代码
    args: list = Field(default_factory=list, description="传递给脚本的参数")


class RPAWaitParams(SQLModel):
    """RPA等待操作参数"""
    selector: str | None = Field(None, description="等待元素出现")
    timeout: int = Field(default=30000, description="超时时间(毫秒)")
    state: str = Field(default="visible", description="等待状态: visible, hidden, attached, detached")


class RPANavigateParams(SQLModel):
    """RPA导航操作参数"""
    url: str  # 目标URL
    wait_until: str = Field(default="load", description="等待条件: load, domcontentloaded, networkidle")
    timeout: int = Field(default=30000, description="超时时间(毫秒)")


class JavaScriptExecuteParams(SQLModel):
    """JavaScript执行参数"""
    code: str = Field(..., description="JavaScript代码")
    timeout: int = Field(default=30000, description="执行超时时间(毫秒)")
    await_result: bool = Field(default=True, description="是否等待异步结果")


class ExecuteJsResponse(SQLModel):
    """JavaScript执行响应"""
    success: bool
    result: Any = Field(description="执行结果")
    error: str | None = Field(None, description="错误信息")
    execution_time: int = Field(description="执行时间(毫秒)")


class SecurityCheckParams(SQLModel):
    """安全检查参数"""
    code: str = Field(..., description="待检查的JavaScript代码")
    strict_mode: bool = Field(default=True, description="严格模式")
    timeout: int = Field(default=5000, description="检查超时时间(毫秒)")


class SecurityRisk(SQLModel):
    """安全风险项"""
    type: str = Field(description="风险类型")
    level: str = Field(description="风险等级: low, medium, high")
    description: str = Field(description="风险描述")
    line: int | None = Field(None, description="风险行号")
    pattern: str = Field(description="匹配的模式")


class SecurityCheckResult(SQLModel):
    """安全检查结果"""
    level: str = Field(description="总体风险等级: low, medium, high")
    score: int = Field(..., ge=0, le=100, description="安全评分 (0-100)")
    risks: list[SecurityRisk] = Field(description="风险列表")
    allowed_operations: list[str] = Field(description="允许的操作")
    blocked_operations: list[str] = Field(description="禁止的操作")
    safe_to_execute: bool = Field(description="是否安全可执行")
    recommendations: list[str] = Field(description="安全建议")


class RPAResponse(SQLModel):
    """RPA操作响应"""
    success: bool
    data: dict | None = None
    error: str | None = None