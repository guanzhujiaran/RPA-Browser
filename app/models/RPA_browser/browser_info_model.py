import sys
import uuid
from enum import StrEnum
from typing import Annotated, List

from browserforge.fingerprints import Fingerprint, ScreenFingerprint, NavigatorFingerprint, VideoCard
from dacite import from_dict
from playwright.async_api import ViewportSize
from pydantic import model_validator
from sqlalchemy import BIGINT, JSON
from sqlmodel import Field, SQLModel, Enum, Column, Relationship
from app.models.base.base_sqlmodel import BaseSQLModel, BasePaginationReq
from snowflake import SnowflakeGenerator
from app.models.RPA_browser.plugin_model import (
    LogPluginModel,
    PageLimitPluginModel,
    RandomWaitPluginModel,
    RetryPluginModel
)

# 初始化雪花ID生成器
snowflake_generator = SnowflakeGenerator(1)  # 传入节点ID，可以根据需要修改

Int32 = Annotated[int, Field(ge=-2147483648, le=2147483647)]


class PlatformEnum(StrEnum):
    windows = 'windows'
    linux = 'linux'
    macos = 'macos'


class BrowserEnum(StrEnum):
    chrome = 'chrome'
    Edge = 'Edge'
    Opera = 'Opera'
    Vivaldi = 'Vivaldi'


class BaseFingerprintBrowserInitParams(BaseSQLModel):
    """
    存放可反射的浏览器的启动args
    """
    fingerprint: Int32 = Field(...,
                               unique=True,
                               alias='--fingerprint')
    fingerprint_platform: PlatformEnum | None = Field(
        PlatformEnum.windows,
        sa_column=Column(name='fingerprint_platform', type_=Enum(PlatformEnum)),
        alias='--fingerprint-platform'
    )
    fingerprint_platform_version: str | None = Field(
        None,
        alias='--fingerprint-platform-version',
        description='Uses default version if not specified'
    )
    fingerprint_browser: BrowserEnum | None = Field(
        None,
        sa_column=Column(name='fingerprint_browser', type_=Enum(BrowserEnum)),
        alias='--fingerprint-browser',
        description='Chrome, Edge, Opera, Vivaldi (default is Chrome)'
    )
    fingerprint_brand_version: str | None = Field(None, alias='--fingerprint-brand-version',
                                                  description='Uses default version if not specified')
    fingerprint_hardware_concurrency: int | None = Field(None, alias='--fingerprint-hardware-concurrency')
    fingerprint_gpu_vendor: str | None = Field(None, alias='--fingerprint-gpu-vendor',
                                               description='Vendor string (e.g., Intel Inc., NVIDIA Corporation). If not specified, uses fingerprint seed')
    fingerprint_gpu_renderer: str | None = Field(None, alias='--fingerprint-gpu-renderer',
                                                 description='Renderer string (e.g., Intel Iris OpenGL Engine, NVIDIA GeForce GTX 1060). If not specified, uses fingerprint seed')
    lang: str | None = Field(None, alias='--lang', description='Language code (e.g., en-US)')
    accept_lang: str | None = Field(None, alias='--accept-lang', description='Language code (e.g., en-US)')
    timezone: str | None = Field("Asia/Shanghai", alias='--timezone', description='Timezone (e.g., America/New_York)')
    proxy_server: str | None = Field(None, alias='--proxy-server',
                                     description='http, socks proxy (password authentication not supported)')
    patchright_screen_width: int = Field(default=1920, gt=1020)
    patchright_screen_height: int = Field(default=1080, gt=780)
    patchright_viewport_width: int = Field(default=1920, gt=1020)
    patchright_viewport_height: int = Field(default=1080, gt=780)
    patchright_proxy_server: str | None = Field(None)
    patchright_fingerprint_dict: dict | None = Field(None, sa_column=Column(JSON))

    @property
    def browserforge_fingerprint_object(self) -> Fingerprint | None:
        if not self.patchright_fingerprint_dict:
            return None
        screen_fingerprint = from_dict(ScreenFingerprint, self.patchright_fingerprint_dict.get('screen'))
        navigator_fingerprint = NavigatorFingerprint(**self.patchright_fingerprint_dict.get('navigator'))
        video_card = from_dict(VideoCard, self.patchright_fingerprint_dict.get('videoCard'))
        return Fingerprint(
            screen=screen_fingerprint,
            navigator=navigator_fingerprint,
            headers=self.patchright_fingerprint_dict.get('headers'),
            videoCodecs=self.patchright_fingerprint_dict.get('videoCodecs'),
            audioCodecs=self.patchright_fingerprint_dict.get('audioCodecs'),
            pluginsData=self.patchright_fingerprint_dict.get('pluginsData'),
            battery=self.patchright_fingerprint_dict.get('battery'),
            videoCard=video_card,
            multimediaDevices=self.patchright_fingerprint_dict.get('multimediaDevices'),
            fonts=self.patchright_fingerprint_dict.get('fonts'),
            mockWebRTC=self.patchright_fingerprint_dict.get('mockWebRTC'),
            slim=self.patchright_fingerprint_dict.get('slim')
        )

    @property
    def viewport(self) -> ViewportSize:
        return {
            "width": self.patchright_viewport_width,
            "height": self.patchright_viewport_height
        }

    @property
    def screen(self) -> ViewportSize:
        return {
            "width": self.patchright_screen_width,
            "height": self.patchright_screen_height
        }

    def fp_2_args_list(self) -> list[str]:
        if not sys.platform.startswith(
                'linux'):  # WebGL 元数据：修改 GPU 供应商和显卡型号（暂时只支持 Linux）。 https://github.com/adryfish/fingerprint-chromium/blob/main/README-ZH.md
            self.fingerprint_gpu_vendor = None
            self.fingerprint_gpu_renderer = None
        # 将指纹参数转换为浏览器启动参数，但过滤掉可能导致问题的参数
        filtered_params = {
            k: v for k, v in self.model_dump(exclude_none=True, by_alias=True).items()
            if v and not k.startswith('patchright')
        }
        return [f'--{k}={v}'.replace('_', '-') for k, v in filtered_params.items()]

    @model_validator(mode='after')
    def check_browser_and_brand_version_consistency(self):
        browser = self.fingerprint_browser
        brand_version = self.fingerprint_brand_version

        if (browser is None) != (brand_version is None):
            raise ValueError(
                "fingerprint_browser and fingerprint_brand_version must be both set or both unset."
            )
        return self

    @model_validator(mode='after')
    def check_browser_vendor_and_renderer_consistency(self):
        gpu_vendor = self.fingerprint_gpu_vendor
        gpu_renderer = self.fingerprint_gpu_renderer
        if (gpu_vendor is None) != (gpu_renderer is None):
            raise ValueError(
                "fingerprint_gpu_vendor and fingerprint_gpu_renderer must be both set or both unset."
            )
        return self


class UserBrowserInfoBase(BaseFingerprintBrowserInitParams):
    id: int = Field(sa_type=BIGINT, default_factory=lambda: next(snowflake_generator), primary_key=True)
    browser_token: uuid.UUID = Field(unique=False, index=True)  # 由外部系统传入，代表一个用户


class UserBrowserInfo(UserBrowserInfoBase, table=True):
    # 建立一对多关系，一个浏览器信息可以有多个插件
    # 修改关系定义以适配新的插件模型结构
    log_plugins: List["LogPluginModel"] = Relationship(
        back_populates="browser_info",
        sa_relationship_kwargs={"lazy": "selectin"}
    )
    page_limit_plugins: List["PageLimitPluginModel"] = Relationship(
        back_populates="browser_info",
        sa_relationship_kwargs={"lazy": "selectin"}
    )
    random_wait_plugins: List["RandomWaitPluginModel"] = Relationship(
        back_populates="browser_info",
        sa_relationship_kwargs={"lazy": "selectin"}
    )
    retry_plugins: List["RetryPluginModel"] = Relationship(
        back_populates="browser_info",
        sa_relationship_kwargs={"lazy": "selectin"}
    )


class UserBrowserInfoCreateParams(SQLModel):
    browser_token: uuid.UUID  # 必须传入，防止随意乱调用
    fingerprint_int: Int32 | None = None
    is_desktop: bool = True


class UserBrowserInfoCreateResp(UserBrowserInfoBase):
    ...


class UserBrowserInfoCountParams(SQLModel):
    browser_token: uuid.UUID


class UserBrowserInfoListParams(BasePaginationReq):
    browser_token: uuid.UUID


class UserBrowserInfoReadParams(SQLModel):
    browser_token: uuid.UUID
    id: int


class UserBrowserInfoReadResp(UserBrowserInfoBase):
    ...


class UserBrowserInfoUpdateParams(SQLModel):
    browser_token: uuid.UUID
    id: int
    fingerprint: Int32 | None = None
    fingerprint_platform: PlatformEnum | None = None
    fingerprint_platform_version: str | None = None
    fingerprint_browser: BrowserEnum | None = None
    fingerprint_brand_version: str | None = None
    fingerprint_hardware_concurrency: int | None = None
    fingerprint_gpu_vendor: str | None = None
    fingerprint_gpu_renderer: str | None = None
    lang: str | None = None
    accept_lang: str | None = None
    timezone: str | None = None
    proxy_server: str | None = None


class UserBrowserInfoUpdateResp(SQLModel):
    browser_token: uuid.UUID
    is_success: bool = True


class UserBrowserInfoDeleteParams(SQLModel):
    browser_token: uuid.UUID
    id: int


class UserBrowserInfoDeleteResp(SQLModel):
    browser_token: uuid.UUID
    is_success: bool = True


class BrowserOpenUrlParams(SQLModel):
    browser_token: uuid.UUID
    browser_id: int  # 添加browser_id参数
    url: str
    headless: bool = True


class BrowserOpenUrlResp(SQLModel):
    title: str | None = None
    current_url: str


class BrowserScreenshotParams(SQLModel):
    browser_token: uuid.UUID
    browser_id: str = None  # 添加browser_id参数
    full_page: bool = True
    headless: bool = True
    type: str | None = 'png'


class BrowserScreenshotResp(SQLModel):
    image_base64: str


class BrowserReleaseParams(SQLModel):
    browser_token: uuid.UUID
    browser_id: str = None  # 添加browser_id参数


class BrowserReleaseResp(SQLModel):
    browser_token: uuid.UUID
    browser_id: str = None  # 添加browser_id参数
    is_success: bool = True


class LiveCreateParams(SQLModel):
    browser_token: uuid.UUID
    browser_id: str | None = None  # 添加browser_id参数
    headless: bool = True


class LiveCreateResp(SQLModel):
    live_id: str
    live_url: str
