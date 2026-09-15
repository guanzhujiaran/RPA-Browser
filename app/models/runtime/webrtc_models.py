"""
WebRTC 视频流核心模型

定义 WebRTC 视频流相关的枚举、数据类和配置模型。
"""
from bili_common.models import StrEnumAutoDoc
from sqlmodel import SQLModel, Field

from dataclasses import dataclass, field
from typing import Optional, TypedDict
import time


class ScreencastFrameSize(TypedDict):
    """screencast size 参数（结构等价于 patchright 的 ScreencastSize）"""
    width: int
    height: int


class ScreencastFrameData(TypedDict):
    """screencast on_frame 回调载荷（结构等价于 patchright 的 ScreencastFrame）"""
    data: bytes
    timestamp: float
    viewportWidth: int
    viewportHeight: int


class WebRTCStreamState(StrEnumAutoDoc):
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


class VideoFrameProducerStats(SQLModel):
    """视频帧生产者出帧统计快照

    丢帧率 = dropped_frames / (dropped_frames + emitted_frames)，
    用于校验闲置降级（限帧 / 降质 / 降分辨率）是否真正生效。
    """

    emitted_frames: int = Field(0, description="已出帧数（解码成功并交给 WebRTC 的帧）")
    dropped_frames: int = Field(
        0, description="丢弃帧数（限帧、队列落后、解码前合并积压）"
    )
    drop_rate: float = Field(0.0, description="丢帧率（0-1）")
    queue_size: int = Field(0, description="当前帧队列积压数")
    degraded: bool = Field(False, description="是否处于闲置降级态")
    quality: int = Field(0, description="当前 screencast JPEG 质量（0-100）")
    frame_interval: float = Field(0.0, description="当前最小帧间隔（秒）")


@dataclass
class WebRTCSessionConfig:
    """WebRTC 会话配置"""
    quality: int = 80  # JPEG 图像质量 (0-100)
    max_fps: int = 30  # 最大帧率
    idle_timeout: int = 300  # 闲置超时时间（秒），默认5分钟
    frame_queue_size: int = 3  # 帧队列大小（丢旧保新策略，够缓冲即可）
    degrade_quality: int = 50  # 闲置降级后的 JPEG 质量 (0-100)
    degrade_max_fps: int = 5  # 闲置降级后的最大帧率
    # 帧最大分辨率（None = 沿用浏览器默认：viewport 等比缩放进 800×800）
    frame_max_width: Optional[int] = None
    frame_max_height: Optional[int] = None
    # 降级态在浏览器侧降分辨率，JPEG 编码 / 传输 / 解码三端同时降载
    degrade_frame_max_width: int = 640
    degrade_frame_max_height: int = 360

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
        if not 0 <= self.degrade_quality <= 100:
            raise ValueError(f"Degrade quality must be between 0 and 100, got {self.degrade_quality}")
        if self.degrade_max_fps <= 0:
            raise ValueError(f"Degrade max fps must be positive, got {self.degrade_max_fps}")
        for name, value in (
            ("frame_max_width", self.frame_max_width),
            ("frame_max_height", self.frame_max_height),
            ("degrade_frame_max_width", self.degrade_frame_max_width),
            ("degrade_frame_max_height", self.degrade_frame_max_height),
        ):
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be positive, got {value}")

    @property
    def frame_interval(self) -> float:
        """正常态最小帧间隔（秒）"""
        return 1.0 / self.max_fps

    @property
    def degrade_frame_interval(self) -> float:
        """降级态最小帧间隔（秒）"""
        return 1.0 / self.degrade_max_fps

    def screencast_size(self, degraded: bool) -> Optional[ScreencastFrameSize]:
        """按当前档位生成 screencast 的 size 参数（None = 交给浏览器自适应）"""
        if degraded:
            return {
                "width": self.degrade_frame_max_width,
                "height": self.degrade_frame_max_height,
            }
        if self.frame_max_width and self.frame_max_height:
            return {"width": self.frame_max_width, "height": self.frame_max_height}
        return None


__all__ = [
    "WebRTCStreamState",
    "WebRTCStreamInfo",
    "WebRTCSessionConfig",
    "VideoFrameProducerStats",
    "ScreencastFrameSize",
    "ScreencastFrameData",
]
