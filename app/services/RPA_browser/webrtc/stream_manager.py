"""
WebRTCStreamManager - WebRTC 流管理器（多观看者并发直播）

对象模型（详见 docs/rpa-多观看者并发直播计划书.md §3.1）：

    WebRTCStreamManager（每会话一份）
    ├─ _sources: dict[page_index, PageFrameSource]   ← 帧源级（每页一份，唯一 screencast）
    └─ _viewers: dict[viewer_id, ViewerStream]       ← 观看者级（每条 PeerConnection 一份）

设计要点：

1. **帧源与观看者解耦**：一个 page 只能开一个 screencast，因此帧源是页级共享的；
   观看者各自持有 PeerConnection 与媒体轨道，互不影响。
2. **不再「新建即淘汰」**：同一 page 可以同时挂多个观看者（改造前会互相抢流）。
3. **观看者级状态**：档位 / 暂停 / 可见性都落在 `ViewerStream` 上，
   帧源档位由 `PageFrameSource.reconcile()` 按「所有未暂停观看者的最高档」仲裁。
4. **弱引用持有 session**：避免 session ↔ manager 循环引用。
"""

from __future__ import annotations

import weakref
from typing import Optional

from loguru import logger

from app.config import settings
from app.models.runtime.control import BrowserSessionViewerData
from app.models.runtime.webrtc_models import (
    StreamQualityLevelEnum,
    StreamQualitySnapshot,
    ViewerStreamInfo,
    WebRTCSessionConfig,
)
from .frame_source import PageFrameSource
from .viewer_stream import ViewerStream


class WebRTCStreamManager:
    """WebRTC 流管理器（帧源 + 观看者）

    索引：
    - `_sources`: page_index → PageFrameSource（帧源，每页一份）
    - `_viewers`: viewer_id → ViewerStream（观看者，每条连接一份）
    - `_viewers_by_key`: stream_key → ViewerStream（信令 O(1) 查找）

    会话级闲置降级 / 挂起仍由 LiveService 的三级软着陆统一驱动
    （见 docs/be-message-统一计划书.md §5.15），本管理器不自行注册定时任务。
    """

    def __init__(self, session):
        """
        Args:
            session: WebRTCEnabledSession 实例（以弱引用持有，打破循环引用）
        """
        self._session_ref = weakref.ref(session)

        # 帧源索引（每页一份，唯一 screencast）
        self._sources: dict[int, PageFrameSource] = {}
        # 观看者索引（每条 PeerConnection 一份）
        self._viewers: dict[str, ViewerStream] = {}
        self._viewers_by_key: dict[str, ViewerStream] = {}

        # 会话级闲置降档：作用于所有帧源（见 §5.15）
        self._degraded: bool = False

        # 档位 / 分辨率参数只与页面有关，全会话共用一份
        self._config = self._build_config()

    @staticmethod
    def _build_config() -> WebRTCSessionConfig:
        """从系统配置构建会话参数
        （original / ultra / medium / low 走系统配置，high 走代码默认）
        """
        return WebRTCSessionConfig(
            idle_timeout=settings.browser_webrtc_idle_timeout,
            original_quality=settings.browser_stream_original_quality,
            original_max_fps=settings.browser_stream_original_max_fps,
            ultra_quality=settings.browser_stream_ultra_quality,
            ultra_max_fps=settings.browser_stream_ultra_max_fps,
            ultra_frame_max_width=settings.browser_stream_ultra_frame_max_width,
            ultra_frame_max_height=settings.browser_stream_ultra_frame_max_height,
            medium_quality=settings.browser_stream_medium_quality,
            medium_max_fps=settings.browser_stream_medium_max_fps,
            medium_frame_max_width=settings.browser_stream_medium_frame_max_width,
            medium_frame_max_height=settings.browser_stream_medium_frame_max_height,
            low_quality=settings.browser_stream_degrade_quality,
            low_max_fps=settings.browser_stream_degrade_max_fps,
            low_frame_max_width=settings.browser_stream_degrade_frame_max_width,
            low_frame_max_height=settings.browser_stream_degrade_frame_max_height,
        )

    # ── session 访问（弱引用解引用） ──

    @property
    def session(self):
        """获取 session 引用，若 session 已被回收返回 None"""
        return self._session_ref()

    # ── 核心流操作 ──

    async def start_stream(
        self,
        page_index: int,
        viewer_id: str,
        client_ip: str = "",
        client_device: str = "",
        client_device_type: str = "",
        client_browser_version: str = "",
        client_ip_region: str = "",
        client_ip_isp: str = "",
        is_admin: bool = False,
    ) -> ViewerStream:
        """
        为指定观看者启动（或重建）一条 WebRTC 视频流。

        与改造前的关键差异：**不淘汰**同页其他观看者的流 —— 多人观看互不影响。
        同一 `viewer_id` 重复调用（前端重连）会先关闭自己的旧连接再新建，保证幂等。

        Args:
            page_index: 页面索引（从 0 开始）
            viewer_id: 观看者标识（前端生成，每次建立连接一枚）
            client_ip: 客户端 IP（网关注入的 x-bili-client-ip，仅用于展示「谁在看」）
            client_device: 设备描述（来自 User-Agent，如「Windows · Chrome 126」）
            client_device_type: 设备类型稳定码（desktop / mobile / tablet）
            client_browser_version: 浏览器大版本（来自 User-Agent）
            client_ip_region: IP 属地（be-message GeoIP RPC 解析，形如「浙江 杭州」）
            client_ip_isp: IP 运营商（ASN 组织名，英文原值，同一次 RPC 返回）
            is_admin: 是否为监管管理员观看。**True 时该观看者对归属者完全隐藏**：
                不计入对外观看人数、不出现在观看者列表中（见计划书 §2.7）

        Returns:
            ViewerStream: 该观看者的流实例

        Raises:
            RuntimeError: session 已被回收
            IndexError: page_index 越界
        """
        session = self.session
        if session is None:
            raise RuntimeError("Session 已被回收，无法创建流")

        pages = session.all_pages
        if page_index >= len(pages):
            raise IndexError(f"页面索引 {page_index} 超出范围 (共 {len(pages)} 个页面)")

        # 幂等重连：同一观看者再次 offer 时先关掉自己的旧连接（只影响自己）
        await self.close_viewer(viewer_id)

        source = self._sources.get(page_index)
        if source is None:
            source = PageFrameSource(page_index, pages[page_index], self._config)
            self._sources[page_index] = source

        mid = session.playwright_instance.mid
        browser_id = session.playwright_instance.browser_id
        # ⚠️ 尾部必须带 viewer_id：改造前不含观看者标识，导致淘汰重建后
        # 「同 key 不同流」，先开端的延迟 answer / 候选会打到后开端的流上（串流污染）
        stream_key = f"{mid}:{browser_id}:page_{page_index}:{viewer_id}"

        slot = source.register_viewer(viewer_id)
        viewer = ViewerStream(
            viewer_id,
            stream_key,
            page_index,
            self._config,
            slot,
            client_ip=client_ip,
            client_device=client_device,
            client_device_type=client_device_type,
            client_browser_version=client_browser_version,
            client_ip_region=client_ip_region,
            client_ip_isp=client_ip_isp,
            is_admin=is_admin,
        )

        # 先挂轨道、再启动帧源：避免首帧广播给空订阅集合
        await viewer.start()
        await source.ensure_started()

        self._viewers[viewer_id] = viewer
        self._viewers_by_key[stream_key] = viewer

        # 新观看者加入：重算帧源档位（可能要从全员暂停中恢复，或按新需求升档）
        await source.reconcile(self._viewers_of(page_index))

        logger.debug(
            f"观看者流已创建: {stream_key} "
            f"(page_index={page_index}, 该页观看者={source.viewer_count}, "
            f"总观看者={len(self._viewers)})"
        )
        return viewer

    def get_viewer(self, viewer_id: str) -> Optional[ViewerStream]:
        """按观看者 id 查找（O(1)）"""
        return self._viewers.get(viewer_id)

    def get_viewer_by_stream_key(self, stream_key: str) -> Optional[ViewerStream]:
        """按 stream_key 查找（O(1)，供 answer / 候选等信令端点使用）"""
        return self._viewers_by_key.get(stream_key)

    async def close_viewer(self, viewer_id: str) -> bool:
        """
        关闭单个观看者并释放其资源（**不影响其他观看者**）。

        Returns:
            bool: 该观看者此前存在并已被关闭
        """
        viewer = self._viewers.pop(viewer_id, None)
        if viewer is None:
            return False

        self._viewers_by_key.pop(viewer.stream_key, None)
        logger.debug(f"关闭观看者流: {viewer.stream_key} | {viewer.client_summary}")

        try:
            await viewer.close()
        except Exception as e:
            logger.error(f"关闭观看者流失败: {viewer_id}, error: {e}")

        # 反订阅 + 重算该页帧源（最后一个观看者离开时会停采并回收帧源）
        source = self._sources.get(viewer.page_index)
        if source is not None:
            source.unregister_viewer(viewer.viewer_id, viewer.slot)
        await self._reconcile_page(viewer.page_index)
        return True

    async def close_all_streams(self) -> None:
        """关闭所有观看者与帧源（幂等）。"""
        if not self._viewers and not self._sources:
            return

        logger.info(f"关闭所有 WebRTC 流（观看者 {len(self._viewers)} 个）")
        for viewer_id in list(self._viewers.keys()):
            await self.close_viewer(viewer_id)

        # close_viewer 在最后一个观看者离开时已停掉对应帧源，这里兜底清空残留
        for page_index, source in list(self._sources.items()):
            try:
                await source.stop()
            except Exception as e:
                logger.error(f"停止帧源失败: page_index={page_index}, error: {e}")
        self._sources.clear()
        logger.info("所有 WebRTC 流已关闭")

    # ── 观看者级操作（档位 / 暂停 / 可见性） ──

    async def set_level(
        self, viewer_id: str, level: StreamQualityLevelEnum
    ) -> StreamQualitySnapshot:
        """设置**该观看者**的清晰度档位（可能连带升高帧源采集档位）。"""
        viewer = self._require_viewer(viewer_id)
        viewer.set_level(level)
        self._sync_slot_level(viewer)
        await self._reconcile_page(viewer.page_index)
        return self.quality_snapshot(viewer_id)

    async def set_paused(self, viewer_id: str, paused: bool) -> StreamQualitySnapshot:
        """暂停 / 恢复**该观看者**的出帧（不影响其他观看者）。"""
        viewer = self._require_viewer(viewer_id)
        viewer.set_paused(paused)
        await self._reconcile_page(viewer.page_index)
        return self.quality_snapshot(viewer_id)

    async def set_visibility(
        self, viewer_id: str, visible: bool
    ) -> StreamQualitySnapshot:
        """上报**该观看者**的页面可见性（不可见时本端降档）。"""
        viewer = self._require_viewer(viewer_id)
        viewer.set_visibility(visible)
        self._sync_slot_level(viewer)
        await self._reconcile_page(viewer.page_index)
        return self.quality_snapshot(viewer_id)

    def quality_snapshot(self, viewer_id: str) -> StreamQualitySnapshot:
        """该观看者的档位快照。

        观看者不存在时返回中性默认值（不抛错）：档位查询是展示用途，
        观看者已被回收时前端仍可能因为时序拿到旧 id。
        """
        viewer = self._viewers.get(viewer_id)
        if viewer is None:
            return StreamQualitySnapshot(
                viewer_id=viewer_id,
                level=StreamQualityLevelEnum.HIGH,
                effective_level=StreamQualityLevelEnum.HIGH,
                degraded=False,
                paused=False,
            )
        return StreamQualitySnapshot(
            viewer_id=viewer_id,
            level=viewer.level,
            effective_level=viewer.effective_level,
            degraded=viewer.is_degraded,
            paused=viewer.is_paused,
        )

    # ── 心跳与回收 ──

    def heartbeat(self, viewer_id: str) -> bool:
        """刷新观看者活跃时间（供保活端点）。

        Returns:
            bool: 该观看者仍在线
        """
        viewer = self._viewers.get(viewer_id)
        if viewer is None:
            return False
        viewer.touch()
        return True

    def has_live_viewer(self) -> bool:
        """是否存在 WebRTC 链路连通的观看者（ICE/DTLS 层活性，见计划书 §10.7）。

        用 PeerConnection 的 `connectionState` 判定而非 HTTP 心跳：

        - ICE/DTLS 自带保活（STUN binding），断网必然反映为 disconnected / failed；
        - **暂停中的观看者保活仍在**（暂停只停出帧、不影响连接），不会被误判失联；
        - 监管页不订阅会话状态 SSE（严格 owner 校验会 403），SSE 存活与否
          覆盖不了监管场景 —— WebRTC 连接状态才是全场景通用的活性依据。
        """
        return any(
            viewer.pc.connectionState == "connected"
            for viewer in self._viewers.values()
        )

    async def reap_idle_viewers(self, idle_timeout: int) -> int:
        """回收 WebRTC 链路已失联的观看者（前端关标签页 / 断网等异常退出）。

        判定以 **`pc.connectionState`** 为准，前端因此不再需要心跳请求：

        - `connected`：ICE/DTLS 保活正常，无论多久没有 HTTP 请求都不回收；
        - 其余状态（含新建未完成协商的 `new` / `connecting`、抖动中的
          `disconnected`）：以 `idle_timeout` 为宽限期，超时仍未恢复才回收 ——
          宽限靠 `idle_seconds`（最后一次信令 / 创建时间起算）。

        Args:
            idle_timeout: 非 connected 状态的宽限秒数

        Returns:
            int: 本次回收的观看者数量
        """
        stale: list[str] = []
        for viewer in self._viewers.values():
            if viewer.pc.connectionState == "connected":
                continue
            if viewer.idle_seconds <= idle_timeout:
                # 宽限：新建尚未完成 offer/answer 协商，或断线后等 ICE 自愈
                continue
            stale.append(viewer.viewer_id)

        for viewer_id in stale:
            viewer = self._viewers.get(viewer_id)
            state = viewer.pc.connectionState if viewer else "?"
            idle = round(viewer.idle_seconds, 1) if viewer else "?"
            # 客户端摘要与前端「观看者信息」面板同源：据此判断「被回收的是谁」
            client = (
                f" | {viewer.client_summary} | 观看时长={viewer.watch_seconds}s"
                if viewer
                else ""
            )
            logger.warning(
                f"观看者 WebRTC 链路失联(state={state}, idle={idle}s)，"
                f"回收: {viewer_id}{client}"
            )
            await self.close_viewer(viewer_id)
        return len(stale)

    # ── 会话级降级 / 挂起（由 LiveService 三级软着陆驱动） ──

    async def set_degraded(self, degraded: bool) -> None:
        """对所有帧源设置闲置降级 / 恢复（幂等，见 §5.15）。

        降级：降低 screencast JPEG 质量并限制帧率，降低 CPU / 带宽占用。
        注意：有活跃观看者时心跳会持续 touch()，因此本方法通常不会在观看期间触发。
        """
        if self._degraded == degraded:
            return
        self._degraded = degraded
        for page_index, source in list(self._sources.items()):
            try:
                await source.producer.set_degraded(degraded)
            except Exception as e:
                logger.error(f"设置帧源降级状态失败 page_index={page_index}: {e}")

    async def suspend_streams(self) -> None:
        """挂起本会话的所有流（关闭观看者与帧源，**保留浏览器实例**）。

        语义化的闲置挂起：释放 screencast + PeerConnection 的 CPU，但保留
        浏览器进程/内存。用户重新拉流时由 start_stream() 幂等重建。
        """
        if not self._viewers:
            return
        logger.info(f"闲置挂起：关闭 {len(self._viewers)} 个观看者流（保留浏览器实例）")
        await self.close_all_streams()

    # ── 查询 ──

    def viewers_info(self, include_admin: bool = False) -> list[ViewerStreamInfo]:
        """观看者信息快照（供 /webrtc/status 的观看者列表）。

        Args:
            include_admin: 是否包含监管管理员观看者。默认 **False** ——
                管理员观看对浏览器归属者完全隐藏（见计划书 §2.7）。
                仅运维排查时才需要传 True。
        """
        return [
            viewer.info()
            for viewer in self._viewers.values()
            if include_admin or not viewer.is_admin
        ]

    def viewer_summaries(
        self, include_admin: bool = False
    ) -> list[BrowserSessionViewerData]:
        """观看者摘要 —— 会话状态 SSE 与 `/webrtc/status` **共用的同一构造**。

        两条出口字段因此完全一致（此前 status 版本多一个 `idle_seconds`，
        前端要维护两套类型）。该字段已移除：它每次请求都在变，既没有消费者
        再轮询 status，也会破坏状态签名去重（见计划书 §4.4 / §10.6）。

        按 `viewer_id` 排序，保证签名稳定（避免字典顺序抖动造成假变化）。
        """
        return [
            BrowserSessionViewerData(
                viewer_id=v.viewer_id,
                stream_key=v.stream_key,
                page_index=v.page_index,
                state=v.state.value,
                paused=v.paused,
                level=v.level,
                effective_level=v.effective_level,
                client_ip=v.client_ip,
                client_device=v.client_device,
                client_device_type=v.client_device_type,
                client_browser_version=v.client_browser_version,
                client_ip_region=v.client_ip_region,
                client_ip_isp=v.client_ip_isp,
                connected_at=int(v.connected_at),
            )
            for v in sorted(
                self.viewers_info(include_admin), key=lambda info: info.viewer_id
            )
        ]

    @property
    def viewer_count(self) -> int:
        """当前观看者总数（**含**监管管理员）—— 用于资源管理与保活判断"""
        return len(self._viewers)

    @property
    def public_viewer_count(self) -> int:
        """对外可见的观看者数（**排除**监管管理员）—— 用于状态接口与界面展示"""
        return sum(1 for v in self._viewers.values() if not v.is_admin)

    @property
    def source_count(self) -> int:
        """当前活跃帧源数量（= 正在出流的页面数）"""
        return len(self._sources)

    @property
    def active_stream_count(self) -> int:
        """活跃观看者数量（兼容旧接口）"""
        return sum(1 for v in self._viewers.values() if v.is_active)

    @property
    def total_stream_count(self) -> int:
        """观看者总数（兼容旧接口）"""
        return len(self._viewers)

    # ── 内部辅助 ──

    def _viewers_of(self, page_index: int) -> list[ViewerStream]:
        """该页上的所有观看者"""
        return [v for v in self._viewers.values() if v.page_index == page_index]

    def _require_viewer(self, viewer_id: str) -> ViewerStream:
        viewer = self._viewers.get(viewer_id)
        if viewer is None:
            raise KeyError(f"观看者不存在或已回收: {viewer_id}")
        return viewer

    def _sync_slot_level(self, viewer: ViewerStream) -> None:
        """把观看者的生效档位同步到帧源订阅槽。

        广播按槽位档位分组缩放（见计划书 §10.8）；与 `ViewerMediaTrack` 的档位
        由同一处（`viewer.effective_level`）驱动，两条路径不会漂移。
        """
        source = self._sources.get(viewer.page_index)
        if source is not None:
            source.producer.set_slot_level(viewer.slot, viewer.effective_level)

    async def _reconcile_page(self, page_index: int) -> None:
        """重算某页帧源的状态；无观看者时停采并回收帧源。"""
        source = self._sources.get(page_index)
        if source is None:
            return
        viewers = self._viewers_of(page_index)
        if not viewers:
            # 无人观看：停止采集并回收（释放 screencast 与解码开销）
            await source.stop()
            self._sources.pop(page_index, None)
            logger.debug(f"帧源已回收（该页已无观看者）: page_index={page_index}")
            return
        await source.reconcile(viewers)


__all__ = ["WebRTCStreamManager"]
