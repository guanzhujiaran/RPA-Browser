"""
Common 模块 - 通用响应和异常模型

提供统一的响应格式、响应码、异常定义等基础设施。
"""

from app.models.common.response_code import ResponseCode
from app.models.common.response_msg import ResponseMsg
from app.models.common.response import (
    StandardResponse,
    success_response,
    error_response,
    custom_response,
    DataT,
)
from app.models.common.exceptions.base_exception import (
    BaseException,
    BrowserNotifyConfNotFoundException,
    BrowserIdIsNoneExeception,
    BrowserIdNotBeloneToUserException,
    NotLoggedInException,
    InvalidUIDException,
    InvalidMidFormatException,
    PluginIdIsNoneException,
    PluginIdNotBelongToUserException,
    BrowserNotStartedException,
    VideoStreamInitFailedException,
    GetBrowserSessionFailedException,
    BrowserFingerprintNotFoundException,
    FingerprintLimitExceededException,
)
from app.models.common.depends import (
    AuthInfo,
    VerifyBrowserDependsReq,
    VerifyFingerprintDependsReq,
    BrowserReqInfo,
    BrowserReqAuthInfo,
    VerifyPluginDependsReq,
    BrowserPluginReqInfo,
)

__all__ = [
    # 响应码
    "ResponseCode",
    # 响应消息
    "ResponseMsg",
    # 响应模型
    "StandardResponse",
    "success_response",
    "error_response",
    "custom_response",
    "DataT",
    # 异常
    "BaseException",
    "BrowserNotifyConfNotFoundException",
    "BrowserIdIsNoneExeception",
    "BrowserIdNotBeloneToUserException",
    "NotLoggedInException",
    "InvalidUIDException",
    "InvalidMidFormatException",
    "PluginIdIsNoneException",
    "PluginIdNotBelongToUserException",
    "BrowserNotStartedException",
    "VideoStreamInitFailedException",
    "GetBrowserSessionFailedException",
    "BrowserFingerprintNotFoundException",
    "FingerprintLimitExceededException",
    # 依赖注入模型
    "AuthInfo",
    "VerifyBrowserDependsReq",
    "VerifyFingerprintDependsReq",
    "BrowserReqInfo",
    "BrowserReqAuthInfo",
    "VerifyPluginDependsReq",
    "BrowserPluginReqInfo",
]
