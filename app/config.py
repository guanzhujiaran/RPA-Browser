import os
from loguru import logger
from pydantic_settings import BaseSettings, SettingsConfigDict
from app.models.consts.enums import ConfigRunningModeEnum

current_dir = os.path.dirname(__file__)


class Settings(BaseSettings):
    mysql_browser_info_url: str
    RUNNING_MODE: ConfigRunningModeEnum
    controller_base_path: str | None = "/api"
    chromium_executable_dir: str | None = os.path.join(current_dir, "chrome")
    jwt_algorithm: str = "HS256"  # JWT算法
    jwt_expire_minutes: int = 7 * 24 * 60  # JWT过期时间（分钟），默认30分钟
    proxy_server_url: str = "http://127.0.0.1:10809"  # 可以访问外网的代理地址
    github_proxy_url: str = "https://gh-proxy.com/"

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
    sys_pushme_token: str = ""
    sys_pushplus_token: str = ""
    GEMINI_API_KEY: str = "NotNecessary"
    default_proxy_server: str = (
        ""  # 只要ip加端口就行,别加协议,httpx的all会自动处理,类似127.0.0.1:3128
    )
    snowflake_id: int = 1

    # 浏览器会话默认配置
    browser_session_auto_cleanup: bool = True  # 是否启用自动清理
    browser_session_max_idle_time: int = 1800  # 最大闲置时间（秒）
    browser_session_max_no_heartbeat_time: int = 300  # 最大无心跳时间（秒）
    browser_session_cleanup_interval: int = 300  # 清理检查间隔（秒）
    browser_session_expiration_time: int | None = None  # 会话过期时间（秒），None表示不过期
    
    # 浏览器页面数量限制配置
    browser_max_pages_per_context: int = 10  # 每个浏览器上下文的最大页面数
    
    # WebRTC 视频流配置
    browser_webrtc_idle_timeout: int = 300  # WebRTC 流最大闲置时间（秒），默认5分钟



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
