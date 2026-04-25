"""
RPA Operation Models - 向后兼容模块

此文件保留用于向后兼容。
请使用 app.models.runtime.operations 中的模型。
"""

from app.models.runtime.operations import (
    RPAClickParams,
    BrowserClickParams,
    ClickResponse,
    RPAFillParams,
    RPAScrollParams,
    RPAEvaluateParams,
    RPAScreenshotParams,
    RPAWaitParams,
    RPANavigateParams,
    JavaScriptExecuteParams,
    ExecuteJsResponse,
    SecurityCheckParams,
    SecurityRisk,
    SecurityCheckResult,
    RPAResponse,
)

__all__ = [
    "RPAClickParams",
    "BrowserClickParams",
    "ClickResponse",
    "RPAFillParams",
    "RPAScrollParams",
    "RPAEvaluateParams",
    "RPAScreenshotParams",
    "RPAWaitParams",
    "RPANavigateParams",
    "JavaScriptExecuteParams",
    "ExecuteJsResponse",
    "SecurityCheckParams",
    "SecurityRisk",
    "SecurityCheckResult",
    "RPAResponse",
]
