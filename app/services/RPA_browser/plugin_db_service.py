from typing import Optional, Dict
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
import uuid

from app.models.RPA_browser.plugin_model import (
    LogPluginModel,
    PageLimitPluginModel,
    RandomWaitPluginModel,
    RetryPluginModel,
    PluginBaseModel
)
from app.models.RPA_browser.default_plugin_config import (
    get_default_plugin_config,
    is_plugin_config_changed
)
from app.services.site_rpa_operation.plugins import PluginTypeEnum
from app.utils.plugin_utils import get_plugin_model_class


class PluginDBService:
    """
    插件设置 CRUD 服务
    """

    @staticmethod
    async def get_user_default_plugins(
            browser_token: uuid.UUID,
            session: AsyncSession
    ) -> Dict[PluginTypeEnum, PluginBaseModel]:
        """
        获取用户的默认插件配置（browser_info_id为空的插件）
        """
        result = {}
        for plugin_type in PluginTypeEnum:
            model = get_plugin_model_class(plugin_type)
            stmt = select(model).where(
                model.browser_token == browser_token,
                model.browser_info_id.is_(None)
            )
            exec_result = await session.exec(stmt)
            plugin = exec_result.first()
            if plugin:
                result[plugin_type] = plugin

        return result

    @staticmethod
    async def get_browser_info_plugins(
            browser_token: uuid.UUID,
            browser_id: int,
            session: AsyncSession
    ) -> Dict[PluginTypeEnum, PluginBaseModel]:
        """
        获取浏览器实例的插件配置
        如果没有特定插件，则使用用户默认插件
        如果连用户默认插件也没有，则返回默认配置的虚拟对象
        """
        result = {}
        for plugin_type in PluginTypeEnum:
            model = get_plugin_model_class(plugin_type)
            # 首先尝试获取浏览器实例特定的插件
            stmt = select(model).where(
                model.browser_token == browser_token,
                model.browser_info_id == browser_id
            )
            exec_result = await session.exec(stmt)
            plugin = exec_result.first()

            # 如果没有找到特定插件，则获取用户默认插件
            if not plugin:
                stmt = select(model).where(
                    model.browser_token == browser_token,
                    model.browser_info_id.is_(None)
                )
                exec_result = await session.exec(stmt)
                plugin = exec_result.first()

            # 如果连用户默认插件也没有，则创建默认配置的虚拟对象
            if not plugin:
                default_config = get_default_plugin_config(plugin_type)
                plugin_data = {
                    "browser_token": browser_token,
                    "browser_info_id": browser_id,  # 注意：这里使用browser_id而不是None
                    **default_config
                }

                plugin = model(**plugin_data)
                plugin.id = -1  # 虚拟ID表示非数据库对象

            result[plugin_type] = plugin

        return result

    @staticmethod
    async def get_specific_plugin_for_browser_info(
            plugin_type: PluginTypeEnum,
            browser_token: uuid.UUID,
            browser_info_id: int,
            session: AsyncSession
    ) -> Optional[PluginBaseModel]:
        """
        获取浏览器实例的特定类型插件
        如果没有特定插件，则使用用户默认插件
        """
        model = get_plugin_model_class(plugin_type)
        if not model:
            return None

        # 首先尝试获取浏览器实例特定的插件
        stmt = select(model).where(
            model.browser_token == browser_token,
            model.browser_info_id == browser_info_id
        )
        result = await session.exec(stmt)
        plugin = result.first()

        # 如果没有找到特定插件，则获取用户默认插件
        if not plugin:
            stmt = select(model).where(
                model.browser_token == browser_token,
                model.browser_info_id.is_(None)
            )
            result = await session.exec(stmt)
            plugin = result.first()

        return plugin

    @staticmethod
    async def create_plugin_for_browser_info(
            plugin_type: PluginTypeEnum,
            browser_token: uuid.UUID,
            browser_info_id: int,
            session: AsyncSession,
            **kwargs
    ) -> Optional[PluginBaseModel]:
        """
        为浏览器实例创建特定类型的插件（覆盖用户默认插件）
        只有当配置与默认值不同时才创建数据库记录
        """
        model = get_plugin_model_class(plugin_type)
        if not model:
            return None

        # 检查配置是否与默认值不同
        if not is_plugin_config_changed(plugin_type, kwargs):
            # 如果配置与默认值相同，返回默认配置的模拟对象而不创建数据库记录
            default_config = get_default_plugin_config(plugin_type)
            plugin_data = {
                "browser_token": browser_token,
                "browser_info_id": browser_info_id,
                **default_config,
                **kwargs  # kwargs中的值会覆盖默认值（如果有）
            }

            # 创建一个不持久化的插件对象
            plugin = model(**plugin_data)
            # 设置一个特殊标记表示这是虚拟对象
            plugin.id = -1  # 虚拟ID表示非数据库对象
            return plugin

        # 如果配置与默认值不同，则创建数据库记录
        plugin_data = {
            "browser_token": browser_token,
            "browser_info_id": browser_info_id,
            **kwargs
        }

        plugin = model(**plugin_data)
        session.add(plugin)
        await session.commit()
        await session.refresh(plugin)
        return plugin

    @staticmethod
    async def get_or_create_user_default_plugins(
            browser_token: uuid.UUID,
            session: AsyncSession
    ) -> Dict[PluginTypeEnum, PluginBaseModel]:
        """
        获取用户默认插件配置
        注意：此方法不再自动创建默认插件，而是返回预定义的默认配置
        """
        # 检查是否已存在用户默认插件配置
        existing_plugins = await PluginDBService.get_user_default_plugins(browser_token, session)

        # 始终返回所有类型的默认配置（无论是否存在于数据库中）
        result = {}

        for plugin_type in PluginTypeEnum:
            if plugin_type in existing_plugins:
                # 如果数据库中有自定义配置，则使用它
                result[plugin_type] = existing_plugins[plugin_type]
            else:
                # 否则返回默认配置的模拟对象
                default_config = get_default_plugin_config(plugin_type)
                model = get_plugin_model_class(plugin_type)
                if model:
                    plugin_data = {
                        "browser_token": browser_token,
                        "browser_info_id": None,
                        **default_config
                    }

                    plugin = model(**plugin_data)
                    plugin.id = -1  # 虚拟ID表示非数据库对象
                    result[plugin_type] = plugin

        return result

    @staticmethod
    async def create_log_plugin(params: LogPluginModel, session: AsyncSession) -> LogPluginModel:
        """
        创建日志插件
        只有当配置与默认值不同时才创建数据库记录
        """
        # 提取参数用于比较
        plugin_params = {
            "name": params.name,
            "description": params.description,
            "is_enabled": params.is_enabled,
            "log_level": params.log_level
        }

        # 检查配置是否与默认值不同
        if not is_plugin_config_changed(PluginTypeEnum.LOG, plugin_params):
            # 如果配置与默认值相同，返回默认配置的模拟对象而不创建数据库记录
            default_config = get_default_plugin_config(PluginTypeEnum.LOG)
            plugin_data = {
                "browser_token": params.browser_token,
                "browser_info_id": params.browser_info_id,
                **default_config
            }

            plugin = LogPluginModel(**plugin_data)
            plugin.id = -1  # 虚拟ID表示非数据库对象
            return plugin

        # 如果配置与默认值不同，则创建数据库记录
        session.add(params)
        await session.commit()
        await session.refresh(params)
        return params

    @staticmethod
    async def create_page_limit_plugin(params: PageLimitPluginModel, session: AsyncSession) -> PageLimitPluginModel:
        """
        创建页面限制插件
        只有当配置与默认值不同时才创建数据库记录
        """
        # 提取参数用于比较
        plugin_params = {
            "name": params.name,
            "description": params.description,
            "is_enabled": params.is_enabled,
            "max_pages": params.max_pages
        }

        # 检查配置是否与默认值不同
        if not is_plugin_config_changed(PluginTypeEnum.PAGE_LIMIT, plugin_params):
            # 如果配置与默认值相同，返回默认配置的模拟对象而不创建数据库记录
            default_config = get_default_plugin_config(PluginTypeEnum.PAGE_LIMIT)
            plugin_data = {
                "browser_token": params.browser_token,
                "browser_info_id": params.browser_info_id,
                **default_config
            }

            plugin = PageLimitPluginModel(**plugin_data)
            plugin.id = -1  # 虚拟ID表示非数据库对象
            return plugin

        # 如果配置与默认值不同，则创建数据库记录
        session.add(params)
        await session.commit()
        await session.refresh(params)
        return params

    @staticmethod
    async def create_random_wait_plugin(params: RandomWaitPluginModel, session: AsyncSession) -> RandomWaitPluginModel:
        """
        创建随机等待插件
        只有当配置与默认值不同时才创建数据库记录
        """
        # 提取参数用于比较
        plugin_params = {
            "name": params.name,
            "description": params.description,
            "is_enabled": params.is_enabled,
            "min_wait": params.min_wait,
            "mid_wait": params.mid_wait,
            "max_wait": params.max_wait,
            "long_wait_interval": params.long_wait_interval,
            "mid_wait_interval": params.mid_wait_interval,
            "base_long_wait_prob": params.base_long_wait_prob,
            "base_mid_wait_prob": params.base_mid_wait_prob,
            "prob_increase_factor": params.prob_increase_factor
        }

        # 检查配置是否与默认值不同
        if not is_plugin_config_changed(PluginTypeEnum.RANDOM_WAIT, plugin_params):
            # 如果配置与默认值相同，返回默认配置的模拟对象而不创建数据库记录
            default_config = get_default_plugin_config(PluginTypeEnum.RANDOM_WAIT)
            plugin_data = {
                "browser_token": params.browser_token,
                "browser_info_id": params.browser_info_id,
                **default_config
            }

            plugin = RandomWaitPluginModel(**plugin_data)
            plugin.id = -1  # 虚拟ID表示非数据库对象
            return plugin

        # 如果配置与默认值不同，则创建数据库记录
        session.add(params)
        await session.commit()
        await session.refresh(params)
        return params

    @staticmethod
    async def create_retry_plugin(params: RetryPluginModel, session: AsyncSession) -> RetryPluginModel:
        """
        创建重试插件
        只有当配置与默认值不同时才创建数据库记录
        """
        # 提取参数用于比较
        plugin_params = {
            "name": params.name,
            "description": params.description,
            "is_enabled": params.is_enabled,
            "retry_times": params.retry_times,
            "delay": params.delay,
            "is_push_msg_on_error": params.is_push_msg_on_error
        }

        # 检查配置是否与默认值不同
        if not is_plugin_config_changed(PluginTypeEnum.RETRY, plugin_params):
            # 如果配置与默认值相同，返回默认配置的模拟对象而不创建数据库记录
            default_config = get_default_plugin_config(PluginTypeEnum.RETRY)
            plugin_data = {
                "browser_token": params.browser_token,
                "browser_info_id": params.browser_info_id,
                **default_config
            }

            plugin = RetryPluginModel(**plugin_data)
            plugin.id = -1  # 虚拟ID表示非数据库对象
            return plugin

        # 如果配置与默认值不同，则创建数据库记录
        session.add(params)
        await session.commit()
        await session.refresh(params)
        return params

    @staticmethod
    async def get_user_plugin(plugin_id: int, session: AsyncSession) -> Optional[PluginBaseModel]:
        """
        获取用户插件
        """
        # 虚拟ID的对象不存储在数据库中
        if plugin_id == -1:
            return None

        # 尝试从所有插件表中查找
        for plugin_type in PluginTypeEnum:
            model = get_plugin_model_class(plugin_type)
            stmt = select(model).where(model.id == plugin_id)
            result = await session.exec(stmt)
            plugin = result.one_or_none()
            if plugin:
                return plugin

        return None

    @staticmethod
    async def update_user_plugin(
            plugin_id: int,
            name: Optional[str] = None,
            description: Optional[str] = None,
            is_enabled: Optional[bool] = None,
            session: AsyncSession = None,
            **kwargs
    ) -> Optional[PluginBaseModel]:
        """
        更新用户插件
        如果更新后的配置与默认值相同，则删除数据库记录
        """
        # 虚拟ID的对象不能更新
        if plugin_id == -1:
            return None

        plugin = await PluginDBService.get_user_plugin(plugin_id, session)
        if not plugin:
            return None

        # 更新字段
        if name is not None:
            plugin.name = name
        if description is not None:
            plugin.description = description
        if is_enabled is not None:
            plugin.is_enabled = is_enabled

        # 根据插件类型更新特定字段
        if isinstance(plugin, LogPluginModel) and 'log_level' in kwargs:
            plugin.log_level = kwargs['log_level']
        elif isinstance(plugin, PageLimitPluginModel) and 'max_pages' in kwargs:
            plugin.max_pages = kwargs['max_pages']
        elif isinstance(plugin, RandomWaitPluginModel):
            for field in ['min_wait', 'mid_wait', 'max_wait', 'long_wait_interval',
                          'mid_wait_interval', 'base_long_wait_prob', 'base_mid_wait_prob',
                          'prob_increase_factor']:
                if field in kwargs:
                    setattr(plugin, field, kwargs[field])
        elif isinstance(plugin, RetryPluginModel):
            for field in ['retry_times', 'delay', 'is_push_msg_on_error']:
                if field in kwargs:
                    setattr(plugin, field, kwargs[field])

        # 检查更新后的配置是否与默认值相同
        # 构造当前配置用于比较
        current_config = {}
        if isinstance(plugin, LogPluginModel):
            current_config = {
                "name": plugin.name,
                "description": plugin.description,
                "is_enabled": plugin.is_enabled,
                "log_level": plugin.log_level
            }
        elif isinstance(plugin, PageLimitPluginModel):
            current_config = {
                "name": plugin.name,
                "description": plugin.description,
                "is_enabled": plugin.is_enabled,
                "max_pages": plugin.max_pages
            }
        elif isinstance(plugin, RandomWaitPluginModel):
            current_config = {
                "name": plugin.name,
                "description": plugin.description,
                "is_enabled": plugin.is_enabled,
                "min_wait": plugin.min_wait,
                "mid_wait": plugin.mid_wait,
                "max_wait": plugin.max_wait,
                "long_wait_interval": plugin.long_wait_interval,
                "mid_wait_interval": plugin.mid_wait_interval,
                "base_long_wait_prob": plugin.base_long_wait_prob,
                "base_mid_wait_prob": plugin.base_mid_wait_prob,
                "prob_increase_factor": plugin.prob_increase_factor
            }
        elif isinstance(plugin, RetryPluginModel):
            current_config = {
                "name": plugin.name,
                "description": plugin.description,
                "is_enabled": plugin.is_enabled,
                "retry_times": plugin.retry_times,
                "delay": plugin.delay,
                "is_push_msg_on_error": plugin.is_push_msg_on_error
            }

        # 确定插件类型
        plugin_type = None
        if isinstance(plugin, LogPluginModel):
            plugin_type = PluginTypeEnum.LOG
        elif isinstance(plugin, PageLimitPluginModel):
            plugin_type = PluginTypeEnum.PAGE_LIMIT
        elif isinstance(plugin, RandomWaitPluginModel):
            plugin_type = PluginTypeEnum.RANDOM_WAIT
        elif isinstance(plugin, RetryPluginModel):
            plugin_type = PluginTypeEnum.RETRY

        # 如果更新后的配置与默认值相同，则删除数据库记录
        if plugin_type and not is_plugin_config_changed(plugin_type, current_config):
            await session.delete(plugin)
            await session.commit()
            # 返回默认配置的模拟对象
            default_config = get_default_plugin_config(plugin_type)
            model = get_plugin_model_class(plugin_type)
            if model:
                plugin_data = {
                    "browser_token": plugin.browser_token,
                    "browser_info_id": plugin.browser_info_id,
                    **default_config
                }

                new_plugin = model(**plugin_data)
                new_plugin.id = -1  # 虚拟ID表示非数据库对象
                return new_plugin
        else:
            # 否则更新数据库记录
            session.add(plugin)
            await session.commit()
            await session.refresh(plugin)

        return plugin

    @staticmethod
    async def delete_user_plugin(plugin_id: int, session: AsyncSession) -> bool:
        """
        删除用户插件
        """
        # 虚拟ID的对象不存储在数据库中
        if plugin_id == -1:
            return True  # 认为删除成功

        plugin = await PluginDBService.get_user_plugin(plugin_id, session)
        if not plugin:
            return False

        await session.delete(plugin)
        await session.commit()
        return True
