"""
Runtime 模块 - 浏览器启动队列模型

定义「按系统内存准入」的浏览器启动排队相关枚举与数据模型：
内存不足时，启动请求进入两条队列（VIP 队列优先于普通用户队列）等待。
"""

from bili_common.models import StrEnumAutoDoc
from sqlmodel import Field, SQLModel


class LaunchQueueTypeEnum(StrEnumAutoDoc):
    """启动队列类型（VIP 队列优先于普通用户队列）"""

    VIP = "vip"  # 大会员队列，优先放行
    NORMAL = "normal"  # 普通用户队列


class LaunchQueueStateEnum(StrEnumAutoDoc):
    """排队请求的等待状态"""

    QUEUED = "queued"  # 仍在队列中等待内存
    LAUNCHING = "launching"  # 已放行，正在创建浏览器


class MemorySnapshot(SQLModel):
    """系统内存快照"""

    total_mb: int = Field(description="物理内存总量(MB)")
    available_mb: int = Field(description="当前可用内存(MB)")
    used_mb: int = Field(description="已用内存(MB)")
    used_percent: float = Field(description="内存使用率(%)")
    required_mb: int = Field(description="启动单个浏览器所需的最小可用内存(MB)")
    can_launch: bool = Field(description="当前可用内存是否满足启动条件")


class BrowserMemorySample(SQLModel):
    """单个浏览器会话的内存采样（按会话的 Chromium 用户数据目录聚合进程树）"""

    user_data_dir: str = Field(
        description="会话的 Chromium 用户数据目录（会话唯一标识）"
    )
    total_mb: float = Field(
        description="会话进程树内存合计(MB)：匿名页 + 共享匿名页（Pss_Anon+Pss_Shmem），不含可回收的文件映射页"
    )
    process_count: int = Field(description="会话关联的 Chromium 进程数")
    sampled_at: int = Field(description="采样时间戳")


class BrowserMemoryEstimatorStatus(SQLModel):
    """浏览器单实例内存占用的实测估算状态

    用真实进程占用（不可回收的匿名内存：Pss_Anon + Pss_Shmem）校准启动准入的
    单实例预留额度，替代写死的固定常量，避免并发启动把内存打爆。
    """

    enabled: bool = Field(description="是否启用实测估算")
    sample_count: int = Field(default=0, description="当前滑动窗口内的样本数")
    window: int = Field(default=0, description="滑动窗口长度")
    min_samples: int = Field(default=0, description="生效所需的最小样本数")
    active_instances: int = Field(
        default=0, description="最近一次扫描到的活跃浏览器实例数"
    )
    last_sample_mb: float | None = Field(
        default=None, description="最近一次采样的单实例占用(MB)"
    )
    average_mb: float | None = Field(
        default=None, description="窗口内单实例平均占用(MB)"
    )
    peak_mb: float | None = Field(default=None, description="单实例占用历史峰值(MB)")
    reserved_mb: int = Field(
        default=0, description="当前准入记账用的单实例预留额度(MB)"
    )
    configured_reserved_mb: int = Field(default=0, description="配置的基准预留额度(MB)")


class LaunchQueueStatus(SQLModel):
    """浏览器启动队列全局状态"""

    enabled: bool = Field(description="是否启用内存准入排队")
    vip_waiting: int = Field(default=0, description="VIP 队列等待数")
    normal_waiting: int = Field(default=0, description="普通队列等待数")
    launching: int = Field(default=0, description="已放行、正在启动的浏览器数")
    submitted_total: int = Field(default=0, description="累计受理的启动请求数")
    queued_total: int = Field(default=0, description="累计进入排队的启动请求数")
    timeout_total: int = Field(default=0, description="累计排队超时数")
    memory: MemorySnapshot = Field(description="当前系统内存快照")
    browser_memory: BrowserMemoryEstimatorStatus = Field(
        description="浏览器单实例内存实测估算（准入预留额度的来源）"
    )
    max_instances: int = Field(
        default=0, description="浏览器实例数上限（运行中+启动中）；0 表示仅按内存限制"
    )


class LaunchQueueEntryStatus(SQLModel):
    """单个会话的排队状态"""

    in_queue: bool = Field(
        default=False, description="是否处于启动队列中（含排队与启动中）"
    )
    state: LaunchQueueStateEnum | None = Field(default=None, description="排队状态")
    queue_type: LaunchQueueTypeEnum | None = Field(
        default=None, description="所在队列类型"
    )
    position: int | None = Field(default=None, description="在同队列中的位置（1 起）")
    waiting_seconds: int = Field(default=0, description="已等待时长(秒)")
    estimated_wait_seconds: int | None = Field(
        default=None,
        description=(
            "预计还需等待时长(秒)，按「前方人数 × 放行节奏」估算（见计划书 §5.17）；"
            "已放行时为 0；null 表示当前不在启动队列中"
        ),
    )
    estimate_reliable: bool = Field(
        default=False,
        description=(
            "估算是否可信：true=样本充足且当前名额已释放（只受冷却限制）；"
            "false=样本不足或内存长期未释放，数字偏乐观，仅作参考"
        ),
    )


class BrowserLaunchQueueWaitingItem(SQLModel):
    """排队 / 启动中的会话明细（管理端监管用）"""

    mid: int = Field(description="用户 mid")
    mid_str: str = Field(default="", description="用户 mid（字符串，避免精度丢失）")
    browser_id: int = Field(description="浏览器实例 ID")
    browser_id_str: str = Field(default="", description="浏览器实例 ID（字符串）")
    queue_type: LaunchQueueTypeEnum = Field(description="所在队列：vip / normal")
    state: LaunchQueueStateEnum = Field(
        description="状态：queued=排队等待，launching=已放行、正在启动"
    )
    position: int | None = Field(
        default=None, description="同队列中的排位（1 起）；launching 时为 null"
    )
    waiting_seconds: int = Field(default=0, description="已等待时长(秒)")


__all__ = [
    "LaunchQueueTypeEnum",
    "LaunchQueueStateEnum",
    "MemorySnapshot",
    "BrowserMemorySample",
    "BrowserMemoryEstimatorStatus",
    "LaunchQueueStatus",
    "LaunchQueueEntryStatus",
    "BrowserLaunchQueueWaitingItem",
]
