#!/usr/bin/env python3
"""单条动态端到端测试（模拟前端真实调用，一次只跑一条指定动态）。

背景 / 目的
-----------
复合动作「参与B站抽奖」(action_id=104682832) 会从爬虫批量拉取三类抽奖动态并逐条处理。
端到端测试**只允许在用户明确指定的动态上执行**，绝不自选、绝不批量。本脚本据此：

- 只跑**指定的一类**抽奖（预约 / 官方转发 / 第三方评论）；
- 只命中**用户给定的那一条**动态；
- 请求体形态**与前端调试台完全一致**（见
  Vue3FrontEndDemoExercise/src/components/rpa-browser/useDebugboxExecution.ts）：

      POST /api/v1/rpa/browser/control/actions/execute?browser_id=<id>
      body = {
        "action_id": 104682832,
        "params": { "steps": [<真实步骤树，含 {{模板}}>] },  # 后端从 params.steps 取步骤
        "input_vars": {...},   # 后端会合并进变量池
        "output_vars": []
      }

  因此 `{{oth_item.dynamicUrl}}`、`others_data.items` 等**模板 / 点路径解析、步骤编译**
  全部走后端真实链路，不预替换、不绕过。

与线上动作的唯一差异
--------------------
把 3 个 `fetch_external_data`（批量拉列表）步骤从 `params.steps` 中移除，改为通过
`input_vars` 注入「所选类型的单条数据」（其余两类注入空列表）→ 只处理这一条。
同时移除全部 `mark_resource_result` 步骤：该动作仅在工作流执行中可用
（依赖 workflow_id 执行上下文，独立执行会返回失败并中断链路），
不属于本次 DOM 流程测试范围。其余步骤树原样保留。

用法
----
    .venv/bin/python scripts/e2e_lottery_single.py \
        --kind others \
        --target https://t.bilibili.com/920195184127377412 \
        --dry-run            # 先打印将要发送的 JSON，人工确认后再去掉 --dry-run 真正执行

三类抽奖的 --target 含义：
    others   第三方评论抽奖：动态链接（如 https://t.bilibili.com/<dynId>）
    official 官方转发抽奖：动态链接（如 https://www.bilibili.com/opus/<dynId>）
    reserve  预约类抽奖    ：预约活动链接，另需 --reserve-sid <sid>
"""

from __future__ import annotations

import argparse
import json
import re
import sys

import httpx

DEFAULT_BASE_URL = "http://localhost:28000"
DEFAULT_ACTION_ID = 104682832
DEFAULT_BROWSER_ID = 104662592
DEFAULT_MID = 140682000
API_PREFIX = "/api/v1/rpa/browser/control"

# 三类抽奖在动作步骤里 loop 遍历的列表变量名（见 DB 步骤 9/10/11 的 loop_items_var）
KIND_LIST_VAR = {
    "reserve": "reserve_data",
    "official": "official_data",
    "others": "others_data",
}


def extract_dyn_id(url: str) -> str:
    """从动态链接解析 dynId（t.bilibili.com/<id> 或 www.bilibili.com/opus/<id>）。"""
    m = re.search(r"(\d{6,})", url or "")
    if not m:
        raise SystemExit(f"无法从链接解析 dynId: {url!r}")
    return m.group(1)


def build_item(
    kind: str, target: str, reserve_sid: str | None, up_uid: str | None
) -> dict:
    """构造该类抽奖的单条数据项（字段与后端步骤引用保持一致）。"""
    if kind == "others":
        # 步骤 11：navigate 到 {{oth_item.dynamicUrl}}；
        # print 步骤还引用 {{oth_item.up_uid}}，缺失会被严格模板解析拦截
        if not up_uid:
            raise SystemExit("第三方抽奖（--kind others）需要 --up-uid <UP主UID>")
        return {"dynamicUrl": target, "dynId": extract_dyn_id(target), "up_uid": up_uid}
    if kind == "official":
        # 步骤 10：navigate 到 https://www.bilibili.com/opus/{{off_item.dynId}}；
        # print 步骤还引用 {{off_item.sender_uid}}
        if not up_uid:
            raise SystemExit("官方抽奖（--kind official）需要 --up-uid <UP主UID>")
        return {"dynId": extract_dyn_id(target), "sender_uid": up_uid}
    # reserve：步骤 9 的 bili_reserve 需要 reserve_sid + reserve_url
    if not reserve_sid:
        raise SystemExit("预约类抽奖（--kind reserve）需要 --reserve-sid <sid>")
    return {"reserve_sid": reserve_sid, "reserve_url": target}


def fetch_action_detail(
    client: httpx.Client, base_url: str, mid: int, action_id: int
) -> dict:
    """读取动作详情（步骤树 + input_vars 定义），与前端 custom-actions/get 一致。"""
    resp = client.post(
        f"{base_url}{API_PREFIX}/custom-actions/get",
        json={"action_id": action_id},
        headers={"x-bili-mid": str(mid)},
    )
    body = resp.json()
    if body.get("code") != 0:
        raise SystemExit(f"获取动作详情失败: {body}")
    return body.get("data") or {}


_FOLLOW_PLACEHOLDERS = ("up_uid", "sender_uid")


def _strip_follow_block(branch: list) -> tuple[list, int]:
    """移除分支里的「关注」子步骤块。

    关注块 = navigate 到 `space.bilibili.com/{{*_item.up_uid|sender_uid}}/dynamic`
    及其后续的 wait / if_else / click，直到下一个 mark_resource_result 之前。

    单条测试注入的数据里没有 up_uid / sender_uid（它们只存在于爬虫条目中，
    而用户给的动态不在爬虫库内），若保留会把未解析的 `{{...}}` 原样带进 URL。
    关注不在本次测试的功能范围内（AI回复/抄评论/转发/点赞），故整块移除。
    """
    out: list = []
    removed = 0
    i = 0
    while i < len(branch):
        s = branch[i] or {}
        url = str(((s.get("params") or {}) or {}).get("url") or "")
        if s.get("action_id") == "navigate" and any(p in url for p in _FOLLOW_PLACEHOLDERS):
            j = i + 1
            while j < len(branch) and (branch[j] or {}).get("action_id") != "mark_resource_result":
                j += 1
            removed += j - i
            i = j
            continue
        # 递归处理嵌套分支（if_else 的 TrueBranch/FalseBranch、内层 loopBranch）
        p = s.get("params")
        if isinstance(p, dict):
            for key in ("TrueBranch", "FalseBranch", "loopBranch"):
                sub = p.get(key)
                if isinstance(sub, list):
                    new_sub, n = _strip_follow_block(sub)
                    if n:
                        p[key] = new_sub
                        removed += n
        out.append(s)
        i += 1
    return out, removed


# 独立执行时需要剥离的步骤：均依赖工作流执行上下文（workflow_id），
# 通过 /actions/execute 单跑时会失败并中断整个链路
_WORKFLOW_ONLY_STEPS = ("fetch_external_data", "mark_resource_result")

# 步骤 JSON 中承载嵌套子步骤的字段（与 _strip_follow_block 保持一致）
_BRANCH_KEYS = ("TrueBranch", "FalseBranch", "loopBranch")


def _strip_workflow_only_steps(steps: list) -> tuple[list, int]:
    """递归移除仅工作流上下文可用的步骤（任意嵌套层级）。

    mark_resource_result 在 loop 分支的 if_else 里还有嵌套副本，只过滤顶层
    会漏掉；单操作直跑时这些步骤必然失败（缺少 workflow_id），
    并按修复后的失败传播语义中断整条链路，故必须在所有层级剥离。
    """
    out: list = []
    removed = 0
    for s in steps:
        s = s or {}
        if s.get("action_id") in _WORKFLOW_ONLY_STEPS:
            removed += 1
            continue
        p = s.get("params")
        if isinstance(p, dict):
            for key in _BRANCH_KEYS:
                sub = p.get(key)
                if isinstance(sub, list):
                    new_sub, n = _strip_workflow_only_steps(sub)
                    if n:
                        p[key] = new_sub
                        removed += n
        out.append(s)
    return out, removed


def build_payload(
    detail: dict,
    *,
    kind: str,
    target: str,
    reserve_sid: str | None,
    up_uid: str | None,
    overrides: dict,
) -> dict:
    """按前端 body 形态构造请求体：真实的 params.steps + 注入单条数据的 input_vars。"""
    raw_steps = detail.get("steps") or []
    # 递归移除批量拉取 / 资源回写步骤（其余步骤树原样保留，模板/点路径交给后端解析）
    steps, removed = _strip_workflow_only_steps(raw_steps)
    if removed == 0:
        print(
            f"[warn] 未发现 {_WORKFLOW_ONLY_STEPS} 步骤，可能动作结构已变化",
            file=sys.stderr,
        )

    # 移除各 loop 分支里的「关注」块（缺 up_uid/sender_uid，详见 _strip_follow_block）
    for s in steps:
        p = s.get("params") if isinstance(s, dict) else None
        if not isinstance(p, dict):
            continue
        br = p.get("loopBranch")
        if isinstance(br, list):
            new_br, n = _strip_follow_block(br)
            if n:
                p["loopBranch"] = new_br
                print(
                    f"[info] 已从 {s.get('action_id')} 分支移除关注块 {n} 步"
                    "（注入数据无 up_uid/sender_uid）",
                    file=sys.stderr,
                )

    # 动作声明的 input_vars 默认值 + 用户覆盖
    input_vars: dict = {}
    for v in detail.get("input_vars") or []:
        name = (v or {}).get("name")
        if name:
            input_vars[name] = v.get("default")
    input_vars.update({k: v for k, v in overrides.items() if v is not None})

    # 注入「单条数据」：所选类型 1 条，其余两类空列表
    item = build_item(kind, target, reserve_sid, up_uid)
    for k, var in KIND_LIST_VAR.items():
        input_vars[var] = {"items": [item]} if k == kind else {"items": []}

    return {
        "action_id": detail.get("action_id") or DEFAULT_ACTION_ID,
        "params": {"steps": steps},
        "input_vars": input_vars,
        "output_vars": [],
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="在用户指定的单条动态上执行 RPA 抽奖动作（模拟前端真实调用）"
    )
    p.add_argument(
        "--kind",
        required=True,
        choices=sorted(KIND_LIST_VAR),
        help="要测试的抽奖类型：reserve / official / others",
    )
    p.add_argument("--target", required=True, help="指定的动态链接（reserve 为预约活动链接）")
    p.add_argument("--reserve-sid", default=None, help="预约类抽奖的 sid（--kind reserve 必填）")
    p.add_argument(
        "--up-uid",
        default=None,
        help="UP主 UID（--kind others/official 必填：print 步骤模板引用该字段）",
    )
    p.add_argument("--action-id", type=int, default=DEFAULT_ACTION_ID)
    p.add_argument("--browser-id", type=int, default=DEFAULT_BROWSER_ID)
    p.add_argument("--mid", type=int, default=DEFAULT_MID, help="鉴权用 x-bili-mid")
    p.add_argument("--base-url", default=DEFAULT_BASE_URL)
    p.add_argument("--timeout", type=float, default=600.0)
    p.add_argument("--dry-run", action="store_true", help="只打印将发送的 JSON，不真正执行")
    # 动作 input_vars 覆盖
    p.add_argument("--forward-text", default=None)
    p.add_argument("--comment-mode", default=None, choices=[None, "llm", "copy"])
    p.add_argument("--llm-server-url", default=None)
    p.add_argument("--llm-api-key", default=None)
    p.add_argument("--llm-model", default=None)
    p.add_argument("--llm-system-prompt", default=None)
    p.add_argument("--comment-like", dest="comment_like", action="store_true", default=None)
    p.add_argument("--no-comment-like", dest="comment_like", action="store_false")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    overrides = {
        "forward_text": args.forward_text,
        "comment_mode": args.comment_mode,
        "llm_server_url": args.llm_server_url,
        "llm_api_key": args.llm_api_key,
        "llm_model": args.llm_model,
        "llm_system_prompt": args.llm_system_prompt,
        "comment_like": args.comment_like,
    }
    url = f"{args.base_url}{API_PREFIX}/actions/execute"
    headers = {"x-bili-mid": str(args.mid)}

    with httpx.Client(timeout=args.timeout) as client:
        detail = fetch_action_detail(client, args.base_url, args.mid, args.action_id)
        payload = build_payload(
            detail,
            kind=args.kind,
            target=args.target,
            reserve_sid=args.reserve_sid,
            up_uid=args.up_uid,
            overrides=overrides,
        )
        print(f"[kind={args.kind}] target={args.target}")
        print(f"[endpoint] POST {url}?browser_id={args.browser_id}")
        print(f"[payload] steps={len(payload['params']['steps'])} "
              f"comment_mode={payload['input_vars'].get('comment_mode')} "
              f"comment_like={payload['input_vars'].get('comment_like')}")

        if args.dry_run:
            print("[payload-json]")
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            print("\n[dry-run] 未执行；确认无误后去掉 --dry-run 再运行。")
            return

        resp = client.post(
            url, params={"browser_id": args.browser_id}, json=payload, headers=headers
        )
        body = resp.json()
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        print(f"[response] code={body.get('code')} msg={body.get('msg')}")
        if data:
            print("  success={} error={!r} execution_time={}".format(
                data.get("success"), data.get("error"), data.get("execution_time")))
            logs = data.get("logs") or []
            print(f"  logs={len(logs)} 条，末 25 条：")
            for line in logs[-25:]:
                print("   |", line)
        else:
            print("  data:", json.dumps(body.get("data"), ensure_ascii=False)[:500])
        if body.get("code") != 0:
            sys.exit(1)


if __name__ == "__main__":
    main()