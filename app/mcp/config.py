"""MCP 服务配置（``RPA_MCP_*``，全部可选）。

MCP 端点内嵌在 RPA 服务里，因此挂载路径直接复用 ``settings.controller_base_path``，
与前端接口同前缀（默认 ``/api/v1/rpa/mcp``）。

鉴权口径：MCP 客户端走**网关**（``http(s)://<gateway>/api/v1/rpa/mcp``），
网关用 ``bili_jwt`` Cookie 校验（含登出黑名单）后注入 ``x-bili-*`` 头；
RPA 侧只做内网透传，**不解析 Cookie，也不接受配置写死 mid**。
"""

from __future__ import annotations

import os

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.config import PROJECT_ROOT, settings as rpa_settings
from app.models.consts.enums import ConfigRunningModeEnum

#: 网关注入、需要透传给 MCP 内部 ASGI 调用的可信身份头
AUTH_HEADER_NAMES: tuple[str, ...] = (
    "x-bili-mid",
    "x-bili-level",
    "x-bili-role",
    "x-bili-user-name",
)


class MCPSettings(BaseSettings):
    """MCP 挂载、鉴权与调试行为参数。"""

    enabled: bool | None = Field(
        default=None, description="是否挂载 MCP 端点；不设置时 dev 开启、prod 关闭"
    )
    mount_suffix: str = Field(default="/mcp", description="挂载路径后缀")
    poll_interval: float = Field(default=1.5, description="等待工作流运行的轮询间隔（秒）")
    max_wait_seconds: int = Field(default=300, description="等待运行的默认超时（秒）")
    payload_max_chars: int = Field(
        default=1200, description="日志中 params/result 等大字段的截断长度"
    )
    exclude_operations: str = Field(
        default="",
        description=(
            "额外排除的 operationId（逗号分隔）。内置排除清单见 "
            "app/mcp/exposure.py 的 EXCLUDED_OPERATIONS"
        ),
    )

    model_config = SettingsConfigDict(
        env_prefix="RPA_MCP_",
        env_file=(
            os.path.join(PROJECT_ROOT, ".env.prod"),
            os.path.join(PROJECT_ROOT, ".env.dev"),
        ),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def enabled_effective(self) -> bool:
        """生效开关：显式配置优先，否则 dev 开启、prod 关闭。"""
        if self.enabled is not None:
            return self.enabled
        return rpa_settings.RUNNING_MODE == ConfigRunningModeEnum.DEV

    @property
    def mount_path(self) -> str:
        """完整挂载路径，如 ``/api/v1/rpa/mcp``。"""
        base = (rpa_settings.controller_base_path or "/api").rstrip("/")
        suffix = self.mount_suffix.strip() or "/mcp"
        if not suffix.startswith("/"):
            suffix = f"/{suffix}"
        return f"{base}{suffix.rstrip('/')}"

    @property
    def excluded_operation_ids(self) -> tuple[str, ...]:
        """``RPA_MCP_EXCLUDE_OPERATIONS`` 解析结果（逗号分隔，忽略空项）。"""
        return tuple(
            item.strip() for item in self.exclude_operations.split(",") if item.strip()
        )


mcp_settings = MCPSettings()
