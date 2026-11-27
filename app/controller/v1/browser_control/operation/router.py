"""
浏览器操作控制路由

提供浏览器的基础操作控制功能：打开页面、关闭页面、切换页面、获取页面信息、清空登录态等。

接口本身只做「取参数 → 干活 → 拼响应」：
- 浏览器归属校验 + 会话存在性 + 刷新活跃时间 + 索引取页，统一由
  `require_active_browser_session` / `ActiveBrowserSession` 依赖承担；
- 出错一律抛业务异常，由统一异常处理器转成 `{code, msg}`，接口内不再手拼错误响应。
"""

from dataclasses import dataclass
from typing import Literal

from fastapi import BackgroundTasks, Depends
from sqlmodel.ext.asyncio.session import AsyncSession

from bili_common.models.depends import BrowserReqAuthInfo
from bili_common.models.response import (
    StandardResponse,
    success_response,
    error_response,
)
from bili_common.models.response_code import ResponseCode
from app.models.common.exceptions.base_exception import (
    BrowserIdNotBeloneToUserException,
    BrowserNotStartedException,
    BrowserWorkflowRunningException,
    GetBrowserSessionFailedException,
    InvalidParamException,
)
from app.models.router.router_prefix import BrowserControlRouterPath
from app.models.runtime.browser_operation import (
    BrowserInfoResponse,
    ClearLoginStateBatchItemResult,
    ClearLoginStateBatchRequest,
    ClearLoginStateBatchResponse,
    ClearLoginStateRequest,
    ClearLoginStateResponse,
    ClosePageRequest,
    CookieData,
    ExportCookiesBatchItemResult,
    ExportCookiesBatchRequest,
    ExportCookiesBatchResponse,
    ExportCookiesRequest,
    ExportCookiesResponse,
    GetPageInfoRequest,
    ImportCookiesBatchItemResult,
    ImportCookiesBatchRequest,
    ImportCookiesBatchResponse,
    ImportCookiesRequest,
    ImportCookiesResponse,
    OpenPageRequest,
    SwitchPageRequest,
)
from app.models.runtime.operation_task import (
    OperationTaskCreatedResponse,
    OperationTaskStatusRequest,
    OperationTaskStatusResponse,
)
from app.services.RPA_browser.session.browser_data_service import BrowserDataService
from app.services.RPA_browser.session.cookie_batch_service import CookieBatchService
from app.services.RPA_browser.session.cookie_import_service import CookieImportService
from app.services.RPA_browser.session.operation_task_service import (
    OperationTaskService,
)
from app.utils.depends.browser_session_depends import (
    ActiveBrowserSession,
    require_active_browser_session,
)
from app.utils.depends.mid_depends import AuthInfo, get_auth_info_from_header
from app.utils.depends.security_depends import (
    verify_browser_ownership,
    verify_browser_ownership_for_id,
)
from app.utils.depends.session_manager import DatabaseSessionManager
from app.utils.depends.vip_depends import is_vip_user
from loguru import logger
from app.services.RPA_browser.session.page_registry import page_registry
from ..base import new_operation_router

router = new_operation_router()


def _raise_for_import_status(result: ImportCookiesBatchItemResult) -> None:
    """把单项失败结果转成与其它控制接口同口径的业务异常（单个导入用）。"""
    if result.status == "success":
        return
    if result.status == "not_running":
        raise BrowserNotStartedException()
    if result.status == "workflow_running":
        raise BrowserWorkflowRunningException()
    if result.status == "invalid":
        raise InvalidParamException(result.message or "cookie 参数非法")
    if result.status == "forbidden":
        raise BrowserIdNotBeloneToUserException(browser_id=result.browser_id)
    raise GetBrowserSessionFailedException(result.message or result.status)


def _raise_for_export_status(result: ExportCookiesBatchItemResult) -> None:
    """把导出失败结果转成与其它控制接口同口径的业务异常（单浏览器导出用）。

    ``launch_failed`` / ``failed`` 统一落到「获取浏览器会话失败」，由统一异常处理器
    转成业务码；成功路径不在这里拼响应。
    """
    if result.status == "success":
        return
    if result.status == "not_running":
        raise BrowserNotStartedException()
    if result.status == "forbidden":
        raise BrowserIdNotBeloneToUserException(browser_id=result.browser_id)
    if result.status == "invalid":
        raise InvalidParamException(result.message or "导出参数非法")
    raise GetBrowserSessionFailedException(result.message or result.status)


def _raise_for_clear_status(result: ClearLoginStateBatchItemResult) -> None:
    """把清空服务的单项结果转成单浏览器接口同口径的业务异常。"""
    if result.status == "success":
        return
    if result.status == "not_running":
        raise BrowserNotStartedException()
    if result.status == "workflow_running":
        raise BrowserWorkflowRunningException()
    if result.status == "forbidden":
        raise BrowserIdNotBeloneToUserException(browser_id=result.browser_id)
    if result.status == "invalid":
        raise InvalidParamException(result.message or "清空登录态参数非法")
    raise GetBrowserSessionFailedException(result.message or result.status)


def _pair_cookie_groups(
    browser_ids: list[str],
    cookie_groups: list[list[CookieData]],
) -> tuple[
    list[tuple[str, list[CookieData]]],
    list[str],
    list[list[CookieData]],
    int,
]:
    """按索引配对浏览器与 cookie 组，并统计两侧多出来的部分。

    配对是「第 i 组给第 i 个浏览器」；数量不匹配时不报错，而是把多余的一侧
    单独返回，由接口响应如实告诉调用方（有多少组没用上 / 多少浏览器分不到）。

    Returns:
        ``(pairs, unassigned_ids, unused_groups, unused_cookie_count)``
    """
    pairs = list(zip(browser_ids, cookie_groups))
    unassigned_ids = browser_ids[len(pairs) :]
    unused_groups = cookie_groups[len(pairs) :]
    unused_cookie_count = sum(len(group) for group in unused_groups)
    return pairs, unassigned_ids, unused_groups, unused_cookie_count


@dataclass(frozen=True)
class _OwnershipFailure:
    """批量接口里单项归属校验失败的原因（两个批量接口共用）。"""

    status: Literal["forbidden", "invalid"]
    message: str


async def _verify_batch_browser_ownership(
    browser_id: str,
    auth_info: AuthInfo,
    db_session: AsyncSession,
) -> _OwnershipFailure | None:
    """批量接口的逐项归属校验：不信任请求体，逐项查库确认浏览器属于当前用户。

    Returns:
        ``None`` = 校验通过；否则返回失败原因，调用方把它记成该项结果后继续处理
        后面的浏览器（单项无权不能导致整批失败）。
    """
    try:
        await verify_browser_ownership_for_id(browser_id, auth_info, db_session)
    except BrowserIdNotBeloneToUserException as e:
        return _OwnershipFailure(
            status="forbidden", message=e.msg or "浏览器不存在或不属于当前用户"
        )
    except Exception as e:
        logger.error(f"[cookie 批量操作] 归属校验异常: {browser_id}, {e}")
        return _OwnershipFailure(status="invalid", message=f"浏览器校验失败: {e}")
    return None


def _build_import_batch_response(
    total_browsers: int,
    assigned: int,
    results: list[ImportCookiesBatchItemResult],
    unassigned_ids: list[str],
    unused_cookie_group_count: int,
    unused_cookie_count: int,
) -> tuple[ImportCookiesBatchResponse, str]:
    """汇总批量导入结果（同步接口与异步任务共用）。"""
    succeeded = sum(1 for result in results if result.status == "success")
    summary = f"批量导入完成：成功 {succeeded} / {assigned}"
    if unused_cookie_group_count:
        summary += (
            f"；忽略了 {unused_cookie_group_count} 组（{unused_cookie_count} 条 cookie）"
        )
    if unassigned_ids:
        summary += f"；{len(unassigned_ids)} 个浏览器未分配 cookie"

    return (
        ImportCookiesBatchResponse(
            total_browsers=total_browsers,
            assigned=assigned,
            succeeded=succeeded,
            failed=assigned - succeeded,
            unassigned_browser_ids=list(unassigned_ids),
            unused_cookie_group_count=unused_cookie_group_count,
            unused_cookie_count=unused_cookie_count,
            results=results,
        ),
        summary,
    )


def _build_clear_batch_response(
    results: list[ClearLoginStateBatchItemResult],
) -> tuple[ClearLoginStateBatchResponse, str]:
    """汇总批量清空结果（同步接口与异步任务共用）。"""
    total = len(results)
    succeeded = sum(1 for result in results if result.status == "success")
    total_affected_domains = sum(
        result.affected_domains for result in results if result.status == "success"
    )
    total_cleared_cookie_count = sum(
        result.cleared_cookie_count
        for result in results
        if result.status == "success"
    )
    return (
        ClearLoginStateBatchResponse(
            total_browsers=total,
            succeeded=succeeded,
            failed=total - succeeded,
            total_affected_domains=total_affected_domains,
            total_cleared_cookie_count=total_cleared_cookie_count,
            results=results,
        ),
        f"批量清空完成：成功 {succeeded} / {total}",
    )


def _task_lock_key(browser_id: str) -> int:
    """任务并发锁的键：非法 id 统一用 0（它们在提交阶段已被判为 forbidden）。"""
    try:
        return int(browser_id)
    except (TypeError, ValueError):
        return 0


def _find_blocked_item(
    blocked: list[ImportCookiesBatchItemResult],
    browser_id: str,
) -> ImportCookiesBatchItemResult | None:
    return next(
        (item for item in blocked if item.browser_id == browser_id), None
    )


async def _run_import_task(
    task_id: str,
    mid: int,
    pairs: list[tuple[str, list[CookieData]]],
    blocked: list[ImportCookiesBatchItemResult],
    unassigned_ids: list[str],
    unused_cookie_group_count: int,
    unused_cookie_count: int,
    launch_if_not_running: bool,
    reload_pages: bool,
    is_vip: bool,
) -> None:
    """后台执行批量导入（由 FastAPI ``BackgroundTasks`` 在响应发出后调度）。"""
    OperationTaskService.start(task_id, "正在写入 cookie")
    try:
        results: list[ImportCookiesBatchItemResult] = []
        for index, (browser_id, cookies) in enumerate(pairs, start=1):
            blocked_item = _find_blocked_item(blocked, browser_id)
            if blocked_item is not None:
                results.append(blocked_item)
            else:
                async with OperationTaskService.lock_for(
                    _task_lock_key(browser_id)
                ):
                    results.append(
                        await CookieImportService.import_for_browser(
                            mid=mid,
                            browser_id=browser_id,
                            cookies=cookies,
                            launch_if_not_running=launch_if_not_running,
                            reload_pages=reload_pages,
                            is_vip=is_vip,
                        )
                    )
            OperationTaskService.update_progress(
                task_id, index, f"已处理 {index} / {len(pairs)}"
            )

        for browser_id in unassigned_ids:
            results.append(
                ImportCookiesBatchItemResult(
                    browser_id=browser_id,
                    status="unassigned",
                    message="没有分配到 cookie（cookie 组数少于浏览器数）",
                )
            )

        response, _ = _build_import_batch_response(
            total_browsers=len(pairs) + len(unassigned_ids),
            assigned=len(pairs),
            results=results,
            unassigned_ids=unassigned_ids,
            unused_cookie_group_count=unused_cookie_group_count,
            unused_cookie_count=unused_cookie_count,
        )
        OperationTaskService.finish(
            task_id,
            import_result=response,
            message=f"导入完成：成功 {response.succeeded} / {response.assigned}",
        )
    except Exception as e:
        logger.exception(f"[任务] 批量导入失败: {e}")
        OperationTaskService.fail(task_id, str(e))


async def _run_clear_task(
    task_id: str,
    mid: int,
    browser_ids: list[str],
    blocked: list[ClearLoginStateBatchItemResult],
    domains: list[str] | None,
    reload_pages: bool,
) -> None:
    """后台执行批量清空（由 FastAPI ``BackgroundTasks`` 在响应发出后调度）。"""
    OperationTaskService.start(task_id, "正在清空登录态")
    try:
        results: list[ClearLoginStateBatchItemResult] = []
        for index, browser_id in enumerate(browser_ids, start=1):
            blocked_item = next(
                (item for item in blocked if item.browser_id == browser_id), None
            )
            if blocked_item is not None:
                results.append(blocked_item)
            else:
                async with OperationTaskService.lock_for(
                    _task_lock_key(browser_id)
                ):
                    results.append(
                        await CookieBatchService.clear_for_browser(
                            mid,
                            browser_id,
                            domains=domains,
                            reload_pages=reload_pages,
                        )
                    )
            OperationTaskService.update_progress(
                task_id, index, f"已处理 {index} / {len(browser_ids)}"
            )

        response, _ = _build_clear_batch_response(results)
        OperationTaskService.finish(
            task_id,
            clear_result=response,
            message=f"清空完成：成功 {response.succeeded} / {response.total_browsers}",
        )
    except Exception as e:
        logger.exception(f"[任务] 批量清空失败: {e}")
        OperationTaskService.fail(task_id, str(e))


# ============ 浏览器操作 API ============


@router.post("/operation/open_page", summary="打开页面")
async def open_page(
    request: OpenPageRequest,
    session: ActiveBrowserSession = Depends(require_active_browser_session()),
) -> StandardResponse[dict]:
    """在浏览器中打开指定URL"""
    # page_index 为负数表示新建页面
    if request.page_index < 0:
        # 走页面注册表：满了先回收未标记页，再等待空位（不关工作流的页）
        page = await session.browser_session.create_new_page_with_limit(
            (session.mid, int(session.browser_id))
        )
        page_index = len(session.all_pages) - 1
    else:
        page = session.get_page(request.page_index)
        page_index = request.page_index

    await page.goto(request.url)

    return success_response(
        {"page_index": page_index, "url": request.url, "message": "页面打开成功"}
    )


@router.post("/operation/close_page", summary="关闭页面")
async def close_page(
    request: ClosePageRequest,
    session: ActiveBrowserSession = Depends(require_active_browser_session()),
) -> StandardResponse[dict]:
    """关闭指定页面（被工作流引用的页面不允许关闭）"""
    page = session.get_page(request.page_index)

    # 不能关闭最后一个页面
    if len(session.all_pages) <= 1:
        return error_response(ResponseCode.BAD_REQUEST, "无法关闭最后一个页面")

    # 工作流占用检查：别名页（open_page 创建）不能手动关闭
    alias = page_registry.find_alias(
        (session.mid, int(session.browser_id)), page
    )
    logger.debug(
        f"[close_page] index={request.page_index} aliases="
        f"{page_registry.list_aliases((session.mid, int(session.browser_id)))} "
        f"pages={len(session.all_pages)}"
    )
    if alias:
        return error_response(
            ResponseCode.BAD_REQUEST,
            f"页面 {alias!r} 正在被工作流使用，不能手动关闭"
            "（请先停止对应工作流或用 close_page 动作关闭）",
        )

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

    return success_response(
        {"page_index": request.page_index, "message": "页面切换成功"}
    )


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

    return success_response(
        {
            "page_index": request.page_index,
            "url": url,
            "title": title,
            "cookies_count": len(cookies),
            "message": "获取页面信息成功",
        }
    )


@router.post(
    "/operation/clear_login_state",
    summary="清空登录态（换号）",
    response_model=StandardResponse[ClearLoginStateResponse],
)
async def clear_login_state(
    request: ClearLoginStateRequest,
    browser_info: BrowserReqAuthInfo = Depends(verify_browser_ownership),
) -> StandardResponse[ClearLoginStateResponse]:
    """清空该浏览器的登录态，用于「换号」或「多站点隔离」——**不关闭、不重建浏览器**。

    - 不传 `domains` = 清空全部站点（换号）；传了则只清「访问这些站点会携带的
      cookie」（含子域与父域）以及 origin 匹配页面的 localStorage / sessionStorage；
    - 清 cookie（含 HttpOnly）+ 权限授权：B 站的 SESSDATA 就是 HttpOnly cookie，
      页面 JS 清不掉，只能在这里做；
    - ⚠️ 权限授权没有按站点清理的能力（Playwright 只有整体 `clear_permissions`），
      指定站点时仍会整体重置，响应 message 会明确说明；
    - ⚠️ 明确不做：HTTP 磁盘缓存 / Service Worker / IndexedDB / 扩展数据 / 浏览历史。
      因此按站点清空**不等于「彻底退出该站点」**：站点若把凭证存在 IndexedDB / SW，
      清完仍可能是登录态，那类只能走「删除浏览器重建」。

    工作流执行中的浏览器会被拒绝（清登录态会打断其登录流程）；浏览器已关闭
    （僵尸会话）时会被自动清理并按 `1007` 处理。
    """
    # 域非法属于请求级参数错误：不能退化成「清空全部」，否则静默放大影响范围
    BrowserDataService.validate_domains(request.domains)

    result = await CookieBatchService.clear_for_browser(
        browser_info.auth_info.mid,
        str(browser_info.browser_id),
        domains=request.domains,
        reload_pages=request.reload_pages,
    )
    _raise_for_clear_status(result)

    logger.warning(
        f"[清空登录态] mid={browser_info.auth_info.mid} "
        f"browser_id={browser_info.browser_id} "
        f"cookies={result.cleared_cookie_count} "
        f"domains={len(result.matched_domains)} "
        f"filter={request.domains or '全部'}"
    )
    return success_response(
        data=ClearLoginStateResponse(
            cleared_cookies=True,
            cleared_cookie_count=result.cleared_cookie_count,
            matched_domains=result.matched_domains,
            cleared_pages=result.cleared_pages,
            reloaded_pages=result.reloaded_pages,
            affected_domains=result.affected_domains,
            message=result.message,
        ),
        msg="清空登录态成功",
    )


@router.post(
    "/operation/export_cookies",
    summary="导出 cookie（按站点 / 全部）",
    response_model=StandardResponse[ExportCookiesResponse],
)
async def export_cookies(
    request: ExportCookiesRequest,
    browser_info: BrowserReqAuthInfo = Depends(verify_browser_ownership),
) -> StandardResponse[ExportCookiesResponse]:
    """把当前浏览器 profile 里的 cookie 原样导出（**只读**）。

    - 不传 `domains` = 导出全部；传了则按「访问该站点时实际会携带的 cookie」过滤
      （含子域与父域，例如 `bilibili.com` 会带出 `.bilibili.com` 与 `api.bilibili.com`）。
    - cookie 只能从运行中的 context 读取：未启动默认返回 `1007`；
      置 `launch_if_not_running=true` 会先启动浏览器再导出（占启动队列与时长配额）。
    - 只读操作，工作流运行中同样可导出，不做执行期互斥。
    - 出口是明文凭证（含 HttpOnly）：服务端只审计条数与域名，**不记录 cookie 值**。
    - 数据源只能是 profile：站点会随请求轮换 cookie，导出结果仅供排查 / 人工迁移，
      不得做成「启动时注入」—— 那会让旧快照覆盖浏览器刷新后的新值。
    """
    result = await CookieBatchService.export_for_browser(
        browser_info.auth_info.mid,
        str(browser_info.browser_id),
        domains=request.domains,
        launch_if_not_running=request.launch_if_not_running,
        is_vip=is_vip_user(browser_info.auth_info),
        source="export_cookies",
    )
    _raise_for_export_status(result)

    logger.warning(
        f"[cookie 导出] mid={browser_info.auth_info.mid} "
        f"browser_id={browser_info.browser_id} count={result.cookie_count} "
        f"domains={len(result.matched_domains)} launched={result.launched} "
        f"filter={request.domains or '全部'}"
    )
    return success_response(
        data=ExportCookiesResponse(
            cookie_count=result.cookie_count,
            matched_domains=result.matched_domains,
            cookies=result.cookies,
            launched=result.launched,
            message=result.message,
        ),
        msg="导出 cookie 成功",
    )


@router.post(
    "/operation/import_cookies",
    summary="导入 cookie（单个浏览器）",
    response_model=StandardResponse[ImportCookiesResponse],
)
async def import_cookies(
    request: ImportCookiesRequest,
    browser_info: BrowserReqAuthInfo = Depends(verify_browser_ownership),
) -> StandardResponse[ImportCookiesResponse]:
    """把 cookie 写入当前浏览器（**一次性**，写后服务端不再持有副本）。

    - 浏览器必须处于运行状态：cookie 只能通过运行中的 context 写入，
      未启动返回 `1007`；确需本次启动可置 `launch_if_not_running=true`。
    - 写入是 **upsert**：同 domain/path/name 覆盖旧值，未提及的旧 cookie 保留，
      要彻底换号请先调 `clear_login_state` 再导入。
    - 工作流执行期拒绝（注入会打乱其登录态）。
    - 写入后刷新与落盘全部交给浏览器，服务端不缓存、不落库、不做启动期注入。
    """
    result = await CookieImportService.import_for_browser(
        mid=browser_info.auth_info.mid,
        browser_id=str(browser_info.browser_id),
        cookies=request.cookies,
        launch_if_not_running=request.launch_if_not_running,
        reload_pages=request.reload_pages,
        is_vip=is_vip_user(browser_info.auth_info),
    )
    _raise_for_import_status(result)

    logger.warning(
        f"[cookie 导入] mid={browser_info.auth_info.mid} "
        f"browser_id={browser_info.browser_id} count={result.imported_count} "
        f"launched={result.launched}"
    )
    return success_response(
        data=ImportCookiesResponse(
            browser_id=result.browser_id,
            launched=result.launched,
            imported_count=result.imported_count,
            reloaded_pages=result.reloaded_pages,
            session_cookie_count=result.session_cookie_count,
            message=result.message,
        ),
        msg="导入 cookie 成功",
    )


@router.post(
    "/operation/import_cookies/batch",
    summary="批量导入 cookie",
    response_model=StandardResponse[ImportCookiesBatchResponse],
)
async def import_cookies_batch(
    request: ImportCookiesBatchRequest,
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    db_session: AsyncSession = DatabaseSessionManager.get_dependency(),
) -> StandardResponse[ImportCookiesBatchResponse]:
    """给多个浏览器按顺序批量写入 cookie（逐项隔离，单项失败不影响其它项）。

    配对规则：`cookie_groups[i]` 写入 `browser_ids[i]`，**按索引顺序**。

    - 每项都重新校验 `browser_id` 归属，不信任请求体；
    - 组数多于浏览器：多出的组整体忽略，响应给出未使用组数与条数（「尽力导入」，不报错）；
    - 浏览器多于组数：多出的浏览器状态为 `unassigned`，并在 `unassigned_browser_ids` 中列出；
    - 默认只处理**运行中**的浏览器，未启动的项返回 `not_running`；
      置 `launch_if_not_running=true` 会逐项串行启动（受启动队列影响，耗时可能较长）；
    - 单次上限 50 个浏览器；工作流执行中的浏览器返回 `workflow_running` 并跳过。
    """
    is_vip = is_vip_user(auth_info)

    # 按索引配对，只取较短的一侧；多出来的分别统计，不能静默丢弃
    pairs, unassigned_ids, unused_groups, unused_cookie_count = _pair_cookie_groups(
        request.browser_ids, request.cookie_groups
    )

    results: list[ImportCookiesBatchItemResult] = []

    for browser_id, group in pairs:
        try:
            await verify_browser_ownership_for_id(browser_id, auth_info, db_session)
        except BrowserIdNotBeloneToUserException as e:
            results.append(
                ImportCookiesBatchItemResult(
                    browser_id=browser_id,
                    status="forbidden",
                    message=e.msg or "浏览器不存在或不属于当前用户",
                )
            )
            continue
        except Exception as e:
            logger.error(f"[cookie 批量导入] 归属校验异常: {browser_id}, {e}")
            results.append(
                ImportCookiesBatchItemResult(
                    browser_id=browser_id,
                    status="invalid",
                    message=f"浏览器校验失败: {e}",
                )
            )
            continue

        results.append(
            await CookieImportService.import_for_browser(
                mid=auth_info.mid,
                browser_id=browser_id,
                cookies=group,
                launch_if_not_running=request.launch_if_not_running,
                reload_pages=request.reload_pages,
                is_vip=is_vip,
            )
        )

    for browser_id in unassigned_ids:
        results.append(
            ImportCookiesBatchItemResult(
                browser_id=browser_id,
                status="unassigned",
                message="没有分配到 cookie（cookie 组数少于浏览器数）",
            )
        )

    logger.warning(
        f"[cookie 批量导入] mid={auth_info.mid} browsers={len(request.browser_ids)} "
        f"assigned={len(pairs)} unused_groups={len(unused_groups)} "
        f"unassigned={len(unassigned_ids)} launch={request.launch_if_not_running}"
    )

    response, summary = _build_import_batch_response(
        total_browsers=len(request.browser_ids),
        assigned=len(pairs),
        results=results,
        unassigned_ids=list(unassigned_ids),
        unused_cookie_group_count=len(unused_groups),
        unused_cookie_count=unused_cookie_count,
    )
    return success_response(data=response, msg=summary)


@router.post(
    "/operation/export_cookies/batch",
    summary="批量导出 cookie（逐项只读）",
    response_model=StandardResponse[ExportCookiesBatchResponse],
)
async def export_cookies_batch(
    request: ExportCookiesBatchRequest,
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    db_session: AsyncSession = DatabaseSessionManager.get_dependency(),
) -> StandardResponse[ExportCookiesBatchResponse]:
    """批量导出多个浏览器的 cookie（**只读**，逐项隔离）。

    契约与单浏览器导出一致（`domains` 语义、`CookieData` 结构），区别只有：

    - **纯只读、不启动浏览器**：cookie 只能从活跃 context 里读，未启动项返回
      `not_running`。批量场景不能在一个请求里串行启动 —— N 个未启动浏览器会跑成
      分钟级长请求，撞上网关读超时后前端显示失败、后端仍在继续执行（最难排查的
      不一致）。需要启动时由调用方**逐项**调用 `POST /browser/control/create`，
      单项请求天然隔离超时与失败，前端也能展示进度；
    - 逐项校验归属、逐项执行，单项失败不影响其它项；
    - `domains` 非法是请求级参数错误：循环前一次性校验并直接报错，
      不退化成「每个浏览器各返回一条 invalid」。

    出口是明文凭证（含 HttpOnly）：服务端只审计条数与域名，**不记录 cookie 值**。
    """
    # 循环前校验一次：域非法属于请求参数错误，不能让每个浏览器各返回一条 invalid
    BrowserDataService.validate_domains(request.domains)

    results: list[ExportCookiesBatchItemResult] = []
    for browser_id in request.browser_ids:
        failure = await _verify_batch_browser_ownership(
            browser_id, auth_info, db_session
        )
        if failure is not None:
            results.append(
                ExportCookiesBatchItemResult(
                    browser_id=browser_id,
                    status=failure.status,
                    message=failure.message,
                )
            )
            continue
        results.append(
            await CookieBatchService.export_for_browser(
                auth_info.mid, browser_id, domains=request.domains
            )
        )

    total = len(results)
    succeeded = sum(1 for result in results if result.status == "success")
    total_cookie_count = sum(
        result.cookie_count for result in results if result.status == "success"
    )

    logger.warning(
        f"[cookie 批量导出] mid={auth_info.mid} browsers={total} "
        f"succeeded={succeeded} failed={total - succeeded} "
        f"total_cookies={total_cookie_count} filter={request.domains or '全部'}"
    )
    return success_response(
        data=ExportCookiesBatchResponse(
            total_browsers=total,
            succeeded=succeeded,
            failed=total - succeeded,
            total_cookie_count=total_cookie_count,
            results=results,
        ),
        msg=f"批量导出完成：成功 {succeeded} / {total}",
    )


@router.post(
    "/operation/clear_login_state/batch",
    summary="批量清空登录态（换号）",
    response_model=StandardResponse[ClearLoginStateBatchResponse],
)
async def clear_login_state_batch(
    request: ClearLoginStateBatchRequest,
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    db_session: AsyncSession = DatabaseSessionManager.get_dependency(),
) -> StandardResponse[ClearLoginStateBatchResponse]:
    """批量清空多个浏览器的登录态（cookie + 权限 + 页面 web storage）。

    语义与单浏览器 `clear_login_state` 完全一致（**不关闭、不重建浏览器**），
    区别只有：

    - 只处理**运行中**的浏览器，未启动项返回 `not_running`；
    - `domains` 为空 = 清空全部（换号）；指定站点时只清匹配的 cookie 与 origin
      匹配页面的 web storage，权限授权仍整体重置（Playwright 无按站点能力）。
      `domains` 非法是请求级错误：循环前一次性校验，整批 400；
    - 工作流执行中的项返回 `workflow_running` 并**跳过而不是排队**：清登录态会把
      正在跑的工作流打成「莫名其妙的登录失败」，与单接口的 `reject_workflow_running`
      同口径；
    - 逐项校验归属、逐项执行，单项失败不影响其它项。
    """
    # 循环前校验一次：域非法属于请求参数错误，不能让每个浏览器各返回一条 invalid
    BrowserDataService.validate_domains(request.domains)

    results: list[ClearLoginStateBatchItemResult] = []
    for browser_id in request.browser_ids:
        failure = await _verify_batch_browser_ownership(
            browser_id, auth_info, db_session
        )
        if failure is not None:
            results.append(
                ClearLoginStateBatchItemResult(
                    browser_id=browser_id,
                    status=failure.status,
                    message=failure.message,
                )
            )
            continue
        results.append(
            await CookieBatchService.clear_for_browser(
                auth_info.mid,
                browser_id,
                domains=request.domains,
                reload_pages=request.reload_pages,
            )
        )

    response, summary = _build_clear_batch_response(results)
    logger.warning(
        f"[批量清空登录态] mid={auth_info.mid} browsers={response.total_browsers} "
        f"succeeded={response.succeeded} failed={response.failed} "
        f"affected_domains={response.total_affected_domains} "
        f"cookies={response.total_cleared_cookie_count} "
        f"filter={request.domains or '全部'}"
    )
    return success_response(data=response, msg=summary)


# ============ 异步操作任务（提交 + 轮询） ============


@router.post(
    "/operation/tasks/import_cookies",
    summary="提交批量导入 cookie 任务",
    response_model=StandardResponse[OperationTaskCreatedResponse],
)
async def submit_import_cookies_task(
    request: ImportCookiesBatchRequest,
    background_tasks: BackgroundTasks,
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    db_session: AsyncSession = DatabaseSessionManager.get_dependency(),
) -> StandardResponse[OperationTaskCreatedResponse]:
    """把批量导入提交为后台任务，立即返回 `task_id`。

    为什么要任务化：慢站点 / 多页面时刷新与串行启动能跑几十秒，超过 nginx 默认
    60s 读超时 → 前端 504，但 cookie 其实已写入。任务化后长耗时不再占用 HTTP 请求，
    前端轮询 `/operation/tasks/status` 拿进度与逐项结果。

    归属校验在**提交阶段**同步完成（快）：校验失败的项直接作为 `forbidden` 结果，
    后台只做浏览器操作，不需要再持有 DB 会话。
    """
    pairs, unassigned_ids, unused_groups, unused_cookie_count = _pair_cookie_groups(
        request.browser_ids, request.cookie_groups
    )

    blocked: list[ImportCookiesBatchItemResult] = []
    for browser_id, _ in pairs:
        failure = await _verify_batch_browser_ownership(
            browser_id, auth_info, db_session
        )
        if failure is not None:
            blocked.append(
                ImportCookiesBatchItemResult(
                    browser_id=browser_id,
                    status=failure.status,
                    message=failure.message,
                )
            )

    task = OperationTaskService.create(
        auth_info.mid, "import_cookies", len(pairs)
    )
    background_tasks.add_task(
        _run_import_task,
        task.task_id,
        auth_info.mid,
        pairs,
        blocked,
        list(unassigned_ids),
        len(unused_groups),
        unused_cookie_count,
        request.launch_if_not_running,
        request.reload_pages,
        is_vip_user(auth_info),
    )
    logger.warning(
        f"[任务] 提交批量导入 task={task.task_id} mid={auth_info.mid} "
        f"browsers={len(request.browser_ids)} pairs={len(pairs)}"
    )
    return success_response(
        data=OperationTaskCreatedResponse(
            task_id=task.task_id,
            task_type="import_cookies",
            total=len(pairs),
        ),
        msg="任务已提交",
    )


@router.post(
    "/operation/tasks/clear_login_state",
    summary="提交批量清空登录态任务",
    response_model=StandardResponse[OperationTaskCreatedResponse],
)
async def submit_clear_login_state_task(
    request: ClearLoginStateBatchRequest,
    background_tasks: BackgroundTasks,
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    db_session: AsyncSession = DatabaseSessionManager.get_dependency(),
) -> StandardResponse[OperationTaskCreatedResponse]:
    """把批量清空提交为后台任务，立即返回 `task_id`。

    `domains` 非法在提交阶段直接 400（不退化成清空全部）；归属校验同导入，
    失败的项作为 `forbidden` 结果，后台只做浏览器操作。
    """
    # 域非法属于请求级参数错误：提交前拒绝，不能等到后台任务里才失败
    BrowserDataService.validate_domains(request.domains)

    blocked: list[ClearLoginStateBatchItemResult] = []
    for browser_id in request.browser_ids:
        failure = await _verify_batch_browser_ownership(
            browser_id, auth_info, db_session
        )
        if failure is not None:
            blocked.append(
                ClearLoginStateBatchItemResult(
                    browser_id=browser_id,
                    status=failure.status,
                    message=failure.message,
                )
            )

    task = OperationTaskService.create(
        auth_info.mid, "clear_login_state", len(request.browser_ids)
    )
    background_tasks.add_task(
        _run_clear_task,
        task.task_id,
        auth_info.mid,
        request.browser_ids,
        blocked,
        request.domains,
        request.reload_pages,
    )
    logger.warning(
        f"[任务] 提交批量清空 task={task.task_id} mid={auth_info.mid} "
        f"browsers={len(request.browser_ids)} filter={request.domains or '全部'}"
    )
    return success_response(
        data=OperationTaskCreatedResponse(
            task_id=task.task_id,
            task_type="clear_login_state",
            total=len(request.browser_ids),
        ),
        msg="任务已提交",
    )


@router.post(
    "/operation/tasks/status",
    summary="查询操作任务状态",
    response_model=StandardResponse[OperationTaskStatusResponse],
)
async def get_operation_task_status(
    request: OperationTaskStatusRequest,
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
) -> StandardResponse[OperationTaskStatusResponse]:
    """前端按 1~2s 轮询；`success` / `failed` 后停止。

    任务只存在进程内存：服务重启会丢任务（操作本身要么已完成、要么被中断），
    这里返回 `404` 让前端提示「任务不存在或已过期」，不能当成成功。
    """
    task = OperationTaskService.get(auth_info.mid, request.task_id)
    if task is None:
        return error_response(
            code=ResponseCode.NOT_FOUND,
            msg="任务不存在或已过期（服务重启会丢失未取走的任务）",
        )
    return success_response(data=OperationTaskService.snapshot(task))


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
    real_version = fingerprint_params.fingerprint_real_version or ""
    user_agent = fingerprint_params.patchright_browser_ua or ""

    # 获取用户数据目录（来自底层 playwright 实例）
    user_data_dir = getattr(browser_session.playwright_instance, "_user_data_dir", None)

    return success_response(
        BrowserInfoResponse(
            # browser_id 在依赖里被规范化为 int，而响应契约是 str：
            # pydantic v2 不会把 int 自动转成 str，必须显式转，否则这里直接抛校验错误
            browser_id=str(session.browser_id),
            mid=session.mid,
            user_data_dir=str(user_data_dir) if user_data_dir else None,
            is_headless=browser_session.headless,
            browser_type="chromium",
            version=version,
            real_version=real_version,
            user_agent=user_agent,
        )
    )
