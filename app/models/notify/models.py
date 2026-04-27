"""
Notify 模块 - 通知请求/响应模型

定义通知相关的 API 请求/响应模型（非数据库表模型）。
"""

from sqlmodel import Field, Column, SQLModel
from sqlalchemy import Text as SQLText

from app.models.core.notify.models import NotificationConfigBase


class NotificationConfigCreate(NotificationConfigBase):
    """通知配置创建模型，用于API请求"""
    browser_id: str | None = None

    @property
    def browser_id_str(self) -> str | None:
        """浏览器ID字符串形式，用于前端交互"""
        return str(self.browser_id) if self.browser_id is not None else None


class NotificationConfigUpdate(SQLModel):
    """通知配置更新模型"""
    id: int | None = None
    # 所有字段都是可选的更新字段
    browser_id: str | None = None
    hitokoto: bool | None = None
    bark_push: str | None = None
    bark_archive: str | None = None
    bark_group: str | None = None
    bark_sound: str | None = None
    bark_icon: str | None = None
    bark_level: str | None = None
    bark_url: str | None = None
    console: bool | None = None
    dd_bot_secret: str | None = None
    dd_bot_token: str | None = None
    fskey: str | None = None
    gobot_url: str | None = None
    gobot_qq: str | None = None
    gobot_token: str | None = None
    gotify_url: str | None = None
    gotify_token: str | None = None
    gotify_priority: int | None = None
    igot_push_key: str | None = None
    push_key: str | None = None
    deer_key: str | None = None
    deer_url: str | None = None
    chat_url: str | None = None
    chat_token: str | None = None
    push_plus_token: str | None = None
    push_plus_user: str | None = None
    push_plus_template: str | None = None
    push_plus_channel: str | None = None
    push_plus_webhook: str | None = None
    push_plus_callbackurl: str | None = None
    push_plus_to: str | None = None
    we_plus_bot_token: str | None = None
    we_plus_bot_receiver: str | None = None
    we_plus_bot_version: str | None = None
    qmsg_key: str | None = None
    qmsg_type: str | None = None
    qywx_origin: str | None = None
    qywx_am: str | None = None
    qywx_key: str | None = None
    tg_bot_token: str | None = None
    tg_user_id: str | None = None
    tg_api_host: str | None = None
    tg_proxy_auth: str | None = None
    tg_proxy_host: str | None = None
    tg_proxy_port: str | None = None
    aibotk_key: str | None = None
    aibotk_type: str | None = None
    aibotk_name: str | None = None
    smtp_server: str | None = None
    smtp_ssl: str | None = None
    smtp_email: str | None = None
    smtp_password: str | None = None
    smtp_name: str | None = None
    pushme_key: str | None = None
    pushme_url: str | None = None
    chronocat_qq: str | None = None
    chronocat_token: str | None = None
    chronocat_url: str | None = None
    webhook_url: str | None = None
    webhook_body: str | None = None
    webhook_headers: str | None = None
    webhook_method: str | None = None
    webhook_content_type: str | None = None
    ntfy_url: str | None = None
    ntfy_topic: str | None = None
    ntfy_priority: str | None = None
    ntfy_token: str | None = None
    ntfy_username: str | None = None
    ntfy_password: str | None = None
    ntfy_actions: str | None = None
    wxpusher_app_token: str | None = None
    wxpusher_topic_ids: str | None = None
    wxpusher_uids: str | None = None


__all__ = [
    "NotificationConfigCreate",
    "NotificationConfigUpdate",
]
