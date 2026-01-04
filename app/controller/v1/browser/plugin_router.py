
from fastapi import Depends
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.RPA_browser.plugin_model import (
    LogPluginCreate,
    PageLimitPluginCreate,
    RandomWaitPluginCreate,
    RetryPluginCreate,
)
from app.models.RPA_browser.plugin_request_model import (
    PluginCreateRequest,
    PluginGetRequest,
    PluginListRequest,
    PluginDeleteRequest,
    PluginUpdateRequest,
    PluginDictResponse,
    PluginResponse,
    LogPluginResponse,
    PageLimitPluginResponse,
    RandomWaitPluginResponse,
    RetryPluginResponse,
)
from app.models.router.router_prefix import PluginRouterPath
from app.services.site_rpa_operation.plugins import PluginTypeEnum
from app.controller.v1.browser.plugin_base import new_router
from app.models.response import StandardResponse, success_response, error_response
from app.models.response_code import ResponseCode
from app.services.RPA_browser.browser_service import BrowserService
from app.utils.depends.mid_depends import AuthInfo, get_auth_info_from_header
from app.utils.depends.session_manager import DatabaseSessionManager


router = new_router()


@router.post(PluginRouterPath.create_plugin, response_model=StandardResponse[PluginResponse])
async def create_or_update_plugin_router(
    params: PluginCreateRequest,
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    session: AsyncSession = DatabaseSessionManager.get_dependency(),
):
    """
    创建或更新插件配置
    
    根据指定的插件类型创建新插件配置或更新现有配置。支持多种插件类型：
    - LOG: 日志插件，用于记录操作日志
    - PAGE_LIMIT: 页面限制插件，限制访问页面数量
    - RANDOM_WAIT: 随机等待插件，模拟人工操作的随机等待时间
    - RETRY: 重试插件，操作失败时自动重试
    
    Args:
        params: 插件创建请求参数，包含插件类型、名称、描述、配置等
        mid: 用户ID，从请求头中自动获取
        session: 数据库会话
        
    Returns:
        PluginResponse: 创建或更新的插件配置信息，包含插件的完整配置
        
    Note:
        如果插件已存在则会更新现有配置，插件配置会关联到指定的浏览器指纹ID
    """
    try:
        # 验证插件类型
        try:
            plugin_enum = PluginTypeEnum(params.plugin_type)
        except ValueError:
            return error_response(
                code=ResponseCode.BAD_REQUEST,
                msg=f"不支持的插件类型: {params.plugin_type}"
            )

        # 直接使用int类型的mid
        browser_service = BrowserService(auth_info.mid)

        # 根据插件类型创建对应的插件
        if plugin_enum == PluginTypeEnum.LOG:
            from app.models.RPA_browser.plugin_model import LogPluginLogLevelEnum
            plugin_params = LogPluginCreate(
                mid=str(mid),
                browser_info_id=str(params.browser_info_id) if params.browser_info_id else None,
                is_enabled=params.is_enabled,
                name=params.name,
                description=params.description,
                log_level=params.log_level or LogPluginLogLevelEnum.INFO
            )
            plugin = await browser_service.create_log_plugin(plugin_params, session)
            
        elif plugin_enum == PluginTypeEnum.PAGE_LIMIT:
            plugin_params = PageLimitPluginCreate(
                mid=str(mid),
                browser_info_id=str(params.browser_info_id) if params.browser_info_id else None,
                is_enabled=params.is_enabled,
                name=params.name,
                description=params.description,
                max_pages=params.max_pages or 5
            )
            plugin = await browser_service.create_page_limit_plugin(plugin_params, session)
            
        elif plugin_enum == PluginTypeEnum.RANDOM_WAIT:
            plugin_params = RandomWaitPluginCreate(
                mid=str(mid),
                browser_info_id=str(params.browser_info_id) if params.browser_info_id else None,
                is_enabled=params.is_enabled,
                name=params.name,
                description=params.description,
                min_wait=params.min_wait or 1.0,
                mid_wait=params.mid_wait or 10.0,
                max_wait=params.max_wait or 30.0,
                long_wait_interval=params.long_wait_interval or 10,
                mid_wait_interval=params.mid_wait_interval or 5,
                base_long_wait_prob=params.base_long_wait_prob or 0.05,
                base_mid_wait_prob=params.base_mid_wait_prob or 0.15,
                prob_increase_factor=params.prob_increase_factor or 0.02
            )
            plugin = await browser_service.create_random_wait_plugin(plugin_params, session)
            
        elif plugin_enum == PluginTypeEnum.RETRY:
            plugin_params = RetryPluginCreate(
                mid=str(mid),
                browser_info_id=str(params.browser_info_id) if params.browser_info_id else None,
                is_enabled=params.is_enabled,
                name=params.name,
                description=params.description,
                retry_times=params.retry_times or 3,
                delay=params.delay or 30.0,
                is_push_msg_on_error=params.is_push_msg_on_error if params.is_push_msg_on_error is not None else True
            )
            plugin = await browser_service.create_retry_plugin(plugin_params, session)

        # 转换为统一的响应模型
        plugin_dict = plugin.model_dump()
        
        # 基础字段
        base_data = {
            "id": plugin_dict["id"],
            "mid": plugin_dict["mid"],
            "browser_info_id": plugin_dict.get("browser_info_id"),
            "is_enabled": plugin_dict["is_enabled"],
            "name": plugin_dict["name"],
            "description": plugin_dict["description"],
            "plugin_type": plugin_enum.value,
            "is_virtual": plugin.id == -1
        }
        
        # 根据插件类型创建对应的响应模型
        if plugin_enum == PluginTypeEnum.LOG:
            response_data = LogPluginResponse(
                **base_data,
                config=plugin
            )
        elif plugin_enum == PluginTypeEnum.PAGE_LIMIT:
            response_data = PageLimitPluginResponse(
                **base_data,
                config=plugin
            )
        elif plugin_enum == PluginTypeEnum.RANDOM_WAIT:
            response_data = RandomWaitPluginResponse(
                **base_data,
                config=plugin
            )
        elif plugin_enum == PluginTypeEnum.RETRY:
            response_data = RetryPluginResponse(
                **base_data,
                config=plugin
            )
        else:
            # 默认情况，理论上不会到达这里
            response_data = PluginResponse(**base_data)
        
        return success_response(data=response_data)

    except Exception as e:
        return error_response(
            code=ResponseCode.INTERNAL_ERROR,
            msg=f"创建插件失败: {str(e)}"
        )


@router.put(PluginRouterPath.update_plugin, response_model=StandardResponse[PluginResponse])
async def update_plugin_router(
    params: PluginUpdateRequest,
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    session: AsyncSession = DatabaseSessionManager.get_dependency(),
):
    """
    更新现有插件配置
    
    根据插件ID更新现有的插件配置。支持更新插件的所有可配置参数，包括启用状态、名称、描述
    以及各种插件特定的配置参数。如果插件不存在则返回错误。
    
    Args:
        params: 插件更新请求参数，包含插件ID和需要更新的配置项
        mid: 用户ID，从请求头中自动获取，用于权限验证
        session: 数据库会话
        
    Returns:
        PluginResponse: 更新后的插件配置信息，包含完整的插件配置
        
    Note:
        只能更新属于当前用户的插件配置，更新操作会覆盖现有的配置
    """
    try:
        # 验证插件类型
        try:
            plugin_enum = PluginTypeEnum(params.plugin_type)
        except ValueError:
            return error_response(
                code=ResponseCode.BAD_REQUEST,
                msg=f"不支持的插件类型: {params.plugin_type}"
            )

        # 直接使用int类型的mid
        browser_service = BrowserService(auth_info.mid)

        # 先获取现有插件
        if params.browser_info_id is not None:
            existing_plugins = await browser_service.get_browser_info_plugins(
                int(params.browser_info_id), session
            )
        else:
            existing_plugins = await browser_service.get_user_default_plugins(session)
        
        existing_plugin = existing_plugins.get(plugin_enum)
        
        if not existing_plugin:
            return error_response(
                code=ResponseCode.NOT_FOUND,
                msg=f"未找到插件配置: {params.plugin_type}"
            )

        # 更新插件配置（这里简化处理，实际应该有专门的更新方法）
        # 由于service层没有更新方法，这里调用创建方法来覆盖
        return await create_or_update_plugin_router(
            PluginCreateRequest(
                plugin_type=params.plugin_type,
                browser_info_id=params.browser_info_id,
                is_enabled=params.is_enabled or existing_plugin.is_enabled,
                name=params.name or existing_plugin.name,
                description=params.description or existing_plugin.description,
                log_level=params.log_level,
                max_pages=params.max_pages,
                min_wait=params.min_wait,
                mid_wait=params.mid_wait,
                max_wait=params.max_wait,
                long_wait_interval=params.long_wait_interval,
                mid_wait_interval=params.mid_wait_interval,
                base_long_wait_prob=params.base_long_wait_prob,
                base_mid_wait_prob=params.base_mid_wait_prob,
                prob_increase_factor=params.prob_increase_factor,
                retry_times=params.retry_times,
                delay=params.delay,
                is_push_msg_on_error=params.is_push_msg_on_error
            ),
            mid,
            session
        )

    except Exception as e:
        return error_response(
            code=ResponseCode.INTERNAL_ERROR,
            msg=f"更新插件失败: {str(e)}"
        )


@router.post(PluginRouterPath.get_plugins, response_model=StandardResponse[PluginDictResponse])
async def get_plugins_router(
    request: PluginListRequest,
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    session: AsyncSession = DatabaseSessionManager.get_dependency(),
):
    """
    获取插件配置列表
    
    获取当前用户的插件配置列表。可以获取特定浏览器实例的插件配置，也可以获取用户的默认插件配置。
    支持获取所有插件类型的配置，包括日志、页面限制、随机等待、重试等插件。
    
    Args:
        request: 包含可选浏览器实例ID的请求对象，如果提供则获取特定实例的插件配置，
                否则获取用户默认插件配置
        mid: 用户ID，从请求头中自动获取，用于权限验证
        session: 数据库会话
        
    Returns:
        PluginDictResponse: 包含所有插件配置的字典，键为插件类型名称，值为插件配置对象
        
    Note:
        如果没有配置特定插件，会返回虚拟插件配置（is_virtual=true）作为默认配置
    """
    try:
        # 直接使用int类型的mid
        browser_service = BrowserService(auth_info.mid)

        if request.browser_info_id is not None:
            # 获取特定浏览器实例的插件配置
            plugins = await browser_service.get_browser_info_plugins(
                int(request.browser_info_id), session
            )
        else:
            # 获取用户默认插件配置
            plugins = await browser_service.get_user_default_plugins(session)

        # 将插件对象转换为字典格式，并添加插件类型信息
        result_data = {}
        for plugin_type, plugin in plugins.items():
            plugin_dict = plugin.model_dump()
            
            # 基础字段
            base_data = {
                "id": plugin_dict["id"],
                "mid": plugin_dict["mid"],
                "browser_info_id": plugin_dict.get("browser_info_id"),
                "is_enabled": plugin_dict["is_enabled"],
                "name": plugin_dict["name"],
                "description": plugin_dict["description"],
                "plugin_type": plugin_type.value,
                "is_virtual": plugin.id == -1
            }
            
            # 根据插件类型创建对应的响应模型
            if plugin_type == PluginTypeEnum.LOG:
                plugin_response = LogPluginResponse(
                    **base_data,
                    config=plugin
                )
            elif plugin_type == PluginTypeEnum.PAGE_LIMIT:
                plugin_response = PageLimitPluginResponse(
                    **base_data,
                    config=plugin
                )
            elif plugin_type == PluginTypeEnum.RANDOM_WAIT:
                plugin_response = RandomWaitPluginResponse(
                    **base_data,
                    config=plugin
                )
            elif plugin_type == PluginTypeEnum.RETRY:
                plugin_response = RetryPluginResponse(
                    **base_data,
                    config=plugin
                )
            else:
                # 默认情况，理论上不会到达这里
                plugin_response = PluginResponse(**base_data)
            
            result_data[plugin_type.value] = plugin_response

        result = PluginDictResponse(**result_data)
        return success_response(data=result)

    except Exception as e:
        return error_response(
            code=ResponseCode.INTERNAL_ERROR,
            msg=f"获取插件配置失败: {str(e)}"
        )


@router.post(PluginRouterPath.get_plugin, response_model=StandardResponse[PluginResponse])
async def get_specific_plugin_router(
    request: PluginGetRequest,
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    session: AsyncSession = DatabaseSessionManager.get_dependency(),
):
    """
    获取特定类型的插件配置
    
    根据插件类型获取指定的插件配置。可以获取特定浏览器实例的插件配置，也可以获取用户的默认插件配置。
    支持获取 log、page_limit、random_wait、retry 等插件类型的详细配置信息。
    
    Args:
        plugin_type: 插件类型 (log, page_limit, random_wait, retry)
        browser_info_id: 可选的浏览器实例ID，如果提供则获取特定实例的插件配置
        mid: 用户ID，从请求头中自动获取，用于权限验证
        session: 数据库会话
        
    Returns:
        PluginResponse: 指定类型的插件配置对象，包含完整的插件配置信息
        
    Note:
        如果插件不存在且没有提供browser_info_id，会返回虚拟插件配置（is_virtual=true）
    """
    try:
        # 验证插件类型
        try:
            plugin_enum = PluginTypeEnum(request.plugin_type)
        except ValueError:
            return error_response(
                code=ResponseCode.BAD_REQUEST,
                msg=f"不支持的插件类型: {request.plugin_type}"
            )

        # 直接使用int类型的mid
        browser_service = BrowserService(auth_info.mid)

        if request.browser_info_id is not None:
            plugin = await browser_service.get_specific_plugin_for_browser_info(
                plugin_enum, int(request.browser_info_id), session
            )
        else:
            # 获取用户默认插件
            user_plugins = await browser_service.get_user_default_plugins(session)
            plugin = user_plugins.get(plugin_enum)

        if plugin:
            plugin_dict = plugin.model_dump()
            
            # 基础字段
            base_data = {
                "id": plugin_dict["id"],
                "mid": plugin_dict["mid"],
                "browser_info_id": plugin_dict.get("browser_info_id"),
                "is_enabled": plugin_dict["is_enabled"],
                "name": plugin_dict["name"],
                "description": plugin_dict["description"],
                "plugin_type": request.plugin_type,
                "is_virtual": plugin.id == -1
            }
            
            # 根据插件类型创建对应的响应模型
            if plugin_enum == PluginTypeEnum.LOG:
                response_data = LogPluginResponse(
                    **base_data,
                    config=plugin
                )
            elif plugin_enum == PluginTypeEnum.PAGE_LIMIT:
                response_data = PageLimitPluginResponse(
                    **base_data,
                    config=plugin
                )
            elif plugin_enum == PluginTypeEnum.RANDOM_WAIT:
                response_data = RandomWaitPluginResponse(
                    **base_data,
                    config=plugin
                )
            elif plugin_enum == PluginTypeEnum.RETRY:
                response_data = RetryPluginResponse(
                    **base_data,
                    config=plugin
                )
            else:
                # 默认情况，理论上不会到达这里
                response_data = PluginResponse(**base_data)
            
            return success_response(data=response_data)
        else:
            return error_response(
                code=ResponseCode.NOT_FOUND,
                msg=f"未找到插件配置: {request.plugin_type}"
            )

    except Exception as e:
        return error_response(
            code=ResponseCode.INTERNAL_ERROR,
            msg=f"获取插件配置失败: {str(e)}"
        )


@router.post(PluginRouterPath.delete_plugin, response_model=StandardResponse[bool])
async def delete_plugin_router(
    request: PluginDeleteRequest,
    auth_info: AuthInfo = Depends(get_auth_info_from_header),
    session: AsyncSession = DatabaseSessionManager.get_dependency(),
):
    """
    删除指定ID的插件配置
    
    根据插件ID从数据库中永久删除插件配置。删除操作不可恢复，请谨慎操作。
    只能删除属于当前用户的插件配置，删除后相关浏览器实例将不再使用该插件。
    
    Args:
        request: 包含要删除的插件ID的请求对象
        mid: 用户ID，从请求头中自动获取，用于权限验证
        session: 数据库会话
        
    Returns:
        bool: 删除操作的结果，true表示删除成功，false表示插件不存在或删除失败
        
    Note:
        删除操作不可恢复，删除后相关插件配置会从数据库中完全移除
    """
    try:
        # 直接使用int类型的mid
        browser_service = BrowserService(auth_info.mid)
        # 将plugin_id从str转换为int
        plugin_id_int = int(request.plugin_id)
        result = await browser_service.delete_plugin(plugin_id_int, session)
        
        if result:
            return success_response(data=True, msg="插件删除成功")
        else:
            return error_response(
                code=ResponseCode.NOT_FOUND,
                msg="插件不存在或删除失败"
            )

    except Exception as e:
        return error_response(
            code=ResponseCode.INTERNAL_ERROR,
            msg=f"删除插件失败: {str(e)}"
        )
