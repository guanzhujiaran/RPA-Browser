"""MCP 暴露面：把「用户可用的 RPA 接口」按 OpenAPI 自动转成 MCP 工具。

分工：
- 本模块只决定「暴露哪些接口」+「让 schema 能被 ``fastapi-mcp`` 正确解析」；
- 挂载、鉴权头注入、工具清单自检仍在 ``app/mcp/mount.py``。

三条实测硬约束（``fastapi-mcp 0.4.0`` + ``mcp 1.30.0``）：

1. ``resolve_schema_references`` 没有循环保护，而 ``WorkflowStepRequest.children``
   是自引用模型，全量展开直接 ``RecursionError``；且这一步发生在 tag 过滤**之前**，
   所以不能靠 ``include_tags`` 规避 —— 只能给展开过程加「已展开模型不再展开」的保护。
2. ``int | str`` 在 OpenAPI 里是 ``anyOf[integer, string]``，部分 MCP 客户端按第一个
   类型（integer）校验，雪花 id 用字符串传会在参数校验阶段被拒；这里把
   ``anyOf[integer, string]`` 折叠成 ``string``（后端 pydantic 的 ``int | str``
   本就接受字符串，语义不变）。
3. SSE / WebRTC 信令是长连接或双向实时通道，不是请求-响应，MCP 调用会挂住 ——
   用 ``EXCLUDED_OPERATIONS`` 按 **operation_id** 精确排除；不能按 tag 排除：
   ``pages/list|switch|close`` 在既有代码里被打上了 ``WebRTC 视频流`` tag，
   按 tag 排除会误伤页面管理能力。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from typing import Any, Final, cast

import fastapi_mcp.openapi.convert as mcp_convert
import fastapi_mcp.server as mcp_server
from bili_common.exceptions import register_business_exception_handlers
from fastapi import APIRouter, FastAPI, Request
from fastapi.dependencies.models import Dependant
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from loguru import logger
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.routing import BaseRoute
from starlette.types import ExceptionHandler

from app.controller.v1 import browser as browser_routes
from app.controller.v1 import browser_control as browser_control_routes
from app.exceptions.handlers import (
    custom_exception_handler,
    http_exception_handler,
    validation_exception_handler,
)
from app.models.common.exceptions.base_exception import (
    BaseException as CustomBaseException,
)
from app.utils.middlewares.ban_guard import BanGuardMiddleware

#: 契约相关：这些接口在 MCP 通道里没有可用的「请求-响应」语义，必须排除。
#: 新增 SSE / WebRTC / 长轮询接口时同步补进这里（``--selftest`` 会校验清单有效性）。
EXCLUDED_OPERATIONS: Final[tuple[str, ...]] = (
    # 运行进度 SSE（首帧快照 + 推送 + 心跳，终态才关闭）
    "workflow_run_events_api_v1_rpa_browser_control_workflows_runs_events_get",
    # 会话状态 SSE
    "browser_session_events_api_v1_rpa_browser_control_events_get",
    # WebRTC 信令：需要信令双方实时交换 SDP / ICE，MCP 工具无法承担
    "create_webrtc_offer_api_v1_rpa_browser_control_webrtc_offer_post",
    "handle_webrtc_answer_api_v1_rpa_browser_control_webrtc_answer_post",
    "add_ice_candidate_api_v1_rpa_browser_control_webrtc_ice_candidate_post",
    "add_ice_candidates_api_v1_rpa_browser_control_webrtc_ice_candidates_post",
    "close_webrtc_stream_api_v1_rpa_browser_control_webrtc_close_post",
    "get_webrtc_status_api_v1_rpa_browser_control_webrtc_status_post",
    "set_webrtc_quality_api_v1_rpa_browser_control_webrtc_quality_post",
    "report_webrtc_visibility_api_v1_rpa_browser_control_webrtc_visibility_post",
    "set_webrtc_paused_api_v1_rpa_browser_control_webrtc_pause_post",
    "webrtc_viewer_heartbeat_api_v1_rpa_browser_control_webrtc_heartbeat_post",
)

#: 管理端守卫依赖（``app/utils/depends/admin_depends.py``）：命中即视为管理员接口。
#: ``require_permission`` 返回闭包，收集依赖名时按 ``qualname`` 首段匹配。
ADMIN_DEPENDENCIES: Final[frozenset[str]] = frozenset(
    {"require_root", "require_admin", "require_permission"}
)


def _resolve_refs_guarded(
    schema_part: dict[str, Any],
    reference_schema: dict[str, Any],
    _opened: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """展开 ``$ref``，但同一引用链上已展开过的模型不再展开。

    自引用模型（``WorkflowStepRequest.children``）在第二次命中时退化成
    ``{"type": "object"}`` —— 顶层字段仍完整，深层递归结构由后端 pydantic 兜住校验。
    """
    schema_part = schema_part.copy()
    ref = schema_part.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
        model_name = ref.split("/")[-1]
        if model_name in _opened:
            return {"type": "object", "description": f"（递归引用 {model_name}）"}
        target = reference_schema.get("components", {}).get("schemas", {}).get(model_name)
        if isinstance(target, dict):
            schema_part.pop("$ref")
            schema_part.update(target.copy())
            return _resolve_refs_guarded(
                schema_part, reference_schema, _opened | {model_name}
            )
    for key, value in list(schema_part.items()):
        if isinstance(value, dict):
            schema_part[key] = _resolve_refs_guarded(value, reference_schema, _opened)
        elif isinstance(value, list):
            schema_part[key] = [
                _resolve_refs_guarded(item, reference_schema, _opened)
                if isinstance(item, dict)
                else item
                for item in value
            ]
    return schema_part


def _fold_union(node: dict[str, Any]) -> None:
    """把 ``anyOf[integer, string]`` 就地折叠成 ``string``（保留原描述）。"""
    variants = node.get("anyOf")
    if isinstance(variants, list):
        kinds = {item.get("type") for item in variants if isinstance(item, dict)}
        if "integer" in kinds and "string" in kinds:
            kept = [
                item
                for item in variants
                if not (isinstance(item, dict) and item.get("type") == "integer")
            ]
            dropped_desc = next(
                (
                    item.get("description")
                    for item in variants
                    if isinstance(item, dict) and item.get("type") == "integer"
                ),
                None,
            )
            extras = {
                key: value for key, value in node.items() if key not in {"anyOf", "oneOf"}
            }
            if len(kept) == 1:
                merged: dict[str, Any] = dict(kept[0])
                merged.update(extras)
                if dropped_desc and not merged.get("description"):
                    merged["description"] = dropped_desc
            else:
                merged = {"anyOf": kept, **extras}
            node.clear()
            node.update(merged)

    for key in list(node.keys()):
        value = node[key]
        if isinstance(value, dict):
            _fold_union(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    _fold_union(item)


def _fold_schema_ids(schema: dict[str, Any]) -> dict[str, Any]:
    """遍历整份 OpenAPI，折叠所有 ``int | str`` 联合类型。"""
    _fold_union(schema)
    return schema


def install_schema_compat() -> None:
    """安装 ``fastapi-mcp`` 兼容补丁（幂等，进程内只需一次）。

    注意 ``fastapi_mcp.server`` 是在模块级 ``from ...convert import convert_openapi_to_mcp_tools``
    绑定的，因此两个模块的属性都要替换。
    """
    if getattr(mcp_convert, "_rpa_compat_installed", False):
        return

    original_convert = mcp_convert.convert_openapi_to_mcp_tools

    def convert_with_relaxed_ids(
        openapi_schema: dict[str, Any], **kwargs: Any
    ) -> Any:
        return original_convert(_fold_schema_ids(openapi_schema), **kwargs)

    mcp_convert.resolve_schema_references = _resolve_refs_guarded
    mcp_convert.convert_openapi_to_mcp_tools = convert_with_relaxed_ids
    mcp_server.convert_openapi_to_mcp_tools = convert_with_relaxed_ids
    mcp_convert._rpa_compat_installed = True  # type: ignore[attr-defined]
    logger.info("[MCP] schema 兼容补丁已安装（$ref 循环保护 + int|str 折叠为 string）")


async def _internal_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """MCP 内部调用未预期异常：打印堆栈并返回可读错误（调用方能看到原因）。"""
    logger.exception(f"[MCP] 内部接口错误: {request.url.path}: {exc}")
    return JSONResponse(
        status_code=500,
        content={
            "code": 500,
            "msg": f"内部错误: {type(exc).__name__}: {exc}",
            "data": None,
        },
    )


def _register_exception_handlers(app: FastAPI) -> None:
    """让被 MCP 内部调用的接口与主应用保持相同的错误语义。"""
    # handler 的第二参数是具体异常子类，starlette 的类型是 Exception（逆变），此处显式收敛
    for exc_class, handler in (
        (CustomBaseException, custom_exception_handler),
        (StarletteHTTPException, http_exception_handler),
        (RequestValidationError, validation_exception_handler),
    ):
        app.add_exception_handler(exc_class, cast(ExceptionHandler, handler))
    app.add_exception_handler(Exception, cast(ExceptionHandler, _internal_error_handler))
    register_business_exception_handlers(app)


def _call_name(call: Any) -> str:
    """取依赖函数的限定名（``require_permission`` 返回的闭包会是 ``require_permission.<locals>._dep``）。"""
    return str(
        getattr(call, "__qualname__", None) or getattr(call, "__name__", "") or ""
    )


def _dependency_names(dependant: Dependant) -> set[str]:
    """递归收集一条路由依赖树里所有可调用对象的名字（``qualname`` 首段）。"""
    names: set[str] = set()
    stack: list[Dependant] = [dependant]
    while stack:
        current = stack.pop()
        call = getattr(current, "call", None)
        if call is not None:
            names.add(_call_name(call).split(".")[0])
        stack.extend(getattr(current, "dependencies", None) or [])
    return names


def _is_admin_path(path: str) -> bool:
    """路径段里带 ``admin`` 即视为管理端接口。"""
    return "admin" in [seg for seg in path.split("/") if seg]


def _iter_leaf_routes(routes: Iterable[BaseRoute]) -> Iterator[Any]:
    """递归展开 ``include_router`` 的惰性节点，拿到叶子路由。

    FastAPI 0.148 起 ``include_router`` 不再立即复制路由，而是放入
    ``_IncludedRouter``（只有 ``original_router``，没有 ``path`` / ``dependant``），
    必须递归 ``original_router.routes`` 才能取到真正的 ``APIRoute``。
    """
    for route in routes:
        original = getattr(route, "original_router", None)
        if original is not None:
            yield from _iter_leaf_routes(original.routes)
            continue
        if hasattr(route, "dependant") and hasattr(route, "path"):
            yield route


def find_admin_guarded_routes(*routers: APIRouter) -> tuple[str, ...]:
    """找出依赖了管理员守卫的叶子路由（诊断用；path 为子 router 相对路径）。"""
    hits: list[str] = []
    for router in routers:
        for route in _iter_leaf_routes(router.routes):
            if _dependency_names(route.dependant) & ADMIN_DEPENDENCIES:
                methods = ",".join(sorted(getattr(route, "methods", None) or []))
                hits.append(f"{methods} {route.path}")
    return tuple(hits)


def find_admin_paths(app: FastAPI) -> tuple[str, ...]:
    """从 OpenAPI 里找出带 ``admin`` 路径段的接口（兜底校验，不依赖 FastAPI 内部结构）。"""
    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    return tuple(sorted(path for path in schema.get("paths", {}) if _is_admin_path(path)))


def assert_no_admin_routes(app: FastAPI) -> None:
    """硬约束：MCP 子应用里不得出现管理端接口，否则拒绝挂载。

    管理员能力一旦混进 MCP，普通用户就等于用配置的 mid 拿到管理员身份，
    因此这里 fail-fast，而不是静默放行。
    """
    admin_paths = find_admin_paths(app)
    if admin_paths:
        raise RuntimeError(
            f"[MCP] 用户能力子应用里出现管理端接口，拒绝挂载: {admin_paths}"
        )


def build_user_app() -> FastAPI:
    """构建只包含「用户可用路由」的轻量子应用。

    - **不 include admin router**：MCP 只开放用户能力（主防线）；
    - 构建后跑 :func:`find_admin_guarded_routes` + :func:`assert_no_admin_routes`
      兜底：将来若有人把管理端接口或管理员守卫写进用户 router，直接拒绝启动；
    - 补 ``BanGuardMiddleware``：MCP 不经过网关，封禁用户不能借 MCP 通道绕过拦截。
    """
    guarded = find_admin_guarded_routes(
        browser_routes.router, browser_control_routes.router
    )
    if guarded:
        logger.error(f"[MCP] 用户路由里出现依赖管理员守卫的接口: {list(guarded)}")

    app = FastAPI(
        title="rpa-user-capability",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(BanGuardMiddleware)
    app.include_router(browser_routes.router)
    app.include_router(browser_control_routes.router)
    assert_no_admin_routes(app)
    _register_exception_handlers(app)
    return app


def group_operations_by_tag(
    app: FastAPI,
    operation_map: dict[str, dict[str, Any]],
) -> dict[str, int]:
    """按 OpenAPI `tag` 统计工具数（启动日志用）。

    比按路径段推断准确：`/browser/control/create|status|close` 这类路径的末段
    就是动作名，按路径会切出 `create` / `status` / `queue_status` 之类伪资源名；
    tag 是路由注册时人工给的业务分类（「工作流管理」「自定义操作管理」等）。
    """
    schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
    paths = schema.get("paths", {})
    summary: dict[str, int] = {}
    for detail in operation_map.values():
        path = str(detail.get("path", ""))
        method = str(detail.get("method", "")).lower()
        operation = (paths.get(path) or {}).get(method) or {}
        tags = operation.get("tags") or []
        tag = str(tags[0]) if tags else "未分类"
        summary[tag] = summary.get(tag, 0) + 1
    return dict(sorted(summary.items(), key=lambda kv: -kv[1]))


#: fastapi-mcp 自动追加响应段的固定分隔符（``openapi/convert.py`` 里的字面量）
_RESPONSE_SECTION_MARKER = "\n\n### Responses:"

#: 自动生成的工具名里一定包含的路径片段（显式设置了 operation_id 的接口没有）
_AUTOGEN_NAME_MARKER = "_api_v1_rpa"


def _tool_name_from_path(path: str, method: str) -> str:
    """按「资源/动作」把接口路径压成短工具名，如 `rpa_workflows_list`。

    去掉部署前缀（``/api/v1/rpa``）与固定前缀（``browser/control``），
    路径分隔符与参数占位符统一成下划线。
    """
    raw = path.replace("/api/v1/rpa", "").strip("/")
    for prefix in ("browser/control/", "browser/"):
        if prefix in raw:
            raw = raw.split(prefix, 1)[1]
            break
    slug = re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")
    return f"rpa_{slug}" if slug else f"rpa_{method.lower()}"


def shorten_tool_names(
    tools: Iterable[Any],
    operation_map: dict[str, dict[str, Any]],
) -> dict[str, str]:
    """把 fastapi-mcp 自动生成的超长工具名收敛成 `rpa_<资源>_<动作>`。

    fastapi-mcp 对**没有显式 operation_id** 的接口用「函数名 + 完整路径 + 方法」
    兜底命名，形如
    ``create_or_update_user_default_settings_api_v1_rpa_browser_default_settings_create_or_update_post``
    （实测中位 69 字符、最长 96），既把部署前缀 ``api/v1/rpa`` 泄进工具名，又挤占
    调用方上下文、逼近部分客户端的名字长度上限。

    显式设置了 operation_id 的接口（``rpa_execute_action`` 等）保持原样。

    Returns:
        ``{旧名: 新名}`` 映射（供日志核对）。注意 **同步改写了 operation_map 的 key**：
        `call_tool` 正是按工具名索引它，只改 tool.name 会让调用查不到路由。
    """
    renamed: dict[str, str] = {}
    taken = {str(getattr(tool, "name", "")) for tool in tools}
    for tool in tools:
        old = str(getattr(tool, "name", ""))
        if not old or _AUTOGEN_NAME_MARKER not in old:
            continue
        detail = operation_map.get(old) or {}
        path = str(detail.get("path", ""))
        method = str(detail.get("method", ""))
        if not path:
            continue
        new = _tool_name_from_path(path, method)
        if new in taken:
            index = 2
            while f"{new}_{index}" in taken:
                index += 1
            new = f"{new}_{index}"
        if len(new) >= len(old):
            continue
        tool.name = new
        operation_map[new] = operation_map.pop(old)
        taken.discard(old)
        taken.add(new)
        renamed[old] = new
    return renamed


def strip_generated_response_section(tools: Iterable[Any]) -> int:
    """裁掉 fastapi-mcp 自动拼进工具描述的 ``### Responses`` 段，返回被裁工具数。

    该段由 ``fastapi_mcp/openapi/convert.py:71-135`` **无条件**拼接
    （``describe_all_responses=False`` 只控制「包含几条响应」，不控制「是否包含」），
    内容是按响应 schema 生成的占位示例：

        **200**: Successful Response (Success Response)
        **Example Response:** {"code": 1, "msg": "Msg"}

    在本服务里它占掉约一半描述字符（94 个工具合计 1.3 万字符），而且给出的
    ``code: 1`` 与真实约定（``code == 0`` 才是成功）相反，容易误导调用方。
    响应语义由各工具自己写明的段落 + MCP 服务描述承担，这里统一裁掉。
    """
    removed = 0
    for tool in tools:
        description = getattr(tool, "description", "") or ""
        changed = False
        if _RESPONSE_SECTION_MARKER in description:
            description = description.split(_RESPONSE_SECTION_MARKER, 1)[0].rstrip()
            changed = True
        # 另一个小噪音：summary 与 docstring 首行相同时会渲染成
        # 「执行工作流\n\n执行工作流\n\n...」，把重复的那几份去掉
        while True:
            first, sep, rest = description.partition("\n\n")
            head = first.strip()
            body = rest.lstrip()
            if not sep or not head or not body.startswith(head):
                break
            # 只吃「完全相同的一段」，避免误删「执行工作流（延迟）」这类同前缀内容
            tail = body[len(head) :]
            if tail and not tail.startswith("\n"):
                break
            description = body
            changed = True
        if changed:
            tool.description = description.rstrip()
            removed += 1
    return removed


def group_operations_by_resource(
    operation_map: dict[str, dict[str, Any]],
) -> dict[str, int]:
    """按接口路径的资源段统计工具数（启动日志用）。"""
    summary: dict[str, int] = {}
    for detail in operation_map.values():
        segments = [seg for seg in str(detail.get("path", "")).split("/") if seg]
        if "control" in segments:
            idx = segments.index("control")
            resource = segments[idx + 1] if len(segments) > idx + 1 else "control"
        else:
            resource = segments[3] if len(segments) > 3 else "root"
        summary[resource] = summary.get(resource, 0) + 1
    return dict(sorted(summary.items(), key=lambda kv: -kv[1]))


__all__ = [
    "ADMIN_DEPENDENCIES",
    "EXCLUDED_OPERATIONS",
    "assert_no_admin_routes",
    "build_user_app",
    "find_admin_guarded_routes",
    "find_admin_paths",
    "group_operations_by_resource",
    "group_operations_by_tag",
    "install_schema_compat",
    "shorten_tool_names",
    "strip_generated_response_section",
]
