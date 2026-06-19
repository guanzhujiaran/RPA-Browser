"""
WebRTCStreamManager - WebRTC 流管理器（无循环引用 + 高效数据结构）

设计原则：
1. 使用 weakref 引用 session，避免 session ↔ manager 循环引用
2. OrderedDict 维护 LRU 淘汰顺序，最近使用的流在末尾
3. 双向索引：_streams_by_index + _streams_by_key，实现 O(1) 双向查找
4. 定期清理基于 LRU 顺序，低开销闲置检测
"""

import asyncio
import weakref
import time
from collections import OrderedDict
from typing import Dict, Optional
from loguru import logger

from app.config import settings
from app.models.runtime.webrtc_models import WebRTCSessionConfig
from .stream_session import WebRTCStreamSession
from app.scheduler_manager import scheduler_manager_ist


class WebRTCStreamManager:
    """
    WebRTC 流管理器

    作为 WebRTCEnabledSession 的内建能力，始终可用，无需调用 enable_webrtc()。

    数据结构：
    ┌─────────────────────────────────────────────┐
    │  _streams_by_index: OrderedDict[int, Session]│  ← LRU 有序主索引
    │  _streams_by_key:   Dict[str, Session]       │  ← stream_key 辅助索引
    │  _session_ref:      weakref.ref[Session]     │  ← 弱引用打破循环
    └─────────────────────────────────────────────┘

    查找算法：
    - 按 page_index 查找: O(1) 直接哈希
    - 按 stream_key 查找: O(1) 直接哈希
    - 闲置淘汰: O(k) 遍历 OrderedDict 前部，k 为超时流数量

    淘汰策略：
    - 新流插入到 OrderedDict 末尾（最近使用）
    - 清理时从前端扫描，越靠前越可能闲置超时
    - 访问时 move_to_end 标记为活跃
    """

    def __init__(self, session):
        """
        初始化流管理器

        Args:
            session: WebRTCEnabledSession 实例（以弱引用持有，打破循环引用）
        """
        self._session_ref = weakref.ref(session)

        # 主索引：OrderedDict 维护 LRU 淘汰顺序（最近活跃的在末尾）
        self._streams_by_index: OrderedDict[int, WebRTCStreamSession] = OrderedDict()
        # 辅助索引：stream_key → stream 的 O(1) 映射
        self._streams_by_key: Dict[str, WebRTCStreamSession] = {}

        # 注册定期清理任务（基于配置的 cleanup_interval）
        mid = getattr(session.playwright_instance, 'mid', 'unknown')
        bid = getattr(session.playwright_instance, 'browser_id', 'unknown')
        scheduler_manager_ist.add_interval_job(
            func=self._cleanup_idle_streams,
            seconds=0,
            minutes=10,
            hours=0,
            id=f"{mid}_{bid}_webrtc_cleanup",
        )

    # ── session 访问（弱引用解引用） ──

    @property
    def session(self):
        """获取 session 引用，若 session 已被回收返回 None"""
        return self._session_ref()

    # ── 核心流操作 ──

    async def start_stream(self, page_index: int) -> WebRTCStreamSession:
        """
        启动指定页面的 WebRTC 视频流

        创建全新的流实例，使用双向索引注册，自动淘汰同页面的旧流。

        Args:
            page_index: 页面索引（从 0 开始）

        Returns:
            WebRTCStreamSession: 视频流会话实例

        Raises:
            RuntimeError: session 已被回收
            IndexError: page_index 越界
        """
        session = self.session
        if session is None:
            raise RuntimeError("Session 已被回收，无法创建流")

        pages = session.all_pages
        if page_index >= len(pages):
            raise IndexError(
                f"页面索引 {page_index} 超出范围 (共 {len(pages)} 个页面)"
            )

        page = pages[page_index]

        # 如果该 page_index 已有旧流，先淘汰
        if page_index in self._streams_by_index:
            old_stream = self._streams_by_index[page_index]
            logger.info(f"淘汰 page_index={page_index} 的旧流，创建新流")
            await self._evict_stream(page_index, old_stream)

        # 构造 stream_key
        mid = session.playwright_instance.mid
        browser_id = session.playwright_instance.browser_id
        stream_key = f"{mid}:{browser_id}:page_{page_index}"

        config = WebRTCSessionConfig(
            quality=80,
            idle_timeout=settings.browser_webrtc_idle_timeout,
        )

        # 创建并启动流
        stream = WebRTCStreamSession(stream_key, page, config, page_index)
        await stream.start()

        # 双向索引注册（新流插入 OrderedDict 末尾 = 最新）
        self._streams_by_index[page_index] = stream
        self._streams_by_key[stream_key] = stream

        logger.info(
            f"WebRTC 流已创建: {stream_key} "
            f"(page_index={page_index}, 总流数={len(self._streams_by_index)})"
        )
        return stream

    def get_stream(
        self, *, page_index: int = None, stream_key: str = None
    ) -> Optional[WebRTCStreamSession]:
        """
        O(1) 查找流 —— 支持按 page_index 或 stream_key

        Args:
            page_index: 页面索引
            stream_key: 流唯一键

        Returns:
            WebRTCStreamSession 或 None
        """
        if stream_key is not None:
            stream = self._streams_by_key.get(stream_key)
            if stream is not None:
                self._touch_lru(stream)
            return stream
        if page_index is not None:
            stream = self._streams_by_index.get(page_index)
            if stream is not None:
                self._touch_lru(stream)
            return stream
        raise ValueError("必须提供 page_index 或 stream_key 之一")

    async def close_stream(
        self, *, page_index: int = None, stream_key: str = None
    ):
        """
        关闭指定流（O(1) 查找 + 自动清理双索引）

        Args:
            page_index: 页面索引
            stream_key: 流唯一键
        """
        stream = None
        if stream_key:
            stream = self._streams_by_key.get(stream_key)
        elif page_index is not None:
            stream = self._streams_by_index.get(page_index)

        if stream is None:
            logger.debug(f"尝试关闭不存在的流: page_index={page_index}, stream_key={stream_key}")
            return

        await self._evict_stream(stream.page_index, stream)

    async def close_all_streams(self):
        """并行关闭所有流，清空双索引"""
        if not self._streams_by_index:
            return

        count = len(self._streams_by_index)
        logger.info(f"关闭所有 WebRTC 流 ({count} 个)")

        tasks = [s.close() for s in list(self._streams_by_index.values())]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        self._streams_by_index.clear()
        self._streams_by_key.clear()
        logger.info("所有 WebRTC 流已关闭")

    # ── LRU 淘汰算法 ──

    def _touch_lru(self, stream: WebRTCStreamSession):
        """
        将流标记为「最近使用」—— 移动到 OrderedDict 末尾

        时间复杂度: O(1)
        """
        if stream.page_index in self._streams_by_index:
            self._streams_by_index.move_to_end(stream.page_index)

    async def _evict_stream(self, page_index: int, stream: WebRTCStreamSession):
        """
        淘汰流 —— 关闭并从双索引中移除

        时间复杂度: O(1)
        """
        try:
            await stream.close()
        except Exception as e:
            logger.error(f"关闭 WebRTC 流时出错 page_index={page_index}: {e}")
        finally:
            self._streams_by_index.pop(page_index, None)
            self._streams_by_key.pop(stream.stream_key, None)

    async def _cleanup_idle_streams(self):
        """
        基于 LRU 顺序的闲置流清理

        算法：
        - 按 OrderedDict 顺序（从旧到新）扫描流
        - 闲置超过 idle_timeout 的流被淘汰
        - 因为越靠前的流越久未被访问，大概率最先超时
        - 一旦遇到未超时流，后续流理论上也不会超时（LRU 保证）
        """
        session = self.session
        if session is None:
            # session 已回收，无法访问页面信息，保守清理所有流
            await self.close_all_streams()
            return

        current_time = time.time()
        to_evict: list[tuple[int, WebRTCStreamSession]] = []

        for page_index, stream in self._streams_by_index.items():
            idle_time = stream.idle_duration
            if idle_time > stream.config.idle_timeout:
                to_evict.append((page_index, stream))
            # 注意：不 break，因为可能有多个超时流连续出现在前面

        for page_index, stream in to_evict:
            logger.warning(
                f"WebRTC 流闲置超时淘汰: page_index={page_index}, "
                f"idle={stream.idle_duration:.0f}s, timeout={stream.config.idle_timeout}s"
            )
            await self._evict_stream(page_index, stream)

        # 清理孤儿索引（stream 已关闭但索引残留）
        orphan_keys = [
            k for k, v in self._streams_by_key.items()
            if v.page_index not in self._streams_by_index
        ]
        for k in orphan_keys:
            self._streams_by_key.pop(k, None)

    # ── 查询属性（兼容旧接口） ──

    @property
    def active_stream_count(self) -> int:
        """活跃流数量"""
        return sum(1 for s in self._streams_by_index.values() if s.is_active)

    @property
    def total_stream_count(self) -> int:
        """总流数量"""
        return len(self._streams_by_index)

    @property
    def streams(self) -> OrderedDict[int, WebRTCStreamSession]:
        """返回流字典（兼容旧接口，OrderedDict 兼容 dict 所有操作）"""
        return self._streams_by_index

    def get_stream_keys(self) -> list[str]:
        """所有流的 stream_key 列表"""
        return list(self._streams_by_key.keys())

    def __len__(self) -> int:
        return len(self._streams_by_index)

    def __contains__(self, key) -> bool:
        return key in self._streams_by_index or key in self._streams_by_key
