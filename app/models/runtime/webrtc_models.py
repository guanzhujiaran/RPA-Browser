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


@dataclass
class ViewerStreamInfo:
    """观看者连接信息（一个观看者 = 一条 PeerConnection）。

    多观看者并发直播下，暂停 / 档位 / 可见性都是**观看者级**状态，
    与帧源（每页一份、唯一 screencast）分离。
    详见 docs/rpa-多观看者并发直播计划书.md。
    """

    viewer_id: str  # 前端生成的观看者标识（每次建立连接一枚）
    stream_key: str  # 信令键，格式: {mid}:{browser_id}:page_{i}:{viewer_id}
    page_index: int  # 订阅的页面索引
    state: WebRTCStreamState = WebRTCStreamState.INITIALIZING  # 该连接的流状态
    paused: bool = False  # 本端是否暂停出帧（不影响其他观看者）
    level: str = ""  # 本端用户档位
    effective_level: str = ""  # 本端生效档位（不可见降档后可能低于用户档位）
    last_activity: float = field(default_factory=time.time)  # 最后心跳时间
    connected_at: float = field(default_factory=time.time)  # 接入时间
    # ── 身份信息（供浏览器归属者判断「是谁在看」）──
    client_ip: str = ""  # 客户端 IP（网关解析 nginx 的 X-Real-IP / XFF 后注入）
    client_device: str = ""  # 设备描述，如「Windows · Chrome 126」
    client_device_type: str = ""  # 设备类型稳定码：desktop / mobile / tablet
    client_browser_version: str = ""  # 浏览器大版本，如 126（结构化，供前端单独展示）
    client_ip_region: str = ""  # IP 属地，如「浙江 杭州」（be-message GeoIP RPC 解析）
    client_ip_isp: str = (
        ""  # IP 运营商（ASN 组织名，英文，如 China Unicom Shanghai network）
    )
    is_admin: bool = False  # 监管管理员观看：对归属者**完全隐藏**，见计划书 §2.7

    @property
    def idle_seconds(self) -> float:
        """距最后一次心跳的时长（秒）"""
        return time.time() - self.last_activity

    @property
    def watch_seconds(self) -> int:
        """已观看时长（秒）—— 断开 / 回收日志展示用"""
        return int(time.time() - self.connected_at)

    @property
    def client_summary(self) -> str:
        """客户端信息单行摘要（日志用）

        字段与前端「观看者信息」面板对齐
        （设备 · 设备类型 · 属地 · 运营商 · IP · 接入时间），并补上生效档位、暂停态与监管标记：
        前者说明「在看什么画质」，后者是运维排查用的 —— 监管观看对归属者仍完全隐藏，
        但日志里要能区分出来（计划书 §2.7）。

        设备类型打印**稳定码**（`desktop` / `mobile` / `tablet`）、缺失项统一打印 `-`
        （「未知 X」这类中文文案只在前端 i18n 出，日志保持语言中立）；
        刻意不打原始 User-Agent（上百字符且高度重复），展示口径统一走设备串。
        """
        parts = [
            self.client_device or "-",
            self.client_device_type or "-",
            self.client_ip_region or "-",
            self.client_ip_isp or "-",
            self.client_ip or "-",
            f"接入={time.strftime('%H:%M:%S', time.localtime(self.connected_at))}",
            f"档位={self.effective_level or '?'}",
        ]
        if self.paused:
            parts.append("已暂停")
        if self.is_admin:
            parts.append("监管观看")
        return " · ".join(parts)


class StreamQualityLevelEnum(StrEnumAutoDoc):
    """WebRTC 流清晰度档位（越靠后越省资源，见计划书 §5.18）"""

    ORIGINAL = "original"  # 原画：JPEG 100 / 30fps / 页面真实视口分辨率（不缩放、不降质，最清晰但最耗带宽）
    ULTRA = "ultra"  # 超清：JPEG 90 / 30fps / 1920×1080（抬高浏览器侧上限，突破默认自适应 800×800）
    HIGH = "high"  # 高清：JPEG 80 / 30fps / 分辨率交给浏览器自适应
    MEDIUM = "medium"  # 标清：JPEG 65 / 15fps / 960×540
    LOW = "low"  # 流畅：JPEG 50 / 5fps / 640×360


@dataclass(frozen=True)
class StreamQualityParams:
    """单个清晰度档位的 screencast 参数"""

    level: StreamQualityLevelEnum
    quality: int  # JPEG 质量 (0-100)
    max_fps: int  # 最大帧率
    size: Optional[ScreencastFrameSize]  # 浏览器侧分辨率上限（None = 交给浏览器自适应）
    native_size: bool = False  # True = 按页面真实视口尺寸采集（原画：不做任何缩放）

    @property
    def frame_interval(self) -> float:
        """最小帧间隔（秒）"""
        return 1.0 / self.max_fps


class VideoFrameProducerStats(SQLModel):
    """视频帧生产者出帧统计快照

    丢帧率 = dropped_frames / (dropped_frames + emitted_frames)，
    用于校验降档（限帧 / 降质 / 降分辨率）是否真正生效。
    """

    emitted_frames: int = Field(0, description="已出帧数（解码成功并交给 WebRTC 的帧）")
    dropped_frames: int = Field(
        0, description="丢弃帧数（限帧、队列落后、解码前合并积压）"
    )
    drop_rate: float = Field(0.0, description="丢帧率（0-1）")
    queue_size: int = Field(0, description="当前帧队列积压数")
    degraded: bool = Field(
        False, description="是否因自动原因（不可见 / 闲置）处于降档态"
    )
    paused: bool = Field(False, description="是否处于用户主动暂停态（暂停时不出帧）")
    level: str = Field(
        "", description="当前生效的清晰度档位（original / ultra / high / medium / low）"
    )
    quality: int = Field(0, description="当前 screencast JPEG 质量（0-100）")
    frame_interval: float = Field(0.0, description="当前最小帧间隔（秒）")


class StreamQualitySnapshot(SQLModel):
    """观看者清晰度档位快照（见计划书 §5.18）

    多观看者并发直播下，档位与暂停均为**观看者级**（见
    docs/rpa-多观看者并发直播计划书.md），因此快照带 `viewer_id` 标明归属，
    避免调用方把它当成会话级状态。
    """

    viewer_id: str = Field(
        "", description="该快照归属的观看者 id（空表示未指定观看者）"
    )
    level: StreamQualityLevelEnum = Field(description="用户档位")
    effective_level: StreamQualityLevelEnum = Field(
        description="当前生效档位（被自动降档时低于用户档位）"
    )
    degraded: bool = Field(
        False, description="是否因自动原因（页面不可见 / 会话闲置）被降档"
    )
    paused: bool = Field(
        False, description="是否处于用户主动暂停态（暂停期间不发送任何视频帧）"
    )


@dataclass
class WebRTCSessionConfig:
    """WebRTC 会话配置（按清晰度档位提供 screencast 参数，见计划书 §5.18）"""

    idle_timeout: int = 300  # 闲置超时时间（秒），默认5分钟
    frame_queue_size: int = 3  # 帧队列大小（丢旧保新策略，够缓冲即可）

    # ── 各档位参数 ──
    # original：原画——JPEG 质量拉满（100）并按页面真实视口尺寸采集，不做任何缩放 / 降质
    original_quality: int = 100
    original_max_fps: int = 30
    # ultra：超清——把浏览器侧分辨率上限抬到 1080p（默认自适应仅 800×800），观感最清晰
    ultra_quality: int = 90
    ultra_max_fps: int = 30
    ultra_frame_max_width: int = 1920
    ultra_frame_max_height: int = 1080
    # high：高清（默认档）——不设分辨率上限，交给浏览器自适应（viewport 等比缩放进 800×800）
    high_quality: int = 80
    high_max_fps: int = 30
    high_frame_max_width: Optional[int] = None
    high_frame_max_height: Optional[int] = None
    # medium：标清——质量与帧率居中，并在浏览器侧下调分辨率
    medium_quality: int = 65
    medium_max_fps: int = 15
    medium_frame_max_width: int = 960
    medium_frame_max_height: int = 540
    # low：流畅——与改造前的「闲置降级档」参数完全一致，保证行为不变
    low_quality: int = 50
    low_max_fps: int = 5
    low_frame_max_width: int = 640
    low_frame_max_height: int = 360

    def __post_init__(self):
        """验证配置参数的有效性"""
        if self.idle_timeout <= 0:
            raise ValueError(f"Idle timeout must be positive, got {self.idle_timeout}")
        if self.frame_queue_size <= 0:
            raise ValueError(
                f"Frame queue size must be positive, got {self.frame_queue_size}"
            )
        # 逐档校验：任一档参数非法都应在构造期暴露，而不是等到切档时才失败
        for level in StreamQualityLevelEnum:
            params = self.params_for(level)
            if not 0 <= params.quality <= 100:
                raise ValueError(
                    f"{level.value} quality must be between 0 and 100, got {params.quality}"
                )
            if params.max_fps <= 0:
                raise ValueError(
                    f"{level.value} max fps must be positive, got {params.max_fps}"
                )
            if params.size and (
                params.size["width"] <= 0 or params.size["height"] <= 0
            ):
                raise ValueError(
                    f"{level.value} frame size must be positive, got {params.size}"
                )

    def params_for(self, level: StreamQualityLevelEnum) -> StreamQualityParams:
        """返回指定档位的 screencast 参数"""
        if level is StreamQualityLevelEnum.ORIGINAL:
            # 原画：不设 size 上限，改由采集侧按页面真实视口尺寸启动 screencast
            return StreamQualityParams(
                level=level,
                quality=self.original_quality,
                max_fps=self.original_max_fps,
                size=None,
                native_size=True,
            )
        if level is StreamQualityLevelEnum.ULTRA:
            return StreamQualityParams(
                level=level,
                quality=self.ultra_quality,
                max_fps=self.ultra_max_fps,
                size=self._size(
                    self.ultra_frame_max_width, self.ultra_frame_max_height
                ),
            )
        if level is StreamQualityLevelEnum.HIGH:
            return StreamQualityParams(
                level=level,
                quality=self.high_quality,
                max_fps=self.high_max_fps,
                size=self._size(self.high_frame_max_width, self.high_frame_max_height),
            )
        if level is StreamQualityLevelEnum.MEDIUM:
            return StreamQualityParams(
                level=level,
                quality=self.medium_quality,
                max_fps=self.medium_max_fps,
                size=self._size(
                    self.medium_frame_max_width, self.medium_frame_max_height
                ),
            )
        return StreamQualityParams(
            level=level,
            quality=self.low_quality,
            max_fps=self.low_max_fps,
            size=self._size(self.low_frame_max_width, self.low_frame_max_height),
        )

    @staticmethod
    def _size(
        width: Optional[int], height: Optional[int]
    ) -> Optional[ScreencastFrameSize]:
        """宽高齐全才生成 size，否则交给浏览器自适应"""
        if width and height:
            return {"width": width, "height": height}
        return None


__all__ = [
    "WebRTCStreamState",
    "WebRTCStreamInfo",
    "ViewerStreamInfo",
    "StreamQualityLevelEnum",
    "StreamQualityParams",
    "StreamQualitySnapshot",
    "WebRTCSessionConfig",
    "VideoFrameProducerStats",
    "ScreencastFrameSize",
    "ScreencastFrameData",
]
