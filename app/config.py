import os
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from app.models.consts.enums import ConfigRunningModeEnum

current_dir = os.path.dirname(__file__)


class PushChannelConfig(BaseModel):
    """全局推送渠道配置（pydantic 模型）。

    字段与 message-service 的 PushChannelConfig 保持一致，以便原样序列化后
    经 RabbitMQ 投递给 message-service 解析；未知字段一律忽略。
    """

    model_config = ConfigDict(extra="ignore")

    # 一言（随机句子）
    hitokoto: bool = True

    # Bark
    bark_push: str = ""
    bark_archive: str = ""
    bark_group: str = ""
    bark_sound: str = ""
    bark_icon: str = ""
    bark_level: str = ""
    bark_url: str = ""

    # 钉钉机器人
    dd_bot_secret: str = ""
    dd_bot_token: str = ""

    # 飞书机器人
    fskey: str = ""

    # go-cqhttp
    gobot_url: str = ""
    gobot_qq: str = ""
    gobot_token: str = ""

    # Gotify
    gotify_url: str = ""
    gotify_token: str = ""
    gotify_priority: int = 0

    # iGot
    igot_push_key: str = ""

    # Server 酱
    push_key: str = ""

    # PushDeer
    deer_key: str = ""
    deer_url: str = ""

    # Synology Chat
    chat_url: str = ""
    chat_token: str = ""

    # PushPlus
    push_plus_token: str = ""
    push_plus_url: str = ""
    push_plus_user: str = ""
    push_plus_template: str = "html"
    push_plus_channel: str = "wechat"
    push_plus_webhook: str = ""
    push_plus_callbackurl: str = ""
    push_plus_to: str = ""

    # 微加机器人
    we_plus_bot_token: str = ""
    we_plus_bot_receiver: str = ""
    we_plus_bot_version: str = "pro"

    # Qmsg 酱
    qmsg_key: str = ""
    qmsg_type: str = ""

    # 企业微信
    qywx_origin: str = ""
    qywx_am: str = ""
    qywx_key: str = ""

    # Telegram
    tg_bot_token: str = ""
    tg_user_id: str = ""
    tg_api_host: str = ""
    tg_proxy_auth: str = ""
    tg_proxy_host: str = ""
    tg_proxy_port: str = ""

    # 智能微秘书
    aibotk_key: str = ""
    aibotk_type: str = ""
    aibotk_name: str = ""

    # SMTP 邮件
    smtp_server: str = ""
    smtp_ssl: str = "false"
    smtp_email: str = ""
    smtp_password: str = ""
    smtp_name: str = ""

    # PushMe
    pushme_key: str = ""
    pushme_url: str = ""

    # Chronocat
    chronocat_qq: str = ""
    chronocat_token: str = ""
    chronocat_url: str = ""

    # 自定义 Webhook
    webhook_url: str = ""
    webhook_body: str = ""
    webhook_headers: str = ""
    webhook_method: str = ""
    webhook_content_type: str = ""

    # Ntfy
    ntfy_url: str = ""
    ntfy_topic: str = ""
    ntfy_priority: str = "3"
    ntfy_token: str = ""
    ntfy_username: str = ""
    ntfy_password: str = ""
    ntfy_actions: str = ""

    # WxPusher
    wxpusher_app_token: str = ""
    wxpusher_topic_ids: str = ""
    wxpusher_uids: str = ""


class Settings(BaseSettings):
    mysql_browser_info_url: str
    RUNNING_MODE: ConfigRunningModeEnum
    controller_base_path: str | None = "/api"
    chromium_executable_dir: str | None = os.path.join(current_dir, "chrome")
    jwt_algorithm: str = "HS256"  # JWT算法
    jwt_expire_minutes: int = 7 * 24 * 60  # JWT过期时间（分钟），默认30分钟
    proxy_server_url: str = "http://127.0.0.1:10809"  # 可以访问外网的代理地址
    github_proxy_url: str = "https://gh-proxy.com/"

    # RabbitMQ 连接地址，用于 HTTP 请求 Action 通过 RPC 调用 FastapiApp 内部业务方法
    # 后端定时执行工作流时通过 RabbitMQ RPC 调用系统接口，不经过网关、不依赖 JWT
    # heartbeat=180：与服务端保持一致，避免 handler 执行时间较长时 heartbeat 超时导致连接关闭
    rabbitmq_url: str = "amqp://guest:guest@rabbitmq:5672/?heartbeat=180"

    admin_base_path: str = "/admin_api"

    model_config = SettingsConfigDict(
        env_file=(
            os.path.join(current_dir, "../.env.prod"),
            os.path.join(current_dir, "../.env.dev"),
        ),
        case_sensitive=False,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 底下是不那么重要的配置
    hitokoto_api_url: str = "https://v1.hitokoto.cn"
    # 全局推送渠道配置（pydantic PushChannelConfig，与 message-service / fastapi 共用同一份）
    # 作为无 per-user 通知配置时的兜底；由 pydantic-settings 自动解析 JSON 环境变量，无需 Json() 包装
    message_config: PushChannelConfig = PushChannelConfig()
    # 本服务标识（写入推送告警标题，便于定位「哪台服务器的哪个服务」报错）
    SERVER_NAME: str = "rpa-browser"
    SERVER_ADDRESS: str = ""  # 缺省自动取本机 hostname
    GEMINI_API_KEY: str = "NotNecessary"
    default_proxy_server: str = (
        ""  # 只要ip加端口就行,别加协议,httpx的all会自动处理,类似127.0.0.1:3128
    )
    snowflake_id: int = 1

    # 浏览器会话默认配置
    browser_session_auto_cleanup: bool = True  # 是否启用自动清理
    browser_session_max_idle_time: int = 1800  # 最大闲置时间（秒）
    browser_session_cleanup_interval: int = 300  # 清理检查间隔（秒）
    browser_session_expiration_time: int | None = None  # 会话过期时间（秒），None表示不过期
    
    # 浏览器页面数量限制配置
    browser_max_pages_per_context: int = 10  # 每个浏览器上下文的最大页面数
    
    # 工作流控制流嵌套深度限制
    workflow_max_nesting_depth: int = 10  # 最大嵌套深度（Loop/IfElse）
    
    # WebRTC 视频流配置
    browser_webrtc_idle_timeout: int = 300  # WebRTC 流最大闲置时间（秒），默认5分钟
    
    # Alembic 数据库迁移配置
    alembic_auto_migrate: bool = True  # 是否在应用启动时自动执行数据库迁移
    alembic_upgrade_target: str = "heads"  # 迁移目标版本，默认为最新版本



settings = Settings()
logger.info(f"Settings loaded\n{settings}")


class CONF:
    """
    配置类
    """

    class Path:
        """
        路径配置
        """

        logs = os.path.join(current_dir, "./logs")


__all__ = ["settings", "CONF"]
