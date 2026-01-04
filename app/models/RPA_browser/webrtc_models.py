from sqlmodel import SQLModel, Field
from typing import List, Dict, Any


class WebRTCOfferRequest(SQLModel):
    """WebRTC Offer 请求"""
    browser_id_str: str = Field(alias="browser_id", description="浏览器ID")


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
    browser_id_str: str = Field(alias="browser_id", description="浏览器ID")
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


class WebRTCCloseConnectionRequest(SQLModel):
    """关闭 WebRTC 连接请求"""
    browser_id_str: str = Field(alias="browser_id", description="浏览器ID")


class WebRTCCloseConnectionResponse(SQLModel):
    """关闭 WebRTC 连接响应"""
    success: bool = Field(description="是否成功")