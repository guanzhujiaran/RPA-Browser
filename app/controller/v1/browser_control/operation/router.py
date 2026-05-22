"""
浏览器操作控制路由

提供浏览器的基础操作控制功能：打开页面、关闭页面、切换页面、执行JavaScript等
"""
from loguru import logger
from typing import Any, Dict
from app.models.response import StandardResponse, success_response, error_response
from app.models.router.router_prefix import BrowserControlRouterPath
from app.services.RPA_browser.live_service import LiveService
from app.utils.depends.mid_depends import get_auth_info_from_header, AuthInfo
from app.utils.depends.security_depends import verify_browser_ownership
from app.models.common.depends import BrowserReqInfo, BrowserReqAuthInfo
from ..base import new_operation_router
from pydantic import BaseModel, Field
from fastapi import Depends

router = new_operation_router()


# ============ 浏览器操作请求模型 ============


class OpenPageRequest(BaseModel):
    """打开页面请求"""
    url: str = Field(..., description="要打开的URL")
    page_index: int = Field(0, description="页面索引，-1表示新建页面")


class ClosePageRequest(BaseModel):
    """关闭页面请求"""
    page_index: int = Field(0, description="要关闭的页面索引")


class SwitchPageRequest(BaseModel):
    """切换页面请求"""
    page_index: int = Field(0, description="目标页面索引")


class ExecuteJSRequest(BaseModel):
    """执行JavaScript请求"""
    script: str = Field(..., description="要执行的JavaScript代码")
    page_index: int = Field(0, description="执行脚本的页面索引")


class GetPageInfoRequest(BaseModel):
    """获取页面信息请求"""
    page_index: int = Field(0, description="页面索引")


class BrowserInfoResponse(BaseModel):
    """浏览器信息响应"""
    browser_id: str = Field(..., description="浏览器实例ID")
    mid: int = Field(..., description="用户ID")
    user_data_dir: str | None = Field(None, description="用户数据目录")
    is_headless: bool = Field(False, description="是否为无头模式")
    browser_type: str = Field("chromium", description="浏览器类型")
    version: str = Field("", description="浏览器版本")
    user_agent: str = Field("", description="User-Agent")


# ============ 浏览器操作 API ============


@router.post("/operation/open_page", summary="打开页面")
async def open_page(
    request: OpenPageRequest,
    browser_req: BrowserReqAuthInfo = Depends(verify_browser_ownership)
) -> StandardResponse[dict]:
    """在浏览器中打开指定URL"""
    mid = browser_req.auth_info.mid
    browser_id = browser_req.browser_id
    
    try:
        # 获取会话
        session_key = LiveService._get_session_key(mid, browser_id)
        if session_key not in LiveService.browser_sessions:
            return error_response(404, "会话不存在")
        
        entry = LiveService.browser_sessions[session_key]
        
        # 如果 page_index 为 -1，新建页面
        if request.page_index < 0:
            page = await entry.plugined_session.browser.new_page()
            page_index = len(entry.plugined_session.browser.pages) - 1
        else:
            # 获取指定页面
            pages = entry.plugined_session.browser.pages
            if request.page_index >= len(pages):
                return error_response(400, "页面索引超出范围")
            page = pages[request.page_index]
            page_index = request.page_index
        
        # 导航到URL
        await page.goto(request.url)
        
        return success_response({
            "page_index": page_index,
            "url": request.url,
            "message": "页面打开成功"
        })
        
    except Exception as e:
        logger.error(f"打开页面失败: {e}")
        return error_response(500, str(e))


@router.post("/operation/close_page", summary="关闭页面")
async def close_page(
    request: ClosePageRequest,
    browser_req: BrowserReqAuthInfo = Depends(verify_browser_ownership)
) -> StandardResponse[dict]:
    """关闭指定页面"""
    mid = browser_req.auth_info.mid
    browser_id = browser_req.browser_id
    
    try:
        session_key = LiveService._get_session_key(mid, browser_id)
        if session_key not in LiveService.browser_sessions:
            return error_response(404, "会话不存在")
        
        entry = LiveService.browser_sessions[session_key]
        pages = entry.plugined_session.browser.pages
        
        if request.page_index >= len(pages):
            return error_response(400, "页面索引超出范围")
        
        # 不能关闭最后一个页面
        if len(pages) <= 1:
            return error_response(400, "无法关闭最后一个页面")
        
        await pages[request.page_index].close()
        
        return success_response({"message": "页面关闭成功"})
        
    except Exception as e:
        logger.error(f"关闭页面失败: {e}")
        return error_response(500, str(e))


@router.post("/operation/switch_page", summary="切换页面")
async def switch_page(
    request: SwitchPageRequest,
    browser_req: BrowserReqAuthInfo = Depends(verify_browser_ownership)
) -> StandardResponse[dict]:
    """切换到指定页面"""
    mid = browser_req.auth_info.mid
    browser_id = browser_req.browser_id
    
    try:
        session_key = LiveService._get_session_key(mid, browser_id)
        if session_key not in LiveService.browser_sessions:
            return error_response(404, "会话不存在")
        
        entry = LiveService.browser_sessions[session_key]
        pages = entry.plugined_session.browser.pages
        
        if request.page_index >= len(pages):
            return error_response(400, "页面索引超出范围")
        
        await pages[request.page_index].bring_to_front()
        
        return success_response({
            "page_index": request.page_index,
            "message": "页面切换成功"
        })
        
    except Exception as e:
        logger.error(f"切换页面失败: {e}")
        return error_response(500, str(e))


@router.post("/operation/execute_js", summary="执行JavaScript")
async def execute_js(
    request: ExecuteJSRequest,
    browser_req: BrowserReqAuthInfo = Depends(verify_browser_ownership)
) -> StandardResponse[dict]:
    """在指定页面执行JavaScript代码"""
    mid = browser_req.auth_info.mid
    browser_id = browser_req.browser_id
    
    try:
        session_key = LiveService._get_session_key(mid, browser_id)
        if session_key not in LiveService.browser_sessions:
            return error_response(404, "会话不存在")
        
        entry = LiveService.browser_sessions[session_key]
        pages = entry.plugined_session.browser.pages
        
        if request.page_index >= len(pages):
            return error_response(400, "页面索引超出范围")
        
        result = await pages[request.page_index].evaluate(request.script)
        
        return success_response({
            "result": result,
            "message": "JavaScript执行成功"
        })
        
    except Exception as e:
        logger.error(f"执行JavaScript失败: {e}")
        return error_response(500, str(e))


@router.post("/operation/get_page_info", summary="获取页面信息")
async def get_page_info(
    request: GetPageInfoRequest,
    browser_req: BrowserReqAuthInfo = Depends(verify_browser_ownership)
) -> StandardResponse[dict]:
    """获取指定页面的信息"""
    mid = browser_req.auth_info.mid
    browser_id = browser_req.browser_id
    
    try:
        session_key = LiveService._get_session_key(mid, browser_id)
        if session_key not in LiveService.browser_sessions:
            return error_response(404, "会话不存在")
        
        entry = LiveService.browser_sessions[session_key]
        pages = entry.plugined_session.browser.pages
        
        if request.page_index >= len(pages):
            return error_response(400, "页面索引超出范围")
        
        page = pages[request.page_index]
        url = await page.evaluate("document.URL")
        title = await page.title()
        cookies = await page.cookies()
        
        return success_response({
            "page_index": request.page_index,
            "url": url,
            "title": title,
            "cookies_count": len(cookies),
            "message": "获取页面信息成功"
        })
        
    except Exception as e:
        logger.error(f"获取页面信息失败: {e}")
        return error_response(500, str(e))


# ============ browser/info API ============


@router.post(BrowserControlRouterPath.browser_info, summary="获取浏览器信息")
async def get_browser_info(
    browser_req: BrowserReqAuthInfo = Depends(verify_browser_ownership)
) -> StandardResponse[BrowserInfoResponse]:
    """获取当前浏览器实例的详细信息"""
    mid = browser_req.auth_info.mid
    browser_id = browser_req.browser_id
    
    try:
        session_key = LiveService._get_session_key(mid, browser_id)
        if session_key not in LiveService.browser_sessions:
            return error_response(404, "会话不存在")
        
        entry = LiveService.browser_sessions[session_key]
        browser = entry.plugined_session.browser
        
        # 获取浏览器版本信息
        version_info = await browser.version()
        user_agent = await browser.user_agent()
        
        # 获取用户数据目录（如果可用）
        user_data_dir = None
        if hasattr(browser, '_user_data_dir'):
            user_data_dir = str(browser._user_data_dir)
        
        return success_response(BrowserInfoResponse(
            browser_id=browser_id,
            mid=mid,
            user_data_dir=user_data_dir,
            is_headless=browser.is_headless,
            browser_type="chromium",
            version=version_info.get('browserVersion', ''),
            user_agent=user_agent
        ))
        
    except Exception as e:
        logger.error(f"获取浏览器信息失败: {e}")
        return error_response(500, str(e))
