from bili_common.models.depends import VerifyBrowserDependsReq
from app.utils.depends.security_depends import verify_browser_ownership
from fastapi import Depends
from sqlmodel.ext.asyncio.session import AsyncSession
from app.models.database.notify.models import (
    NotificationConfig,
)
from app.models.notify.models import (
    NotificationConfigCreate,
    NotificationConfigUpdate,
)
from app.models.notify.response_models import (
    NotificationConfigUpsertResp,
    NotificationConfigDeleteResp,
)
from app.models.notify.request_models import (
    NotifyConfigReadRequest,
)
from app.models.router.router_prefix import NotifyRouterPath
from .base import new_notify_router
from bili_common.models.response import (
    StandardResponse,
    success_response,
)
from app.services.RPA_browser.browser import BrowserService
from app.utils.depends.mid_depends import AuthInfo, get_auth_info_from_header
from app.utils.depends.session_manager import DatabaseSessionManager
from app.utils.bigint_utils import str_to_int

router = new_notify_router()


@router.post(
    NotifyRouterPath.upsert_notify_config,
    response_model=StandardResponse[NotificationConfigUpsertResp],
)
async def upsert_notify_config_router(
    config: NotificationConfigCreate,
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    session: AsyncSession = DatabaseSessionManager.get_dependency(),
):
    """
    创建或更新推送通知配置（统一Upsert接口）

    为当前用户创建或更新推送通知配置。如果配置已存在则更新，否则创建新配置。
    支持配置多种通知渠道，如邮件、短信、webhook等。

    Args:
        config: 通知配置参数，包含可选的browser_id
        mid: 用户ID，从请求头中自动获取
        session: 数据库会话

    Returns:
        NotificationConfigUpsertResp: 操作成功的通知配置响应

    Note:
        - 如果config.browser_id为None，则操作全局配置
        - 如果config.browser_id有值，则操作特定浏览器的配置
        - 如果配置已存在则更新，否则创建新配置
    """

    # 转换browser_id为int（如果提供）
    browser_id_int = (
        str_to_int(config.browser_id) if config.browser_id is not None else None
    )
    if browser_id_int:
        await verify_browser_ownership(
            VerifyBrowserDependsReq(browser_id=browser_id_int), auth_info.mid, session
        )
    browser_service = BrowserService(auth_info.mid)
    # 先查找现有配置
    existing_config = await browser_service.get_notification_config(
        session, browser_id_int
    )

    if existing_config:
        # 配置存在，执行更新
        # 创建更新对象，包含ID和所有字段
        update_data = NotificationConfigUpdate(id=existing_config.id)
        for field_name, field_value in config.model_dump(exclude_unset=True).items():
            if hasattr(update_data, field_name):
                setattr(update_data, field_name, field_value)

        await browser_service.update_notification_config(update_data, session)
    else:
        # 配置不存在，创建新配置
        await browser_service.create_notification_config(config, session)

    return success_response(data=NotificationConfigUpsertResp(mid=str(auth_info.mid)))


@router.post(
    NotifyRouterPath.read_notify_config,
    response_model=StandardResponse[NotificationConfig | None],
)
async def read_notify_config_router(
    request: NotifyConfigReadRequest,
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    session: AsyncSession = DatabaseSessionManager.get_dependency(),
):
    """
    读取推送通知配置

    根据条件查询当前用户的通知配置。可以查询全局默认配置，也可以查询特定浏览器实例的通知配置。
    如果指定了浏览器ID，则返回该实例的通知配置；否则返回用户的默认配置。

    Args:
        request: 包含可选浏览器实例ID的请求对象，如果提供则查询特定实例的配置
        mid: 用户ID，从请求头中自动获取，用于权限验证
        session: 数据库会话

    Returns:
        NotificationConfig | None: 找到的通知配置信息，如果未找到则返回None

    Note:
        只能查询属于当前用户的通知配置信息
    """
    # 直接使用int类型的mid
    browser_service = BrowserService(auth_info.mid)

    # 转换browser_id为int（如果提供）
    browser_id_int = (
        str_to_int(request.browser_id) if request.browser_id is not None else None
    )
    result = await browser_service.get_notification_config(session, browser_id_int)
    return success_response(data=result)


@router.post(
    NotifyRouterPath.delete_notify_config,
    response_model=StandardResponse[NotificationConfigDeleteResp],
)
async def delete_notify_config_router(
    request: NotifyConfigReadRequest,  # 复用读取请求模型
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    session: AsyncSession = DatabaseSessionManager.get_dependency(),
):
    """
    删除推送通知配置

    根据条件删除通知配置。如果提供了浏览器ID，则删除特定实例的通知配置；
    否则删除用户的默认全局配置。删除操作不可恢复。

    Args:
        request: 包含可选浏览器实例ID的请求对象，如果提供则删除特定实例的配置
        mid: 用户ID，从请求头中自动获取，用于权限验证
        session: 数据库会话

    Returns:
        NotificationConfigDeleteResp: 删除操作的结果

    Note:
        删除操作不可恢复，请确保不再需要该通知配置后再执行删除
    """
    # 直接使用int类型的mid
    browser_service = BrowserService(auth_info.mid)

    # 转换browser_id为int（如果提供）
    browser_id_int = (
        str_to_int(request.browser_id) if request.browser_id is not None else None
    )
    result = await browser_service.delete_notification_config(session, browser_id_int)
    return success_response(
        data=NotificationConfigDeleteResp(
            mid=str(auth_info.mid), is_success=bool(result)
        )
    )
