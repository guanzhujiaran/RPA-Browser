import uuid
from typing import Optional, Dict
from app.models.RPA_browser.plugin_model import (
    LogPluginModel,
    PageLimitPluginModel,
    RandomWaitPluginModel,
    RetryPluginModel,
    PluginBaseModel
)
from app.models.RPA_browser.notify_model import (
    NotificationConfig,
    NotificationConfigCreate,
    NotificationConfigUpdate
)
from app.models.RPA_browser.browser_info_model import (
    BaseFingerprintBrowserInitParams,
    UserBrowserInfoCreateParams,
    BrowserOpenUrlParams,
    BrowserOpenUrlResp,
    BrowserScreenshotParams,
    BrowserScreenshotResp,
    BrowserReleaseResp
)
from app.services.RPA_browser.plugin_db_service import PluginDBService
from app.services.RPA_browser.notification_service import NotificationService
from app.services.broswer_fingerprint.fingerprint_gen import gen_from_browserforge_fingerprint
from app.services.RPA_browser.browser_session_pool.playwright_pool import get_default_session_pool
from app.services.RPA_browser.jwt_cache_service import JWTTokenService
from sqlmodel.ext.asyncio.session import AsyncSession

from app.services.site_rpa_operation.plugins import PluginTypeEnum


class BrowserService:
    def __init__(self, browser_token: uuid.UUID):
        """
        初始化BrowserService实例
        
        Args:
            browser_token: 浏览器令牌，用于标识特定的浏览器实例
        """
        self.browser_token = browser_token

    def gen_rand_fingerprint(self, params: UserBrowserInfoCreateParams) -> BaseFingerprintBrowserInitParams:
        """
        生成随机浏览器指纹
        
        Args:
            params: 浏览器指纹生成参数
            
        Returns:
            BaseFingerprintBrowserInitParams: 生成的浏览器指纹参数
        """
        return gen_from_browserforge_fingerprint(params=params)

    async def create_log_plugin(self, params: LogPluginModel, session: AsyncSession) -> LogPluginModel:
        """
        创建日志插件
        只有当配置与默认值不同时才创建数据库记录
        """
        return await PluginDBService.create_log_plugin(params=params, session=session)

    async def create_page_limit_plugin(self, params: PageLimitPluginModel, session: AsyncSession) -> PageLimitPluginModel:
        """
        创建页面限制插件
        只有当配置与默认值不同时才创建数据库记录
        """
        return await PluginDBService.create_page_limit_plugin(params=params, session=session)

    async def create_random_wait_plugin(self, params: RandomWaitPluginModel, session: AsyncSession) -> RandomWaitPluginModel:
        """
        创建随机等待插件
        只有当配置与默认值不同时才创建数据库记录
        """
        return await PluginDBService.create_random_wait_plugin(params=params, session=session)

    async def create_retry_plugin(self, params: RetryPluginModel, session: AsyncSession) -> RetryPluginModel:
        """
        创建重试插件
        只有当配置与默认值不同时才创建数据库记录
        """
        return await PluginDBService.create_retry_plugin(params=params, session=session)

    async def delete_plugin(self, plugin_id: int, session: AsyncSession) -> bool:
        """
        删除插件配置
        """
        return await PluginDBService.delete_user_plugin(plugin_id, session)

    # Notification related methods
    async def get_notification_config(self, session: AsyncSession) -> Optional[NotificationConfig]:
        """
        获取推送通知配置
        """
        return await NotificationService.get_notification_config(self.browser_token, session)

    async def create_notification_config(self, config_create: NotificationConfigCreate, session: AsyncSession) -> NotificationConfig:
        """
        创建推送通知配置
        """
        return await NotificationService.create_notification_config(config_create, session)

    async def update_notification_config(self, config_update: NotificationConfigUpdate, session: AsyncSession) -> Optional[NotificationConfig]:
        """
        更新推送通知配置
        """
        return await NotificationService.update_notification_config(config_update, session)

    async def delete_notification_config(self, session: AsyncSession) -> bool:
        """
        删除推送通知配置
        """
        return await NotificationService.delete_notification_config(self.browser_token, session)

    async def get_jwt_token(self) -> str:
        """
        获取JWT访问令牌
        
        Returns:
            str: JWT访问令牌
        """
        return JWTTokenService.generate_jwt_token(self.browser_token)

    async def get_user_default_plugins(self, session: AsyncSession) -> Dict[PluginTypeEnum, PluginBaseModel]:
        """
        获取用户默认插件配置
        """
        return await PluginDBService.get_user_default_plugins(self.browser_token, session)
        
    async def get_or_create_user_default_plugins(self, session: AsyncSession) -> Dict[PluginTypeEnum, PluginBaseModel]:
        """
        获取用户默认插件配置（兼容旧接口）
        """
        return await PluginDBService.get_or_create_user_default_plugins(self.browser_token, session)
        
    async def get_browser_info_plugins(self, browser_info_id: int, session: AsyncSession) -> Dict[PluginTypeEnum, PluginBaseModel]:
        """
        获取浏览器实例的插件配置
        """
        return await PluginDBService.get_browser_info_plugins(self.browser_token, browser_info_id, session)
        
    async def get_specific_plugin_for_browser_info(self, plugin_type: PluginTypeEnum, browser_info_id: int, session: AsyncSession) -> Optional[PluginBaseModel]:
        """
        获取浏览器实例的特定类型插件
        """
        return await PluginDBService.get_specific_plugin_for_browser_info(plugin_type, self.browser_token, browser_info_id, session)
        
    async def create_plugin_for_browser_info(self, plugin_type: PluginTypeEnum, browser_info_id: int, session: AsyncSession, **kwargs) -> Optional[PluginBaseModel]:
        """
        为浏览器实例创建特定类型的插件
        只有当配置与默认值不同时才创建数据库记录
        """
        return await PluginDBService.create_plugin_for_browser_info(plugin_type, self.browser_token, browser_info_id, session, **kwargs)