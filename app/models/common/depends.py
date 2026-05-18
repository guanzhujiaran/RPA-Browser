"""
Depends 模块 - 依赖注入相关模型

用于安全校验和参数验证的模型定义。
"""

from sqlmodel import SQLModel
from pydantic import field_validator
from app.models.core.browser.fingerprint import BaseBrowserId, BaseUserMid


class AuthInfo(SQLModel):
    """认证信息"""
    mid: int
    level: int


class VerifyBrowserDependsReq(BaseBrowserId):
    """验证浏览器所有权的请求模型"""
    ...


class BrowserReqInfo(BaseUserMid,BaseBrowserId):
    """浏览器请求信息模型"""
    ...

class BrowserReqAuthInfo(BaseBrowserId):
    auth_info: AuthInfo


class VerifyPluginDependsReq(VerifyBrowserDependsReq):
    """验证插件所有权的请求模型"""

    plugin_id: int | str

    @field_validator("plugin_id", mode="before")
    @classmethod
    def validate_plugin_id(cls, v):
        """将字符串类型的plugin_id转换为整数"""
        if isinstance(v, str):
            return int(v)
        return v


class BrowserPluginReqInfo(BrowserReqInfo):
    """浏览器插件请求信息模型"""

    plugin_id: int
