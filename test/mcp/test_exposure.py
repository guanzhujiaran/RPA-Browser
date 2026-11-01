"""MCP 暴露面的离线单测（不连服务、不开浏览器）。

覆盖：`$ref` 展开的循环保护（自引用模型不再递归）、`int | str` 折叠成 `string`、
工具描述的自动噪音裁剪、按业务 tag 分组统计。
"""

from __future__ import annotations

from typing import Any

from bili_common.deps.permissions import BizPermOp
from bili_common.models.interaction import InteractionBizTypeEnum
from fastapi import APIRouter, Depends, FastAPI
import pytest

from app.mcp.exposure import (
    _fold_schema_ids,
    _fold_union,
    _is_admin_path,
    _resolve_refs_guarded,
    assert_no_admin_routes,
    find_admin_guarded_routes,
    find_admin_paths,
    group_operations_by_tag,
    shorten_tool_names,
    strip_generated_response_section,
)
from app.utils.depends.admin_depends import require_permission, require_root


def _reference_schema() -> dict[str, Any]:
    """构造一个 Node.children 自引用的最小 OpenAPI 片段。"""
    return {
        "components": {
            "schemas": {
                "Node": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "children": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/Node"},
                        },
                    },
                }
            }
        }
    }


def test_guarded_resolve_stops_at_recursive_ref() -> None:
    """自引用位置退化成 object，且不抛 RecursionError。"""
    resolved = _resolve_refs_guarded(
        {"$ref": "#/components/schemas/Node"}, _reference_schema()
    )

    assert resolved["type"] == "object"
    assert resolved["properties"]["name"] == {"type": "string"}
    nested = resolved["properties"]["children"]["items"]
    assert nested["type"] == "object"
    assert "递归引用 Node" in nested["description"]


def test_guarded_resolve_keeps_sibling_branches_expandable() -> None:
    """同一模型在不同分支上仍可展开（循环保护只作用于当前引用链）。"""
    reference = {
        "components": {
            "schemas": {
                "Pair": {
                    "type": "object",
                    "properties": {
                        "left": {"$ref": "#/components/schemas/Leaf"},
                        "right": {"$ref": "#/components/schemas/Leaf"},
                    },
                },
                "Leaf": {"type": "object", "properties": {"v": {"type": "integer"}}},
            }
        }
    }

    resolved = _resolve_refs_guarded(reference["components"]["schemas"]["Pair"], reference)

    assert resolved["properties"]["left"] == {
        "type": "object",
        "properties": {"v": {"type": "integer"}},
    }
    assert resolved["properties"]["right"] == {
        "type": "object",
        "properties": {"v": {"type": "integer"}},
    }


def test_fold_union_merges_int_and_str() -> None:
    """`int | str` 折叠成 string，并保留 title / 原 integer 分支的描述。"""
    node: dict[str, Any] = {
        "title": "browser_id",
        "anyOf": [
            {"type": "integer", "description": "雪花 ID（也可传字符串）"},
            {"type": "string"},
        ],
    }

    _fold_union(node)

    assert node["type"] == "string"
    assert node["title"] == "browser_id"
    assert node["description"] == "雪花 ID（也可传字符串）"
    assert "anyOf" not in node


def test_fold_union_keeps_nullable() -> None:
    """带 null 的可空字段折叠后仍是可空。"""
    node: dict[str, Any] = {
        "anyOf": [{"type": "integer"}, {"type": "string"}, {"type": "null"}]
    }

    _fold_union(node)

    assert node["anyOf"] == [{"type": "string"}, {"type": "null"}]


def test_fold_schema_ids_walks_nested_structures() -> None:
    """整份 OpenAPI（path → body → properties → items）里的联合类型都要被折叠。"""
    schema: dict[str, Any] = {
        "paths": {
            "/x": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "ids": {
                                            "type": "array",
                                            "items": {
                                                "anyOf": [
                                                    {"type": "integer"},
                                                    {"type": "string"},
                                                ]
                                            },
                                        }
                                    },
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    _fold_schema_ids(schema)

    items = schema["paths"]["/x"]["post"]["requestBody"]["content"]["application/json"][
        "schema"
    ]["properties"]["ids"]["items"]
    assert items == {"type": "string"}


def test_admin_path_detection_does_not_hit_similar_words() -> None:
    """只有独立的 admin 路径段才算管理端，``administrator`` 不误伤。"""
    assert _is_admin_path("/api/v1/rpa/admin/workflows/list")
    assert _is_admin_path("/admin/permission/grant")
    assert not _is_admin_path("/api/v1/rpa/browser/control/administrator")


def test_find_admin_paths_detects_admin_route() -> None:
    """admin 路径会被检出（含 include_router 惰性展开）。"""
    app = FastAPI()

    @app.post("/api/v1/rpa/admin/workflows/list")
    async def admin_list() -> None:
        return None

    sub = APIRouter(prefix="/api/v1/rpa/admin")
    sub.add_api_route("/users/ban", admin_list, methods=["POST"])
    app.include_router(sub)

    paths = find_admin_paths(app)
    assert "/api/v1/rpa/admin/workflows/list" in paths
    assert "/api/v1/rpa/admin/users/ban" in paths


def test_assert_no_admin_routes_rejects_admin_route() -> None:
    """出现管理端接口时必须拒绝挂载。"""
    app = FastAPI()

    @app.post("/api/v1/rpa/admin/workflows/list")
    async def admin_list() -> None:
        return None

    with pytest.raises(RuntimeError, match="管理端接口"):
        assert_no_admin_routes(app)


class _FakeTool:
    """最小可写的工具替身（只有 description 会被改写）。"""

    def __init__(self, description: str) -> None:
        self.description = description


def test_strip_generated_response_section_removes_noise() -> None:
    """裁掉 fastapi-mcp 追加的 `### Responses` 段，并去掉重复的首段。"""
    description = (
        "执行工作流\n\n"
        "执行工作流\n\n"
        "支持两种模式：\n- 提供 action_id\n- 提供 steps"
        "\n\n### Responses:\n\n"
        "**200**: Successful Response (Success Response)"
        "\nContent-Type: application/json"
        '\n\n**Example Response:**\n```json\n{\n  "code": 1,\n  "msg": "Msg"\n}\n```'
    )
    tools = [_FakeTool(description), _FakeTool("没有噪音的描述")]

    removed = strip_generated_response_section(tools)

    assert removed == 1
    assert "### Responses" not in tools[0].description
    assert "code" not in tools[0].description
    # summary 与 docstring 首行重复的那一份也被去掉（只留一份 + 正文）
    assert tools[0].description.count("执行工作流") == 1
    assert "支持两种模式" in tools[0].description
    assert tools[1].description == "没有噪音的描述"


def test_strip_generated_response_section_counts_only_changed() -> None:
    """干净描述不被计入「已裁剪」。"""
    tools = [_FakeTool("干净的描述")]

    assert strip_generated_response_section(tools) == 0
    assert tools[0].description == "干净的描述"


class _NamedTool(_FakeTool):
    """带名字的工具替身。"""

    def __init__(self, name: str, description: str = "") -> None:
        super().__init__(description)
        self.name = name


def test_shorten_tool_names_rewrites_only_autogen_names() -> None:
    """自动生成的长名被压缩，并在 operation_map 里同步换 key。"""
    old = (
        "create_or_update_user_default_settings_api_v1_rpa_browser_"
        "default_settings_create_or_update_post"
    )
    tools = [_NamedTool(old), _NamedTool("rpa_execute_action", "已经是短名")]
    operation_map = {
        old: {
            "path": "/api/v1/rpa/browser/default_settings/create_or_update",
            "method": "post",
        },
        "rpa_execute_action": {
            "path": "/api/v1/rpa/browser/control/debug/execute",
            "method": "post",
        },
    }

    renamed = shorten_tool_names(tools, operation_map)

    assert tools[0].name == "rpa_default_settings_create_or_update"
    assert tools[1].name == "rpa_execute_action"
    assert renamed == {old: "rpa_default_settings_create_or_update"}
    # key 必须同步：call_tool 按工具名索引 operation_map
    assert "rpa_default_settings_create_or_update" in operation_map
    assert old not in operation_map
    assert "rpa_execute_action" in operation_map


def test_shorten_tool_names_disambiguates_collisions() -> None:
    """不同接口压缩出同名时，第二个加序号，避免互相覆盖。"""
    first = "list_router_api_v1_rpa_browser_control_workflows_list_post"
    second = "list_v2_router_api_v1_rpa_browser_control_workflows_list_post"
    tools = [_NamedTool(first), _NamedTool(second)]
    operation_map = {
        first: {"path": "/api/v1/rpa/browser/control/workflows/list", "method": "post"},
        second: {"path": "/api/v1/rpa/browser/control/workflows/list", "method": "post"},
    }

    shorten_tool_names(tools, operation_map)

    assert tools[0].name == "rpa_workflows_list"
    assert tools[1].name == "rpa_workflows_list_2"
    assert set(operation_map) == {"rpa_workflows_list", "rpa_workflows_list_2"}


def test_group_operations_by_tag_uses_business_tags() -> None:
    """按 OpenAPI tag 分组，而不是按路径末段（`create`/`status` 之类伪资源）。"""
    app = FastAPI()

    @app.post("/api/v1/rpa/browser/control/workflows/list", tags=["工作流管理"])
    async def list_workflows() -> None:
        return None

    @app.post("/api/v1/rpa/browser/control/create", tags=["浏览器会话控制"])
    async def create_session() -> None:
        return None

    operation_map = {
        "list_workflows": {
            "path": "/api/v1/rpa/browser/control/workflows/list",
            "method": "post",
        },
        "create_session": {
            "path": "/api/v1/rpa/browser/control/create",
            "method": "post",
        },
    }

    groups = group_operations_by_tag(app, operation_map)

    assert groups == {"工作流管理": 1, "浏览器会话控制": 1}


def test_find_admin_guarded_routes_flags_guards() -> None:
    """依赖管理员守卫的路由即使路径不在 /admin 也要被检出。"""
    router = APIRouter()

    @router.post("/actions/running")
    async def guarded(_: None = Depends(require_root)) -> None:
        return None

    @router.post("/actions/running-by-perm")
    async def guarded_by_perm(
        _: None = Depends(require_permission(InteractionBizTypeEnum.USER, BizPermOp.BAN)),
    ) -> None:
        return None

    @router.post("/actions/list")
    async def normal() -> None:
        return None

    hits = find_admin_guarded_routes(router)

    assert len(hits) == 2
    assert all("actions/list" not in hit for hit in hits)
