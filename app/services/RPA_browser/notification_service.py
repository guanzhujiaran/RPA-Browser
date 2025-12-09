from typing import Optional
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
import uuid

from app.models.RPA_browser.notify_model import (
    NotificationConfig,
    NotificationConfigCreate,
    NotificationConfigUpdate
)
from app.services.notify import push_msg


class NotificationService:
    """
    推送设置 CRUD 服务
    """

    @staticmethod
    async def get_notification_config(
            browser_token: uuid.UUID,
            session: AsyncSession
    ) -> Optional[NotificationConfig]:
        """
        根据 browser_token 获取推送配置
        """
        stmt = select(NotificationConfig).where(NotificationConfig.browser_token == browser_token)
        result = await session.exec(stmt)
        return result.one_or_none()

    @staticmethod
    async def create_notification_config(
            config_create: NotificationConfigCreate,
            session: AsyncSession
    ) -> NotificationConfig:
        """
        创建推送配置
        """
        config = NotificationConfig(**config_create.model_dump())
        session.add(config)
        await session.commit()
        await session.refresh(config)
        return config

    @staticmethod
    async def update_notification_config(
            config_update: NotificationConfigUpdate,
            session: AsyncSession
    ) -> Optional[NotificationConfig]:
        """
        更新推送配置
        """
        stmt = select(NotificationConfig).where(NotificationConfig.id == config_update.id)
        result = await session.exec(stmt)
        config = result.one_or_none()

        if config is None:
            return None

        update_data = config_update.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            if key != 'id' and hasattr(config, key):
                setattr(config, key, value)

        session.add(config)
        await session.commit()
        await session.refresh(config)
        return config

    @staticmethod
    async def delete_notification_config(
            browser_token: uuid.UUID,
            session: AsyncSession
    ) -> bool:
        """
        删除推送配置
        """
        stmt = select(NotificationConfig).where(NotificationConfig.browser_token == browser_token)
        result = await session.exec(stmt)
        config = result.one_or_none()

        if config is None:
            return False

        await session.delete(config)
        await session.commit()
        return True

    @staticmethod
    async def push_msg(browser_token: uuid.UUID, title: str, content: str, session: AsyncSession):
        config = await NotificationService.get_notification_config(browser_token, session)
        if config is None:
            return
        await NotificationService.push_msg_by_config(title, content, config)

    @classmethod
    async def push_msg_by_config(cls, title: str, content: str, conf: NotificationConfig, **kwargs):
        return await push_msg.send(title, content, conf, **kwargs)