"""
WebRTC 视频流核心模型

定义 WebRTC 视频流相关的枚举、数据类和配置模型。
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import time


class WebRTCStreamState(Enum):
    """WebRTC 视频流状态枚举"""
    INITIALIZING = "initializing"  # 初始化中
    ACTIVE = "active"  # 活跃状态
    CLOSED = "closed"  # 已关闭
    ERROR = "error"  # 错误状态


@dataclass
class WebRTCStreamInfo:
    """WebRTC 视频流信息"""
    stream_key: str  # 流的唯一标识符，格式: {mid}:{browser_id}:{page_index}
    page_index: int  # 页面索引
    state: WebRTCStreamState = WebRTCStreamState.INITIALIZING  # 当前状态
    created_at: float = field(default_factory=time.time)  # 创建时间戳
    last_activity: float = field(default_factory=time.time)  # 最后活动时间戳
    
    @property
    def age_seconds(self) -> float:
        """获取流的存活时长（秒）"""
        return time.time() - self.created_at
    
    @property
    def idle_seconds(self) -> float:
        """获取闲置时长（秒）"""
        return time.time() - self.last_activity


@dataclass
class WebRTCSessionConfig:
    """WebRTC 会话配置"""
    quality: int = 80  # JPEG 图像质量 (0-100)
    max_fps: int = 30  # 最大帧率
    idle_timeout: int = 300  # 闲置超时时间（秒），默认5分钟
    frame_queue_size: int = 10  # 帧队列大小（丢旧保新策略）
    
    def __post_init__(self):
        """验证配置参数的有效性"""
        if not 0 <= self.quality <= 100:
            raise ValueError(f"Quality must be between 0 and 100, got {self.quality}")
        if self.max_fps <= 0:
            raise ValueError(f"Max FPS must be positive, got {self.max_fps}")
        if self.idle_timeout <= 0:
            raise ValueError(f"Idle timeout must be positive, got {self.idle_timeout}")
        if self.frame_queue_size <= 0:
            raise ValueError(f"Frame queue size must be positive, got {self.frame_queue_size}")


__all__ = [
    "WebRTCStreamState",
    "WebRTCStreamInfo",
    "WebRTCSessionConfig",
]
