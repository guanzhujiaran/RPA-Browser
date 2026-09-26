"""
VideoFrameProducer - 视频帧生产者

负责从 Playwright/Patchright screencast API 捕获页面帧，并转换为 av.VideoFrame 格式。

性能设计要点（见 docs/be-message-统一计划书.md 5.16）：
1. 背压式限帧：CDP screencast 是 ack 驱动的（未 ack 不发下一帧），故把帧间隔对齐放在
   on_frame 回调内、入队之前——避免「丢弃 + 立即 ack」造成的浏览器空转编码，
   同时让 max_fps / degrade_max_fps 真正生效。
2. 解码前合并积压：消费落后时只解码最新一帧，跳过无意义的 JPEG 解码。
3. 专用线程池：解码不再抢占 asyncio.to_thread 的默认全局执行器，避免多流互相拖慢事件循环。
4. 停止可唤醒：stop() 投递哨兵唤醒阻塞中的 get_next_frame()，并释放帧缓冲。
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
    StreamQualityLevelEnum,
    VideoFrameProducerStats,
    WebRTCSessionConfig,
)

# 停止哨兵：用于唤醒阻塞在 frame_queue.get() 的消费者
_STOP_SENTINEL = object()

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


class VideoFrameProducer:
    """
    视频帧生产者

    使用 Playwright 的 page.screencast.start() API 捕获页面帧，
    并通过异步队列提供给消费者（WebRTCMediaTrack）。

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
        # 生效档位 = 用户档位与各自动降档来源中最省的一个，三者互不覆盖：
        #   _level          用户手动选择的档位
        #   _degraded       后端闲置生命周期降档（§5.15）
        #   _visibility_low 前端页面不可见（标签页切后台 / 组件被遮挡）
        self._level = StreamQualityLevelEnum.HIGH
        self._degraded = False
        self._visibility_low = False
        _params = config.params_for(self._level)
        self._quality = _params.quality  # 当前 screencast JPEG 质量
        self._frame_interval = _params.frame_interval  # 当前最小帧间隔（秒）
        self._screencast_size = _params.size  # 当前分辨率上限（None = 浏览器自适应）
        self._next_frame_at = 0.0  # 下一帧允许出帧的单调时间戳
        self._frame_size = (640, 480)  # 最近一次成功解码的帧尺寸（绿屏兜底跟随）
        self._decode_failures = 0  # 连续解码失败次数
        self._emitted_frames = 0
        self._dropped_frames = 0
        # 用户暂停态（见计划书 §5.18）：暂停时停止 screencast 并挂起出帧。
        # _resume_event 用于唤起阻塞中的 get_next_frame —— 注意**不能**用「返回 None」
        # 表示暂停，那会被 WebRTCMediaTrack 判定为「生产者已停止」而直接结束轨道。
        self._paused = False
        self._resume_event = asyncio.Event()
        self._resume_event.set()

    # -- 生命周期 --

    async def start(self):
        """启动帧捕获"""
        if self._is_running:
            logger.debug("VideoFrameProducer 已经在运行，跳过启动")
            return

        try:
            logger.info(
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

            # 暂停态下不启动 screencast：浏览器侧零 JPEG 编码，出帧由 get_next_frame 挂起
            if not self._paused:
                await self._start_screencast()

            logger.info("VideoFrameProducer 启动成功")
        except Exception as e:
            logger.error(f"启动 VideoFrameProducer 失败: {e}")
            self._is_running = False
            raise

    async def stop(self):
        """停止帧捕获并清理资源"""
        if not self._is_running and self.screencast_session is None:
            return

        # 先置位：回调不再入队，get_next_frame 不再产出新帧
        self._is_running = False
        # 唤醒可能正被「暂停」挂起的 get_next_frame，否则它会一直等到轨道被取消
        self._resume_event.set()

        await self._stop_screencast()

        # 唤醒阻塞在队列上的消费者，并释放已缓存的帧缓冲
        self._notify_consumer()
        self._last_frame = None
        logger.info("VideoFrameProducer 已停止")

    def effective_level(self) -> StreamQualityLevelEnum:
        """生效档位：用户档位与各自动降档来源中最省的一个（见计划书 §5.18）"""
        if self._degraded or self._visibility_low:
            return StreamQualityLevelEnum.LOW
        return self._level

    async def set_level(self, level: StreamQualityLevelEnum) -> None:
        """设置用户档位（幂等）。未运行时仅记录，下次 start() 生效。"""
        if self._level == level:
            return
        self._level = level
        await self._apply_effective_level()

    async def set_visibility(self, visible: bool) -> None:
        """前端可见性信号：不可见降到最省档，恢复可见时回落到用户档位。

        与闲置降档分开记录——``touch()`` 的「恢复全速」只应解除闲置来源，
        不该把「页面仍在后台」这件事一并恢复。
        """
        low = not visible
        if self._visibility_low == low:
            return
        self._visibility_low = low
        await self._apply_effective_level()

    async def set_degraded(self, degraded: bool) -> None:
        """闲置生命周期驱动的降档（幂等，见 §5.15）。

        与用户档位、可见性信号取「最省」；未运行时仅记录，下次 start() 生效。
        """
        if self._degraded == degraded:
            return
        self._degraded = degraded
        await self._apply_effective_level()

    async def set_paused(self, paused: bool) -> None:
        """暂停 / 恢复出帧（幂等，见 §5.18）

        - **暂停**：停止 screencast（浏览器侧不再 JPEG 编码），并让 get_next_frame 挂起。
          带宽与 CPU 归零；接收端画面停留在最后一帧（浏览器行为，非后端保证）。
        - **恢复**：重启 screencast，画面立即续上。全程不重建 WebRTC 连接。

        暂停优先级高于清晰度档位，且不参与「取最省」仲裁。
        """
        if self._paused == paused:
            return
        self._paused = paused
        # 先切事件再操作 screencast：恢复时让挂起的 get_next_frame 能立刻继续
        if paused:
            self._resume_event.clear()
        else:
            self._resume_event.set()
        logger.info(
            f"VideoFrameProducer {'暂停' if paused else '恢复'}出帧 "
            f"(用户档位={self._level.value})"
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

    @property
    def is_paused(self) -> bool:
        """是否处于用户暂停态"""
        return self._paused

    async def _apply_effective_level(self) -> None:
        """按生效档位刷新 screencast 参数；运行中且参数确有变化时才重启生效

        参数未变化则跳过重启——例如「不可见降档」与「闲置降档」同时命中，
        两者生效档位相同，不该重复重启 screencast（会白白丢帧）。
        """
        level = self.effective_level()
        params = self.config.params_for(level)
        changed = (
            params.quality != self._quality
            or params.frame_interval != self._frame_interval
            or params.size != self._screencast_size
        )
        self._quality = params.quality
        self._frame_interval = params.frame_interval
        self._screencast_size = params.size
        logger.info(
            f"VideoFrameProducer 档位生效: level={level.value}, quality={params.quality}, "
            f"帧间隔={params.frame_interval * 1000:.1f}ms, size={params.size} "
            f"(用户={self._level.value}, 闲置降档={self._degraded}, 不可见={self._visibility_low})"
        )
        if changed and self._is_running:
            await self._restart_screencast()

    # -- screencast 会话管理 --

    async def _start_screencast(self):
        """启动 screencast 会话（含「已启动」异常恢复）。"""
        size = self._screencast_size
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

            logger.info("重新尝试启动 Screencast 会话...")
            self.screencast_session = await self.page.screencast.start(
                on_frame=self._on_frame_callback, quality=self._quality, size=size
            )

        self._next_frame_at = 0.0
        logger.info(f"Screencast 会话启动成功 (quality={self._quality}, size={size})")

    async def _restart_screencast(self):
        """重启 screencast 以应用新的质量 / 分辨率参数。"""
        await self._stop_screencast()
        # 丢掉旧质量档位的残留帧，避免切换后仍在播放降级前的画面
        self._drain_queue()
        try:
            await self._start_screencast()
            logger.info(f"Screencast 已重启，quality={self._quality}")
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
                    logger.info("Screencast session 已停止")
                    return
                except Exception as e:
                    logger.warning(f"停止 Screencast session 时出错（尝试回退）: {e}")
        try:
            await self.page.screencast.stop()
            logger.info("Screencast session 已停止（page 级回退）")
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

    # -- 取帧 --

    async def get_next_frame(self) -> av.VideoFrame | None:
        """
        获取下一帧（异步）

        从队列中获取 JPEG 数据并在专用线程池中解码为 av.VideoFrame。
        队列为空时阻塞等待新帧；stop() 会投递哨兵将其唤醒并返回 None。
        这是唯一的外部访问接口，供 WebRTCMediaTrack 调用。

        Returns:
            av.VideoFrame: 解码后的视频帧（YUV420P 格式）
            None: 如果生产者已停止
        """
        if not self._is_running:
            return None

        try:
            while self._is_running:
                # 暂停态：挂起直到恢复。**不能返回 None** —— WebRTCMediaTrack 会把 None
                # 当作「生产者已停止」并抛 MediaStreamError 直接结束轨道（见 §5.18）。
                # resume / stop 都会 set 该事件，保证这里不会被永久挂住。
                if self._paused:
                    await self._resume_event.wait()
                    if not self._is_running:
                        return None
                    continue

                jpeg_data = await self.frame_queue.get()
                if jpeg_data is _STOP_SENTINEL:
                    return None

                # 合并积压：落后时只解码最新的一帧
                jpeg_data, dropped = self._coalesce_pending(jpeg_data)
                if jpeg_data is None:
                    return None
                self._dropped_frames += dropped

                frame = await self._decode_in_executor(jpeg_data)
                if frame is not None:
                    self._last_frame = frame
                    self._frame_size = (frame.width, frame.height)
                    self._decode_failures = 0
                    self._emitted_frames += 1
                    return frame

                # 单帧解码失败：跳过继续取下一帧（不终止轨道，也不立刻吐尺寸可能不符的绿屏）
                self._decode_failures += 1
                if self._decode_failures >= _MAX_DECODE_FAILURES:
                    self._decode_failures = 0
                    logger.error(
                        f"连续 {_MAX_DECODE_FAILURES} 帧解码失败，使用绿屏保活 "
                        f"(size={self._frame_size})"
                    )
                    return self._last_frame or self._green_frame(*self._frame_size)
                logger.warning(f"JPEG 解码失败，跳过该帧（连续 {self._decode_failures} 次）")

            return None

        except asyncio.CancelledError:
            logger.debug("get_next_frame 被取消")
            return None
        except Exception as e:
            logger.error(f"获取视频帧时出错: {e}")
            return self._last_frame

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

    def _notify_consumer(self):
        """唤醒阻塞在 get_next_frame() 的消费者：清队后投递停止哨兵。"""
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
            degraded=self._degraded or self._visibility_low,
            paused=self._paused,
            level=self.effective_level().value,
            quality=self._quality,
            frame_interval=self._frame_interval,
        )
