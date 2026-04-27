"""
Core 模块 - 通知数据库模型

定义通知相关的数据库表模型。
"""

from sqlmodel import Field, Column
from sqlalchemy import Text as SQLText, BIGINT

from app.models.base.base_sqlmodel import BaseSQLModel


class NotificationConfigBase(BaseSQLModel):
    """通知配置基础模型"""
    # 关联的mid
    mid: str = Field(index=True, sa_type=BIGINT)

    # 关联的browser_id（可选，为空表示全局配置）
    browser_id: int | None = Field(default=None, index=True, sa_type=BIGINT)

    # 一言（随机句子）
    hitokoto: bool = True

    # Bark推送配置
    bark_push: str = Field(default="", sa_column=Column(SQLText))
    bark_archive: str = Field(default="", max_length=128)
    bark_group: str = Field(default="", max_length=128)
    bark_sound: str = Field(default="", max_length=128)
    bark_icon: str = Field(default="", sa_column=Column(SQLText))
    bark_level: str = Field(default="", max_length=128)
    bark_url: str = Field(default="", sa_column=Column(SQLText))

    # 钉钉机器人
    dd_bot_secret: str = Field(default="", sa_column=Column(SQLText))
    dd_bot_token: str = Field(default="", sa_column=Column(SQLText))

    # 飞书机器人
    fskey: str = Field(default="", sa_column=Column(SQLText))

    # go-cqhttp
    gobot_url: str = Field(default="", sa_column=Column(SQLText))
    gobot_qq: str = Field(default="", max_length=128)
    gobot_token: str = Field(default="", sa_column=Column(SQLText))

    # Gotify
    gotify_url: str = Field(default="", sa_column=Column(SQLText))
    gotify_token: str = Field(default="", sa_column=Column(SQLText))
    gotify_priority: int = 0

    # iGot
    igot_push_key: str = Field(default="", sa_column=Column(SQLText))

    # Server酱
    push_key: str = Field(default="", sa_column=Column(SQLText))

    # PushDeer
    deer_key: str = Field(default="", sa_column=Column(SQLText))
    deer_url: str = Field(default="", sa_column=Column(SQLText))

    # Synology Chat
    chat_url: str = Field(default="", sa_column=Column(SQLText))
    chat_token: str = Field(default="", sa_column=Column(SQLText))

    # PushPlus
    push_plus_token: str = Field(default="", sa_column=Column(SQLText))
    push_plus_user: str = Field(default="", max_length=128)
    push_plus_template: str = Field(default="html", max_length=128)
    push_plus_channel: str = Field(default="wechat", max_length=128)
    push_plus_webhook: str = Field(default="", sa_column=Column(SQLText))
    push_plus_callbackurl: str = Field(default="", sa_column=Column(SQLText))
    push_plus_to: str = Field(default="", sa_column=Column(SQLText))

    # 微加机器人
    we_plus_bot_token: str = Field(default="", sa_column=Column(SQLText))
    we_plus_bot_receiver: str = Field(default="", sa_column=Column(SQLText))
    we_plus_bot_version: str = Field(default="pro", max_length=128)

    # Qmsg酱
    qmsg_key: str = Field(default="", sa_column=Column(SQLText))
    qmsg_type: str = Field(default="", max_length=128)

    # 企业微信
    qywx_origin: str = Field(default="", sa_column=Column(SQLText))
    qywx_am: str = Field(default="", sa_column=Column(SQLText))
    qywx_key: str = Field(default="", sa_column=Column(SQLText))

    # Telegram
    tg_bot_token: str = Field(default="", sa_column=Column(SQLText))
    tg_user_id: str = Field(default="", max_length=128)
    tg_api_host: str = Field(default="", sa_column=Column(SQLText))
    tg_proxy_auth: str = Field(default="", sa_column=Column(SQLText))
    tg_proxy_host: str = Field(default="", sa_column=Column(SQLText))
    tg_proxy_port: str = Field(default="", max_length=128)

    # 智能微秘书
    aibotk_key: str = Field(default="", sa_column=Column(SQLText))
    aibotk_type: str = Field(default="", max_length=128)
    aibotk_name: str = Field(default="", max_length=128)

    # SMTP邮件
    smtp_server: str = Field(default="", max_length=128)
    smtp_ssl: str = Field(default="false", max_length=128)
    smtp_email: str = Field(default="", max_length=256)
    smtp_password: str = Field(default="", sa_column=Column(SQLText))
    smtp_name: str = Field(default="", max_length=128)

    # PushMe
    pushme_key: str = Field(default="", sa_column=Column(SQLText))
    pushme_url: str = Field(default="", sa_column=Column(SQLText))

    # Chronocat
    chronocat_qq: str = Field(default="", max_length=128)
    chronocat_token: str = Field(default="", sa_column=Column(SQLText))
    chronocat_url: str = Field(default="", sa_column=Column(SQLText))

    # 自定义Webhook
    webhook_url: str = Field(default="", sa_column=Column(SQLText))
    webhook_body: str = Field(default="", sa_column=Column(SQLText))
    webhook_headers: str = Field(default="", sa_column=Column(SQLText))
    webhook_method: str = Field(default="", max_length=128)
    webhook_content_type: str = Field(default="", max_length=128)

    # Ntfy
    ntfy_url: str = Field(default="", sa_column=Column(SQLText))
    ntfy_topic: str = Field(default="", max_length=128)
    ntfy_priority: str = Field(default="3", max_length=128)
    ntfy_token: str = Field(default="", sa_column=Column(SQLText))
    ntfy_username: str = Field(default="", max_length=128)
    ntfy_password: str = Field(default="", sa_column=Column(SQLText))
    ntfy_actions: str = Field(default="", sa_column=Column(SQLText))

    # WxPusher
    wxpusher_app_token: str = Field(default="", sa_column=Column(SQLText))
    wxpusher_topic_ids: str = Field(default="", sa_column=Column(SQLText))
    wxpusher_uids: str = Field(default="", sa_column=Column(SQLText))
    
    @property
    def browser_id_str(self) -> str | None:
        """浏览器ID字符串形式，用于前端交互"""
        return str(self.browser_id) if self.browser_id is not None else None


class NotificationConfig(NotificationConfigBase, table=True):
    __table_args__ = {
        "sqlite_autoincrement": True,
        "extend_existing": True,
    }
    id: int | None = Field(
        default=None, primary_key=True, sa_column_kwargs={"autoincrement": True}
    )


__all__ = [
    "NotificationConfigBase",
    "NotificationConfig",
]
