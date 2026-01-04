from app.models.RPA_browser.depends_models import VerifyBrowserDependsReq
from app.utils.depends.security_depends import verify_browser_ownership
from fastapi import Depends
from sqlmodel.ext.asyncio.session import AsyncSession
from app.models.RPA_browser.notify_model import (
    NotificationConfig,
    NotificationConfigCreate,
    NotificationConfigUpdate,
    NotificationConfigUpsertResp,
    NotificationConfigDeleteResp,
    NotifyConfigReadRequest,
    TestNotificationRequest,
    TestNotificationResponse,
)
from app.models.router.router_prefix import NotifyRouterPath
from app.controller.v1.browser.notify_base import new_router
from app.models.response import StandardResponse, success_response
from app.services.RPA_browser.browser_service import BrowserService
from app.utils.depends.mid_depends import AuthInfo, get_auth_info_from_header
from app.utils.depends.session_manager import DatabaseSessionManager
from app.utils.bigint_utils import str_to_int
import loguru

router = new_router()


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

    return success_response(data=NotificationConfigUpsertResp(mid=str(mid)))


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
        data=NotificationConfigDeleteResp(mid=str(mid), is_success=bool(result))
    )


@router.post(
    NotifyRouterPath.test_notify,
    response_model=StandardResponse[TestNotificationResponse],
)
async def test_notify_router(
    request: TestNotificationRequest,
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    session: AsyncSession = DatabaseSessionManager.get_dependency(),
):
    """
    测试推送通知

    发送一条测试通知消息来验证推送配置是否正确工作。
    可以测试全局配置或特定浏览器实例的配置。

    Args:
        request: 测试通知的请求参数，包含标题、内容和可选的浏览器ID
        mid: 用户ID，从请求头中自动获取，用于权限验证
        session: 数据库会话

    Returns:
        TestNotificationResponse: 测试结果，包含发送状态和详细信息

    Note:
        - 如果提供了browser_id，则测试特定浏览器的通知配置
        - 如果不提供browser_id，则测试全局通知配置
        - 会尝试所有配置的推送渠道，并返回成功发送的渠道列表
    """
    # 转换browser_id为int（如果提供）
    browser_id_int = (
        str_to_int(request.browser_id) if request.browser_id is not None else None
    )

    # 如果提供了browser_id，需要验证所有权
    if browser_id_int:
        await verify_browser_ownership(
            VerifyBrowserDependsReq(browser_id=browser_id_int), mid, session
        )

    # 获取有效的通知配置
    browser_service = BrowserService(auth_info.mid)
    effective_config = await browser_service.get_effective_notification_config(
        session, browser_id_int
    )

    if not effective_config:
        return success_response(
            data=TestNotificationResponse(
                success=False,
                message="未找到通知配置，请先配置推送通知",
                config_found=False,
                browser_id=request.browser_id,
                config_source=None,
                sent_channels=[],
            )
        )

    try:
        # 创建NotificationConfig对象
        # 从effective_config创建配置对象，但需要补充必要的字段
        config_dict = effective_config.model_dump()
        config_dict["mid"] = str(mid)
        # 确保有browser_id字段
        if "browser_id" not in config_dict or config_dict["browser_id"] is None:
            config_dict["browser_id"] = browser_id_int

        notification_config = NotificationConfig(**config_dict)

        # 尝试发送测试通知
        from app.services.notify import push_msg

        push_service = push_msg.PushMessageService(notification_config)

        # 获取可用的推送渠道
        available_methods = push_service.get_available_methods()
        sent_channels = [
            method
            for method in available_methods
            if getattr(notification_config, method, None)
        ]

        # 异步发送通知
        await push_service.send(request.title, request.content)

        # 根据配置判断可能发送的渠道（简化版本）
        if notification_config.bark_push:
            sent_channels.append("bark")
        if notification_config.push_key:
            sent_channels.append("server酱")
        if notification_config.smtp_email:
            sent_channels.append("smtp")
        if notification_config.tg_bot_token:
            sent_channels.append("telegram")
        if notification_config.dd_bot_token:
            sent_channels.append("钉钉")
        if notification_config.fskey:
            sent_channels.append("飞书")

        return success_response(
            data=TestNotificationResponse(
                success=True,
                message="测试通知发送成功",
                config_found=True,
                browser_id=(
                    str(effective_config.browser_id)
                    if effective_config.browser_id
                    else None
                ),
                config_source=effective_config.config_source,
                sent_channels=sent_channels,
            )
        )

    except Exception as e:

        loguru.logger.error(f"测试推送通知失败: {str(e)}")
        return success_response(
            data=TestNotificationResponse(
                success=False,
                message=f"测试通知发送失败: {str(e)}",
                config_found=True,
                browser_id=(
                    str(effective_config.browser_id)
                    if effective_config.browser_id
                    else None
                ),
                config_source=effective_config.config_source,
                sent_channels=[],
            )
        )
