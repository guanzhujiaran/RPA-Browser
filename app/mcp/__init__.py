"""RPA MCP（用户能力全量暴露）。

MCP 端点由 ``app/mcp/mount.py`` 挂载进 FastAPI（``fastapi-mcp`` 按 OpenAPI 自动生成工具），
暴露范围由 ``app/mcp/exposure.py`` 决定（用户路由全集，排除 SSE / WebRTC / 管理端），
鉴权在**网关**完成（``bili_jwt`` Cookie + 登出黑名单 → 注入 ``x-bili-*``），
RPA 侧只透传这些内网可信头；调试能力本体在 ``app/services/execution/debug_service.py``。
离线自检：``python -m app.mcp --selftest``；设计见 ``docs/mcp动作调试计划书.md``。
"""

from app.mcp.config import mcp_settings

__all__ = ["mcp_settings"]
