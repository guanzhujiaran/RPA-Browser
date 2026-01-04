# from typing import Optional  # Python 3.10+ 使用 | None 语法
from app.models.exceptions.base_exception import BrowserNotifyConfNotFoundException
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.RPA_browser.notify_model import (
    NotificationConfig,
    NotificationConfigCreate,
    NotificationConfigUpdate,
    NotificationConfigEffectiveResp,
)
from app.services.notify import push_msg


class NotificationService:
    """
    推送设置 CRUD 服务
    """

    @staticmethod
    async def get_notification_config(
        mid: str, session: AsyncSession, browser_id: int | None = None
    ) -> NotificationConfig | None:
        """
        根据 mid 和 browser_id 获取推送配置
        优先获取 browser_id 特定的配置，如果不存在则返回全局配置
        """
        # 如果提供了browser_id，先尝试获取browser特定的配置
        if browser_id is not None:
            stmt = select(NotificationConfig).where(
                NotificationConfig.mid == mid,
                NotificationConfig.browser_id == browser_id,
            )
            result = await session.exec(stmt)
            browser_config = result.one_or_none()
            if browser_config is not None:
                return browser_config

        # 获取全局配置（browser_id为None）
        stmt = select(NotificationConfig).where(
            NotificationConfig.mid == mid, NotificationConfig.browser_id == None
        )
        result = await session.exec(stmt)
        return result.one_or_none()

    @staticmethod
    async def get_effective_notification_config(
        mid: str, session: AsyncSession, browser_id: int | None = None
    ) -> NotificationConfigEffectiveResp | None:
        """
        获取有效的通知配置（包含优先级逻辑）
        返回浏览器特定配置或全局配置，并指明配置来源
        """
        # 如果提供了browser_id，先尝试获取browser特定的配置
        if browser_id is not None:
            stmt = select(NotificationConfig).where(
                NotificationConfig.mid == mid,
                NotificationConfig.browser_id == browser_id,
            )
            result = await session.exec(stmt)
            browser_config = result.one_or_none()
            if browser_config is not None:
                # 返回浏览器特定配置
                config_data = browser_config.model_dump()
                config_data.pop("mid", None)  # 移除mid字段
                config_data.pop("id", None)  # 移除id字段
                return NotificationConfigEffectiveResp(
                    **config_data, browser_id=browser_id, config_source="browser"
                )

        # 获取全局配置（browser_id为None）
        stmt = select(NotificationConfig).where(
            NotificationConfig.mid == mid, NotificationConfig.browser_id == None
        )
        result = await session.exec(stmt)
        global_config = result.one_or_none()
        if global_config is not None:
            # 返回全局配置
            config_data = global_config.model_dump()
            config_data.pop("mid", None)  # 移除mid字段
            config_data.pop("id", None)  # 移除id字段
            return NotificationConfigEffectiveResp(
                **config_data, browser_id=None, config_source="global"
            )

        return None

    @staticmethod
    async def create_notification_config(
        config_create: NotificationConfigCreate, mid: int, session: AsyncSession
    ) -> NotificationConfig:
        """
        创建推送配置
        """
        config_data = config_create.model_dump()
        config_data["mid"] = mid  # 添加mid字段
        config = NotificationConfig(**config_data)
        session.add(config)
        await session.commit()
        await session.refresh(config)
        return config

    @staticmethod
    async def update_notification_config(
        config_update: NotificationConfigUpdate, session: AsyncSession
    ) -> NotificationConfig | None:
        """
        更新推送配置
        """
        stmt = select(NotificationConfig).where(
            NotificationConfig.id == config_update.id
        )
        result = await session.exec(stmt)
        config = result.one_or_none()

        if config is None:
            return None

        update_data = config_update.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            if key != "id" and hasattr(config, key):
                setattr(config, key, value)

        session.add(config)
        await session.commit()
        await session.refresh(config)
        return config

    @staticmethod
    async def delete_notification_config(
        mid: str, session: AsyncSession, browser_id: int | None = None
    ) -> bool:
        """
        删除推送配置
        如果提供了browser_id，删除特定browser的配置；否则删除全局配置
        """
        if browser_id is not None:
            stmt = select(NotificationConfig).where(
                NotificationConfig.mid == mid,
                NotificationConfig.browser_id == browser_id,
            )
        else:
            stmt = select(NotificationConfig).where(
                NotificationConfig.mid == mid, NotificationConfig.browser_id == None
            )

        result = await session.exec(stmt)
        config = result.one_or_none()

        if config is None:
            raise BrowserNotifyConfNotFoundException()

        await session.delete(config)
        await session.commit()
        return True

    @staticmethod
    async def push_msg(
        mid: str,
        title: str,
        content: str,
        session: AsyncSession,
        browser_id: int | None = None,
    ):
        config = await NotificationService.get_notification_config(
            mid, session, browser_id
        )
        if config is None:
            return
        await NotificationService.push_msg_by_config(title, content, config)

    @classmethod
    async def push_msg_by_config(
        cls, title: str, content: str, conf: NotificationConfig, **kwargs
    ):
        return await push_msg.send(title, content, conf, **kwargs)
