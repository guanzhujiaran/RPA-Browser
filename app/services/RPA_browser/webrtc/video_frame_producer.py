"""
VideoFrameProducer - 视频帧生产者

负责从 Playwright/Patchright screencast API 捕获页面帧，并转换为 av.VideoFrame 格式。

性能设计要点（见 docs/be-message-统一计划书.md 5.16）：
1. 背压式限帧：CDP screencast 是 ack 驱动的（未 ack 不发下一帧），故把帧间隔对齐放在
   on_frame 回调内、入队之前——避免「丢弃 + 立即 ack」造成的浏览器空转编码，
   同时让 max_fps / degrade_max_fps 真正生效。
2. 解码前合并积压：消费落后时只解码最新一帧，跳过无意义的 JPEG 解码。
3. 专用线程池：解码不再抢占 asyncio.to_thread 的默认全局执行器，避免多流互相拖慢事件循环。
4. 停止可唤醒：stop() 投递哨兵唤醒阻塞中的广播任务，并释放帧缓冲。
5. **一次解码、多路广播**：一个 page 只能开一个 screencast，因此本生产者是**页级共享**的；
   每帧解码一次后分发给所有订阅槽（见 docs/rpa-多观看者并发直播计划书.md §2.2）。
   慢消费者只丢自己槽里的旧帧，不会拖慢其他观看者。

调用方式变更（多观看者改造）：不再由消费者 `get_next_frame()` 逐个拉取，而是
`subscribe()` 拿到自己的 `FrameSlot`，由内部广播任务 `_pump()` 主动投递。
"""

import asyncio
import io
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import av
from PIL import Image
from loguru import logger
from playwright.async_api import Page

from app.models.runtime.webrtc_models import (
    ScreencastFrameData,
    ScreencastFrameSize,
    StreamQualityLevelEnum,
    VideoFrameProducerStats,
    WebRTCSessionConfig,
)

# 停止哨兵：用于唤醒阻塞在 frame_queue.get() 的消费者
_STOP_SENTINEL = object()

# 订阅槽关闭哨兵：区分「帧源停止」与正常的 (frame, shared) 元组
_SLOT_CLOSED = object()

# 解码线程池（模块级共享）：JPEG 解码 CPU 密集，独立成池可避免与
# asyncio.to_thread 的默认执行器（业务 / IO 任务）互相抢占
_DECODE_EXECUTOR: ThreadPoolExecutor | None = None

# 绿屏兜底帧缓存：key=(width, height)，避免每次失败都重建 PIL Image + 格式转换
_GREEN_FRAMES: dict[tuple[int, int], "av.VideoFrame"] = {}

# MJPEG 解码器（CodecContext 非线程安全）：每线程各持一个
_MJPEG_DECODER = threading.local()

# 连续解码失败多少次后用绿屏保活（避免持续坏帧把轨道饿死）
_MAX_DECODE_FAILURES = 5


def _get_mjpeg_context() -> Any:
    """获取本线程的 MJPEG 解码上下文（CodecContext 非线程安全，故按线程各持一个）

    返回 Any 是因为 av 的类型存根未声明 `CodecContext.decode()`（上游存根缺失）。
    """
    ctx = getattr(_MJPEG_DECODER, "ctx", None)
    if ctx is None:
        ctx = av.codec.CodecContext.create("mjpeg", "r")
        _MJPEG_DECODER.ctx = ctx
    return ctx


def _decode_mjpeg(jpeg_data: bytes) -> av.VideoFrame:
    """libavcodec MJPEG 直接解到 YUV，省掉 PIL 的 RGB 中间层（约省 30%-50% 解码耗时）"""
    ctx = _get_mjpeg_context()
    try:
        frames = ctx.decode(av.Packet(jpeg_data))
    except Exception:
        # 上下文可能停留在坏包状态，丢弃重建后再交给上层回退
        _MJPEG_DECODER.ctx = None
        raise
    if not frames:
        raise ValueError("MJPEG 解码器未产出帧")
    return frames[0]


def _get_decode_executor() -> ThreadPoolExecutor:
    """惰性创建解码专用线程池（进程内共享一份）"""
    global _DECODE_EXECUTOR
    if _DECODE_EXECUTOR is None:
        workers = min(8, max(2, (os.cpu_count() or 2)))
        _DECODE_EXECUTOR = ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="webrtc-frame-decode"
        )
        logger.debug(f"已创建 WebRTC 帧解码线程池（workers={workers}）")
    return _DECODE_EXECUTOR


def clone_video_frame(frame: av.VideoFrame) -> av.VideoFrame:
    """深拷贝一帧（YUV plane 逐层复制）。

    为什么必须拷贝：`ViewerMediaTrack.recv()` 会**就地**设置 `frame.pts`，
    而广播模式下多个观看者拿到的是**同一个帧对象**，会互相覆盖时间戳。
    仅在「多观看者且本端无需缩放」时调用（缩放本身会通过 `reformat` 产生新对象）。
    详见 docs/rpa-多观看者并发直播计划书.md §2.6。
    """
    clone = av.VideoFrame(frame.width, frame.height, frame.format.name)
    for src_plane, dst_plane in zip(frame.planes, clone.planes):
        dst_plane.update(src_plane)
    return clone


class FrameSlot:
    """观看者专属的「最新帧槽」（容量 1）。

    帧源每解码一帧就投递到所有订阅槽；槽满时**丢旧保新**，因此慢消费者
    只会影响自己（拿到的是最新画面，而不是积压的旧画面），不会拖慢别人。

    `shared` 标记表示该帧是否被多个槽共享：共享帧的 `pts` 会被多个
    `ViewerMediaTrack` 就地改写，消费者复用前必须 `clone_video_frame()`。
    """

    __slots__ = ("_queue", "_closed")

    def __init__(self) -> None:
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=1)
        self._closed = False

    def offer(self, frame: av.VideoFrame, shared: bool) -> None:
        """投递最新帧；槽满则丢最旧的，保证订阅者最终看到的是最新画面。"""
        if self._closed:
            return
        while True:
            try:
                self._queue.put_nowait((frame, shared))
                return
            except asyncio.QueueFull:
                try:
                    self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass

    def clear(self) -> None:
        """丢弃槽内残留帧。

        帧源从暂停恢复时调用：否则观看者会先播一段暂停前的旧画面。
        """
        try:
            while True:
                self._queue.get_nowait()
        except asyncio.QueueEmpty:
            pass

    def close(self) -> None:
        """关闭槽并唤醒等待者（`next_frame()` 随即返回 None，轨道据此收尾）。"""
        if self._closed:
            return
        self._closed = True
        self.clear()
        try:
            self._queue.put_nowait(_SLOT_CLOSED)
        except asyncio.QueueFull:
            pass

    async def next_frame(self) -> tuple[av.VideoFrame, bool] | None:
        """等待下一帧；帧源已停止时返回 None。"""
        item = await self._queue.get()
        if item is _SLOT_CLOSED:
            return None
        return item


class VideoFrameProducer:
    """
    视频帧生产者

    使用 Playwright 的 page.screencast.start() API 捕获页面帧，
    并通过**广播**提供给所有订阅者（`ViewerMediaTrack`）。

    每次启动都会创建全新的 screencast 会话，不依赖任何缓存机制。
    """

    def __init__(self, page: Page, config: WebRTCSessionConfig):
        """
        初始化视频帧生产者

        Args:
            page: Playwright Page 对象
            config: WebRTC 会话配置
        """
        self.page = page
        self.config = config
        self.frame_queue: asyncio.Queue = asyncio.Queue(maxsize=config.frame_queue_size)
        self.screencast_session = None
        self._is_running = False
        self._last_frame: av.VideoFrame | None = None  # 最后一帧（解码失败时兜底）
        # ── 清晰度档位（见计划书 §5.18）──
        # 本生产者为**页级共享**（一个 page 一份），因此这里记录的是「帧源采集档位」：
        #   _level      = 所有未暂停观看者档位中的**最高档**（由 PageFrameSource 仲裁）
        #   _degraded   后端闲置生命周期降档（§5.15）
        # 注意：「页面不可见」「用户暂停」已下沉为观看者级状态，不再作用于帧源。
        self._level = StreamQualityLevelEnum.HIGH
        self._degraded = False
        _params = config.params_for(self._level)
        self._quality = _params.quality  # 当前 screencast JPEG 质量
        self._frame_interval = _params.frame_interval  # 当前最小帧间隔（秒）
        self._screencast_size = _params.size  # 当前分辨率上限（None = 浏览器自适应）
        self._native_size = _params.native_size  # 原画：按页面真实视口采集（不缩放）
        self._next_frame_at = 0.0  # 下一帧允许出帧的单调时间戳
        self._frame_size = (640, 480)  # 最近一次成功解码的帧尺寸（绿屏兜底跟随）
        self._decode_failures = 0  # 连续解码失败次数
        self._emitted_frames = 0
        self._dropped_frames = 0
        # 帧源暂停态：**所有**观看者都暂停时才置位（引用计数语义）。
        # 置位后停止 screencast（浏览器侧零编码）；恢复时重启并清空各订阅槽，
        # 避免观看者先播一段暂停前的旧画面。
        self._paused = False
        # 订阅槽 → 该观看者的生效档位（广播时按档位分组缩放，见计划书 §10.8）
        self._slots: dict[FrameSlot, StreamQualityLevelEnum] = {}
        self._pump_task: asyncio.Task | None = None

    # -- 帧订阅（多观看者广播） --

    def subscribe(
        self, level: StreamQualityLevelEnum = StreamQualityLevelEnum.HIGH
    ) -> FrameSlot:
        """注册一个观看者订阅槽（一条 PeerConnection 一份）。"""
        slot = FrameSlot()
        self._slots[slot] = level
        logger.debug(f"帧订阅者已加入: {len(self._slots)} 个")
        return slot

    def set_slot_level(self, slot: FrameSlot, level: StreamQualityLevelEnum) -> None:
        """更新某订阅槽的目标档位（观看者切档 / 可见性变化时调用）。

        由 `WebRTCStreamManager._sync_slot_level` 维护，与 `ViewerMediaTrack`
        的档位保持一致 —— 广播时的分组缩放以此为依据。
        """
        if slot in self._slots:
            self._slots[slot] = level

    def unsubscribe(self, slot: FrameSlot) -> None:
        """注销订阅槽（观看者断开时调用）。"""
        self._slots.pop(slot, None)
        slot.close()
        logger.debug(f"帧订阅者已移除: 剩余 {len(self._slots)} 个")

    @property
    def subscriber_count(self) -> int:
        """当前订阅者数量（= 该页的观看者数）"""
        return len(self._slots)

    # -- 生命周期 --

    async def start(self):
        """启动帧捕获"""
        if self._is_running:
            logger.debug("VideoFrameProducer 已经在运行，跳过启动")
            return

        try:
            logger.debug(
                f"启动 VideoFrameProducer，质量: {self._quality}, "
                f"帧间隔: {self._frame_interval * 1000:.1f}ms, 暂停={self._paused}"
            )

            # 先置位 + 先清残留，**再**启动 screencast：
            # CDP 在 start 之后立刻推首帧，而 _on_frame_callback 会 `if not self._is_running: return`
            # 直接丢弃它且**不做 ack** —— CDP screencast 依赖 ack 才推下一帧，于是整条流停摆：
            # 静止页面（不重绘）就再也不会来第二帧 → 前端「已连接但 0B/s、全程黑屏」。
            self._is_running = True
            # 清掉上一轮残留，避免首帧就是旧画面
            self._drain_queue()
            self._decode_failures = 0

            # 暂停态下不启动 screencast：浏览器侧零 JPEG 编码
            if not self._paused:
                await self._start_screencast()

            # 唯一的解码 + 广播任务：一次解码，投递给所有订阅槽
            self._pump_task = asyncio.create_task(self._pump())

            logger.debug("VideoFrameProducer 启动成功")
        except Exception as e:
            logger.error(f"启动 VideoFrameProducer 失败: {e}")
            self._is_running = False
            raise

    async def stop(self):
        """停止帧捕获并清理资源"""
        if not self._is_running and self.screencast_session is None:
            return

        # 先置位：回调不再入队，广播任务不再产出新帧
        self._is_running = False

        await self._stop_screencast()

        # 唤醒阻塞在队列上的广播任务并等待其退出
        self._notify_pump()
        task, self._pump_task = self._pump_task, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 - 收尾阶段不抛出
                pass

        # 关闭所有订阅槽：等待中的轨道据此收尾（否则会永久挂住）
        for slot in list(self._slots):
            slot.close()
        self._slots.clear()
        self._last_frame = None
        logger.debug("VideoFrameProducer 已停止")

    def effective_level(self) -> StreamQualityLevelEnum:
        """帧源生效档位：采集档位与闲置降档取最省（见计划书 §5.18）

        「页面不可见」已下沉为观看者级，不再参与帧源仲裁。
        """
        if self._degraded:
            return StreamQualityLevelEnum.LOW
        return self._level

    async def set_level(self, level: StreamQualityLevelEnum) -> None:
        """设置**帧源采集档位**（幂等）。

        多观看者下由 PageFrameSource 传入「所有未暂停观看者档位的最大值」；
        未运行时仅记录，下次 start() 生效。
        """
        if self._level == level:
            return
        self._level = level
        await self._apply_effective_level()

    async def set_degraded(self, degraded: bool) -> None:
        """闲置生命周期驱动的降档（幂等，见 §5.15）。

        与采集档位取「最省」；未运行时仅记录，下次 start() 生效。
        """
        if self._degraded == degraded:
            return
        self._degraded = degraded
        await self._apply_effective_level()

    async def set_paused(self, paused: bool) -> None:
        """暂停 / 恢复**帧源**出帧（幂等，见 §5.18）

        - **暂停**：停止 screencast（浏览器侧不再 JPEG 编码），带宽与 CPU 归零。
        - **恢复**：重启 screencast，画面立即续上。全程不重建 WebRTC 连接。

        多观看者语义：由 `PageFrameSource` 在「所有观看者都暂停」时才置位
        （引用计数），因此单人观看时行为与改造前完全一致。
        暂停优先级高于清晰度档位，且不参与「取最省」仲裁。
        """
        if self._paused == paused:
            return
        self._paused = paused
        logger.debug(
            f"VideoFrameProducer {'暂停' if paused else '恢复'}出帧 "
            f"(帧源档位={self._level.value})"
        )

        if not self._is_running:
            # 未运行：仅记录状态，start() 会按 _paused 决定是否启动 screencast
            return
        if paused:
            # 先停采再清队列：清掉暂停前积压的帧，避免恢复后先吐一段「旧画面」
            await self._stop_screencast()
            self._drain_queue()
        else:
            await self._start_screencast()
            # 各订阅槽同样要清：否则观看者会先播一帧暂停前的旧画面
            for slot in list(self._slots):
                slot.clear()

    @property
    def is_paused(self) -> bool:
        """帧源是否处于暂停态（所有观看者都暂停）"""
        return self._paused

    async def _apply_effective_level(self) -> None:
        """按生效档位刷新 screencast 参数；运行中且参数确有变化时才重启生效

        参数未变化则跳过重启——例如多个观看者先后切档、最终收敛到同一档位时，
        不该重复重启 screencast（会白白丢帧）。
        """
        level = self.effective_level()
        params = self.config.params_for(level)
        changed = (
            params.quality != self._quality
            or params.frame_interval != self._frame_interval
            or params.size != self._screencast_size
            or params.native_size != self._native_size
        )
        self._quality = params.quality
        self._frame_interval = params.frame_interval
        self._screencast_size = params.size
        self._native_size = params.native_size
        logger.debug(
            f"VideoFrameProducer 档位生效: level={level.value}, quality={params.quality}, "
            f"帧间隔={params.frame_interval * 1000:.1f}ms, size={params.size}, "
            f"原画={params.native_size} "
            f"(采集档位={self._level.value}, 闲置降档={self._degraded}, "
            f"观看者={len(self._slots)})"
        )
        # 暂停态下只更新参数、不重启：screencast 已被停掉，此时 restart 会把它重新拉起来，
        # 破坏「全员暂停 = 浏览器侧零编码」的语义。参数会在 set_paused(False) 时生效。
        if changed and self._is_running and not self._paused:
            await self._restart_screencast()

    # -- screencast 会话管理 --

    def _resolve_screencast_size(self) -> ScreencastFrameSize | None:
        """解析本次 screencast 使用的分辨率参数。

        - **原画档**（`native_size`）：按页面真实视口尺寸采集 —— 浏览器侧默认会把
          画面等比缩放进 800×800，只有显式给出视口尺寸才能拿到未缩放的原始图像；
        - 其余档位：沿用档位配置的固定上限（None = 交给浏览器自适应）。
        """
        if not self._native_size:
            return self._screencast_size
        viewport = self.page.viewport_size
        if not viewport:
            # 未配置视口（如最大化窗口）：退回浏览器自适应，避免臆造尺寸
            logger.debug("原画档未取到页面视口尺寸，退回浏览器自适应")
            return None
        return {"width": viewport["width"], "height": viewport["height"]}

    async def _start_screencast(self):
        """启动 screencast 会话（含「已启动」异常恢复）。"""
        size = self._resolve_screencast_size()
        try:
            self.screencast_session = await self.page.screencast.start(
                on_frame=self._on_frame_callback, quality=self._quality, size=size
            )
        except Exception as e:
            # 上一轮 stop() 失败时会话会残留，这里先停后启
            if "already started" not in str(e).lower():
                raise
            logger.warning(f"检测到 Screencast 已启动，执行恢复流程: {e}")
            try:
                await self.page.screencast.stop()
                logger.info("已停止异常的 Screencast 会话")
            except Exception as stop_error:
                logger.warning(f"停止异常会话时出错（继续重试）: {stop_error}")

            logger.debug("重新尝试启动 Screencast 会话...")
            self.screencast_session = await self.page.screencast.start(
                on_frame=self._on_frame_callback, quality=self._quality, size=size
            )

        self._next_frame_at = 0.0
        logger.debug(f"Screencast 会话启动成功 (quality={self._quality}, size={size})")

    async def _restart_screencast(self):
        """重启 screencast 以应用新的质量 / 分辨率参数。"""
        await self._stop_screencast()
        # 丢掉旧质量档位的残留帧，避免切换后仍在播放降级前的画面
        self._drain_queue()
        try:
            await self._start_screencast()
            logger.debug(f"Screencast 已重启，quality={self._quality}")
        except Exception as e:
            logger.error(f"重启 screencast 失败: {e}")

    async def _stop_screencast(self):
        """停止 screencast 会话。

        screencast.start() 返回的是 DisposableStub，只有 dispose()/close()、没有 stop()；
        原实现调用 session.stop() 会抛 AttributeError 被吞掉 —— 会话从未真正停止，
        浏览器仍在持续 JPEG 编码。这里统一走 dispose，失败再回退 page.screencast.stop()。
        """
        session, self.screencast_session = self.screencast_session, None
        if session is not None:
            disposer = getattr(session, "dispose", None) or getattr(
                session, "close", None
            )
            if disposer is not None:
                try:
                    await disposer()
                    logger.debug("Screencast session 已停止")
                    return
                except Exception as e:
                    logger.warning(f"停止 Screencast session 时出错（尝试回退）: {e}")
        try:
            await self.page.screencast.stop()
            logger.debug("Screencast session 已停止（page 级回退）")
        except Exception as e:
            logger.warning(f"page.screencast.stop() 失败: {e}")

    # -- 帧捕获回调 --

    async def _on_frame_callback(self, frame_data: ScreencastFrameData | bytes):
        """
        Playwright screencast 回调函数

        帧节奏控制（背压式限帧）：CDP screencast 依赖客户端 ack 才推下一帧，
        因此在回调内把帧对齐到目标帧间隔后再入队并 ack —— 比「收下再丢」更省：
        浏览器侧不做多余的 JPEG 编码，CDP 链路上也不产生多余传输。

        Args:
            frame_data: 包含 'data' 字段的字典，{'data': <bytes>, 'timestamp':..., ...}
        """
        if not self._is_running:
            return

        # Playwright 返回的是字典，需要提取 'data' 字段
        if isinstance(frame_data, dict):
            jpeg_data = frame_data.get("data")
            if jpeg_data is None:
                logger.warning(f"Frame data 缺少 'data' 字段: {frame_data.keys()}")
                return
        else:
            # 兼容直接传入 bytes 的情况
            jpeg_data = frame_data

        if not await self._wait_for_frame_slot():
            return

        try:
            self.frame_queue.put_nowait(jpeg_data)
        except asyncio.QueueFull:
            # 消费者落后：丢新帧（队列里留着更新的帧），快速 ack 让浏览器继续
            self._dropped_frames += 1

    async def _wait_for_frame_slot(self) -> bool:
        """把出帧时刻对齐到目标帧间隔，返回本帧是否应当保留。

        未到帧间隔就 await —— 延迟 ack 形成背压，浏览器侧自动降帧。
        """
        now = time.monotonic()
        deadline = self._next_frame_at
        if now < deadline:
            await asyncio.sleep(deadline - now)
            if not self._is_running:
                return False
            now = time.monotonic()
            # 同间隔内已有帧抢占名额，本帧作废（快速 ack，浏览器继续推下一帧）
            if now < self._next_frame_at:
                self._dropped_frames += 1
                return False
        self._next_frame_at = now + self._frame_interval
        return True

    # -- 取帧与广播 --

    async def _pump(self) -> None:
        """唯一的解码消费者：**解码一次，广播给所有订阅槽**。

        取代改造前的 `get_next_frame()`（消费式、一帧只能被一个消费者取走）。
        队列为空时阻塞等待新帧；stop() 投递哨兵将其唤醒。
        """
        try:
            while self._is_running:
                jpeg_data = await self.frame_queue.get()
                if jpeg_data is _STOP_SENTINEL:
                    return

                # 暂停态：丢弃帧（screencast 已停时不会有新帧，这里是竞态兜底）
                if self._paused:
                    continue

                # 合并积压：落后时只解码最新的一帧
                jpeg_data, dropped = self._coalesce_pending(jpeg_data)
                if jpeg_data is None:
                    return
                self._dropped_frames += dropped

                frame = await self._decode_in_executor(jpeg_data)

                # 解码期间可能被暂停 / 停止，此时不再广播（否则暂停后仍会推一帧）
                if not self._is_running or self._paused:
                    continue

                if frame is not None:
                    self._last_frame = frame
                    self._frame_size = (frame.width, frame.height)
                    self._decode_failures = 0
                    self._emitted_frames += 1
                    self._broadcast(frame)
                    continue

                # 单帧解码失败：跳过继续取下一帧（不终止轨道，也不立刻吐尺寸可能不符的绿屏）
                self._decode_failures += 1
                if self._decode_failures >= _MAX_DECODE_FAILURES:
                    self._decode_failures = 0
                    logger.error(
                        f"连续 {_MAX_DECODE_FAILURES} 帧解码失败，使用绿屏保活 "
                        f"(size={self._frame_size})"
                    )
                    self._broadcast(
                        self._last_frame or self._green_frame(*self._frame_size)
                    )
                else:
                    logger.warning(f"JPEG 解码失败，跳过该帧（连续 {self._decode_failures} 次）")

        except asyncio.CancelledError:
            logger.debug("帧广播任务被取消")
            raise
        except Exception as e:
            logger.error(f"帧广播任务出错: {e}")

    def _scaled(
        self, frame: av.VideoFrame, level: StreamQualityLevelEnum
    ) -> av.VideoFrame:
        """把帧缩放到指定档位的采集尺寸（H264 要求偶数宽高）。

        无需缩放（原画 / high 等无固定尺寸的档位、或尺寸已一致）时返回**原帧对象**，
        由调用方据此判断是否可以零拷贝。
        """
        size = self.config.params_for(level).size
        if size is None:
            return frame
        width = max(2, size["width"] - size["width"] % 2)
        height = max(2, size["height"] - size["height"] % 2)
        if (frame.width, frame.height) == (width, height):
            return frame
        try:
            return frame.reformat(width=width, height=height, format="yuv420p")
        except Exception as e:  # noqa: BLE001 - 缩放失败不应打断整条流
            logger.warning(f"帧缩放失败，回退原帧: {e}")
            return frame

    def _broadcast(self, frame: av.VideoFrame) -> None:
        """把一帧投递给所有订阅槽。

        **按档位分组缩放**（见计划书 §10.8）：帧源按最高档采集，
        同档位的多个观看者共享**一次**重采样结果，组内各自持有副本
        （`shared=True`，消费者克隆后改写 pts）——
        N 个同档观看者从「N 次缩放」降为「1 次缩放 + N 次内存拷贝」，
        拷贝远便宜于重采样。

        `shared` 标记告知消费者「该帧对象被多方共享」：共享帧的 `pts`
        会被多个观看者就地改写，消费者复用前必须克隆（见 §2.6）。
        单订阅者且无需缩放时保持零拷贝路径（与单观看者场景一致）。
        """
        if not self._slots:
            return

        # 按档位分组（保持插入序，避免换组顺序引起的行为差异）
        by_level: dict[StreamQualityLevelEnum, list[FrameSlot]] = {}
        for slot, level in self._slots.items():
            by_level.setdefault(level, []).append(slot)

        for level, slots in by_level.items():
            scaled = self._scaled(frame, level)
            # 缩放产物是新建对象（组内共享）；未缩放的源帧只有组内单订阅者才可独占
            shared = len(slots) > 1 or scaled is not frame
            for slot in slots:
                slot.offer(scaled, shared)

    def _coalesce_pending(self, jpeg_data: bytes) -> tuple[bytes | None, int]:
        """把已取出的帧推进到队列中最新的那一帧。

        Returns:
            (最新帧数据, 被丢弃的帧数)；遇到停止哨兵返回 (None, 丢弃数)。
        """
        dropped = 0
        latest = jpeg_data
        while True:
            try:
                candidate = self.frame_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if candidate is _STOP_SENTINEL:
                return None, dropped
            dropped += 1
            latest = candidate
        return latest, dropped

    async def _decode_in_executor(self, jpeg_data: bytes) -> av.VideoFrame | None:
        """在专用线程池中解码 JPEG，避免阻塞事件循环"""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            _get_decode_executor(), self._decode_jpeg, jpeg_data
        )

    def _decode_jpeg(self, jpeg_data: bytes) -> av.VideoFrame | None:
        """
        将 JPEG 字节解码为 av.VideoFrame

        此方法应该在线程池中执行，因为它是 CPU 密集型的。
        优先走 libavcodec MJPEG（直接出 YUV），失败回退 PIL（兼容 CMYK / 异常 JPEG）。

        Args:
            jpeg_data: JPEG 编码的图像数据

        Returns:
            av.VideoFrame: YUV420P 格式的视频帧；失败返回 None
        """
        try:
            frame = _decode_mjpeg(jpeg_data)
        except Exception as e:
            logger.debug(f"MJPEG 解码失败，回退 PIL: {e}")
            frame = self._decode_jpeg_via_pil(jpeg_data)
        if frame is None:
            return None

        # yuv420p 与 H264 编码器要求偶数宽高，奇数会直接编码失败
        width = max(2, frame.width - (frame.width % 2))
        height = max(2, frame.height - (frame.height % 2))
        if frame.format.name == "yuv420p" and (frame.width, frame.height) == (
            width,
            height,
        ):
            return frame
        try:
            return frame.reformat(width=width, height=height, format="yuv420p")
        except Exception as e:
            logger.error(f"JPEG 解码失败: {e}")
            return None

    def _decode_jpeg_via_pil(self, jpeg_data: bytes) -> av.VideoFrame | None:
        """PIL 回退路径：灰度 / CMYK 等非 RGB 的 JPEG 需先归一，否则 from_image 会抛错"""
        try:
            with Image.open(io.BytesIO(jpeg_data)) as image:
                if image.mode != "RGB":
                    image = image.convert("RGB")
                return av.VideoFrame.from_image(image)
        except Exception as e:
            logger.error(f"JPEG 解码失败: {e}")
            return None

    # -- 辅助 --

    def _drain_queue(self):
        """清空帧队列（启动 / 停止时调用）"""
        while True:
            try:
                self.frame_queue.get_nowait()
            except asyncio.QueueEmpty:
                return

    def _notify_pump(self):
        """唤醒阻塞在 frame_queue.get() 的广播任务：清队后投递停止哨兵。"""
        self._drain_queue()
        try:
            self.frame_queue.put_nowait(_STOP_SENTINEL)
        except asyncio.QueueFull:
            pass

    def _green_frame(self, width: int, height: int) -> av.VideoFrame:
        """获取绿屏兜底帧（按尺寸缓存复用，避免重复构造）"""
        key = (max(2, width - width % 2), max(2, height - height % 2))
        frame = _GREEN_FRAMES.get(key)
        if frame is None:
            image = Image.new("RGB", key, color="green")
            frame = av.VideoFrame.from_image(image).reformat(format="yuv420p")
            _GREEN_FRAMES[key] = frame
            logger.debug(f"已创建绿屏帧缓存: {key[0]}x{key[1]}")
        return frame

    @property
    def is_running(self) -> bool:
        """检查生产者是否正在运行"""
        return self._is_running

    @property
    def queue_size(self) -> int:
        """获取当前队列中的帧数"""
        return self.frame_queue.qsize()

    @property
    def stats(self) -> VideoFrameProducerStats:
        """出帧统计快照（丢帧率 = dropped / (dropped + emitted)）"""
        total = self._emitted_frames + self._dropped_frames
        return VideoFrameProducerStats(
            emitted_frames=self._emitted_frames,
            dropped_frames=self._dropped_frames,
            drop_rate=(self._dropped_frames / total) if total else 0.0,
            queue_size=self.frame_queue.qsize(),
            degraded=self._degraded,
            paused=self._paused,
            level=self.effective_level().value,
            quality=self._quality,
            frame_interval=self._frame_interval,
        )
