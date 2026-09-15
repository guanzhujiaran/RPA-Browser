"""浏览器监管 API（管理端）

用途：审核员查看**运行中**的浏览器实例是否在跑恶意内容并就地处置。

- 只读：`/browser/monitors`（列表）、`/browser/monitor/pages`（标签页）——绕过
  `verify_browser_ownership`，改由 `RPA_BROWSER` 资源域 VIEW 权限把关；
- 处置：`/browser/session/stop`（强制停止，BAN 权限）；通知由 be-message
  `POST /api/v1/message/notify/admin/create` 定向发送、封号由 `/ban/create`
  （`scope=rpa`，短时封禁 = `temporary` + `duration_minutes`）处理；
- 所有处置写 `admin_audit_log`。

口径：指纹 / 浏览器实例是用户私有资源，不对外公开；监管页不提供任何代替用户
操作浏览器的能力（不调用 `/operation/*` 与 `/actions/execute`）。
"""

from loguru import logger
from fastapi import APIRouter, Depends
from sqlmodel import select, col

from bili_common.deps.auth import AuthInfo
from bili_common.deps.permissions import BizPermOp
from bili_common.models.interaction import InteractionBizTypeEnum
from bili_common.models.response import (
    StandardResponse,
    success_response,
    error_response,
)
from bili_common.models.response_code import ResponseCode

from app.models.database.browser.info import UserBrowserInfo
from app.models.system.browser_monitor import (
    BrowserMonitorItem,
    BrowserMonitorListRequest,
    BrowserMonitorListResponse,
    BrowserMonitorPageItem,
    BrowserMonitorPagesRequest,
    BrowserMonitorPagesResponse,
    BrowserMonitorStopRequest,
    BrowserMonitorStopResponse,
)
from app.services.RPA_browser.session.live_service import live_service
from app.services.admin_audit import log_admin_action
from app.utils.depends.admin_depends import require_permission
from app.utils.depends.session_manager import DatabaseSessionManager

router = APIRouter()  # tag 由 admin/__init__.py 聚合父路由统一提供


def _to_int(value: int | str | None) -> int | None:
    """入参归一：前端优先传 *_str 字符串 ID，统一转 int"""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def _load_fingerprint_map(mids: set[int]) -> dict[tuple[int, int], UserBrowserInfo]:
    """按 mid 批量拉取指纹信息，避免逐条查询（会话数有限，内存映射即可）"""
    if not mids:
        return {}
    async with DatabaseSessionManager.async_session() as session:
        stmt = select(UserBrowserInfo).where(col(UserBrowserInfo.mid).in_(mids))
        rows = (await session.exec(stmt)).all()
    return {(row.mid, row.browser_id): row for row in rows}


async def _build_page_items(entry) -> list[BrowserMonitorPageItem]:
    """读取标签页信息（只读，失败降级为空列表）"""
    try:
        infos = await entry.browser_session.get_all_page_infos()
    except Exception as e:  # pragma: no cover - 浏览器已关闭等边界
        logger.warning(f"👨‍💼 Admin: 读取标签页失败: {e}")
        return []
    return [
        BrowserMonitorPageItem(index=info.index, url=info.url or "", title=info.title or "")
        for info in infos
    ]


def _safe_stream_count(entry) -> int:
    try:
        return int(entry.browser_session.webrtc_active_streams)
    except Exception:
        return 0


@router.post(
    "/browser/monitors",
    response_model=StandardResponse[BrowserMonitorListResponse],
    summary="浏览器监管列表（管理员）",
)
async def list_browser_monitors(
    request: BrowserMonitorListRequest,
    auth: AuthInfo = Depends(
        require_permission(InteractionBizTypeEnum.RPA_BROWSER, BizPermOp.VIEW)
    ),
):
    """运行中浏览器实例列表：会话信息 + 指纹信息 + 时间 + 标签页概览"""
    try:
        mid_filter = _to_int(request.mid)
        browser_id_filter = _to_int(request.browser_id)

        entries = []
        for _session_key, entry in list(live_service._browser_sessions.items()):
            if mid_filter is not None and entry.mid != mid_filter:
                continue
            if browser_id_filter is not None and entry.browser_id != browser_id_filter:
                continue
            entries.append(entry)

        fp_map = await _load_fingerprint_map({e.mid for e in entries})

        items: list[BrowserMonitorItem] = []
        for entry in entries:
            pages = await _build_page_items(entry)
            fp = fp_map.get((entry.mid, entry.browser_id))
            first_page = pages[0] if pages else None
            items.append(
                BrowserMonitorItem(
                    mid=entry.mid,
                    mid_str=str(entry.mid),
                    browser_id=entry.browser_id,
                    browser_id_str=str(entry.browser_id),
                    custom_name=fp.custom_name if fp else None,
                    platform=fp.fingerprint_platform if fp else None,
                    browser=fp.fingerprint_browser if fp else None,
                    started_at=int(entry.created_at or 0),
                    last_activity_at=int(entry.last_activity or 0),
                    page_count=len(pages),
                    active_page_url=first_page.url if first_page else "",
                    active_page_title=first_page.title if first_page else "",
                    webrtc_active_streams=_safe_stream_count(entry),
                    is_closed=bool(getattr(entry.browser_session, "is_closed", False)),
                )
            )

        total = len(items)
        start = (request.page - 1) * request.per_page
        paged = items[start : start + request.per_page]

        return success_response(
            data=BrowserMonitorListResponse(
                total=total,
                page=request.page,
                per_page=request.per_page,
                items=paged,
            )
        )
    except Exception as e:
        logger.error(f"❌ 浏览器监管列表查询失败: {e}")
        return error_response(
            msg=f"查询失败: {str(e)}", code=ResponseCode.INTERNAL_ERROR
        )


@router.post(
    "/browser/monitor/pages",
    response_model=StandardResponse[BrowserMonitorPagesResponse],
    summary="浏览器标签页列表（管理员，只读）",
)
async def get_browser_monitor_pages(
    request: BrowserMonitorPagesRequest,
    auth: AuthInfo = Depends(
        require_permission(InteractionBizTypeEnum.RPA_BROWSER, BizPermOp.VIEW)
    ),
):
    """查看指定浏览器实例的标签页（url / title），仅供审核判断，不提供任何写操作"""
    mid = _to_int(request.mid)
    browser_id = _to_int(request.browser_id)
    if mid is None or browser_id is None:
        return error_response(msg="mid / browser_id 不合法", code=ResponseCode.BAD_REQUEST)

    session_key = live_service._get_session_key(mid, browser_id)
    entry = live_service._browser_sessions.get(session_key)
    if entry is None:
        return error_response(
            msg="会话不存在或浏览器未启动", code=ResponseCode.NOT_FOUND
        )

    pages = await _build_page_items(entry)
    return success_response(
        data=BrowserMonitorPagesResponse(
            mid=mid,
            mid_str=str(mid),
            browser_id=browser_id,
            browser_id_str=str(browser_id),
            total=len(pages),
            pages=pages,
        )
    )


@router.post(
    "/browser/session/stop",
    response_model=StandardResponse[BrowserMonitorStopResponse],
    summary="强制停止浏览器会话（管理员）",
)
async def stop_browser_session(
    request: BrowserMonitorStopRequest,
    auth: AuthInfo = Depends(
        require_permission(InteractionBizTypeEnum.RPA_BROWSER, BizPermOp.BAN)
    ),
):
    """强制关闭指定用户的浏览器会话（含直播流），并写审计日志"""
    mid = _to_int(request.mid)
    browser_id = _to_int(request.browser_id)
    if mid is None or browser_id is None:
        return error_response(msg="mid / browser_id 不合法", code=ResponseCode.BAD_REQUEST)

    session_key = live_service._get_session_key(mid, browser_id)
    if session_key not in live_service._browser_sessions:
        return error_response(
            msg="会话不存在或浏览器未启动", code=ResponseCode.NOT_FOUND
        )

    try:
        closed = await live_service.release_browser_session(mid, browser_id)
        await log_admin_action(
            auth.mid,
            "browser:stop",
            "browser",
            browser_id,
            f"mid={mid}, reason={request.reason}",
        )
        msg = "会话已强制停止" if closed else "会话停止失败"
        return success_response(
            data=BrowserMonitorStopResponse(
                mid=mid,
                browser_id=browser_id,
                closed=bool(closed),
                message=msg,
            ),
            msg=msg,
        )
    except Exception as e:
        logger.error(f"❌ 强制停止会话失败 (mid={mid}, browser_id={browser_id}): {e}")
        return error_response(
            msg=f"停止失败: {str(e)}", code=ResponseCode.INTERNAL_ERROR
        )


__all__ = ["router"]
