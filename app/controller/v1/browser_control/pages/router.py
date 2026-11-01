"""
页面管理路由 - 提供页面列表、切换、关闭等功能
"""

from numpy.ma import count
from app.services.RPA_browser.session.live_service import live_service
from fastapi import Depends
from bili_common.models.depends import BrowserReqAuthInfo
from bili_common.models.response import (
    StandardResponse,
    success_response,
    error_response,
)
from bili_common.models.response_code import ResponseCode
from app.services.RPA_browser.session.live_service import LiveService
from app.services.RPA_browser.session.page_registry import page_registry
from app.utils.depends.security_depends import verify_browser_ownership
from app.utils.depends.vip_depends import is_vip_user
from ..base import new_webrtc_router
import loguru
from app.models.runtime.browser_operation import ClosePageRequest, SwitchPageRequest
from app.models.runtime.control import PagesListResponse

router = new_webrtc_router()


@router.post(
    "/pages/list",
    summary="获取页面列表",
    response_model=StandardResponse[PagesListResponse],
)
async def get_pages_list(
    browser_info: BrowserReqAuthInfo = Depends(verify_browser_ownership),
):
    """
    获取浏览器所有页面列表

    返回指定浏览器实例的全部页面信息（索引、URL、标题、是否当前活动页）。
    会话不存在时返回空列表，不报错。

    Args:
        browser_info: 浏览器认证信息（query browser_id）

    Returns:
        PagesListResponse: 页面列表
    """
    browser_id, mid = browser_info.browser_id, browser_info.auth_info.mid

    try:
        # 获取浏览器会话
        entry = live_service.get_browser_session_entry(mid, int(browser_id))
        if not entry:
            return success_response(data=PagesListResponse())
        # 🔑 调用 service 层方法获取页面列表（包含是否激活）
        pages = await entry.browser_session.get_all_page_infos()

        return success_response(data=PagesListResponse(pages=pages))
    except Exception as e:
        loguru.logger.error(f"获取页面列表失败: {e}")
        return error_response(
            code=ResponseCode.GET_BROWSER_INFO_FAILED,
            msg=f"获取页面列表失败: {str(e)}",
        )


@router.post(
    "/pages/switch",
    summary="切换页面",
    response_model=StandardResponse[dict],
)
async def switch_page(
    request: SwitchPageRequest,
    browser_info: BrowserReqAuthInfo = Depends(verify_browser_ownership),
):
    """切换到指定索引的页面

    把 `page_index` 指向的页面置为前台（bring_to_front）；索引越界或目标页已关闭时
    返回 PAGE_NAVIGATION_FAILED，不做静默兜底。
    与 `rpa_operation_switch_page` 语义相同（后者参数模型更宽松），二者任一皆可。

    Args:
        request: {"page_index": <页面索引，0 基>}
        browser_info: 浏览器认证信息（query browser_id）

    Returns:
        dict: 切换结果（含 page_index）
    """
    browser_id, mid = browser_info.browser_id, browser_info.auth_info.mid

    try:
        # 🔑 内存不足时不阻塞在排队上，直接返回排队业务码
        if live_service.would_queue_browser_session(
            mid, int(browser_id), is_vip_user(browser_info.auth_info)
        ):
            return error_response(
                code=ResponseCode.BROWSER_LAUNCH_MEMORY_INSUFFICIENT,
                msg="当前服务器内存不足，浏览器启动请求已进入排队，请稍后重试",
            )

        # 🔑 获取浏览器会话
        entry = await live_service.get_or_create_browser_session_entry(
            mid,
            int(browser_id),
            headless=False,
            is_vip=is_vip_user(browser_info.auth_info),
        )

        pages = entry.browser_session.all_pages
        if not (0 <= request.page_index < len(pages)):
            return error_response(
                code=ResponseCode.PAGE_NAVIGATION_FAILED,
                msg=f"页面索引 {request.page_index} 超出范围（共 {len(pages)} 个页面）",
            )

        page = pages[request.page_index]
        if page.is_closed():
            return error_response(
                code=ResponseCode.PAGE_NAVIGATION_FAILED,
                msg=f"页面索引 {request.page_index} 已关闭，请重新获取页面列表",
            )

        # 切换页面
        await page.bring_to_front()

        return success_response(
            data={
                "page_index": request.page_index,
                "message": "页面切换成功",
                "note": "如果使用 WebRTC 视频流，请重新调用 /webrtc/offer 接口",
            }
        )

    except Exception as e:
        loguru.logger.error(f"切换页面失败: {e}")
        return error_response(
            code=ResponseCode.PAGE_NAVIGATION_FAILED,
            msg=f"切换页面失败: {str(e)}",
        )


@router.post(
    "/pages/close",
    summary="关闭页面",
    response_model=StandardResponse[dict],
)
async def close_page(
    request: ClosePageRequest,
    browser_info: BrowserReqAuthInfo = Depends(verify_browser_ownership),
):
    """关闭指定索引的页面

    两种情形会被拒绝（返回 PAGE_CLOSED），**不会**自动补建空白页：
    - 该页正被工作流占用（别名页 / open_page 创建）；
    - 它是最后一个页面（关掉后浏览器就没有页面可用了）。

    与 `rpa_operation_close_page` 语义相同，二者任一皆可。

    Args:
        request: {"page_index": <页面索引，0 基>}
        browser_info: 浏览器认证信息（query browser_id）

    Returns:
        dict: 关闭结果（含 page_index）
    """
    browser_id, mid = browser_info.browser_id, browser_info.auth_info.mid
    page_index = request.page_index

    try:
        # 🔑 内存不足时不阻塞在排队上，直接返回排队业务码
        if live_service.would_queue_browser_session(
            mid, int(browser_id), is_vip_user(browser_info.auth_info)
        ):
            return error_response(
                code=ResponseCode.BROWSER_LAUNCH_MEMORY_INSUFFICIENT,
                msg="当前服务器内存不足，浏览器启动请求已进入排队，请稍后重试",
            )

        # 获取浏览器会话
        session = await live_service.get_or_create_browser_session_entry(
            mid,
            int(browser_id),
            headless=False,
            is_vip=is_vip_user(browser_info.auth_info),
        )

        # 工作流占用检查：别名页（工作流 open_page 创建）不允许手动关闭
        pages = session.browser_session.all_pages
        if not 0 <= page_index < len(pages):
            return error_response(
                code=ResponseCode.PAGE_CLOSED,
                msg=f"页面索引 {page_index} 超出范围",
            )
        alias = page_registry.find_alias((mid, int(browser_id)), pages[page_index])
        if alias:
            return error_response(
                code=ResponseCode.PAGE_CLOSED,
                msg=f"页面 {alias!r} 正在被工作流使用，不能手动关闭",
            )
        if len(pages) <= 1:
            return error_response(
                code=ResponseCode.PAGE_CLOSED, msg="无法关闭最后一个页面"
            )

        target = pages[page_index]
        if not target.is_closed():
            await target.close()
        return success_response(
            data={"page_index": page_index, "message": "页面关闭成功"}
        )
    except Exception as e:
        loguru.logger.error(f"关闭页面失败: {e}")
        return error_response(
            code=ResponseCode.PAGE_CLOSED,
            msg=f"关闭页面失败: {str(e)}",
        )
