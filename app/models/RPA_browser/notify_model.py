from datetime import datetime
from typing import Optional
import uuid
from sqlmodel import Field, SQLModel, Column, Text
from sqlalchemy import Text as SQLText

from app.models.base.base_sqlmodel import BaseSQLModel


class NotificationConfigBase(BaseSQLModel):
    # 关联的browser_token
    browser_token: uuid.UUID = Field(unique=True, index=True)
    
    # 一言（随机句子）
    hitokoto: bool = True

    # Bark推送配置
    bark_push: str = Field(default='', sa_column=Column(SQLText))
    bark_archive: str = Field(default='', max_length=128)
    bark_group: str = Field(default='', max_length=128)
    bark_sound: str = Field(default='', max_length=128)
    bark_icon: str = Field(default='', sa_column=Column(SQLText))
    bark_level: str = Field(default='', max_length=128)
    bark_url: str = Field(default='', sa_column=Column(SQLText))

    # 钉钉机器人
    dd_bot_secret: str = Field(default='', sa_column=Column(SQLText))
    dd_bot_token: str = Field(default='', sa_column=Column(SQLText))

    # 飞书机器人
    fskey: str = Field(default='', sa_column=Column(SQLText))

    # go-cqhttp
    gobot_url: str = Field(default='', sa_column=Column(SQLText))
    gobot_qq: str = Field(default='', max_length=128)
    gobot_token: str = Field(default='', sa_column=Column(SQLText))

    # Gotify
    gotify_url: str = Field(default='', sa_column=Column(SQLText))
    gotify_token: str = Field(default='', sa_column=Column(SQLText))
    gotify_priority: int = 0

    # iGot
    igot_push_key: str = Field(default='', sa_column=Column(SQLText))

    # Server酱
    push_key: str = Field(default='', sa_column=Column(SQLText))

    # PushDeer
    deer_key: str = Field(default='', sa_column=Column(SQLText))
    deer_url: str = Field(default='', sa_column=Column(SQLText))

    # Synology Chat
    chat_url: str = Field(default='', sa_column=Column(SQLText))
    chat_token: str = Field(default='', sa_column=Column(SQLText))

    # PushPlus
    push_plus_token: str = Field(default='', sa_column=Column(SQLText))
    push_plus_user: str = Field(default='', max_length=128)
    push_plus_template: str = Field(default='html', max_length=128)
    push_plus_channel: str = Field(default='wechat', max_length=128)
    push_plus_webhook: str = Field(default='', sa_column=Column(SQLText))
    push_plus_callbackurl: str = Field(default='', sa_column=Column(SQLText))
    push_plus_to: str = Field(default='', sa_column=Column(SQLText))

    # 微加机器人
    we_plus_bot_token: str = Field(default='', sa_column=Column(SQLText))
    we_plus_bot_receiver: str = Field(default='', sa_column=Column(SQLText))
    we_plus_bot_version: str = Field(default='pro', max_length=128)

    # Qmsg酱
    qmsg_key: str = Field(default='', sa_column=Column(SQLText))
    qmsg_type: str = Field(default='', max_length=128)

    # 企业微信
    qywx_origin: str = Field(default='', sa_column=Column(SQLText))
    qywx_am: str = Field(default='', sa_column=Column(SQLText))
    qywx_key: str = Field(default='', sa_column=Column(SQLText))

    # Telegram
    tg_bot_token: str = Field(default='', sa_column=Column(SQLText))
    tg_user_id: str = Field(default='', max_length=128)
    tg_api_host: str = Field(default='', sa_column=Column(SQLText))
    tg_proxy_auth: str = Field(default='', sa_column=Column(SQLText))
    tg_proxy_host: str = Field(default='', sa_column=Column(SQLText))
    tg_proxy_port: str = Field(default='', max_length=128)

    # 智能微秘书
    aibotk_key: str = Field(default='', sa_column=Column(SQLText))
    aibotk_type: str = Field(default='', max_length=128)
    aibotk_name: str = Field(default='', max_length=128)

    # SMTP邮件
    smtp_server: str = Field(default='', max_length=128)
    smtp_ssl: str = Field(default='false', max_length=128)
    smtp_email: str = Field(default='', max_length=256)
    smtp_password: str = Field(default='', sa_column=Column(SQLText))
    smtp_name: str = Field(default='', max_length=128)

    # PushMe
    pushme_key: str = Field(default='', sa_column=Column(SQLText))
    pushme_url: str = Field(default='', sa_column=Column(SQLText))

    # Chronocat
    chronocat_qq: str = Field(default='', max_length=128)
    chronocat_token: str = Field(default='', sa_column=Column(SQLText))
    chronocat_url: str = Field(default='', sa_column=Column(SQLText))

    # 自定义Webhook
    webhook_url: str = Field(default='', sa_column=Column(SQLText))
    webhook_body: str = Field(default='', sa_column=Column(SQLText))
    webhook_headers: str = Field(default='', sa_column=Column(SQLText))
    webhook_method: str = Field(default='', max_length=128)
    webhook_content_type: str = Field(default='', max_length=128)

    # Ntfy
    ntfy_url: str = Field(default='', sa_column=Column(SQLText))
    ntfy_topic: str = Field(default='', max_length=128)
    ntfy_priority: str = Field(default='3', max_length=128)
    ntfy_token: str = Field(default='', sa_column=Column(SQLText))
    ntfy_username: str = Field(default='', max_length=128)
    ntfy_password: str = Field(default='', sa_column=Column(SQLText))
    ntfy_actions: str = Field(default='', sa_column=Column(SQLText))

    # WxPusher
    wxpusher_app_token: str = Field(default='', sa_column=Column(SQLText))
    wxpusher_topic_ids: str = Field(default='', sa_column=Column(SQLText))
    wxpusher_uids: str = Field(default='', sa_column=Column(SQLText))


class NotificationConfig(NotificationConfigBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True, sa_column_kwargs={"autoincrement": True})


class NotificationConfigCreate(NotificationConfigBase):
    pass


class NotificationConfigUpdate(SQLModel):
    id: int
    # 所有字段都是可选的更新字段
    browser_token: Optional[uuid.UUID] = None
    hitokoto: Optional[bool] = None
    bark_push: Optional[str] = None
    bark_archive: Optional[str] = None
    bark_group: Optional[str] = None
    bark_sound: Optional[str] = None
    bark_icon: Optional[str] = None
    bark_level: Optional[str] = None
    bark_url: Optional[str] = None
    console: Optional[bool] = None
    dd_bot_secret: Optional[str] = None
    dd_bot_token: Optional[str] = None
    fskey: Optional[str] = None
    gobot_url: Optional[str] = None
    gobot_qq: Optional[str] = None
    gobot_token: Optional[str] = None
    gotify_url: Optional[str] = None
    gotify_token: Optional[str] = None
    gotify_priority: Optional[int] = None
    igot_push_key: Optional[str] = None
    push_key: Optional[str] = None
    deer_key: Optional[str] = None
    deer_url: Optional[str] = None
    chat_url: Optional[str] = None
    chat_token: Optional[str] = None
    push_plus_token: Optional[str] = None
    push_plus_user: Optional[str] = None
    push_plus_template: Optional[str] = None
    push_plus_channel: Optional[str] = None
    push_plus_webhook: Optional[str] = None
    push_plus_callbackurl: Optional[str] = None
    push_plus_to: Optional[str] = None
    we_plus_bot_token: Optional[str] = None
    we_plus_bot_receiver: Optional[str] = None
    we_plus_bot_version: Optional[str] = None
    qmsg_key: Optional[str] = None
    qmsg_type: Optional[str] = None
    qywx_origin: Optional[str] = None
    qywx_am: Optional[str] = None
    qywx_key: Optional[str] = None
    tg_bot_token: Optional[str] = None
    tg_user_id: Optional[str] = None
    tg_api_host: Optional[str] = None
    tg_proxy_auth: Optional[str] = None
    tg_proxy_host: Optional[str] = None
    tg_proxy_port: Optional[str] = None
    aibotk_key: Optional[str] = None
    aibotk_type: Optional[str] = None
    aibotk_name: Optional[str] = None
    smtp_server: Optional[str] = None
    smtp_ssl: Optional[str] = None
    smtp_email: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_name: Optional[str] = None
    pushme_key: Optional[str] = None
    pushme_url: Optional[str] = None
    chronocat_qq: Optional[str] = None
    chronocat_token: Optional[str] = None
    chronocat_url: Optional[str] = None
    webhook_url: Optional[str] = None
    webhook_body: Optional[str] = None
    webhook_headers: Optional[str] = None
    webhook_method: Optional[str] = None
    webhook_content_type: Optional[str] = None
    ntfy_url: Optional[str] = None
    ntfy_topic: Optional[str] = None
    ntfy_priority: Optional[str] = None
    ntfy_token: Optional[str] = None
    ntfy_username: Optional[str] = None
    ntfy_password: Optional[str] = None
    ntfy_actions: Optional[str] = None
    wxpusher_app_token: Optional[str] = None
    wxpusher_topic_ids: Optional[str] = None
    wxpusher_uids: Optional[str] = None


class NotificationConfigRead(NotificationConfigBase):
    pass