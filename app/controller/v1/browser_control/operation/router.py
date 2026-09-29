"""
浏览器操作控制路由

提供浏览器的基础操作控制功能：打开页面、关闭页面、切换页面、获取页面信息、清空登录态等。

接口本身只做「取参数 → 干活 → 拼响应」：
- 浏览器归属校验 + 会话存在性 + 刷新活跃时间 + 索引取页，统一由
  `require_active_browser_session` / `ActiveBrowserSession` 依赖承担；
- 出错一律抛业务异常，由统一异常处理器转成 `{code, msg}`，接口内不再手拼错误响应。
"""
from fastapi import Depends
from bili_common.models.response import StandardResponse, success_response, error_response
from bili_common.models.response_code import ResponseCode
from app.models.router.router_prefix import BrowserControlRouterPath
from app.models.runtime.browser_operation import (
    BrowserInfoResponse,
    ClearLoginStateRequest,
    ClearLoginStateResponse,
    ClosePageRequest,
    GetPageInfoRequest,
    OpenPageRequest,
    SwitchPageRequest,
)
from app.services.RPA_browser.session.browser_data_service import BrowserDataService
from app.utils.depends.browser_session_depends import (
    ActiveBrowserSession,
    require_active_browser_session,
)
from ..base import new_operation_router

router = new_operation_router()


# ============ 浏览器操作 API ============


@router.post("/operation/open_page", summary="打开页面")
async def open_page(
    request: OpenPageRequest,
    session: ActiveBrowserSession = Depends(require_active_browser_session()),
) -> StandardResponse[dict]:
    """在浏览器中打开指定URL"""
    # page_index 为负数表示新建页面
    if request.page_index < 0:
        page = await session.browser_session.create_new_page_with_limit()
        page_index = len(session.all_pages) - 1
    else:
        page = session.get_page(request.page_index)
        page_index = request.page_index

    await page.goto(request.url)

    return success_response({
        "page_index": page_index,
        "url": request.url,
        "message": "页面打开成功"
    })


@router.post("/operation/close_page", summary="关闭页面")
async def close_page(
    request: ClosePageRequest,
    session: ActiveBrowserSession = Depends(require_active_browser_session()),
) -> StandardResponse[dict]:
    """关闭指定页面"""
    page = session.get_page(request.page_index)

    # 不能关闭最后一个页面
    if len(session.all_pages) <= 1:
        return error_response(ResponseCode.BAD_REQUEST, "无法关闭最后一个页面")

    await page.close()

    return success_response({"message": "页面关闭成功"})


@router.post("/operation/switch_page", summary="切换页面")
async def switch_page(
    request: SwitchPageRequest,
    session: ActiveBrowserSession = Depends(require_active_browser_session()),
) -> StandardResponse[dict]:
    """切换到指定页面"""
    page = session.get_page(request.page_index)
    await page.bring_to_front()

    return success_response({
        "page_index": request.page_index,
        "message": "页面切换成功"
    })


@router.post("/operation/get_page_info", summary="获取页面信息")
async def get_page_info(
    request: GetPageInfoRequest,
    session: ActiveBrowserSession = Depends(require_active_browser_session()),
) -> StandardResponse[dict]:
    """获取指定页面的信息"""
    page = session.get_page(request.page_index)
    url = await page.evaluate("document.URL")
    title = await page.title()
    cookies = await page.context.cookies()

    return success_response({
        "page_index": request.page_index,
        "url": url,
        "title": title,
        "cookies_count": len(cookies),
        "message": "获取页面信息成功"
    })


@router.post(
    "/operation/clear_login_state",
    summary="清空登录态（换号）",
    response_model=StandardResponse[ClearLoginStateResponse],
)
async def clear_login_state(
    request: ClearLoginStateRequest,
    session: ActiveBrowserSession = Depends(
        require_active_browser_session(
            source="clear_login_state",
            # 工作流可能正在登录 / 下单，清掉登录态会把它打成「莫名其妙的登录失败」
            reject_workflow_running=True,
        )
    ),
) -> StandardResponse[ClearLoginStateResponse]:
    """清空该浏览器的登录态，用于「换号」——**不关闭、不重建浏览器**。

    只做两件事：
    1. 清 cookie（全部站点，含 HttpOnly）+ 权限授权：登录态的真正载体，B 站的
       SESSDATA 就是 HttpOnly cookie，页面 JS 清不掉，只能在这里做；
    2. 清已打开页面的 localStorage / sessionStorage：否则 cookie 没了、
       前端缓存的用户信息还在，页面会继续显示旧账号；再按需刷新页面让站点回到未登录态。

    ⚠️ 明确不做：HTTP 磁盘缓存 / Service Worker / IndexedDB / 扩展数据 / 浏览历史。
    正因为不动这些，才不需要删 profile 重建浏览器 —— 会话 ID 不变、不断 WebRTC 流、
    不重排启动队列。反过来，若站点把凭证存在 IndexedDB / SW 里（不靠 cookie），
    本接口对它无效，那类只能走「删除浏览器重建」。
    """
    data = await BrowserDataService.clear_login_state(
        session.browser_session, reload_pages=request.reload_pages
    )
    return success_response(data=data, msg="清空登录态成功")


# ============ browser/info API ============


@router.post(BrowserControlRouterPath.browser_info, summary="获取浏览器信息")
async def get_browser_info(
    session: ActiveBrowserSession = Depends(require_active_browser_session()),
) -> StandardResponse[BrowserInfoResponse]:
    """获取当前浏览器实例的详细信息"""
    browser_session = session.browser_session
    fingerprint_params = browser_session.fingerprint_params

    # 浏览器版本 / User-Agent 在会话初始化时由指纹确定，直接取用即可
    version = fingerprint_params.fingerprint_brand_version or ""
    user_agent = fingerprint_params.patchright_browser_ua or ""

    # 获取用户数据目录（来自底层 playwright 实例）
    user_data_dir = getattr(
        browser_session.playwright_instance, "_user_data_dir", None
    )

    return success_response(BrowserInfoResponse(
        # browser_id 在依赖里被规范化为 int，而响应契约是 str：
        # pydantic v2 不会把 int 自动转成 str，必须显式转，否则这里直接抛校验错误
        browser_id=str(session.browser_id),
        mid=session.mid,
        user_data_dir=str(user_data_dir) if user_data_dir else None,
        is_headless=browser_session.headless,
        browser_type="chromium",
        version=version,
        user_agent=user_agent
    ))
