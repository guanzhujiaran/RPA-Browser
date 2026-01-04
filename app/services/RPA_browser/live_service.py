from app.models.exceptions.base_exception import BrowserNotStartedException
import time
import asyncio
import io
from typing import Optional, Dict, Set
from dataclasses import dataclass, field
from playwright.async_api import Page, BrowserContext
from loguru import logger
from app.models.exceptions.base_exception import GetBrowserSessionFailedException
from app.models.RPA_browser.browser_info_model import (
    RPAClickParams,
    RPAFillParams,
    RPAScrollParams,
    RPAScreenshotParams,
    RPAEvaluateParams,
    RPAWaitParams,
    RPANavigateParams,
    RPAResponse,
)
from app.models.RPA_browser.live_control_models import (
    BrowserStatusEnum,
    OperationPriority,
    BrowserStatus,
    LiveControlCommand,
    VideoStreamParams,
    HeartbeatRequest,
    HeartbeatResponse,
    ManualOperationRequest,
    AutomationResumeRequest,
    BrowserCleanupPolicy,
    SessionLifecycleState,
    CreateSessionRequest,
    BrowserInfoData,
    VideoStreamStatusData,
    ManualOperationResult,
    AutomationResult,
    OperationStatusData,
    PluginStatusData,
    SessionStatisticsData,
    CreateSessionData,
    BrowserSessionStatusData,
)
from app.services.RPA_browser.browser_session_pool.playwright_pool import (
    get_default_session_pool,
)
from app.services.RPA_browser.browser_session_pool.session_pool_model import (
    PluginedSessionInfo,
)


@dataclass
class BrowserSessionEntry:
    """浏览器会话条目"""

    mid: int  # 用户ID
    browser_id: int  # 浏览器实例ID
    plugined_session: PluginedSessionInfo  # 完整的插件化会话
    active_connections: Set[str] = field(
        default_factory=set
    )  # 活跃连接集合（客户端ID）
    last_activity: int = 0  # 最后活动时间
    last_heartbeat: int = 0  # 最后心跳时间
    status: BrowserStatusEnum = BrowserStatusEnum.RUNNING  # 会话状态
    is_manual_mode: bool = False  # 是否处于人工操作模式
    current_operation_priority: OperationPriority = OperationPriority.NORMAL
    automation_paused_time: int = 0  # 自动化暂停时间
    manual_operation_start_time: int = 0  # 人工操作开始时间
    heartbeat_clients: Dict[str, int] = field(default_factory=dict)  # 客户端心跳时间
    cleanup_policy: BrowserCleanupPolicy = field(default_factory=BrowserCleanupPolicy)
    created_at: int = field(default_factory=lambda: int(time.time()))  # 会话创建时间
    lifecycle_state: SessionLifecycleState = (
        SessionLifecycleState.ACTIVE
    )  # 会话生命周期状态
    expires_at: int | None = None  # 会话过期时间


@dataclass
class VideoStreamInfo:
    """视频流信息"""

    mid: int  # 用户ID
    browser_id: int  # 浏览器实例ID
    session: PluginedSessionInfo  # 浏览器会话
    params: VideoStreamParams  # 流参数
    active: bool = True  # 是否活跃
    last_frame: Optional[bytes] = None  # 最新帧数据
    last_frame_time: float = 0.0  # 最后帧时间戳


@dataclass
class LiveStreamingEntry:
    """直播流条目"""

    mid: int  # 用户ID
    browser_id: int  # 浏览器实例ID
    start_time: int  # 开始时间
    last_heartbeat: int  # 最后心跳时间
    is_active: bool = True  # 是否活跃
    stream_params: Optional[VideoStreamParams] = None  # 流参数
    cleanup_scheduled: bool = False  # 是否已安排清理


@dataclass
class LiveServiceState:
    """LiveService状态管理"""

    browser_sessions: Dict[str, BrowserSessionEntry]  # key: f"{mid}_{browser_id}"
    video_streams: Dict[str, VideoStreamInfo]  # key: f"{mid}_{browser_id}"
    auto_streams: Dict[str, bool]  # key: f"{mid}_{browser_id}" 表示是否由自动管理
    live_streams: Dict[str, LiveStreamingEntry]  # key: f"{mid}_{browser_id}" 直播流管理
    cleanup_task: Optional[asyncio.Task] = None  # 清理任务
    heartbeat_monitor_task: Optional[asyncio.Task] = None  # 心跳监控任务
    stream_monitor_task: Optional[asyncio.Task] = None  # 流监控任务


class RPAOperationService:
    """RPA操作服务类"""

    @staticmethod
    async def click_element(page: Page, params: RPAClickParams) -> RPAResponse:
        """点击元素"""
        try:
            element = page.locator(params.selector)
            await element.wait_for(state="visible", timeout=params.timeout)
            await element.click()
            return RPAResponse(success=True, data={"message": "点击成功"})
        except Exception as e:
            return RPAResponse(success=False, error=str(e))

    @staticmethod
    async def fill_form(page: Page, params: RPAFillParams) -> RPAResponse:
        """填充表单"""
        try:
            element = page.locator(params.selector)
            await element.wait_for(state="visible", timeout=params.timeout)
            await element.fill(params.value)
            return RPAResponse(success=True, data={"message": "填充成功"})
        except Exception as e:
            return RPAResponse(success=False, error=str(e))

    @staticmethod
    async def scroll_page(page: Page, params: RPAScrollParams) -> RPAResponse:
        """滚动页面"""
        try:
            await page.evaluate(
                f"window.scrollTo({{top: {params.y}, left: {params.x}, behavior: '{params.behavior}'}})"
            )
            return RPAResponse(success=True, data={"message": "滚动成功"})
        except Exception as e:
            return RPAResponse(success=False, error=str(e))

    @staticmethod
    async def take_screenshot(page: Page, params: RPAScreenshotParams) -> RPAResponse:
        """截图"""
        try:
            if params.selector:
                element = page.locator(params.selector)
                await element.wait_for(state="visible", timeout=30000)
                screenshot_bytes = await element.screenshot(
                    type=params.type, quality=params.quality
                )
            else:
                screenshot_bytes = await page.screenshot(
                    full_page=params.full_page, type=params.type, quality=params.quality
                )

            import base64

            image_base64 = base64.b64encode(screenshot_bytes).decode("utf-8")
            return RPAResponse(success=True, data={"image": image_base64})
        except Exception as e:
            return RPAResponse(success=False, error=str(e))

    @staticmethod
    async def evaluate_script(page: Page, params: RPAEvaluateParams) -> RPAResponse:
        """执行JavaScript"""
        try:
            result = await page.evaluate(params.script, *params.args)
            return RPAResponse(success=True, data={"result": result})
        except Exception as e:
            return RPAResponse(success=False, error=str(e))

    @staticmethod
    async def wait_for_element(page: Page, params: RPAWaitParams) -> RPAResponse:
        """等待元素"""
        try:
            if params.selector:
                element = page.locator(params.selector)
                await element.wait_for(state=params.state, timeout=params.timeout)
            else:
                await page.wait_for_timeout(params.timeout)
            return RPAResponse(success=True, data={"message": "等待完成"})
        except Exception as e:
            return RPAResponse(success=False, error=str(e))

    @staticmethod
    async def navigate_to(page: Page, params: RPANavigateParams) -> RPAResponse:
        """导航到URL"""
        try:
            await page.goto(
                params.url, wait_until=params.wait_until, timeout=params.timeout
            )
            title = await page.title()
            current_url = page.url
            return RPAResponse(
                success=True, data={"title": title, "current_url": current_url}
            )
        except Exception as e:
            return RPAResponse(success=False, error=str(e))

    @staticmethod
    async def get_browser_info(session: PluginedSessionInfo) -> BrowserInfoData:
        """获取完整的浏览器信息"""
        browser_context: BrowserContext = session.browser_context
        pages = browser_context.pages if browser_context else []

        page_info_list = []
        for i, page in enumerate(pages):
            try:
                page_info = {
                    "index": i,
                    "url": page.url,
                    "title": await page.title() if not page.is_closed() else "",
                    "is_closed": page.is_closed(),
                }
                page_info_list.append(page_info)
            except Exception:
                continue

        return BrowserInfoData(
            browser_context={"pages_count": len(pages), "pages": page_info_list},
            plugins={
                "count": len(session.plugin_configs) if session.plugin_configs else 0,
                "enabled_plugins": [
                    {"name": config.name, "description": config.description}
                    for config in (
                        session.plugin_configs.values()
                        if session.plugin_configs
                        else []
                    )
                    if config.is_enabled
                ],
            },
            session={
                "mid": session.playwright_instance.mid,
                "browser_id": session.playwright_instance.browser_id,
                "headless": session.headless,
                "is_closed": session.is_closed,
            },
        )


class VideoStreamService:
    """视频流服务类"""

    # 维护视频流状态
    video_streams: Dict[str, VideoStreamInfo] = {}  # key: f"{mid}_{browser_id}"
    # 视频流自动管理标志
    auto_streams: Dict[str, bool] = (
        {}
    )  # key: f"{mid}_{browser_id}" 表示是否由WebSocket自动管理

    @staticmethod
    def _get_stream_key(mid: int, browser_id: int) -> str:
        """获取流键"""
        return f"{mid}_{browser_id}"

    @staticmethod
    async def generate_video_stream(
        mid: int, browser_id: int, params: VideoStreamParams
    ) -> io.BytesIO:
        """生成视频流 - 使用MJPEG格式"""
        import time

        stream_key = VideoStreamService._get_stream_key(mid, browser_id)

        # 获取浏览器会话
        try:
            plugined_session = await LiveService.get_plugined_session(mid, browser_id)
        except Exception as e:
            raise GetBrowserSessionFailedException(error=str(e))

        # 创建视频流缓冲区
        stream_buffer = io.BytesIO()

        # 设置视频流状态
        VideoStreamService.video_streams[stream_key] = VideoStreamInfo(
            mid=mid,
            browser_id=browser_id,
            session=plugined_session,
            params=params,
            active=True,
            last_frame=None,
            last_frame_time=time.time(),
        )

        return stream_buffer

    @staticmethod
    async def generate_mjpeg_frame(
        page: Page, quality: int = 80, width: int = None, height: int = None
    ) -> bytes:
        """生成MJPEG帧"""
        import base64

        try:
            # 截图并转换为JPEG
            screenshot_bytes = await page.screenshot(
                type="jpeg", quality=quality, full_page=False
            )

            # 如果有尺寸要求，使用PIL进行缩放
            if width or height:
                from PIL import Image
                import io

                image = Image.open(io.BytesIO(screenshot_bytes))

                # 计算新的尺寸
                if width and height:
                    new_size = (width, height)
                elif width:
                    ratio = width / image.width
                    new_size = (width, int(image.height * ratio))
                else:
                    ratio = height / image.height
                    new_size = (int(image.width * ratio), height)

                image = image.resize(new_size, Image.Resampling.LANCZOS)

                # 重新编码为JPEG
                output = io.BytesIO()
                image.save(output, format="JPEG", quality=quality)
                screenshot_bytes = output.getvalue()

            # 构建MJPEG帧
            frame_header = f"Content-Type: image/jpeg\r\nContent-Length: {len(screenshot_bytes)}\r\n\r\n"
            frame_data = frame_header.encode() + screenshot_bytes

            return frame_data

        except Exception as e:
            # 生成错误帧
            error_frame = f"Content-Type: text/plain\r\nContent-Length: {len(str(e))}\r\n\r\n{str(e)}"
            return error_frame.encode()

    @staticmethod
    async def start_video_stream(
        mid: int, browser_id: int, params: VideoStreamParams, auto_managed: bool = False
    ):
        """启动视频流"""
        stream_key = VideoStreamService._get_stream_key(mid, browser_id)

        # 如果视频流未初始化，先初始化
        if stream_key not in VideoStreamService.video_streams:
            await VideoStreamService.generate_video_stream(mid, browser_id, params)

        stream_info = VideoStreamService.video_streams[stream_key]
        stream_info.active = True

        # 设置自动管理标志
        VideoStreamService.auto_streams[stream_key] = auto_managed

        # 如果是自动管理模式，启动直播流管理
        if auto_managed:
            await LiveService.start_live_streaming(mid, browser_id, params)

        # 获取当前页面
        try:
            page = await stream_info.session.get_current_page()

            while stream_info.active:
                # 检查是否应该停止（自动管理模式）
                if auto_managed:
                    live_key = LiveService._get_session_key(mid, browser_id)
                    if (
                        live_key not in LiveService.live_streams
                        or not LiveService.live_streams[live_key].is_active
                    ):
                        stream_info.active = False
                        break

                # 生成帧
                frame_data = await VideoStreamService.generate_mjpeg_frame(
                    page,
                    quality=params.quality,
                    width=params.width,
                    height=params.height,
                )

                # 更新帧数据
                stream_info.last_frame = frame_data
                stream_info.last_frame_time = time.time()

                # 等待下一帧
                await asyncio.sleep(1.0 / params.fps)

        except Exception as e:
            stream_info.active = False
            raise e

    @staticmethod
    async def stop_video_stream(mid: int, browser_id: int, force: bool = False):
        """停止视频流"""
        stream_key = VideoStreamService._get_stream_key(mid, browser_id)

        if stream_key in VideoStreamService.video_streams:
            # 检查是否自动管理，如果不是自动管理的流或强制停止，则停止
            if force or not VideoStreamService.auto_streams.get(stream_key, False):
                VideoStreamService.video_streams[stream_key].active = False
                # 延迟清理，确保所有异步操作完成
                await asyncio.sleep(1.0)
                if stream_key in VideoStreamService.video_streams:
                    del VideoStreamService.video_streams[stream_key]

                # 清理自动管理标志
                if stream_key in VideoStreamService.auto_streams:
                    del VideoStreamService.auto_streams[stream_key]

    @staticmethod
    def get_video_stream_status(
        mid: int, browser_id: int
    ) -> Optional[VideoStreamStatusData]:
        """获取视频流状态"""
        stream_key = VideoStreamService._get_stream_key(mid, browser_id)

        if stream_key in VideoStreamService.video_streams:
            stream_info = VideoStreamService.video_streams[stream_key]
            return VideoStreamStatusData(
                mid=stream_info.mid,
                browser_id=stream_info.browser_id,
                active=stream_info.active,
                last_frame_time=stream_info.last_frame_time,
                params=(
                    stream_info.params.dict()
                    if hasattr(stream_info.params, "dict")
                    else {}
                ),
            )
        return None

    @staticmethod
    async def get_latest_frame(mid: int, browser_id: int) -> Optional[bytes]:
        """获取最新帧"""
        try:
            stream_key = VideoStreamService._get_stream_key(mid, browser_id)

            if stream_key in VideoStreamService.video_streams:
                stream_info = VideoStreamService.video_streams[stream_key]
                # 检查流是否仍然活跃
                if stream_info.active:
                    return stream_info.last_frame
                else:
                    # 流已不活跃，清理资源
                    del VideoStreamService.video_streams[stream_key]
                    if stream_key in VideoStreamService.auto_streams:
                        del VideoStreamService.auto_streams[stream_key]
                    return None
            return None
        except Exception:
            return None


class LiveService:
    """浏览器控制服务类 - 支持人工干预、心跳检测和自动清理"""

    # 维护浏览器会话状态
    browser_sessions: Dict[str, BrowserSessionEntry] = {}  # key: f"{mid}_{browser_id}"
    # 维护直播流状态
    live_streams: Dict[str, LiveStreamingEntry] = {}  # key: f"{mid}_{browser_id}"
    # 全局状态
    state = LiveServiceState(
        browser_sessions={}, video_streams={}, auto_streams={}, live_streams={}
    )
    # 默认配置
    DEFAULT_SESSION_TIMEOUT = 3600  # 1小时
    DEFAULT_HEARTBEAT_INTERVAL = 30  # 心跳间隔30秒
    DEFAULT_CLEANUP_INTERVAL = 300  # 清理间隔5分钟
    DEFAULT_LIVE_STREAM_TIMEOUT = 60  # 直播流超时时间60秒

    @staticmethod
    def _get_session_key(mid: int, browser_id: int) -> str:
        """获取会话键"""
        return f"{mid}_{browser_id}"

    @staticmethod
    async def start_background_tasks():
        """启动后台任务"""
        if not LiveService.state.cleanup_task or LiveService.state.cleanup_task.done():
            LiveService.state.cleanup_task = asyncio.create_task(
                LiveService._cleanup_task_loop()
            )

        if (
            not LiveService.state.heartbeat_monitor_task
            or LiveService.state.heartbeat_monitor_task.done()
        ):
            LiveService.state.heartbeat_monitor_task = asyncio.create_task(
                LiveService._heartbeat_monitor_loop()
            )

        if (
            not LiveService.state.stream_monitor_task
            or LiveService.state.stream_monitor_task.done()
        ):
            LiveService.state.stream_monitor_task = asyncio.create_task(
                LiveService._live_stream_monitor_loop()
            )

    @staticmethod
    async def stop_background_tasks():
        """停止后台任务"""
        if LiveService.state.cleanup_task and not LiveService.state.cleanup_task.done():
            LiveService.state.cleanup_task.cancel()

        if (
            LiveService.state.heartbeat_monitor_task
            and not LiveService.state.heartbeat_monitor_task.done()
        ):
            LiveService.state.heartbeat_monitor_task.cancel()

        if (
            LiveService.state.stream_monitor_task
            and not LiveService.state.stream_monitor_task.done()
        ):
            LiveService.state.stream_monitor_task.cancel()

    @staticmethod
    async def _cleanup_task_loop():
        """清理任务循环"""
        while True:
            try:
                await asyncio.sleep(LiveService.DEFAULT_CLEANUP_INTERVAL)
                await LiveService.cleanup_expired_sessions()
                await LiveService._cleanup_idle_browsers()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"清理任务执行失败: {str(e)}")

    @staticmethod
    async def _heartbeat_monitor_loop():
        """心跳监控循环"""
        while True:
            try:
                await asyncio.sleep(LiveService.DEFAULT_HEARTBEAT_INTERVAL)
                await LiveService._check_heartbeat_timeouts()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"心跳监控任务执行失败: {str(e)}")

    @staticmethod
    async def _live_stream_monitor_loop():
        """直播流监控循环"""
        while True:
            try:
                await asyncio.sleep(LiveService.DEFAULT_HEARTBEAT_INTERVAL)
                await LiveService._check_live_stream_timeouts()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"直播流监控任务执行失败: {str(e)}")

    @staticmethod
    async def _check_heartbeat_timeouts():
        """检查心跳超时"""
        current_time = int(time.time())
        timeout_sessions = []

        for session_key, entry in LiveService.browser_sessions.items():
            policy = entry.cleanup_policy
            max_no_heartbeat = policy.max_no_heartbeat_time

            # 检查所有客户端的心跳
            active_clients = []
            for client_id, last_heartbeat in entry.heartbeat_clients.items():
                if current_time - last_heartbeat <= max_no_heartbeat:
                    active_clients.append(client_id)

            # 更新活跃客户端列表
            entry.heartbeat_clients = {
                client_id: entry.heartbeat_clients[client_id]
                for client_id in active_clients
            }

            # 如果没有活跃客户端
            if not active_clients:
                # 处于人工操作模式，自动恢复自动化
                if entry.is_manual_mode:
                    logger.info(f"会话 {session_key} 无活跃心跳，自动恢复自动化模式")
                    await LiveService.resume_automation(entry.mid, entry.browser_id)

                # 🔧 修复：检查最后一次心跳时间，如果超时则清理会话
                # 不论会话状态是 IDLE 还是 RUNNING，只要长时间无心跳就清理
                time_since_last_heartbeat = current_time - entry.last_heartbeat
                if time_since_last_heartbeat > max_no_heartbeat:
                    logger.warning(
                        f"会话 {session_key} 心跳超时 (距离上次心跳: {time_since_last_heartbeat}秒)，准备清理"
                    )
                    timeout_sessions.append(session_key)
                elif (
                    entry.status == BrowserStatusEnum.IDLE
                    and current_time - entry.last_activity
                    > LiveService.DEFAULT_SESSION_TIMEOUT
                ):
                    # 闲置会话超时
                    timeout_sessions.append(session_key)

        # 清理超时会话
        for session_key in timeout_sessions:
            mid, browser_id = map(int, session_key.split("_"))
            await LiveService.release_browser_session(mid, browser_id)
            logger.warning(f"清理无心跳会话: {session_key}")

    @staticmethod
    async def _check_live_stream_timeouts():
        """检查直播流超时"""
        current_time = int(time.time())
        timeout_streams = []

        for stream_key, entry in list(LiveService.live_streams.items()):
            # 检查直播流是否超时
            if (
                current_time - entry.last_heartbeat
                > LiveService.DEFAULT_LIVE_STREAM_TIMEOUT
            ):
                # 标记为超时
                entry.is_active = False
                timeout_streams.append(stream_key)
                logger.warning(
                    f"直播流超时: {stream_key}, 最后心跳: {entry.last_heartbeat}"
                )

        # 清理超时直播流
        for stream_key in timeout_streams:
            mid, browser_id = map(int, stream_key.split("_"))
            await LiveService._cleanup_live_stream(mid, browser_id)
            logger.warning(f"清理超时直播流: {stream_key}")

    @staticmethod
    async def start_live_streaming(
        mid: int, browser_id: int, params: Optional[VideoStreamParams] = None
    ):
        """开始直播流管理

        直播流启动后，不会自动暂停任务执行，只有设置了manual模式才会暂停任务。
        直播和任务可以并行执行，互不影响。
        """
        stream_key = LiveService._get_session_key(mid, browser_id)
        current_time = int(time.time())

        # 如果已有直播流，先停止其他任务
        if stream_key in LiveService.live_streams:
            await LiveService._stop_other_tasks(mid, browser_id)

        # 创建或更新直播流条目
        LiveService.live_streams[stream_key] = LiveStreamingEntry(
            mid=mid,
            browser_id=browser_id,
            start_time=current_time,
            last_heartbeat=current_time,
            is_active=True,
            stream_params=params,
        )

        # 更新会话状态为直播中
        if stream_key in LiveService.browser_sessions:
            entry = LiveService.browser_sessions[stream_key]
            entry.status = BrowserStatusEnum.RUNNING
            entry.last_activity = current_time

            # 直播不影响任务执行，只有设置了manual模式才会暂停任务
            # 保持当前的操作模式不变，不强制切换到人工操作模式

        logger.info(f"开始直播流管理: {stream_key}")

    @staticmethod
    async def _stop_other_tasks(mid: int, browser_id: int):
        """停止其他任务，确保直播优先

        直播启动时，只停止冲突的视频流任务，不影响插件自动化任务。
        直播和任务可以并行执行，互不影响。
        """
        stream_key = LiveService._get_session_key(mid, browser_id)

        # 停止视频流（如果有）
        if stream_key in VideoStreamService.video_streams:
            await VideoStreamService.stop_video_stream(mid, browser_id, force=True)

        # 直播不影响任务执行，只有设置了manual模式才会暂停任务
        # 保持当前的操作模式不变，不强制切换到人工操作模式

    @staticmethod
    async def _cleanup_live_stream(mid: int, browser_id: int):
        """清理直播流"""
        stream_key = LiveService._get_session_key(mid, browser_id)

        # 停止视频流
        if stream_key in VideoStreamService.video_streams:
            await VideoStreamService.stop_video_stream(mid, browser_id, force=True)

        # 从直播流管理中移除
        if stream_key in LiveService.live_streams:
            del LiveService.live_streams[stream_key]

        # 恢复自动化（如果有会话且无其他活跃连接）
        if stream_key in LiveService.browser_sessions:
            entry = LiveService.browser_sessions[stream_key]

            # 检查是否还有其他活跃连接
            if not entry.active_connections and not entry.heartbeat_clients:
                # 恢复自动化
                await LiveService.resume_automation(mid, browser_id)
                # 🔧 修复：如果是因为心跳超时导致的清理，直接释放浏览器资源
                # 不论会话状态是 IDLE 还是 RUNNING，都应该清理
                current_time = int(time.time())
                if (
                    current_time - entry.last_heartbeat
                    > LiveService.DEFAULT_LIVE_STREAM_TIMEOUT
                ):
                    logger.warning(f"直播流心跳超时，释放浏览器资源: {stream_key}")
                    await LiveService.release_browser_session(mid, browser_id)
                elif entry.status == BrowserStatusEnum.IDLE:
                    await LiveService.release_browser_session(mid, browser_id)

        logger.info(f"清理直播流完成: {stream_key}")

    @staticmethod
    async def _cleanup_idle_browsers():
        """清理闲置浏览器"""
        current_time = int(time.time())
        idle_sessions = []

        for session_key, entry in LiveService.browser_sessions.items():
            policy = entry.cleanup_policy
            max_idle_time = policy.max_idle_time

            # 检查是否为闲置状态且超过最大闲置时间
            if (
                entry.status == BrowserStatusEnum.IDLE
                and not entry.active_connections
                and current_time - entry.last_activity > max_idle_time
            ):
                idle_sessions.append(session_key)

        # 清理闲置会话
        for session_key in idle_sessions:
            await LiveService.release_browser_session(*map(int, session_key.split("_")))
            logger.warning(f"清理闲置会话: {session_key}")

    @staticmethod
    async def handle_heartbeat(
        mid: int, browser_id: int, heartbeat: HeartbeatRequest
    ) -> HeartbeatResponse:
        """处理心跳请求"""
        session_key = LiveService._get_session_key(mid, browser_id)
        current_time = int(time.time())

        if session_key not in LiveService.browser_sessions:
            return HeartbeatResponse(
                success=False,
                server_timestamp=current_time,
                next_heartbeat_interval=LiveService.DEFAULT_HEARTBEAT_INTERVAL,
                status="session_not_found",
            )

        entry = LiveService.browser_sessions[session_key]

        # 更新直播流心跳（如果存在）
        if session_key in LiveService.live_streams:
            live_entry = LiveService.live_streams[session_key]
            live_entry.last_heartbeat = current_time
            live_entry.is_active = True

            # 如果直播流曾被标记为清理，重新激活
            if live_entry.cleanup_scheduled:
                live_entry.cleanup_scheduled = False
                logger.info(f"直播流重新激活: {session_key}")

        # 更新心跳时间
        entry.last_heartbeat = current_time
        entry.heartbeat_clients[heartbeat.client_id] = current_time
        entry.last_activity = current_time

        # 更新状态
        if not entry.active_connections:
            entry.active_connections.add(heartbeat.client_id)

        # 检查是否需要自动启动直播流
        if session_key not in LiveService.live_streams and not entry.heartbeat_clients:
            # 首次心跳，自动启动直播流管理
            await LiveService.start_live_streaming(mid, browser_id)

        return HeartbeatResponse(
            success=True,
            server_timestamp=current_time,
            next_heartbeat_interval=LiveService.DEFAULT_HEARTBEAT_INTERVAL,
            status="heartbeat_received",
            active_clients=len(entry.active_connections),
        )

    @staticmethod
    async def start_manual_operation(
        mid: int, browser_id: int, request: ManualOperationRequest
    ) -> ManualOperationResult:
        """开始人工操作"""
        session_key = LiveService._get_session_key(mid, browser_id)

        if session_key not in LiveService.browser_sessions:
            await LiveService.create_browser_session(mid, browser_id)

        entry = LiveService.browser_sessions[session_key]
        current_time = int(time.time())

        # 如果当前有更低优先级的操作，先暂停
        if (
            not entry.is_manual_mode
            or request.priority.value > entry.current_operation_priority.value
        ):

            # 暂停自动化任务
            await LiveService.pause_plugins(mid, browser_id)

            # 更新状态
            entry.is_manual_mode = True
            entry.current_operation_priority = request.priority
            entry.manual_operation_start_time = current_time
            entry.status = BrowserStatusEnum.PAUSED

            message = f"人工操作已开始，优先级: {request.priority.value}"
            if request.reason:
                message += f", 原因: {request.reason}"

            return ManualOperationResult(
                success=True,
                message=message,
                status="manual_mode_active",
                priority=request.priority.value,
                start_time=current_time,
            )
        else:
            return ManualOperationResult(
                success=False,
                message=f"当前已有更高或相同优先级的操作在进行: {entry.current_operation_priority.value}",
                status="conflict",
                priority=entry.current_operation_priority.value,
                start_time=0,
            )

    @staticmethod
    async def stop_manual_operation(mid: int, browser_id: int) -> AutomationResult:
        """停止人工操作，恢复自动化"""
        return await LiveService.resume_automation(mid, browser_id)

    @staticmethod
    async def resume_automation(
        mid: int, browser_id: int, request: Optional[AutomationResumeRequest] = None
    ) -> AutomationResult:
        """恢复自动化任务"""
        session_key = LiveService._get_session_key(mid, browser_id)

        if session_key not in LiveService.browser_sessions:
            return AutomationResult(
                success=False, message="会话不存在", status="error", resume_time=0
            )

        entry = LiveService.browser_sessions[session_key]

        if not entry.is_manual_mode:
            return AutomationResult(
                success=False,
                message="当前未处于人工操作模式",
                status="not_manual_mode",
                resume_time=0,
            )

        # 恢复插件自动操作
        try:
            await LiveService.resume_plugins(mid, browser_id)

            # 重置状态
            entry.is_manual_mode = False
            entry.current_operation_priority = OperationPriority.NORMAL
            entry.status = BrowserStatusEnum.RUNNING
            entry.automation_paused_time = 0

            message = "自动化任务已恢复"
            if request and request.reason:
                message += f", 原因: {request.reason}"

            return AutomationResult(
                success=True,
                message=message,
                status="automation_resumed",
                resume_time=int(time.time()),
            )

        except Exception as e:
            return AutomationResult(
                success=False,
                message=f"恢复自动化失败: {str(e)}",
                status="error",
                resume_time=0,
            )

    @staticmethod
    def get_operation_status(mid: int, browser_id: int) -> OperationStatusData:
        """获取操作状态"""
        session_key = LiveService._get_session_key(mid, browser_id)

        if session_key not in LiveService.browser_sessions:
            return OperationStatusData(
                status="not_found",
                is_manual_mode=False,
                current_priority="none",
                active_connections=0,
                last_activity=0,
                last_heartbeat=0,
                manual_operation_duration=0,
                heartbeat_clients=[],
            )

        entry = LiveService.browser_sessions[session_key]
        current_time = int(time.time())

        return OperationStatusData(
            status=entry.status.value,
            is_manual_mode=entry.is_manual_mode,
            current_priority=entry.current_operation_priority.value,
            active_connections=len(entry.active_connections),
            last_activity=entry.last_activity,
            last_heartbeat=entry.last_heartbeat,
            manual_operation_duration=(
                current_time - entry.manual_operation_start_time
                if entry.is_manual_mode
                else 0
            ),
            heartbeat_clients=list(entry.heartbeat_clients.keys()),
        )

    @staticmethod
    async def get_plugined_session(
        mid: int, browser_id: int, headless: bool = True, is_create_browser: bool = True
    ) -> PluginedSessionInfo:
        """获取插件化浏览器会话"""
        pool = get_default_session_pool()
        session_key = LiveService._get_session_key(mid, browser_id)
        current_time = int(time.time())

        # 检查是否已有会话
        if session_key in LiveService.browser_sessions:
            entry = LiveService.browser_sessions[session_key]
            entry.last_activity = current_time
            return entry.plugined_session
        if not is_create_browser:
            raise BrowserNotStartedException()
        # 获取浏览器会话
        session_params = type(
            "",
            (),
            {
                "mid": mid,
                "browser_id": browser_id,
                "headless": headless,
            },
        )()

        plugined_session = await pool.get_session(session_params)

        # 创建会话条目
        entry = BrowserSessionEntry(
            mid=mid,
            browser_id=browser_id,
            plugined_session=plugined_session,
            last_activity=current_time,
            last_heartbeat=current_time,
        )

        LiveService.browser_sessions[session_key] = entry

        # 启动后台任务（如果还未启动）
        # await LiveService.start_background_tasks() 不需要这里启动，直接放到apscheduler里面处理

        return plugined_session

    @staticmethod
    async def execute_browser_command(
        mid: int, browser_id: int, command: LiveControlCommand
    ) -> RPAResponse:
        """执行浏览器命令 - 支持优先级和人工操作检测"""
        session_key = LiveService._get_session_key(mid, browser_id)
        entry = LiveService.browser_sessions.get(session_key)

        if not entry:
            return RPAResponse(success=False, error="会话不存在")

        # 检查是否需要人工操作模式
        if command.require_manual_mode and not entry.is_manual_mode:
            return RPAResponse(success=False, error="该命令需要人工操作模式")

        # 检查当前操作优先级
        if (
            entry.is_manual_mode
            and command.priority.value <= entry.current_operation_priority.value
        ):
            return RPAResponse(
                success=False,
                error=f"当前人工操作优先级({entry.current_operation_priority.value})更高，无法执行此命令",
            )

        # 如果命令需要中断自动化且当前处于自动化模式，则暂停自动化
        if command.interrupt_automation and not entry.is_manual_mode:
            await LiveService.pause_plugins(mid, browser_id)
            entry.is_manual_mode = True
            entry.status = BrowserStatusEnum.PAUSED
            entry.manual_operation_start_time = int(time.time())

        try:
            page = await entry.plugined_session.get_current_page()

            # 更新活动时间和状态
            entry.last_activity = int(time.time())

            # 根据命令类型执行相应的RPA操作
            command_type = command.type
            params = command.params

            if command_type == "click":
                return await RPAOperationService.click_element(
                    page, RPAClickParams(**params)
                )
            elif command_type == "fill":
                return await RPAOperationService.fill_form(
                    page, RPAFillParams(**params)
                )
            elif command_type == "scroll":
                return await RPAOperationService.scroll_page(
                    page, RPAScrollParams(**params)
                )
            elif command_type == "screenshot":
                return await RPAOperationService.take_screenshot(
                    page, RPAScreenshotParams(**params)
                )
            elif command_type == "evaluate":
                return await RPAOperationService.evaluate_script(
                    page, RPAEvaluateParams(**params)
                )
            elif command_type == "wait":
                return await RPAOperationService.wait_for_element(
                    page, RPAWaitParams(**params)
                )
            elif command_type == "navigate":
                return await RPAOperationService.navigate_to(
                    page, RPANavigateParams(**params)
                )
            elif command_type == "get_browser_info":
                # 获取完整的浏览器信息
                browser_info = await RPAOperationService.get_browser_info(
                    entry.plugined_session
                )
                return RPAResponse(success=True, data=browser_info)
            else:
                return RPAResponse(success=False, error=f"未知命令类型: {command_type}")

        except Exception as e:
            return RPAResponse(success=False, error=str(e))

    @staticmethod
    async def pause_plugins(mid: int, browser_id: int) -> PluginStatusData:
        """暂停插件自动操作"""
        session_key = LiveService._get_session_key(mid, browser_id)
        entry = LiveService.browser_sessions.get(session_key)

        if not entry:
            return PluginStatusData(is_paused=False, message="会话不存在")

        try:
            entry.plugined_session.pause_plugins()
            return PluginStatusData(
                is_paused=True, message="插件自动操作已暂停，启用手动操作模式"
            )
        except Exception as e:
            return PluginStatusData(is_paused=False, message=f"暂停插件失败: {str(e)}")

    @staticmethod
    async def resume_plugins(mid: int, browser_id: int) -> PluginStatusData:
        """恢复插件自动操作"""
        session_key = LiveService._get_session_key(mid, browser_id)
        entry = LiveService.browser_sessions.get(session_key)

        if not entry:
            return PluginStatusData(is_paused=True, message="会话不存在")

        try:
            entry.plugined_session.resume_plugins()
            return PluginStatusData(is_paused=False, message="插件自动操作已恢复")
        except Exception as e:
            return PluginStatusData(is_paused=True, message=f"恢复插件失败: {str(e)}")

    @staticmethod
    def get_plugin_status(mid: int, browser_id: int) -> PluginStatusData:
        """获取插件状态"""
        session_key = LiveService._get_session_key(mid, browser_id)
        entry = LiveService.browser_sessions.get(session_key)

        if not entry:
            return PluginStatusData(is_paused=False, message="会话不存在")

        try:
            is_paused = entry.plugined_session.is_plugins_paused()
            return PluginStatusData(
                is_paused=is_paused,
                message="插件已暂停" if is_paused else "插件正常运行",
            )
        except Exception as e:
            return PluginStatusData(
                is_paused=False, message=f"获取插件状态失败: {str(e)}"
            )

    @staticmethod
    async def release_browser_session(mid: int, browser_id: int) -> bool:
        """释放浏览器会话"""
        session_key = LiveService._get_session_key(mid, browser_id)

        try:
            pool = get_default_session_pool()

            # 关闭浏览器会话
            if session_key in LiveService.browser_sessions:
                entry = LiveService.browser_sessions[session_key]
                try:
                    await entry.plugined_session.close()
                except:
                    pass
                del LiveService.browser_sessions[session_key]

            # 从池中释放会话
            remove_params = type(
                "",
                (),
                {
                    "mid": mid,
                    "browser_id": browser_id,
                    "force_close": False,
                },
            )()

            await pool.release_session(remove_params)
            return True

        except Exception:
            return False

    @staticmethod
    def get_browser_status(mid: int, browser_id: int) -> Optional[BrowserStatus]:
        """获取浏览器状态"""
        session_key = LiveService._get_session_key(mid, browser_id)
        entry = LiveService.browser_sessions.get(session_key)

        if not entry:
            return None

        return BrowserStatus(
            mid=mid,
            browser_id=browser_id,
            status=entry.status,
            active_connections=len(entry.active_connections),
            last_activity=entry.last_activity,
            last_heartbeat=entry.last_heartbeat,
            is_manual_mode=entry.is_manual_mode,
            current_operation_priority=entry.current_operation_priority,
        )

    @staticmethod
    async def cleanup_expired_sessions():
        """清理过期会话"""
        current_time = int(time.time())
        expired_sessions = []

        for session_key, entry in list(LiveService.browser_sessions.items()):
            # 检查会话是否超时
            if current_time - entry.last_activity > LiveService.DEFAULT_SESSION_TIMEOUT:
                # 如果处于人工操作模式且有活跃连接，暂不清理
                if entry.is_manual_mode and entry.active_connections:
                    continue
                expired_sessions.append(session_key)

        # 清理过期会话
        for session_key in expired_sessions:
            await LiveService.release_browser_session(*map(int, session_key.split("_")))
            logger.warning(f"清理过期会话: {session_key}")

    @staticmethod
    async def release_browser_session(mid: int, browser_id: int) -> bool:
        """释放浏览器会话 - 增强版本"""
        session_key = LiveService._get_session_key(mid, browser_id)

        try:
            pool = get_default_session_pool()

            # 清理 WebRTC 连接
            from app.services.RPA_browser.webrtc_service import WebRTCService

            connection_key = WebRTCService._get_connection_key(mid, browser_id)
            if connection_key in WebRTCService.active_connections:
                logger.info(f"清理 WebRTC 连接: {connection_key}")
                await WebRTCService.close_connection(mid, browser_id)

            # 关闭浏览器会话
            if session_key in LiveService.browser_sessions:
                entry = LiveService.browser_sessions[session_key]
                try:
                    # 恢复插件状态
                    if entry.is_manual_mode:
                        await entry.plugined_session.resume_plugins()
                    await entry.plugined_session.close()
                except:
                    pass
                del LiveService.browser_sessions[session_key]

            # 从池中释放会话
            remove_params = type(
                "",
                (),
                {
                    "mid": mid,
                    "browser_id": browser_id,
                    "force_close": False,
                },
            )()

            await pool.release_session(remove_params)

            # 清理相关视频流
            if session_key in VideoStreamService.video_streams:
                await VideoStreamService.stop_video_stream(mid, browser_id, force=True)

            return True

        except Exception:
            return False

    @staticmethod
    def get_session_statistics() -> SessionStatisticsData:
        """获取会话统计信息 - 增强版本"""
        total_sessions = len(LiveService.browser_sessions)
        running_sessions = sum(
            1
            for entry in LiveService.browser_sessions.values()
            if entry.status == BrowserStatusEnum.RUNNING
        )
        paused_sessions = sum(
            1
            for entry in LiveService.browser_sessions.values()
            if entry.status == BrowserStatusEnum.PAUSED
        )
        idle_sessions = sum(
            1
            for entry in LiveService.browser_sessions.values()
            if entry.status == BrowserStatusEnum.IDLE
        )
        manual_mode_sessions = sum(
            1 for entry in LiveService.browser_sessions.values() if entry.is_manual_mode
        )

        total_connections = sum(
            len(entry.active_connections)
            for entry in LiveService.browser_sessions.values()
        )
        total_heartbeat_clients = sum(
            len(entry.heartbeat_clients)
            for entry in LiveService.browser_sessions.values()
        )

        return SessionStatisticsData(
            total_sessions=total_sessions,
            status_distribution={
                "running": running_sessions,
                "paused": paused_sessions,
                "idle": idle_sessions,
                "stopped": 0,
                "error": 0,
            },
            manual_mode_sessions=manual_mode_sessions,
            total_active_connections=total_connections,
            total_heartbeat_clients=total_heartbeat_clients,
            session_timeout=LiveService.DEFAULT_SESSION_TIMEOUT,
            heartbeat_interval=LiveService.DEFAULT_HEARTBEAT_INTERVAL,
            cleanup_interval=LiveService.DEFAULT_CLEANUP_INTERVAL,
        )

    @staticmethod
    async def create_browser_session(
        mid: int, browser_id: int, request: CreateSessionRequest
    ) -> CreateSessionData:
        """
        创建浏览器会话

        这是一个独立的会话创建接口，与心跳机制完全解耦。
        只有显式调用此接口才会创建浏览器会话。

        Args:
            mid: 用户ID
            browser_id: 浏览器实例ID
            request: 创建会话的请求参数

        Returns:
            CreateSessionData: 创建结果，包含会话信息
        """
        session_key = LiveService._get_session_key(mid, browser_id)
        current_time = int(time.time())

        # 检查会话是否已存在
        if session_key in LiveService.browser_sessions:
            entry = LiveService.browser_sessions[session_key]

            # 确保向后兼容性
            created_at = getattr(entry, "created_at", entry.last_activity)
            expires_at = getattr(entry, "expires_at", None)

            return CreateSessionData(
                success=True,
                session_id=session_key,
                browser_started=True,
                created_at=created_at,
                expires_at=expires_at,
                message="会话已存在，返回现有会话信息",
            )

        try:
            # 创建新的浏览器会话
            plugined_session = await LiveService.get_plugined_session(
                mid, browser_id, headless=request.headless
            )

            # 获取会话条目并设置生命周期状态
            entry = LiveService.browser_sessions[session_key]
            entry.lifecycle_state = SessionLifecycleState.ACTIVE
            entry.expires_at = (
                current_time + request.expiration_time
                if request.expiration_time
                else None
            )

            # 设置清理策略
            if request.cleanup_policy:
                entry.cleanup_policy = request.cleanup_policy
            elif request.auto_cleanup:
                entry.cleanup_policy = BrowserCleanupPolicy(
                    max_idle_time=1800, max_no_heartbeat_time=60, cleanup_interval=300
                )

            return CreateSessionData(
                success=True,
                session_id=session_key,
                browser_started=True,
                created_at=entry.created_at,
                expires_at=entry.expires_at,
                message="浏览器会话创建成功",
            )

        except Exception as e:
            return CreateSessionData(
                success=False,
                session_id=session_key,
                browser_started=False,
                created_at=0,
                expires_at=None,
                error=f"创建会话失败: {str(e)}",
            )

    @staticmethod
    async def create_browser_session_background(
        mid: int, browser_id: int, request: CreateSessionRequest
    ) -> None:
        """
        后台创建浏览器会话

        这个方法在后台任务中执行，不返回结果给客户端。
        主要用于异步创建浏览器会话，避免阻塞HTTP请求。

        Args:
            mid: 用户ID
            browser_id: 浏览器实例ID
            request: 创建会话的请求参数
        """
        try:
            # 创建新的浏览器会话
            plugined_session = await LiveService.get_plugined_session(
                mid, browser_id, headless=request.headless
            )

            # 获取会话条目并设置生命周期状态
            session_key = LiveService._get_session_key(mid, browser_id)
            current_time = int(time.time())
            entry = LiveService.browser_sessions[session_key]
            entry.lifecycle_state = SessionLifecycleState.ACTIVE
            entry.expires_at = (
                current_time + request.expiration_time
                if request.expiration_time
                else None
            )

            # 设置清理策略
            if request.cleanup_policy:
                entry.cleanup_policy = request.cleanup_policy
            elif request.auto_cleanup:
                entry.cleanup_policy = BrowserCleanupPolicy(
                    max_idle_time=1800, max_no_heartbeat_time=60, cleanup_interval=300
                )

        except Exception as e:
            # 在后台任务中记录错误，但不影响客户端响应
            logger.error(
                f"后台创建浏览器会话失败 (mid={mid}, browser_id={browser_id}): {str(e)}"
            )

    @staticmethod
    def get_browser_session_status(
        mid: int, browser_id: int
    ) -> BrowserSessionStatusData:
        """
        获取浏览器会话的详细状态

        提供统一的会话状态查询，包含所有相关的状态信息。

        Args:
            mid: 用户ID
            browser_id: 浏览器实例ID

        Returns:
            BrowserSessionStatusData: 会话状态信息
        """
        session_key = LiveService._get_session_key(mid, browser_id)

        if session_key not in LiveService.browser_sessions:
            return BrowserSessionStatusData(
                session_exists=False,
                browser_running=False,
                lifecycle_state=SessionLifecycleState.TERMINATED,
                last_heartbeat=0,
                active_connections=0,
                video_streaming=False,
                manual_mode=False,
                created_at=0,
                expires_at=None,
                status="terminated",
                cleanup_policy={},
                message="会话不存在",
            )

        entry = LiveService.browser_sessions[session_key]

        # 确保向后兼容性
        created_at = getattr(entry, "created_at", entry.last_activity)
        lifecycle_state = getattr(
            entry, "lifecycle_state", SessionLifecycleState.ACTIVE
        )
        expires_at = getattr(entry, "expires_at", None)

        return BrowserSessionStatusData(
            session_exists=True,
            browser_running=entry.status != BrowserStatusEnum.STOPPED,
            lifecycle_state=lifecycle_state,
            last_heartbeat=entry.last_heartbeat,
            active_connections=len(entry.active_connections),
            video_streaming=session_key in LiveService.live_streams,
            manual_mode=entry.is_manual_mode,
            created_at=created_at,
            expires_at=expires_at,
            status=entry.status.value,
            cleanup_policy={
                "max_idle_time": entry.cleanup_policy.max_idle_time,
                "max_no_heartbeat_time": entry.cleanup_policy.max_no_heartbeat_time,
                "cleanup_interval": entry.cleanup_policy.cleanup_interval,
            },
            message="会话状态正常",
        )
