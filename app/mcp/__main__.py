"""``python -m app.mcp --selftest``：离线自检（不连数据库、不启动服务）。

检查项：
1. 挂载配置（开关 / 路径 / 注入头 / 额外排除项）；
2. MCP 工具清单：暴露数量、排除清单是否真的生效、调试工具是否齐备；
3. 静态校验样例（合法 / 非法 steps 的定位结果）。

真正的 MCP 端点在 ``python main.py`` 启动后由 ``app/mcp/mount.py`` 挂载，
不需要本模块常驻。
"""

from __future__ import annotations

import json
import sys

from fastapi import FastAPI
from fastapi_mcp import FastApiMCP

from app.mcp.config import AUTH_HEADER_NAMES, mcp_settings
from app.mcp.exposure import (
    EXCLUDED_OPERATIONS,
    build_user_app,
    group_operations_by_tag,
    install_schema_compat,
    shorten_tool_names,
    strip_generated_response_section,
)
from app.mcp.mount import EXPECTED_TOOLS
from app.mcp.validator import static_validation_available, validate_steps


def _dump(value: object) -> str:
    """把 pydantic 模型 / 列表输出成格式化 JSON。"""
    if hasattr(value, "model_dump"):
        value = getattr(value, "model_dump")(mode="json")
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _build_probe(
    app: FastAPI,
    excluded: tuple[str, ...],
    *,
    shorten: bool = False,
) -> FastApiMCP:
    """按挂载时的同一套配置生成工具（不挂载，仅用于自检）。

    Args:
        app: 用户能力子应用（``build_user_app()``）
        excluded: 要排除的 operationId
        shorten: 是否同步「裁描述 + 收敛工具名」，让自检输出与线上工具名一致。
            校验排除清单时必须用**未缩短**的名字（排除项写的是 operationId）。
    """
    install_schema_compat()
    mcp = FastApiMCP(
        app,
        name="selftest",
        describe_all_responses=False,
        exclude_operations=list(excluded),
        headers=list(AUTH_HEADER_NAMES),
    )
    if shorten:
        strip_generated_response_section(mcp.tools)
        shorten_tool_names(mcp.tools, mcp.operation_map)
    return mcp


def run_selftest() -> int:
    """执行自检并返回退出码。"""
    print("[config]")
    print(f"  enabled          = {mcp_settings.enabled_effective}")
    print(f"  mount_path       = {mcp_settings.mount_path}")
    print(
        "  auth             = 网关 Cookie(bili_jwt) 校验后注入 "
        f"{list(AUTH_HEADER_NAMES)}（RPA 侧仅透传）"
    )
    print(f"  extra_excluded   = {mcp_settings.excluded_operation_ids or '无'}")
    print(f"  static_check     = {static_validation_available()}")

    excluded = (*EXCLUDED_OPERATIONS, *mcp_settings.excluded_operation_ids)
    print("\n[tools]")
    try:
        source_app = build_user_app()
        full = _build_probe(source_app, ())
        exposed = _build_probe(source_app, excluded, shorten=True)
        full_names = {tool.name for tool in full.tools}
        names = {tool.name for tool in exposed.tools}

        print(f"  full     = {len(full_names)}")
        print(f"  exposed  = {len(names)}")
        print(f"  excluded = {len(excluded)}")
        print(f"  by tag   = {group_operations_by_tag(source_app, exposed.operation_map)}")

        unknown = [name for name in excluded if name not in full_names]
        print(f"  unknown_excluded = {unknown or '无'}")
        # 排除项写的是 operationId（full 的名字），泄漏判定必须按 path 比对，
        # 否则工具名收敛后两者永远对不上
        excluded_paths = {
            str((full.operation_map.get(name) or {}).get("path", ""))
            for name in EXCLUDED_OPERATIONS
        }
        leaked = sorted(
            {
                str(detail.get("path", ""))
                for detail in exposed.operation_map.values()
            }
            & excluded_paths
        )
        print(f"  leaked           = {leaked or '无'}")
        admin_leaks = sorted(
            {
                str(detail.get("path", ""))
                for detail in exposed.operation_map.values()
                if "admin" in [seg for seg in str(detail.get("path", "")).split("/") if seg]
            }
        )
        print(f"  admin_leaks      = {admin_leaks or '无'}")
        missing = [name for name in EXPECTED_TOOLS if name not in names]
        print(f"  missing_debug    = {missing or '无'}")
    except Exception as exc:  # noqa: BLE001 - 自检不应因环境问题抛栈
        print(f"  [error] 生成工具清单失败: {type(exc).__name__}: {exc}")

    print("\n[invalid sample]")
    print(_dump(validate_steps([{"action_id": "not_an_action"}, "oops"])))

    print("\n[empty sample]")
    print(_dump(validate_steps([])))
    return 0


def main() -> None:
    """命令行入口（仅 --selftest；常驻 MCP 由 FastAPI 挂载）。"""
    if "--selftest" in sys.argv[1:]:
        raise SystemExit(run_selftest())
    print(
        "MCP 由 FastAPI 挂载，不单独常驻。\n"
        f"启动服务后访问 {mcp_settings.mount_path}\n"
        "自检：python -m app.mcp --selftest"
    )


if __name__ == "__main__":
    main()
