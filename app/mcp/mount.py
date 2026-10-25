"""把用户可用的 RPA 接口以 MCP（streamable HTTP）挂载到 FastAPI。

- 工具集合 = 「用户可用的全部 RPA 路由」（由 ``app/mcp/exposure.py`` 提供轻量子应用，
  ``fastapi-mcp`` 按 OpenAPI 自动生成），不额外维护第二套 MCP 接口；
- SSE / WebRTC 这类长连接语义的接口按 operation_id 精确排除；
- **鉴权完全交给网关**：MCP 客户端配置的是网关地址
  （``http(s)://<gateway>/api/v1/rpa/mcp``），由网关的 ``bili_jwt`` Cookie 校验
  （含登出黑名单）后注入 ``x-bili-mid/level/role``；RPA 侧只把这些可信头
  透传给内部 ASGI 调用，不重复解析 Cookie、也不接受配置写死 mid；
- 默认 dev 开启、prod 关闭（``RPA_MCP_ENABLED`` 可强制）。

前置条件：RPA 端口只在内网可达（``x-bili-*`` 是内网可信头，绕过网关直连即可伪造）。
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi_mcp import FastApiMCP
from loguru import logger

from app.mcp.config import AUTH_HEADER_NAMES, mcp_settings
from app.mcp.exposure import (
    EXCLUDED_OPERATIONS,
    build_user_app,
    group_operations_by_resource,
    install_schema_compat,
)

#: 调试专用工具（``tag="MCP 调试"``）的期望集合，用于启动自检。
EXPECTED_TOOLS: tuple[str, ...] = (
    "rpa_list_actions",
    "rpa_get_action",
    "rpa_validate_steps",
    "rpa_execute_action",
    "rpa_save_action",
    "rpa_run_workflow",
    "rpa_get_run_logs",
    "rpa_get_action_logs",
)


def setup_mcp(app: FastAPI) -> FastApiMCP | None:
    """在 FastAPI 应用上挂载 MCP 端点；未启用时返回 None。"""
    if not mcp_settings.enabled_effective:
        logger.info("[MCP] 未启用（RPA_MCP_ENABLED=false 或 prod 模式）")
        return None

    mount_path = mcp_settings.mount_path

    install_schema_compat()

    excluded = (*EXCLUDED_OPERATIONS, *mcp_settings.excluded_operation_ids)
    # 主 app 的 schema 里存在自引用模型（WorkflowStepRequest.children），
    # fastapi-mcp 解析 $ref 会 RecursionError；这里用「用户路由子应用」作为
    # schema / 内部调用目标（兼容补丁见 app/mcp/exposure.py）。
    source_app = build_user_app()
    mcp = FastApiMCP(
        source_app,
        name="rpa",
        description=(
            "RPA 用户能力全集：自定义动作 / 工作流 / 浏览器会话与页面 / "
            "操作日志 / 动作调试（校验、试跑、落库回读）"
        ),
        describe_all_responses=False,
        exclude_operations=list(excluded),
        # 透传网关注入的可信身份头（内网口径；MCP 客户端本身不经过这层）
        headers=list(AUTH_HEADER_NAMES),
    )
    # MCP 端点挂在主应用上（对外唯一入口），工具内部调用走 source_app
    mcp.mount_http(app, mount_path=mount_path)

    tool_names = [tool.name for tool in mcp.tools]
    missing = [name for name in EXPECTED_TOOLS if name not in tool_names]
    leaked = [name for name in EXCLUDED_OPERATIONS if name in tool_names]
    admin_leaks = sorted(
        {
            str(detail.get("path", ""))
            for detail in mcp.operation_map.values()
            if "admin" in [seg for seg in str(detail.get("path", "")).split("/") if seg]
        }
    )
    groups = group_operations_by_resource(mcp.operation_map)
    logger.info(
        f"[MCP] 已挂载 {mount_path}，工具 {len(tool_names)} 个（排除 {len(excluded)} 个），"
        f"鉴权=网关注入 {list(AUTH_HEADER_NAMES)}，按资源分布: {groups}"
    )
    if missing:
        logger.warning(f"[MCP] 预期调试工具缺失（检查 operation_id）: {missing}")
    if leaked:
        logger.warning(f"[MCP] 应排除的接口出现在工具表里（检查 exclude_operations）: {leaked}")
    if admin_leaks:
        logger.error(f"[MCP] 管理员接口出现在工具表里（必须修复）: {admin_leaks}")
    return mcp


__all__ = ["EXPECTED_TOOLS", "setup_mcp"]
