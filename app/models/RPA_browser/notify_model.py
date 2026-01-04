# from typing import Optional  # Python 3.10+ 使用 | None 语法
from sqlmodel import Field, SQLModel, Column
from sqlalchemy import Text as SQLText, BIGINT

from app.models.base.base_sqlmodel import BaseSQLModel


class NotificationConfigBase(BaseSQLModel):
    # 关联的mid
    mid: str = Field(index=True, sa_type=BIGINT)

    # 关联的browser_id（可选，为空表示全局配置）
    browser_id: int | None = Field(default=None, index=True, sa_type=BIGINT)

    # 唯一约束：mid + browser_id（browser_id为None时表示全局配置）
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

    # 在SQLModel中，unique约束需要通过sa_column_kwargs设置
    # 但由于我们需要复合唯一约束(mid, browser_id)，这里通过索引来模拟
    # 实际的唯一性约束会在数据库层面处理


class NotificationConfigCreate(BaseSQLModel):
    """通知配置创建模型，用于API请求（不包含mid，mid从header获取）"""
    # 关联的browser_id（可选，为空表示全局配置）
    browser_id: str | None = None

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


class NotificationConfigUpdate(SQLModel):
    id: int | None = None
    # 所有字段都是可选的更新字段，不包含mid
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


# 响应模型
class NotificationConfigUpsertResp(SQLModel):
    """统一的通知配置操作响应模型（创建/更新）"""
    mid: str
    is_success: bool = True
    message: str = "通知配置操作成功"


class NotificationConfigDeleteResp(SQLModel):
    """通知配置删除响应模型"""
    mid: str
    is_success: bool = True


class NotificationConfigEffectiveResp(BaseSQLModel):
    """
    有效通知配置响应模型
    包含了优先级逻辑：如果存在浏览器特定配置，返回浏览器配置；否则返回全局配置
    """
    # 关联的browser_id（可选，为空表示全局配置）
    browser_id: int | None = None

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
    
    # 实际使用的配置的browser_id，None表示使用全局配置
    effective_browser_id: int | None = None
    
    # 配置来源：'browser' 或 'global'
    config_source: str


class NotifyConfigReadRequest(SQLModel):
    """读取通知配置的请求模型"""
    browser_id: str | None = Field(None, description="浏览器实例ID，如果提供则查询特定实例的配置")


class BrowserEffectiveNotifyRequest(SQLModel):
    """获取特定浏览器有效通知配置的请求模型"""
    browser_id: str = Field(..., description="浏览器实例ID")


class TestNotificationRequest(SQLModel):
    """测试推送通知的请求模型"""
    title: str = Field(default="测试通知", description="通知标题")
    content: str = Field(default="这是一条测试通知消息", description="通知内容")
    browser_id: str | None = Field(None, description="浏览器实例ID，如果提供则测试特定实例的配置，否则测试全局配置")


class TestNotificationResponse(SQLModel):
    """测试推送通知的响应模型"""
    success: bool = Field(..., description="测试是否成功")
    message: str = Field(..., description="测试结果消息")
    config_found: bool = Field(..., description="是否找到通知配置")
    browser_id: str | None = Field(None, description="使用的浏览器实例ID")
    config_source: str | None = Field(None, description="配置来源：'browser' 或 'global'")
    sent_channels: list[str] = Field(default=[], description="成功发送的推送渠道列表")
