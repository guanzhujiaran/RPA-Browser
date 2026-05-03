"""
浏览器安全相关模型
"""
from sqlmodel import SQLModel, Field


class SecurityCheckResult(SQLModel):
    """URL 安全检查结果"""
    allowed: bool = Field(description="是否允许访问")
    reason: str = Field(default="", description="拒绝原因或警告信息")
