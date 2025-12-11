from app.config import settings
from app.controller.v1.browser_control.base import new_router
from app.models.response import StandardResponse, success_response, error_response
from app.models.RPA_browser.browser_info_model import (
    BrowserOpenUrlParams,
    BrowserOpenUrlResp,
    BrowserScreenshotParams,
    BrowserScreenshotResp,
    BrowserReleaseParams,
    BrowserReleaseResp,
    LiveCreateParams,
    LiveCreateResp,
)
from app.models.router.router_prefix import BrowserControlRouterPath
import uuid
from fastapi import WebSocket, Query
from fastapi.responses import StreamingResponse

from app.models.response_code import ResponseCode
from app.services.RPA_browser.live_service import LiveService
from app.services.RPA_browser.browser_session_pool.playwright_pool import (
    get_default_session_pool,
)
from app.services.RPA_browser.browser_db_service import BrowserDBService
from app.utils.depends.session_manager import DatabaseSessionManager
from app.models.RPA_browser.browser_info_model import UserBrowserInfoListParams

router = new_router()


@router.post(
    BrowserControlRouterPath.open_url,
    response_model=StandardResponse[BrowserOpenUrlResp],
)
async def open_url_router(
    params: BrowserOpenUrlParams, session=DatabaseSessionManager.get_dependency()
):
    """
    打开URL - 根据browser_token和browser_id确定唯一的浏览器实例
    """
    # 验证browser_token和browser_id的组合是否存在
    fingerprint_info = await BrowserDBService.read_fingerprint(
        params=type(
            "", (), {"browser_token": params.browser_token, "id": params.browser_id}
        )(),
        session=session,
    )

    if not fingerprint_info:
        return error_response(
            code=ResponseCode.NOT_FOUND,
            msg=f"浏览器实例不存在: browser_token={params.browser_token}, browser_id={params.browser_id}",
        )

    try:
        # 获取会话池和指定浏览器实例
        pool = get_default_session_pool()
        session_params = type(
            "",
            (),
            {
                "browser_token": params.browser_token,
                "browser_id": params.browser_id,
                "headless": params.headless,
            },
        )()

        plugined_session = await pool.get_session(session_params)
        page = await plugined_session.get_current_page()

        # 导航到指定URL
        await page.goto(params.url)

        # 获取页面信息
        title = await page.title()
        current_url = page.url

        return success_response(
            data=BrowserOpenUrlResp(title=title, current_url=current_url)
        )

    except Exception as e:
        return error_response(
            code=ResponseCode.INTERNAL_ERROR, msg=f"打开URL失败: {str(e)}"
        )


@router.post(
    BrowserControlRouterPath.screenshot,
    response_model=StandardResponse[BrowserScreenshotResp],
)
async def screenshot_router(
    params: BrowserScreenshotParams, session=DatabaseSessionManager.get_dependency()
):
    """
    截图 - 根据browser_token和browser_id确定唯一的浏览器实例
    """
    # 验证browser_token和browser_id的组合是否存在
    fingerprint_info = await BrowserDBService.read_fingerprint(
        params=type(
            "",
            (),
            {"browser_token": params.browser_token, "id": int(params.browser_id)},
        )(),
        session=session,
    )

    if not fingerprint_info:
        return error_response(
            code=ResponseCode.NOT_FOUND,
            msg=f"浏览器实例不存在: browser_token={params.browser_token}, browser_id={params.browser_id}",
        )

    try:
        # 获取会话池和指定浏览器实例
        pool = get_default_session_pool()
        session_params = type(
            "",
            (),
            {
                "browser_token": params.browser_token,
                "browser_id": int(params.browser_id),
                "headless": params.headless,
            },
        )()

        plugined_session = await pool.get_session(session_params)
        page = await plugined_session.get_current_page()

        # 截图
        screenshot_bytes = await page.screenshot(
            full_page=params.full_page, type=params.type or "png"
        )

        # 转换为base64
        import base64

        image_base64 = base64.b64encode(screenshot_bytes).decode("utf-8")

        return success_response(data=BrowserScreenshotResp(image_base64=image_base64))

    except Exception as e:
        return error_response(
            code=ResponseCode.INTERNAL_ERROR, msg=f"截图失败: {str(e)}"
        )


@router.post(
    BrowserControlRouterPath.release,
    response_model=StandardResponse[BrowserReleaseResp],
)
async def release_router(params: BrowserReleaseParams):
    """
    释放浏览器实例 - 根据browser_token和browser_id确定唯一的浏览器实例
    """
    try:
        # 获取会话池
        pool = get_default_session_pool()

        if params.browser_id:
            # 释放特定的browser_id会话
            remove_params = type(
                "",
                (),
                {
                    "browser_token": params.browser_token,
                    "browser_id": int(params.browser_id),
                    "force_close": False,
                },
            )()

            response = await pool.release_session(remove_params)

            return success_response(
                data=BrowserReleaseResp(
                    browser_token=params.browser_token,
                    browser_id=str(params.browser_id),
                    is_success=response.is_closed,
                    success_message=response.feedback,
                )
            )
        else:
            # 释放该browser_token下的所有会话
            base_params = type("", (), {"browser_token": params.browser_token})()
            response = await pool.release_all_session(base_params)

            return success_response(
                data=BrowserReleaseResp(
                    browser_token=params.browser_token,
                    browser_id=None,
                    is_success=True,
                    success_message="所有会话已释放",
                )
            )

    except Exception as e:
        return error_response(
            code=ResponseCode.INTERNAL_ERROR, msg=f"释放会话失败: {str(e)}"
        )


@router.post(
    BrowserControlRouterPath.live_create,
    response_model=StandardResponse[LiveCreateResp],
)
async def live_create_router(
    params: LiveCreateParams, session=DatabaseSessionManager.get_dependency()
):
    """
    创建直播会话 - 根据browser_token和browser_id确定唯一的浏览器实例
    """
    # 验证browser_token和browser_id的组合是否存在
    fingerprint_info = await BrowserDBService.read_fingerprint(
        params=type(
            "",
            (),
            {"browser_token": params.browser_token, "id": int(params.browser_id)},
        )(),
        session=session,
    )
    if not fingerprint_info:
        return error_response(
            code=ResponseCode.NOT_FOUND,
            msg=f"浏览器实例不存在: browser_token={params.browser_token}, browser_id={params.browser_id}",
        )

    try:
        # 创建直播会话
        live_id = await LiveService.create_live_session(
            browser_token=params.browser_token,
            browser_id=str(params.browser_id) if params.browser_id else None,
            headless=params.headless,
        )

        live_url = f"{settings.controller_base_path}{BrowserControlRouterPath.live_view.value}?live_id={live_id}"
        return success_response(data=LiveCreateResp(live_id=live_id, live_url=live_url))

    except Exception as e:
        return error_response(
            code=ResponseCode.INTERNAL_ERROR, msg=f"创建直播会话失败: {str(e)}"
        )


@router.get(BrowserControlRouterPath.live_view, response_model=StandardResponse[dict])
async def live_view_router(live_id: str):
    """
    查看直播会话状态
    """
    entry = LiveService.get_live_entry(live_id)
    if entry is None:
        return error_response(code=ResponseCode.NOT_FOUND, msg="live_id not found")

    return success_response(
        data={
            "live_id": live_id,
            "exists": True,
            "browser_token": entry.browser_token,
            "browser_id": entry.browser_id,
            "headless": entry.headless,
        }
    )


@router.get(BrowserControlRouterPath.live_stream)
async def live_stream_router(live_id: str):
    """
    获取直播视频流
    """
    entry = LiveService.get_live_entry(live_id)
    if entry is None:
        return error_response(code=ResponseCode.NOT_FOUND, msg="live_id not found")

    try:
        frame_generator = await LiveService.generate_video_stream(entry)
        return StreamingResponse(
            frame_generator(), media_type="multipart/x-mixed-replace; boundary=frame"
        )
    except Exception as e:
        return error_response(
            code=ResponseCode.INTERNAL_ERROR, msg=f"生成视频流失败: {str(e)}"
        )


@router.post(BrowserControlRouterPath.live_stop, response_model=StandardResponse[dict])
async def live_stop_router(live_id: str):
    """
    停止直播会话
    """
    # 获取会话条目以获取browser_token
    entry = LiveService.get_live_entry(live_id)
    if entry is None:
        return error_response(code=ResponseCode.NOT_FOUND, msg="live_id not found")

    # 将字符串转换为UUID对象
    try:
        browser_token = uuid.UUID(entry.browser_token)
    except ValueError:
        return error_response(
            code=ResponseCode.BAD_REQUEST, msg="Invalid browser_token format"
        )

    try:
        stopped = await LiveService.stop_live_session(browser_token)
        return success_response(
            data={
                "live_id": live_id,
                "stopped": stopped,
                "browser_token": entry.browser_token,
                "browser_id": entry.browser_id,
            }
        )
    except Exception as e:
        return error_response(
            code=ResponseCode.INTERNAL_ERROR, msg=f"停止直播会话失败: {str(e)}"
        )


@router.websocket(BrowserControlRouterPath.live_ws)
async def live_ws(websocket: WebSocket, live_id: str = Query(...)):
    """
    WebSocket连接用于实时控制浏览器
    """
    entry = LiveService.get_live_entry(live_id)
    if entry is None:
        await websocket.close(code=4404)
        return

    await websocket.accept()

    try:
        # 通过服务层获取页面
        page = await LiveService.get_page_for_entry(entry)

        while True:
            msg = await websocket.receive_text()
            # 使用服务层处理WebSocket消息
            await LiveService.handle_websocket_message(websocket, page, msg)

    except Exception as e:
        # 连接异常时关闭WebSocket
        try:
            await websocket.close()
        except:
            pass
