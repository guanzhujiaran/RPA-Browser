"""
WebRTC Models - 向后兼容模块

此文件保留用于向后兼容。
请使用 app.models.runtime.webrtc 中的模型。
"""

from app.models.runtime.webrtc import (
    WebRTCOfferResponse,
    WebRTCAnswerRequest,
    WebRTCAnswerResponse,
    WebRTCIceCandidateRequest,
    WebRTCIceCandidateResponse,
    WebRTCGetIceCandidatesResponse,
    WebRTCConnectionStatusResponse,
    WebRTCCloseConnectionResponse,
)

__all__ = [
    "WebRTCOfferResponse",
    "WebRTCAnswerRequest",
    "WebRTCAnswerResponse",
    "WebRTCIceCandidateRequest",
    "WebRTCIceCandidateResponse",
    "WebRTCGetIceCandidatesResponse",
    "WebRTCConnectionStatusResponse",
    "WebRTCCloseConnectionResponse",
]
