"""
依赖注入相关模型定义
用于安全校验和参数验证
"""

from sqlmodel import SQLModel
from pydantic import field_validator


class VerifyBrowserDependsReq(SQLModel):
    """验证浏览器所有权的请求模型"""

    browser_id: int | str

    @field_validator("browser_id", mode="before")
    @classmethod
    def validate_browser_id(cls, v):
        """将字符串类型的browser_id转换为整数"""
        if isinstance(v, str):
            return int(v)
        return v


class VerifyFingerprintDependsReq(SQLModel):
    """验证指纹所有权的请求模型"""

    browser_id: int | str

    @field_validator("browser_id", mode="before")
    @classmethod
    def validate_browser_id(cls, v):
        """将字符串类型的browser_id转换为整数"""
        if isinstance(v, str):
            return int(v)
        return v


class BrowserReqInfo(SQLModel):
    """浏览器请求信息模型"""

    mid: int
    browser_id: int


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
