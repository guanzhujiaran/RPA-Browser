"""
页面管理路由 - 提供页面列表、切换、关闭等功能
"""

from fastapi import Depends
from app.models.common.depends import BrowserReqAuthInfo
from app.models.response import StandardResponse, success_response, error_response
from app.models.response_code import ResponseCode
from app.models.router.router_prefix import BrowserControlRouterPath
from app.services.RPA_browser.live_service import LiveService
from app.services.RPA_browser.rpa_operation_service import RPAOperationService
from app.utils.depends.security_depends import verify_browser_ownership
from ..base import new_webrtc_router
import loguru

router = new_webrtc_router()


@router.post(
    "/pages/list",
    response_model=StandardResponse[dict],
)
async def get_pages_list(
    browser_info: BrowserReqAuthInfo = Depends(verify_browser_ownership),
):
    """
    获取浏览器所有页面列表
    
    返回指定浏览器实例的所有页面信息，包括URL、标题等。
    
    Args:
        browser_info: 浏览器认证信息
        
    Returns:
        dict: 页面列表信息
    """
    browser_id, mid = browser_info.browser_id, browser_info.auth_info.mid
    
    try:
        # 获取浏览器会话
        session = await LiveService.get_or_create_browser_session(
            mid, browser_id, headless=False
        )
        
        # 🔑 调用 service 层方法获取页面列表（包含是否激活）
        pages_data = await RPAOperationService.get_pages_list(session)
        
        return success_response(data=pages_data)
    except Exception as e:
        loguru.logger.error(f"获取页面列表失败: {e}")
        return error_response(
            code=ResponseCode.GET_BROWSER_INFO_FAILED,
            msg=f"获取页面列表失败: {str(e)}",
        )


@router.post(
    "/pages/switch",
    response_model=StandardResponse[dict],
)
async def switch_page(
    request: dict,
    browser_info: BrowserReqAuthInfo = Depends(verify_browser_ownership),
):
    """
    切换到指定页面
    
    将浏览器的当前活动页面切换到指定索引的页面。
    
    Args:
        request: 包含 page_index 的请求体
        browser_info: 浏览器认证信息
        
    Returns:
        dict: 切换结果
    """
    browser_id, mid = browser_info.browser_id, browser_info.auth_info.mid
    page_index = request.get("page_index")
    
    if page_index is None:
        return error_response(
            code=ResponseCode.PAGE_NAVIGATION_FAILED,
            msg="缺少 page_index 参数",
        )
    
    try:
        # 🔑 获取浏览器会话
        session = await LiveService.get_or_create_browser_session(
            mid, browser_id, headless=False
        )
        
        # 验证浏览器是否可用
        page = await session.get_current_page()
        if not page or page.is_closed():
            return error_response(
                code=ResponseCode.BROWSER_NOT_STARTED,
                msg="浏览器已关闭，请重新启动",
            )
        
        # 切换页面
        result = await RPAOperationService.switch_page(session, page_index)
        
        if result.success:
            # 🔑 注意：WebRTC 方案下，客户端需要重新建立连接
            return success_response(
                data={
                    **result.data,
                    "note": "如果使用 WebRTC 视频流，请重新调用 /webrtc/offer 接口"
                }
            )
        else:
            return error_response(
                code=ResponseCode.PAGE_NAVIGATION_FAILED,
                msg=result.error,
            )
    except Exception as e:
        loguru.logger.error(f"切换页面失败: {e}")
        return error_response(
            code=ResponseCode.PAGE_NAVIGATION_FAILED,
            msg=f"切换页面失败: {str(e)}",
        )



@router.post(
    "/pages/close",
    response_model=StandardResponse[dict],
)
async def close_page(
    request: dict,
    browser_info: BrowserReqAuthInfo = Depends(verify_browser_ownership),
):
    """
    关闭指定页面
    
    关闭指定索引的页面。如果关闭后没有页面了，会创建一个新的空白页面。
    
    Args:
        request: 包含 page_index 的请求体
        browser_info: 浏览器认证信息
        
    Returns:
        dict: 关闭结果
    """
    browser_id, mid = browser_info.browser_id, browser_info.auth_info.mid
    page_index = request.get("page_index")
    
    if page_index is None:
        return error_response(
            code=ResponseCode.PAGE_CLOSED,
            msg="缺少 page_index 参数",
        )
    
    try:
        # 获取浏览器会话
        session = await LiveService.get_or_create_browser_session(
            mid, browser_id, headless=False
        )
        
        # 关闭页面
        result = await RPAOperationService.close_page_by_index(session, page_index)
        
        if result.success:
            return success_response(data=result.data)
        else:
            return error_response(
                code=ResponseCode.PAGE_CLOSED,
                msg=result.error,
            )
    except Exception as e:
        loguru.logger.error(f"关闭页面失败: {e}")
        return error_response(
            code=ResponseCode.PAGE_CLOSED,
            msg=f"关闭页面失败: {str(e)}",
        )
