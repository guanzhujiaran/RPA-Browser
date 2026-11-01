#!/usr/bin/env python3
"""通过导出的 JSON 文件把复合操作导入 / 更新到数据库。

背景 / 目的
-----------
复合操作（如「参与B站抽奖」action_id=104682832）的权威定义在代码库的
JSON 导出文件里（统一存放于 `actions/`，如 `actions/action_104682832_参与B站抽奖.json`）。本地迭代修改后，
需要把它同步进数据库才能真正被 RPA 执行（`/actions/execute` 从 DB 读步骤）。

本脚本**不直接连数据库**，而是调用与前端完全一致的 HTTP 接口
（`/custom-actions/import`、`/custom-actions/update`、`/custom-actions/list`），
由服务端完成校验、雪花 ID 生成与落库，避免在脚本里重复实现数据层逻辑。

语义（upsert）
--------------
按「操作名」在当前用户名下查找：
  - 已存在 → 调 `custom-actions/update` 全量更新（步骤 / 入出参 / 日志配置等）；
  - 不存在 → 调 `custom-actions/import` 新建（action_id 由服务端重新生成）。

JSON 里携带的数据库冗余字段（mid / id / created_at / likes_count 等）
由服务端请求模型自动忽略，原样传即可。

用法
----
    # 预演：只打印将发送的载荷，不落库
    .venv/bin/python scripts/import_action_from_json.py --dry-run

    # 正式导入 / 更新（动作 JSON 统一放在 actions/ 目录）
    .venv/bin/python scripts/import_action_from_json.py \
        --file actions/action_104682832_参与B站抽奖.json --mid 140682000

    # 强制新建一份（不更新同名操作）
    .venv/bin/python scripts/import_action_from_json.py --new

导入后建议
----------
该操作包含 fetch_external_data（去重取数）与 mark_resource_result（结果回写），
二者仅在工作流执行上下文中可用；单操作直跑（/actions/execute）会跳过 / 报错。
完整体验请把它挂到工作流（workflows_create 的 custom_action_id）后运行，
或参考 scripts/e2e_lottery_single.py 做单条 DOM 流程测试。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ACTIONS_DIR = PROJECT_ROOT / "actions"
DEFAULT_FILE = str(ACTIONS_DIR / "action_104682832_参与B站抽奖.json")
DEFAULT_BASE_URL = "http://localhost:28000"
DEFAULT_MID = 140682000
API_PREFIX = "/api/v1/rpa/browser/control"

#: 导入载荷字段 → JSON 键名（两侧一致，直接透传）；
#: JSON 中不在此表中的字段（mid/id/created_at/likes_count 等）为数据库
#: 冗余字段，由服务端请求模型忽略。
#: 注意：``version`` 自 2026-11 起参与落库（内容指纹方案），不能再当冗余字段丢弃，
#: 详见 docs/动作版本与内容指纹计划书.md §6.3/§6.4。
_PAYLOAD_FIELDS = (
    "name",
    "version",
    "action_type",
    "description",
    "icon_series",
    "icon_id",
    "parameters_schema",
    "steps",
    "tags",
    "input_vars",
    "output_vars",
    "is_public",
    "timeout",
    "retry_on_error",
    "retry_times",
    "retry_delay",
    "log_enabled",
    "log_debug",
    "log_record_params",
    "log_record_result",
    "log_record_variables",
    "log_only_on_error",
    "log_max_payload_length",
    "log_retention_days",
)

#: update 额外支持的字段（import/create 不接受）
_UPDATE_ONLY_FIELDS = ("is_enabled",)


@dataclass
class ActionPayload:
    """自定义操作导入 / 更新载荷（与导出 JSON 字段对齐）。

    steps / input_vars / parameters_schema 为导出文件里的原始 JSON 结构，
    字段语义由服务端请求模型校验，这里只做透传。
    """

    name: str
    version: str = "1.0.0"
    action_type: str = "COMPOSITE"
    description: str = ""
    icon_series: int = 0
    icon_id: int = 0
    parameters_schema: list[object] = field(default_factory=list)
    steps: list[object] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    input_vars: list[object] = field(default_factory=list)
    output_vars: list[str] = field(default_factory=list)
    is_public: bool = False
    timeout: int = 30000
    retry_on_error: bool = False
    retry_times: int = 0
    retry_delay: float = 1.0
    log_enabled: bool | None = None
    log_debug: bool | None = None
    log_record_params: bool = True
    log_record_result: bool = True
    log_record_variables: bool = False
    log_only_on_error: bool = False
    log_max_payload_length: int = 4000
    log_retention_days: int = 30
    is_enabled: bool | None = None

    @classmethod
    def from_json(cls, data: dict, *, name_override: str | None = None) -> ActionPayload:
        known = {k: v for k, v in data.items() if k in _PAYLOAD_FIELDS + _UPDATE_ONLY_FIELDS}
        payload = cls(**known)
        if name_override:
            payload.name = name_override
        if not payload.name:
            raise SystemExit("JSON 中缺少 name（操作显示名称）")
        if not payload.steps:
            raise SystemExit("JSON 中缺少 steps 步骤列表，无法导入")
        return payload

    def to_create_body(self) -> dict:
        """import / create 请求体（不含 update 专属字段）。"""
        return {k: getattr(self, k) for k in _PAYLOAD_FIELDS}

    def to_update_body(self, action_id: int) -> dict:
        """update 请求体（含 action_id 与 update 专属字段）。"""
        body = self.to_create_body()
        body.update({k: getattr(self, k) for k in _UPDATE_ONLY_FIELDS})
        return {"action_id": action_id, **body}


def _post(
    client: httpx.Client,
    base_url: str,
    mid: int,
    path: str,
    body: dict,
) -> dict:
    """统一 POST 封装：返回响应体，业务码非 0 直接退出并带上下文。"""
    resp = client.post(
        f"{base_url}{API_PREFIX}{path}",
        json=body,
        headers={"x-bili-mid": str(mid)},
    )
    try:
        payload = resp.json()
    except Exception:  # noqa: BLE001
        raise SystemExit(f"接口 {path} 返回非 JSON: HTTP {resp.status_code} {resp.text[:300]}")
    if resp.status_code != 200 or payload.get("code") != 0:
        raise SystemExit(
            f"接口 {path} 失败: HTTP {resp.status_code} code={payload.get('code')} "
            f"msg={payload.get('msg')}"
        )
    return payload


def find_existing_action_id(client: httpx.Client, base_url: str, mid: int, name: str) -> int | None:
    """按操作名在当前用户名下查找已有操作，返回其雪花 action_id。"""
    body = _post(
        client,
        base_url,
        mid,
        "/custom-actions/list",
        {"page": 1, "per_page": 50, "name": name},
    )
    items = ((body.get("data") or {}).get("items")) or []
    for item in items:
        if item.get("name") == name and item.get("action_id"):
            return int(item["action_id"])
    return None


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="通过导出 JSON 调用 RPA 接口，把复合操作导入 / 更新到数据库"
    )
    p.add_argument("--file", default=DEFAULT_FILE, help=f"导出 JSON 路径（默认 {DEFAULT_FILE}）")
    p.add_argument("--mid", type=int, default=DEFAULT_MID, help="目标用户 mid（鉴权 x-bili-mid）")
    p.add_argument("--base-url", default=DEFAULT_BASE_URL, help="RPA 服务地址")
    p.add_argument("--name", default=None, help="覆盖 JSON 中的操作名称（默认用 JSON 的 name）")
    p.add_argument("--new", action="store_true", help="强制新建一份，不更新同名操作")
    p.add_argument("--private", action="store_true", help="以私有（is_public=False）保存")
    p.add_argument("--dry-run", action="store_true", help="只打印将发送的载荷，不调用接口")
    p.add_argument("--timeout", type=float, default=30.0, help="HTTP 超时（秒）")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    path = Path(args.file)
    if not path.is_file():
        raise SystemExit(f"找不到 JSON 文件: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"JSON 顶层应为对象: {path}")

    payload = ActionPayload.from_json(data, name_override=args.name)
    if args.private:
        payload.is_public = False

    print(f"[file] {path}")
    print(f"[mid] {args.mid}  [name] {payload.name}  [steps] {len(payload.steps)}")
    print(f"[input_vars] {len(payload.input_vars)}  [tags] {payload.tags}")

    with httpx.Client(timeout=args.timeout) as client:
        # dry-run 不发起任何写操作，也跳过「按名查找」的读请求
        existing_id = (
            None
            if args.new or args.dry_run
            else find_existing_action_id(client, args.base_url, args.mid, payload.name)
        )
        if existing_id:
            body = payload.to_update_body(existing_id)
            print(f"[mode] 更新已有操作 action_id={existing_id}")
        else:
            body = payload.to_create_body()
            print("[mode] 新建操作（import，action_id 由服务端生成）")

        if args.dry_run:
            print("[payload-json]")
            print(json.dumps(body, ensure_ascii=False, indent=2)[:4000])
            print("\n[dry-run] 未调用接口；确认无误后去掉 --dry-run 再运行。")
            return

        if existing_id:
            result = _post(client, args.base_url, args.mid, "/custom-actions/update", body)
        else:
            result = _post(client, args.base_url, args.mid, "/custom-actions/import", body)

    saved = result.get("data") or {}
    print(f"[done] action_id={saved.get('action_id')} name={saved.get('name')!r} "
          f"id={saved.get('id')} steps={len(saved.get('steps') or [])}")
    print("[next] 单条 DOM 测试: .venv/bin/python scripts/e2e_lottery_single.py "
          f"--action-id {saved.get('action_id')} --kind others --target <动态链接>")
    print("[next] 完整体验（去重 / 结果回写）需把该操作挂到工作流后运行")


if __name__ == "__main__":
    main()
