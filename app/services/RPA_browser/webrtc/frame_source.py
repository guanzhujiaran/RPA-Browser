"""PageFrameSource - 页面帧源（多观看者并发直播）

一个 page = 一份帧源 = **唯一**的 screencast。

约束来源：CDP 的 `Page.startScreencast` 对同一 page 只能有一个会话
（`VideoFrameProducer._start_screencast()` 里那段 `already started` 恢复逻辑即为此），
因此帧源必须被同一页的**所有观看者共享**，不能每人一份。

帧源的采集档位取「所有未暂停观看者档位的**最大值**」：
- 有人要高清 → 按高清采集，其余人各自在自己的轨道里缩放下来；
- 全员低档 → 采集也降下来，不做无用的高分辨率编码。
详见 docs/rpa-多观看者并发直播计划书.md §2.3。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from loguru import logger

from app.models.runtime.webrtc_models import StreamQualityLevelEnum, WebRTCSessionConfig
from .video_frame_producer import FrameSlot, VideoFrameProducer

if TYPE_CHECKING:
    from playwright.async_api import Page

    from .viewer_stream import ViewerStream

#: 档位「清晰度」排序（数字越大越清晰）。StrEnum 无法直接比较大小，故显式映射。
_LEVEL_RANK: dict[StreamQualityLevelEnum, int] = {
    StreamQualityLevelEnum.ORIGINAL: 5,
    StreamQualityLevelEnum.ULTRA: 4,
    StreamQualityLevelEnum.HIGH: 3,
    StreamQualityLevelEnum.MEDIUM: 2,
    StreamQualityLevelEnum.LOW: 1,
}


class PageFrameSource:
    """页面帧源：一个 page 一份，向多个观看者广播帧。"""

    def __init__(self, page_index: int, page: "Page", config: WebRTCSessionConfig):
        self.page_index = page_index
        self.page = page
        self.config = config
        self.producer = VideoFrameProducer(page, config)
        self._viewer_ids: set[str] = set()
        self._started = False
        # 保证「加入/离开/切档」的仲裁串行执行，避免多个观看者同时切档时
        # 出现「先按 A 的档位重启、再按 B 的档位重启」的抖动
        self._lock = asyncio.Lock()

    # ── 查询 ──

    @property
    def viewer_ids(self) -> set[str]:
        """当前挂在帧源上的观看者 id 集合（副本）"""
        return set(self._viewer_ids)

    @property
    def viewer_count(self) -> int:
        return len(self._viewer_ids)

    @property
    def is_idle(self) -> bool:
        """是否已无观看者（可释放）"""
        return not self._viewer_ids

    @property
    def is_started(self) -> bool:
        return self._started

    # ── 观看者登记 ──

    def register_viewer(self, viewer_id: str) -> FrameSlot:
        """登记观看者并返回其专属订阅槽。

        Raises:
            ValueError: 同一 viewer_id 重复登记（调用方应先注销旧的）
        """
        if viewer_id in self._viewer_ids:
            raise ValueError(f"观看者已存在于该帧源内: {viewer_id}")
        self._viewer_ids.add(viewer_id)
        slot = self.producer.subscribe()
        logger.debug(
            f"观看者已挂到帧源: page_index={self.page_index} viewer={viewer_id} "
            f"(该页观看者数={len(self._viewer_ids)})"
        )
        return slot

    def unregister_viewer(self, viewer_id: str, slot: FrameSlot) -> None:
        """注销观看者（反订阅，不影响其他观看者）。"""
        self._viewer_ids.discard(viewer_id)
        self.producer.unsubscribe(slot)
        logger.debug(
            f"观看者已脱离帧源: page_index={self.page_index} viewer={viewer_id} "
            f"(该页观看者数={len(self._viewer_ids)})"
        )

    # ── 生命周期 ──

    async def ensure_started(self) -> None:
        """确保帧源在采集（幂等）。"""
        if self._started:
            return
        await self.producer.start()
        self._started = True
        logger.debug(
            f"帧源已启动: page_index={self.page_index} (观看者={len(self._viewer_ids)})"
        )

    async def stop(self) -> None:
        """停止采集并释放 screencast（幂等）。"""
        if not self._started:
            return
        await self.producer.stop()
        self._started = False
        logger.debug(f"帧源已停止: page_index={self.page_index}")

    # ── 档位 / 暂停仲裁 ──

    async def reconcile(self, viewers: list["ViewerStream"]) -> None:
        """按当前观看者集合重新仲裁帧源的采集档位与暂停态。

        由 `WebRTCStreamManager` 在**观看者加入 / 离开 / 切档 / 暂停 / 可见性变化**时调用。

        - 全员暂停 → 停 screencast（浏览器侧零 JPEG 编码），此时**不**调整档位参数；
        - 有人在看 → 先按最高需求调档（暂停态下只改参数、不重启），再确保采集在跑。

        两个 setter 都是幂等的，因此可以安全地高频调用。
        """
        async with self._lock:
            active = [v for v in viewers if not v.is_paused]
            if not active:
                await self.producer.set_paused(True)
                return

            highest = max(
                (v.effective_level for v in active),
                key=lambda level: _LEVEL_RANK[level],
            )
            # 顺序不能反：先调档（暂停态下仅记录参数），再恢复采集，
            # 这样 screencast 是用最新参数启动的，避免「先按旧档启动、再重启一次」
            await self.producer.set_level(highest)
            await self.producer.set_paused(False)


__all__ = ["PageFrameSource"]
