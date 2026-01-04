from fastapi import Depends, Response
from app.models.RPA_browser.live_control_models import (
    VideoStreamStatusResponse,
)
from app.models.RPA_browser.simplified_models import (
    SimplifiedScreenshotRequest,
)
from app.models.response import StandardResponse, success_response
from app.models.router.router_prefix import BrowserControlRouterPath
from app.services.RPA_browser.live_service import LiveService
from app.utils.depends.mid_depends import get_auth_info_from_header, AuthInfo
from app.utils.depends.security_depends import verify_browser_ownership
from app.models.RPA_browser.depends_models import BrowserReqInfo
from app.controller.v1.browser_control.base import new_router
from app.config import settings

router = new_router()


@router.post(
    BrowserControlRouterPath.stream_status,
    response_model=StandardResponse[VideoStreamStatusResponse],
)
async def get_video_stream_status(
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    browser_info: BrowserReqInfo = Depends(verify_browser_ownership),
):
    """
    检查浏览器视频流状态

    检查浏览器实例是否启动，如果启动则返回视频流URL。

    Returns:
        dict: 浏览器状态和视频流信息
    """
    # 检查浏览器会话状态
    session_status = LiveService.get_browser_session_status(
        auth_info.mid, browser_info.browser_id
    )

    if session_status.session_exists and session_status.browser_running:
        return success_response(
            data=VideoStreamStatusResponse(
                browser_id=browser_info.browser_id,
                status="running",
                stream_url=f"{settings.controller_base_path}{router.prefix}{BrowserControlRouterPath.stream_mjpeg}?browser_id={browser_info.browser_id}",
                message="浏览器正在运行，可以获取视频流",
                active_connections=session_status.active_connections,
            )
        )


@router.post(BrowserControlRouterPath.screenshot)
async def get_screenshot(
    request: SimplifiedScreenshotRequest,
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    browser_info: BrowserReqInfo = Depends(verify_browser_ownership),
):
    """
    获取浏览器实例的当前界面截图

    获取指定浏览器实例当前页面的截图，返回JPEG格式的图像数据。
    可自定义图片质量，用于快速查看浏览器当前状态或保存快照。

    Args:
        request: 包含browser_id和图片质量的请求

    Returns:
        Response: JPEG格式的图片数据

    Note:
        返回的图片不会缓存，每次都是最新的页面状态
    """
    # 获取浏览器会话
    plugined_session = await LiveService.get_plugined_session(
        auth_info.mid, browser_info.browser_id, headless=False
    )

    # 获取当前页面
    page = await plugined_session.get_current_page()

    # 截图 - 默认截取整个页面
    screenshot_bytes = await page.screenshot(
        type="jpeg",
        quality=request.quality,
        full_page=request.full_page
    )

    # 返回图片响应
    return Response(
        content=screenshot_bytes,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )
