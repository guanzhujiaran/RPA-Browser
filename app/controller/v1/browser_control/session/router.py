from app.models.runtime.control import BrowserSessionStatusData
from app.services.RPA_browser.session.live_service import live_service
from app.services.RPA_browser.session.session_status_bus import (
    iter_session_status_events,
)
from fastapi import Depends, BackgroundTasks
from fastapi.responses import StreamingResponse
import time
from app.config import settings
from app.models.runtime.control import (
    CreateSessionResponse,
    CloseSessionResponse,
    BrowserSessionStatus,
    BrowserLaunchQueueStatusResponse,
)
from bili_common.models.response import (
    StandardResponse,
    success_response,
    error_response,
)
from bili_common.models.response_code import ResponseCode
from app.models.router.router_prefix import BrowserSessionRouterPath
from app.services.RPA_browser.session.launch_queue import get_launch_queue
from app.services.RPA_browser.session.live_service import LiveService
from app.utils.depends.mid_depends import AuthInfo, get_auth_info_from_header
from app.utils.depends.vip_depends import is_vip_user
from app.utils.depends.security_depends import verify_browser_ownership
from bili_common.models.depends import BrowserReqInfo, BrowserReqAuthInfo
from ..base import new_session_router

router = new_session_router()


@router.post(
    BrowserSessionRouterPath.create,
    response_model=StandardResponse[CreateSessionResponse],
)
async def create_browser_session(
    background_tasks: BackgroundTasks,
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    browser_info: BrowserReqAuthInfo = Depends(verify_browser_ownership),
):
    """
    创建浏览器会话

    独立的会话创建接口，与心跳机制完全解耦。
    如果浏览器未运行，将创建任务添加到后台任务中异步执行，立即返回响应。

    Args:
        request: 会话创建参数请求
        background_tasks: FastAPI 后台任务

    Returns:
        CreateSessionResponse: 会话创建结果
    """
    # 检查会话是否已存在
    session_key = f"{auth_info.mid}_{browser_info.browser_id}"

    if session_key in LiveService._browser_sessions:
        # 会话已存在，返回现有会话信息
        entry = LiveService._browser_sessions[session_key]
        created_at = getattr(entry, "created_at", entry.last_activity)
        expires_at = getattr(entry, "expires_at", None)
        # 获取当前会话状态，确保返回的是实际运行状态
        session_status = getattr(entry, "status", None)
        status_value = session_status.value if session_status else "running"

        response_data = CreateSessionResponse(
            success=True,
            session_id=session_key,
            browser_started=True,
            status=status_value,
            created_at=created_at,
            expires_at=expires_at,
            message="会话已存在，返回现有会话信息",
        )
        return success_response(data=response_data)

    # 🔑 VIP 身份决定内存不足时进入哪条启动队列（VIP 队列优先）
    is_vip = is_vip_user(auth_info)
    result = await LiveService.create_browser_session(
        live_service,
        auth_info.mid,
        int(browser_info.browser_id),
        is_vip=is_vip,
    )

    current_time = int(time.time())
    expiration_time = settings.browser_session_expiration_time
    expires_at = current_time + expiration_time if expiration_time else None

    # 创建失败（如排队超时）：返回业务错误码，由前端展示提示
    if not result.success:
        return error_response(
            code=ResponseCode(result.error_code)
            if result.error_code
            else ResponseCode.INTERNAL_ERROR,
            msg=result.error or "创建浏览器会话失败",
            data=CreateSessionResponse(
                success=False,
                session_id=session_key,
                browser_started=False,
                status="failed",
                created_at=current_time,
                expires_at=None,
                message=result.message or "创建浏览器会话失败",
            ),
        )

    # 内存不足时返回 queued + 排位，真正的启动在后台进行，前端轮询 /status 获取进度
    response_data = CreateSessionResponse(
        success=True,
        session_id=session_key,
        browser_started=result.browser_started,
        status="queued" if result.queued else "running",
        created_at=result.created_at or current_time,
        expires_at=result.expires_at if result.expires_at else expires_at,
        message=result.message or "浏览器会话已创建",
        queued=result.queued,
        queue_type=result.queue_type,
        queue_position=result.queue_position,
    )

    return success_response(data=response_data)


@router.post(
    BrowserSessionRouterPath.status,
    response_model=StandardResponse[BrowserSessionStatus],
)
async def browser_session_status(
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    browser_info: BrowserReqAuthInfo = Depends(verify_browser_ownership),
):
    """
    获取浏览器会话状态

    提供统一的会话状态查询，包含所有相关的状态信息。
    可以用来检查会话是否存在、浏览器是否运行、生命周期状态等。

    Returns:
        BrowserSessionStatus: 会话状态信息
    """
    status_data: BrowserSessionStatusData = live_service.get_browser_session_status(
        auth_info.mid, int(browser_info.browser_id)
    )

    # 会话状态查询是**只读语义**：「会话不存在」「浏览器未运行」都是正常状态，不是错误。
    # 状态完全由 data 的 session_exists / browser_running / lifecycle_state / status 表达，
    # 因此这里恒返回成功码，不再用错误码表达状态。
    #
    # 历史问题：此前返回 SESSION_NOT_FOUND(1006) / BROWSER_NOT_STARTED(1007)，
    # 而 add_error_status_middleware 会把业务码回写成 HTTP 状态
    # （http_status_for_code 对 1000+ 自定义码兜底为 400），于是「尚未建立会话」这个
    # 高频正常轮询结果变成了 HTTP 400，前端与日志都会按失败处理。
    return success_response(
        data=status_data,
        msg=status_data.message or "获取会话状态成功",
    )


@router.get(
    BrowserSessionRouterPath.events,
    response_class=StreamingResponse,
    # 必须显式声明 SSE 内容类型：
    # 1) openapi.json / Swagger 才能如实描述契约（否则被记成 application/json）；
    # 2) hey-api 依据响应 mediaType === "text/event-stream" 才会生成 client.sse.* 方法
    #    （见 @hey-api/openapi-ts 的 SSE 判定），否则会生成一个会读满响应体的普通 .get()。
    responses={
        200: {
            "description": "会话状态 SSE 事件流（text/event-stream 长连接）",
            "content": {"text/event-stream": {}},
        }
    },
)
async def browser_session_events(
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    browser_info: BrowserReqAuthInfo = Depends(verify_browser_ownership),
):
    """订阅浏览器会话状态事件流（Server-Sent Events）。

    替代前端 20s 生命周期轮询：会话状态发生变更时由服务端主动推送全量快照，
    建连时先下发一帧当前状态（等价于一次 /status），前端无需再补请求。

    事件名固定 `session_status`，`data` 为 /status 的 data 全量快照；
    无状态变化时下发 SSE 注释行作为心跳（详见 docs/rpa-会话状态SSE推送计划书.md）。

    Returns:
        StreamingResponse: text/event-stream 长连接
    """
    mid = int(auth_info.mid)
    browser_id = int(browser_info.browser_id)

    initial = live_service.get_browser_session_status(mid, browser_id)

    # 流式响应与统一 envelope（StandardResponse）互斥，故不声明 response_model
    return StreamingResponse(
        iter_session_status_events(
            mid,
            browser_id,
            initial,
            heartbeat_interval=settings.browser_session_sse_heartbeat_interval,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # 若后续前面挂 nginx，必须关闭响应缓冲，否则推送会被攒批
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    BrowserSessionRouterPath.queue_status,
    response_model=StandardResponse[BrowserLaunchQueueStatusResponse],
)
async def browser_launch_queue_status(
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    browser_info: BrowserReqAuthInfo = Depends(verify_browser_ownership),
):
    """
    查询浏览器启动排队状态

    内存不足时浏览器启动会进入排队（VIP 队列优先于普通队列），本接口返回：

    - 当前会话：是否在排队、所在队列（vip / normal）、排位、已等待时长、当前阶段
      （queued=排队等待，launching=已放行正在启动）；
    - 服务器整体：两条队列的等待数、正在启动数、可用内存水位、排队最大等待时长。

    仅查询、不产生副作用：未排队时 in_queue=False，全局字段依然可用，
    前端可据此展示「服务器繁忙」等提示。

    Returns:
        BrowserLaunchQueueStatusResponse: 启动排队状态
    """
    queue_data = live_service.get_launch_queue_status(
        auth_info.mid, int(browser_info.browser_id)
    )

    if not queue_data.enabled:
        msg = "内存准入排队未启用，浏览器启动无排队"
    elif queue_data.in_queue:
        msg = "当前会话正在启动队列中"
    else:
        msg = "当前会话未在启动队列中"

    return success_response(data=queue_data, msg=msg)


@router.post(
    BrowserSessionRouterPath.close,
    response_model=StandardResponse[CloseSessionResponse],
)
async def close_browser_session(
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    browser_info: BrowserReqAuthInfo = Depends(verify_browser_ownership),
):
    """
    手动关闭浏览器会话

    主动关闭指定的浏览器会话，释放相关资源。
    如果会话不存在，将返回错误响应。

    Returns:
        CloseSessionResponse: 会话关闭结果
    """
    import time

    session_key = f"{auth_info.mid}_{browser_info.browser_id}"
    browser_id = int(browser_info.browser_id)

    # 🔑 仍在启动队列中排队：关闭即取消排队，避免「关掉又被拉起」
    if session_key not in LiveService._browser_sessions:
        cancelled = await get_launch_queue().cancel(auth_info.mid, browser_id)
        if cancelled:
            return success_response(
                data=CloseSessionResponse(
                    success=True,
                    session_id=session_key,
                    browser_id=browser_info.browser_id,
                    mid=auth_info.mid,
                    closed_at=int(time.time()),
                    message="已取消浏览器启动排队",
                )
            )
        return error_response(
            code=ResponseCode.SESSION_NOT_FOUND,
            msg="浏览器会话不存在",
            data=CloseSessionResponse(
                success=False,
                session_id=session_key,
                browser_id=browser_info.browser_id,
                mid=auth_info.mid,
                closed_at=int(time.time()),
                message="浏览器会话不存在",
            ),
        )

    # 释放浏览器会话
    success = await live_service.release_browser_session(
        auth_info.mid, int(browser_info.browser_id)
    )

    current_time = int(time.time())

    if success:
        response_data = CloseSessionResponse(
            success=True,
            session_id=session_key,
            browser_id=browser_info.browser_id,
            mid=auth_info.mid,
            closed_at=current_time,
            message="浏览器会话已成功关闭",
        )
        return success_response(data=response_data)
    else:
        return error_response(
            code=ResponseCode.INTERNAL_ERROR,
            msg="关闭浏览器会话失败",
            data=CloseSessionResponse(
                success=False,
                session_id=session_key,
                browser_id=browser_info.browser_id,
                mid=auth_info.mid,
                closed_at=current_time,
                message="关闭浏览器会话时发生错误",
            ),
        )
