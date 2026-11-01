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
    group_operations_by_tag,
    install_schema_compat,
    shorten_tool_names,
    strip_generated_response_section,
)

#: 调试专用工具（``tag="MCP 调试"``）的期望集合，用于启动自检。
#: 统一 ``rpa_debug_*`` 前缀：与前端同款接口的镜像工具（``rpa_actions_execute`` /
#: ``rpa_workflows_execute_inline`` 等）区分开，避免调用方在近义工具间选错。
EXPECTED_TOOLS: tuple[str, ...] = (
    "rpa_debug_actions_list",
    "rpa_debug_action_get",
    "rpa_debug_steps_validate",
    "rpa_debug_action_execute",
    "rpa_debug_action_save",
    "rpa_debug_workflow_run",
    "rpa_debug_run_logs",
    "rpa_debug_action_logs",
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
            "RPA 用户能力全集：自定义动作 / 工作流 / 浏览器会话与页面 / 指纹与通知配置 / "
            "操作日志 / 动作调试（校验、试跑、落库回读）。\n\n"
            "工具分两组，按前缀选：\n"
            "- `rpa_debug_*`：动作调试主路径（列出/导出动作、静态校验、试跑、落库回读、"
            "运行工作流并等终态、查步骤链路）。**要改动作或跑动作优先用这组**，"
            "其中 `rpa_debug_action_execute` 支持内联 steps 且先做静态校验；\n"
            "- 其余（`rpa_actions_*` / `rpa_workflows_*` / `rpa_session_*` / "
            "`rpa_pages_*` / `rpa_custom_actions_*` 等）：与前端页面同款接口的镜像，"
            "参数更贴近页面表单（如 `rpa_actions_execute` 只接受已保存的 action_id）。\n\n"
            "统一响应约定：`{\"code\": 0, \"msg\": \"success\", \"data\": ...}`，"
            "**code == 0 才表示成功**；非 0 时 msg 是失败原因（业务类错误同样返回 "
            "HTTP 200 + 非 0 code），常见值：1011=动作不存在、1012=工作流不存在、"
            "403=无权访问该资源。"
        ),
        describe_all_responses=False,
        exclude_operations=list(excluded),
        # 透传网关注入的可信身份头（内网口径；MCP 客户端本身不经过这层）
        headers=list(AUTH_HEADER_NAMES),
    )
    # 裁掉 fastapi-mcp 自动追加的 `### Responses` 段（占掉约一半描述，且示例是
    # 与真实约定相反的占位值 code=1），再挂载端点
    stripped = strip_generated_response_section(mcp.tools)

    # 自动生成的长名（中位 69 字符，带 api/v1/rpa 前缀）收敛成 rpa_<资源>_<动作>；
    # 显式 operation_id 的调试工具保持不变。改名会同步 operation_map 的 key
    renamed = shorten_tool_names(mcp.tools, mcp.operation_map)

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
    groups = group_operations_by_tag(source_app, mcp.operation_map)
    logger.info(
        f"[MCP] 已挂载 {mount_path}，工具 {len(tool_names)} 个（排除 {len(excluded)} 个），"
        f"已裁剪 {stripped} 个工具的描述噪音、规范化 {len(renamed)} 个工具名，"
        f"鉴权=网关注入 {list(AUTH_HEADER_NAMES)}，按业务分类: {groups}"
    )
    if missing:
        logger.warning(f"[MCP] 预期调试工具缺失（检查 operation_id）: {missing}")
    if leaked:
        logger.warning(f"[MCP] 应排除的接口出现在工具表里（检查 exclude_operations）: {leaked}")
    if admin_leaks:
        logger.error(f"[MCP] 管理员接口出现在工具表里（必须修复）: {admin_leaks}")
    return mcp


__all__ = ["EXPECTED_TOOLS", "setup_mcp"]
