from pydantic import computed_field, field_validator
from sqlmodel import SQLModel

from app.models.base.base_sqlmodel import BasePaginationReq
from app.models.RPA_browser.browser_fingerprint_models import Int32
from app.models.RPA_browser.browser_database_models import UserBrowserInfoWithoutPlugin


# ========================
# 浏览器指纹相关请求参数
# ========================


class BrowserFingerprintCreateParams(SQLModel):
    """创建浏览器指纹参数"""

    fingerprint_int: Int32 | None = None
    is_desktop: bool = True


class BrowserFingerprintUpdateParams(SQLModel):
    """更新浏览器指纹参数"""

    id: int | str  # 允许int或str类型传入
    fingerprint_int: Int32 | None = None
    fingerprint_platform: str | None = None
    fingerprint_platform_version: str | None = None
    fingerprint_browser: str | None = None
    fingerprint_brand_version: str | None = None
    fingerprint_hardware_concurrency: int | None = None
    fingerprint_gpu_vendor: str | None = None
    fingerprint_gpu_renderer: str | None = None
    lang: str | None = None
    accept_lang: str | None = None
    timezone: str | None = None
    proxy_server: str | None = None

    @field_validator("id", mode="before")
    @classmethod
    def validate_id(cls, v):
        """将字符串类型的id转换为整数"""
        if isinstance(v, str):
            return int(v)
        return v


class BrowserFingerprintUpsertParams(SQLModel):
    """创建或更新浏览器指纹参数 (upsert)"""

    id: int | str | None = None  # 允许int或str类型传入，None表示创建
    fingerprint_int: Int32 | None = None
    fingerprint_platform: str | None = None
    fingerprint_platform_version: str | None = None
    fingerprint_browser: str | None = None
    fingerprint_brand_version: str | None = None
    fingerprint_hardware_concurrency: int | None = None
    fingerprint_gpu_vendor: str | None = None
    fingerprint_gpu_renderer: str | None = None
    lang: str | None = None
    accept_lang: str | None = None
    timezone: str | None = None
    proxy_server: str | None = None

    @field_validator("id", mode="before")
    @classmethod
    def validate_id(cls, v):
        """将字符串类型的id转换为整数"""
        if v is not None and isinstance(v, str):
            return int(v)
        return v


class BrowserFingerprintQueryParams(SQLModel):
    """查询浏览器指纹参数"""

    id: int | str  # 允许int或str类型传入

    @field_validator("id", mode="before")
    @classmethod
    def validate_id(cls, v):
        """将字符串类型的id转换为整数"""
        if isinstance(v, str):
            return int(v)
        return v


class BrowserFingerprintDeleteParams(SQLModel):
    """删除浏览器指纹参数"""

    id: int | str  # 允许int或str类型传入

    @field_validator("id", mode="before")
    @classmethod
    def validate_id(cls, v):
        """将字符串类型的id转换为整数"""
        if isinstance(v, str):
            return int(v)
        return v


class BrowserFingerprintListParams(BasePaginationReq):
    """分页查询浏览器指纹参数"""

    pass


# ========================
# 浏览器指纹相关响应
# ========================


class BrowserFingerprintCreateResp(SQLModel):
    """创建浏览器指纹响应"""

    mid: int
    id: int

    @computed_field
    @property
    def mid_str(self) -> str:
        return str(self.mid)

    @computed_field
    @property
    def id_str(self) -> str:
        return str(self.id)


class BrowserFingerprintUpdateResp(SQLModel):
    """更新浏览器指纹响应"""

    mid: int
    id: int
    is_success: bool = True

    @computed_field
    @property
    def mid_str(self) -> str:
        return str(self.mid)

    @computed_field
    @property
    def id_str(self) -> str:
        return str(self.id)


class BrowserFingerprintDeleteResp(SQLModel):
    """删除浏览器指纹响应"""

    mid: int
    id: int
    is_success: bool = True

    @computed_field
    @property
    def mid_str(self) -> str:
        return str(self.mid)

    @computed_field
    @property
    def id_str(self) -> str:
        return str(self.id)


class BrowserFingerprintQueryResp(UserBrowserInfoWithoutPlugin):
    """查询浏览器指纹响应"""

    pass


# ========================
# 浏览器操作相关请求参数
# ========================


class BrowserOperationOpenUrlParams(SQLModel):
    """打开浏览器URL参数"""

    browser_id: int | str  # 允许int或str类型传入
    url: str
    headless: bool = True

    @field_validator("browser_id", mode="before")
    @classmethod
    def validate_browser_id(cls, v):
        """将字符串类型的browser_id转换为整数"""
        if isinstance(v, str):
            return int(v)
        return v


class BrowserOperationScreenshotParams(SQLModel):
    """浏览器截图参数"""

    browser_id: int | str  # 允许int或str类型传入
    full_page: bool = True
    headless: bool = True
    image_type: str | None = "png"

    @field_validator("browser_id", mode="before")
    @classmethod
    def validate_browser_id(cls, v):
        """将字符串类型的browser_id转换为整数"""
        if isinstance(v, str):
            return int(v)
        return v


class BrowserOperationReleaseParams(SQLModel):
    """释放浏览器参数"""

    browser_id: int | str  # 允许int或str类型传入

    @field_validator("browser_id", mode="before")
    @classmethod
    def validate_browser_id(cls, v):
        """将字符串类型的browser_id转换为整数"""
        if isinstance(v, str):
            return int(v)
        return v


# ========================
# 浏览器操作相关响应
# ========================


class BrowserOperationOpenUrlResp(SQLModel):
    """打开浏览器URL响应"""

    title: str | None = None
    current_url: str


class BrowserOperationScreenshotResp(SQLModel):
    """浏览器截图响应"""

    image_base64: str


class BrowserOperationReleaseResp(SQLModel):
    """释放浏览器响应"""

    mid: int
    browser_id: int
    is_success: bool = True

    @computed_field
    @property
    def mid_str(self) -> str:
        return str(self.mid)

    @computed_field
    @property
    def browser_id_str(self) -> str:
        return str(self.browser_id)


# ========================
# 向后兼容的别名（保持旧名称可用）
# ========================


UserBrowserInfoCreateParams = BrowserFingerprintCreateParams
UserBrowserInfoUpsertParams = BrowserFingerprintUpsertParams
UserBrowserInfoCreateResp = BrowserFingerprintCreateResp
UserBrowserInfoCountParams = SQLModel
UserBrowserInfoListParams = BrowserFingerprintListParams
UserBrowserInfoReadParams = BrowserFingerprintQueryParams
UserBrowserInfoReadResp = BrowserFingerprintQueryResp
UserBrowserInfoUpdateParams = BrowserFingerprintUpdateParams
UserBrowserInfoUpdateResp = BrowserFingerprintUpdateResp
UserBrowserInfoDeleteParams = BrowserFingerprintDeleteParams
UserBrowserInfoDeleteResp = BrowserFingerprintDeleteResp
BrowserOpenUrlParams = BrowserOperationOpenUrlParams
BrowserOpenUrlResp = BrowserOperationOpenUrlResp
BrowserScreenshotParams = BrowserOperationScreenshotParams
BrowserScreenshotResp = BrowserOperationScreenshotResp
BrowserReleaseParams = BrowserOperationReleaseParams
BrowserReleaseResp = BrowserOperationReleaseResp
