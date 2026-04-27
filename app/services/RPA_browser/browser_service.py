from app.models.core.notify.models import (
    NotificationConfig,
)
from app.models.notify.models import (
    NotificationConfigCreate,
    NotificationConfigUpdate,
)
from app.models.notify.response_models import NotificationConfigEffectiveResp
from app.models.core.browser.fingerprint import BaseFingerprintBrowserInitParams
from app.models.runtime.api import BrowserFingerprintCreateParams
from app.services.RPA_browser.notification_service import NotificationService
from app.services.broswer_fingerprint.fingerprint_gen import (
    gen_from_browserforge_fingerprint,
)
from sqlmodel.ext.asyncio.session import AsyncSession

from app.utils.depends.session_manager import DatabaseSessionManager


class BrowserService:
    def __init__(self, mid: int):
        """
        初始化BrowserService实例

        Args:
            mid: 微服务主系统的用户ID，用于标识特定的用户
        """
        self.mid = mid

    async def gen_rand_fingerprint(
        self, params: BrowserFingerprintCreateParams
    ) -> BaseFingerprintBrowserInitParams:
        """
        生成随机浏览器指纹

        Args:
            params: 浏览器指纹生成参数

        Returns:
            BaseFingerprintBrowserInitParams: 生成的浏览器指纹参数
        """
        res = await gen_from_browserforge_fingerprint(params=params)
        res.proxy_server = None  # 清除代理服务器配置
        return res

    # Notification related methods
    async def get_notification_config(
        self, session: AsyncSession, browser_id: int | None = None
    ) -> NotificationConfig | None:
        """
        获取推送通知配置
        优先获取browser特定的配置，如果不存在则返回全局配置
        """
        return await NotificationService.get_notification_config(
            str(self.mid), session, browser_id
        )

    async def get_effective_notification_config(
        self, session: AsyncSession, browser_id: int | None = None
    ) -> NotificationConfigEffectiveResp | None:
        """
        获取有效的通知配置（包含优先级逻辑）
        返回浏览器特定配置或全局配置，并指明配置来源
        """
        return await NotificationService.get_effective_notification_config(
            str(self.mid), session, browser_id
        )

    async def create_notification_config(
        self, config_create: NotificationConfigCreate, session: AsyncSession
    ) -> NotificationConfig:
        """
        创建推送通知配置
        """
        return await NotificationService.create_notification_config(
            config_create, self.mid, session
        )

    async def update_notification_config(
        self, config_update: NotificationConfigUpdate, session: AsyncSession
    ) -> NotificationConfig | None:
        """
        更新推送通知配置
        """
        return await NotificationService.update_notification_config(
            config_update, session
        )

    async def delete_notification_config(
        self, session: AsyncSession, browser_id: int | None = None
    ) -> bool:
        """
        删除推送通知配置
        如果提供了browser_id，删除特定browser的配置；否则删除全局配置
        """
        return await NotificationService.delete_notification_config(
            str(self.mid), session, browser_id
        )

    async def push_notification(
        self,
        title: str,
        content: str,
        session: AsyncSession,
        browser_id: int | None = None,
    ):
        """
        发送推送通知
        优先使用browser特定的配置，如果不存在则使用全局配置
        """
        return await NotificationService.push_msg(
            str(self.mid), title, content, session, browser_id
        )

    async def send_msg(
        self,
        browser_id,
        title: str,
        content: str,
    ) -> None:
        async with DatabaseSessionManager.async_session() as session:
            conf = await self.get_notification_config(
                session=session, browser_id=browser_id
            )
        if not conf:
            return
        await send(title=title, content=content, conf=conf)
