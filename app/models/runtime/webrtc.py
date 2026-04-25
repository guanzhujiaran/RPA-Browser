"""
Runtime 模块 - WebRTC 模型

定义 WebRTC 相关的请求和响应模型。
"""

from sqlmodel import SQLModel, Field
from typing import List, Dict, Any


class WebRTCOfferResponse(SQLModel):
    """WebRTC Offer 响应"""

    sdp: str = Field(description="SDP offer")
    type: str = Field(default="offer", description="SDP 类型")


class WebRTCAnswerRequest(SQLModel):
    """WebRTC Answer 请求"""

    browser_id_str: str = Field(alias="browser_id", description="浏览器ID")
    sdp: str = Field(description="SDP answer")


class WebRTCAnswerResponse(SQLModel):
    """WebRTC Answer 响应"""

    success: bool = Field(description="是否成功")


class WebRTCIceCandidateRequest(SQLModel):
    """WebRTC ICE Candidate 请求"""

    candidate: Dict[str, Any] = Field(description="ICE candidate 数据")


class WebRTCIceCandidateResponse(SQLModel):
    """WebRTC ICE Candidate 响应"""

    success: bool = Field(description="是否成功")


class WebRTCGetIceCandidatesResponse(SQLModel):
    """获取 ICE Candidates 响应"""

    candidates: List[Dict[str, Any]] = Field(description="ICE candidates 列表")
    ice_gathering_state: str = Field(description="ICE gathering 状态")


class WebRTCConnectionStatusResponse(SQLModel):
    """WebRTC 连接状态响应"""

    active: bool = Field(description="连接是否活跃")
    ice_connection_state: str = Field(description="ICE 连接状态")
    signaling_state: str = Field(description="信令状态")


class WebRTCCloseConnectionResponse(SQLModel):
    """关闭 WebRTC 连接响应"""

    success: bool = Field(description="是否成功")


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
