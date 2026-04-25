"""
Simplified Models - 向后兼容模块

此文件保留用于向后兼容。
请使用 app.models.runtime.simplified 中的模型。
"""

from app.models.runtime.simplified import (
    SimplifiedHeartbeatRequest,
    SimplifiedCreateSessionRequest,
    SimplifiedManualOperationRequest,
    SimplifiedAutomationResumeRequest,
    SimplifiedLiveControlCommand,
    SimplifiedStartVideoStreamRequest,
    SimplifiedBrowserCleanupPolicyRequest,
    SimplifiedScreenshotRequest,
    SimplifiedNavigateRequest,
    SimplifiedJavaScriptExecuteRequest,
    SimplifiedBrowserClickRequest,
    SimplifiedJavaScriptExecuteWithParamsRequest,
    SimplifiedExecuteJSRequest,
    SimplifiedVideoStreamMjpegRequest,
    SimplifiedForceReleaseRequest,
    SimplifiedPausePluginsRequest,
    ExecuteJSResponse,
    EmptyRequest,
)

__all__ = [
    "SimplifiedHeartbeatRequest",
    "SimplifiedCreateSessionRequest",
    "SimplifiedManualOperationRequest",
    "SimplifiedAutomationResumeRequest",
    "SimplifiedLiveControlCommand",
    "SimplifiedStartVideoStreamRequest",
    "SimplifiedBrowserCleanupPolicyRequest",
    "SimplifiedScreenshotRequest",
    "SimplifiedNavigateRequest",
    "SimplifiedJavaScriptExecuteRequest",
    "SimplifiedBrowserClickRequest",
    "SimplifiedJavaScriptExecuteWithParamsRequest",
    "SimplifiedExecuteJSRequest",
    "SimplifiedVideoStreamMjpegRequest",
    "SimplifiedForceReleaseRequest",
    "SimplifiedPausePluginsRequest",
    "ExecuteJSResponse",
    "EmptyRequest",
]
