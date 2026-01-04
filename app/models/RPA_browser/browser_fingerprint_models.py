import sys
from enum import Enum
from typing import Annotated
from sqlmodel import Field, SQLModel, Column, JSON
from app.config import settings
from browserforge.fingerprints import (
    Fingerprint,
    ScreenFingerprint,
    NavigatorFingerprint,
    VideoCard,
)
from dacite import from_dict
from playwright.async_api import ViewportSize
from pydantic import model_validator
from pydantic import computed_field


# Compatibility for StrEnum in Python < 3.11
if sys.version_info >= (3, 11):
    from enum import StrEnum
else:

    class StrEnum(str, Enum):
        """String enum for Python < 3.11"""

        pass


from snowflake import SnowflakeGenerator

# 初始化雪花ID生成器，配置为生成更小的ID
# epoch 使用 2025-01-01 的时间戳，使生成的 ID 更小
snowflake_generator = SnowflakeGenerator(
    instance=settings.snowflake_id, epoch=1735689600000, seq=2
)

Int32 = Annotated[int, Field(ge=-2147483648, le=2147483647)]


class PlatformEnum(StrEnum):
    """操作系统平台枚举"""

    windows = "windows"
    linux = "linux"
    macos = "macos"


class BrowserEnum(StrEnum):
    """浏览器类型枚举"""

    chrome = "chrome"
    Edge = "Edge"
    Opera = "Opera"
    Vivaldi = "Vivaldi"


class BaseFingerprintBrowserInitParams(SQLModel):
    """
    浏览器指纹基础参数模型
    存放可反射的浏览器的启动args
    """

    fingerprint: Int32 = Field(None, unique=True, alias="--fingerprint")
    fingerprint_platform: PlatformEnum | None = Field(
        PlatformEnum.windows,
        alias="--fingerprint-platform",
    )
    fingerprint_platform_version: str | None = Field(
        None,
        alias="--fingerprint-platform-version",
        description="Uses default version if not specified",
    )
    fingerprint_browser: BrowserEnum | None = Field(
        None,
        alias="--fingerprint-browser",
        description="Chrome, Edge, Opera, Vivaldi (default is Chrome)",
    )
    fingerprint_brand_version: str | None = Field(
        None,
        alias="--fingerprint-brand-version",
        description="Uses default version if not specified",
    )
    fingerprint_hardware_concurrency: int | None = Field(
        None, alias="--fingerprint-hardware-concurrency"
    )
    fingerprint_gpu_vendor: str | None = Field(
        None,
        alias="--fingerprint-gpu-vendor",
        description="Vendor string (e.g., Intel Inc., NVIDIA Corporation). If not specified, uses fingerprint seed",
    )
    fingerprint_gpu_renderer: str | None = Field(
        None,
        alias="--fingerprint-gpu-renderer",
        description="Renderer string (e.g., Intel Iris OpenGL Engine, NVIDIA GeForce GTX 1060). If not specified, uses fingerprint seed",
    )
    lang: str | None = Field(
        None, alias="--lang", description="Language code (e.g., en-US)"
    )
    accept_lang: str | None = Field(
        None, alias="--accept-lang", description="Language code (e.g., en-US)"
    )
    timezone: str | None = Field(
        "Asia/Shanghai",
        alias="--timezone",
        description="Timezone (e.g., America/New_York)",
    )
    proxy_server: str | None = Field(
        None,
        alias="--proxy-server",
        description="http, socks proxy (password authentication not supported)",
    )
    patchright_screen_width: int = Field(default=1920)
    patchright_screen_height: int = Field(default=1080)
    patchright_viewport_width: int = Field(default=1920)
    patchright_viewport_height: int = Field(default=1080)
    patchright_fingerprint_dict: dict | None = Field(
        default=None, sa_column=Column(JSON)
    )
    patchright_browser_ua: str | None = Field(None, nullable=True)

    model_config = {"populate_by_name": True}

    @property
    def browserforge_fingerprint_object(self) -> Fingerprint | None:
        """生成BrowserForge指纹对象"""
        if not self.patchright_fingerprint_dict:
            return None
        screen_fingerprint = from_dict(
            ScreenFingerprint, self.patchright_fingerprint_dict.get("screen")
        )
        navigator_fingerprint = NavigatorFingerprint(
            **self.patchright_fingerprint_dict.get("navigator")
        )
        navigator_fingerprint.userAgent = (
            self.patchright_browser_ua or navigator_fingerprint.userAgent
        )
        video_card = from_dict(
            VideoCard, self.patchright_fingerprint_dict.get("videoCard")
        )
        return Fingerprint(
            screen=screen_fingerprint,
            navigator=navigator_fingerprint,
            headers=self.patchright_fingerprint_dict.get("headers"),
            videoCodecs=self.patchright_fingerprint_dict.get("videoCodecs"),
            audioCodecs=self.patchright_fingerprint_dict.get("audioCodecs"),
            pluginsData=self.patchright_fingerprint_dict.get("pluginsData"),
            battery=self.patchright_fingerprint_dict.get("battery"),
            videoCard=video_card,
            multimediaDevices=self.patchright_fingerprint_dict.get("multimediaDevices"),
            fonts=self.patchright_fingerprint_dict.get("fonts"),
            mockWebRTC=self.patchright_fingerprint_dict.get("mockWebRTC"),
            slim=self.patchright_fingerprint_dict.get("slim"),
        )

    @computed_field
    @property
    def viewport(self) -> ViewportSize:
        """浏览器视口大小"""
        return {
            "width": self.patchright_viewport_width,
            "height": self.patchright_viewport_height,
        }

    @computed_field
    @property
    def screen(self) -> ViewportSize:
        """屏幕大小"""
        return {
            "width": self.patchright_screen_width,
            "height": self.patchright_screen_height,
        }

    def fp_2_args_list(self) -> list[str]:
        """将指纹参数转换为浏览器启动参数列表"""
        if not sys.platform.startswith(
            "linux"
        ):  # WebGL 元数据：修改 GPU 供应商和显卡型号（暂时只支持 Linux）。 https://github.com/adryfish/fingerprint-chromium/blob/main/README-ZH.md
            self.fingerprint_gpu_vendor = None
            self.fingerprint_gpu_renderer = None
        # 将指纹参数转换为浏览器启动参数，但过滤掉可能导致问题的参数
        filtered_params = {
            k: v
            for k, v in self.model_dump(exclude_none=True, by_alias=True).items()
            if v and not k.startswith("patchright")
        }
        return [f"--{k}={v}".replace("_", "-") for k, v in filtered_params.items()]

    @model_validator(mode="after")
    def check_browser_and_brand_version_consistency(self):
        """验证浏览器类型和品牌版本的一致性"""
        browser = self.fingerprint_browser
        brand_version = self.fingerprint_brand_version

        if (browser is None) != (brand_version is None):
            raise ValueError(
                "fingerprint_browser and fingerprint_brand_version must be both set or both unset."
            )
        return self

    @model_validator(mode="after")
    def check_browser_vendor_and_renderer_consistency(self):
        """验证GPU供应商和渲染器的一致性"""
        gpu_vendor = self.fingerprint_gpu_vendor
        gpu_renderer = self.fingerprint_gpu_renderer
        if (gpu_vendor is None) != (gpu_renderer is None):
            raise ValueError(
                "fingerprint_gpu_vendor and fingerprint_gpu_renderer must be both set or both unset."
            )
        return self
