import os
from pydantic_settings import BaseSettings, SettingsConfigDict

current_dir = os.path.dirname(__file__)


class Settings(BaseSettings):
    mysql_browser_info_url: str
    controller_base_path: str | None = '/api'
    chromium_executable_dir: str | None = os.path.join(current_dir,
                                                        'chrome')
    jwt_secret_key: str = "your-secret-key-change-in-production"  # JWT密钥
    jwt_algorithm: str = "HS256"  # JWT算法
    jwt_expire_minutes: int = 7 * 24 * 60  # JWT过期时间（分钟），默认30分钟
    proxy_server_url: str = "http://127.0.0.1:10809"  # 可以访问外网的代理地址

    model_config = SettingsConfigDict(
        env_file=(os.path.join(current_dir, '../.env.prod'), os.path.join(current_dir, '../.env.dev')),
        case_sensitive=False,
        env_file_encoding='utf-8',
        extra='ignore'
    )

    # 底下是不那么重要的配置
    hitokoto_api_url: str = "https://v1.hitokoto.cn"
    sys_pushme_token: str = ""
    sys_pushplus_token: str = ""
    GEMINI_API_KEY: str = "1234"
    default_proxy_server:str = ""

settings = Settings()


class CONF:
    """
    配置类
    """

    class Path:
        """
        路径配置
        """
        logs = os.path.join(current_dir, './logs')


__all__ = [
    "settings",
    "CONF"
]
