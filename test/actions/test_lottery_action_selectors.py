"""「参与B站抽奖」动作：选择器与配置防回归测试

背景（2026-10-21）：
    - `.header-avatar-wrap--name` 在当前 B 站头部（`bili-header.umd.js`）中**不存在**，
      只有 `.header-avatar-wrap--container`；昵称实际在
      `.header-avatar-wrap .avatar-panel-popover .nickname-item`（需要 hover 才渲染）。
    - 空间页昵称不是 `.h-name`（全站 bundle / CSS 中均为 0 次），
      而是 `.upinfo-detail__top .nickname`。
    这两处写错曾导致 `步骤 4/19 失败：get_text ... .header-avatar-wrap--name`、
    以及空间页昵称 `wait .h-name` 超时把运行打断。这里把选择器口径与关键配置固化成测试。

运行方式：
    # 默认就包含真实页面校验（启动 chromium 访问 B 站空间页，需要外网）
    .venv/bin/python -m pytest test/actions/test_lottery_action_selectors.py -q

    # 纯离线（只跑静态检查，跳过真实页面）
    RPA_SELECTOR_E2E=0 .venv/bin/python -m pytest test/actions/test_lottery_action_selectors.py -q

    # 指定浏览器内核 / 验证页
    RPA_SELECTOR_CHROMIUM=/path/to/chrome RPA_SELECTOR_SPACE_URL=... pytest ...

浏览器可执行文件由 test/conftest.py 统一解析：未指定 RPA_SELECTOR_CHROMIUM 时，
自动复用 `~/.cache/ms-playwright/` 下已下载的最新 chromium（不必为跑测试重装内核）。
"""

import json
import os
from pathlib import Path

import pytest

from app.models.execution.action_params import (
    _ensure_action_type,
    workflow_step_adapter,
)
from app.services.execution.pipeline import PipelineBuilder

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ACTION_JSON = PROJECT_ROOT / "actions" / "action_104682832_参与B站抽奖.json"

#: 已失效的选择器：任何一处出现都说明回归了
STALE_SELECTORS = (
    ".header-avatar-wrap--name",
    ".h-name",
)

#: 空间页昵称节点（当前 B 站 fresh-space 页面实际结构）
SPACE_NICKNAME_SELECTOR = ".upinfo-detail__top .nickname"


def _flatten_steps(branch):
    """展开步骤（含嵌套 TrueBranch / FalseBranch 与步骤级 on_error_branch）。"""
    out = []
    for step in branch or []:
        out.append(step)
        params = step.get("params") or {}
        for key in ("TrueBranch", "FalseBranch"):
            out.extend(_flatten_steps(params.get(key)))
        # 步骤级错误分支（on_error_branch 是步骤字段，不在 params 里）
        out.extend(_flatten_steps(step.get("on_error_branch")))
    return out


def _others_exec_branch(action_data: dict) -> list:
    """第三方评论抽奖循环的「单条执行流程」。

    结构说明：`loopBranch[3]` 是 page_ok 闸门，它的 FalseBranch 就是完整执行流程。
    2026-10-24 起删除了「已点赞即已参与」的跳过分支（`already_liked` → skipped），
    所以这里不再有 `FalseBranch[1].params.FalseBranch` 这一层嵌套。
    """
    loop = [
        step
        for step in action_data["steps"]
        if step.get("action_id") == "loop"
        and "others" in str((step.get("params") or {}).get("loop_items_var", ""))
    ][0]
    return loop["params"]["loopBranch"][3]["params"]["FalseBranch"]


def _exec_branches(action_data: dict) -> list[list]:
    """所有「有 page_ok 闸门」的循环（官方转发 / 第三方评论）的单条执行流程。"""
    branches = []
    for step in action_data["steps"]:
        if step.get("action_id") != "loop":
            continue
        body = (step.get("params") or {}).get("loopBranch") or []
        page_ok_if = body[3] if len(body) > 3 else None
        if not page_ok_if or page_ok_if.get("action_id") != "if_else":
            continue
        if (page_ok_if.get("params") or {}).get("condition") is None:
            continue
        branch = (page_ok_if["params"] or {}).get("FalseBranch") or []
        if branch:
            branches.append(branch)
    return branches

#: 真实页面验证开关：**默认开启**（选择器只有打到真实页面才算验证过）；
#: 纯离线环境用 RPA_SELECTOR_E2E=0 关闭。
E2E_ENABLED = os.getenv("RPA_SELECTOR_E2E", "1") != "0"
#: 验证用空间页（公开主页即可，不依赖登录态）
SPACE_URL = os.getenv("RPA_SELECTOR_SPACE_URL", "https://space.bilibili.com/149416208")


@pytest.fixture(scope="module")
def action_data() -> dict:
    """动作 JSON（JSON 是动作的可读源头，与数据库 steps 保持一致）"""
    assert ACTION_JSON.exists(), f"动作 JSON 不存在: {ACTION_JSON}"
    return json.loads(ACTION_JSON.read_text(encoding="utf-8"))


def _iter_steps(steps, path=""):
    """递归遍历步骤树（含 loopBranch / TrueBranch / FalseBranch）"""
    for index, step in enumerate(steps or []):
        current = f"{path}[{index}]{step.get('action_id')}"
        yield current, step
        params = step.get("params") or {}
        for key in ("loopBranch", "TrueBranch", "FalseBranch"):
            child = step.get(key) or params.get(key)
            if child:
                yield from _iter_steps(
                    child if isinstance(child, list) else child.get("steps", []),
                    f"{current}/{key}",
                )


class TestSelectorsStatic:
    """静态校验：选择器 / 模板 / 可编译性（无网络、无浏览器）"""

    def test_no_stale_selectors(self, action_data):
        """不得再出现已失效的选择器（.h-name / .header-avatar-wrap--name）"""
        raw = json.dumps(action_data, ensure_ascii=False)
        for stale in STALE_SELECTORS:
            assert stale not in raw, (
                f"动作 JSON 中仍存在失效选择器 {stale}："
                "当前 B 站前端已无该节点，运行必然取不到元素"
            )

    def test_space_nickname_selector_used(self, action_data):
        """空间页昵称取值必须用 .upinfo-detail__top .nickname（wait + get_text 各一处）"""
        selectors = [
            (step.get("params") or {}).get("selector")
            for _, step in _iter_steps(action_data["steps"])
        ]
        hits = [s for s in selectors if s == SPACE_NICKNAME_SELECTOR]
        assert len(hits) == 2, (
            f"期望 wait + get_text 各一处 {SPACE_NICKNAME_SELECTOR}，实际 {len(hits)} 处"
        )

    def test_login_detection_still_waits_avatar_wrap(self, action_data):
        """登录检测仍以 `.header-avatar-wrap`（头像容器，确实存在）为准"""
        steps = list(_iter_steps(action_data["steps"]))
        waits = [
            (path, step)
            for path, step in steps
            if step.get("action_id") == "wait"
            and (step.get("params") or {}).get("selector") == ".header-avatar-wrap"
        ]
        assert waits, "登录检测缺少 `.header-avatar-wrap` 的 wait（头像容器是登录态判据）"
        # 该 wait 必须把结果写入 is_login，供循环 break_condition 使用
        assert any(
            "is_login" in (step.get("output_vars") or []) for _, step in waits
        ), "登录检测的 wait 需要 output_vars 包含 is_login"

    def test_forward_without_text_and_no_comment_like(self, action_data):
        """转发不再填文案；comment_like 开关已移除"""
        raw = json.dumps(action_data, ensure_ascii=False)
        assert "forward_text" not in raw, "转发文案变量应已移除（直接转发）"
        assert "comment_like" not in raw, "comment_like 开关应已移除（点赞改为固定执行）"

    def test_only_print_steps_record_logs(self, action_data):
        """只有 print 步骤可以带 `log`：先打印，才允许记日志

        约定（2026-10-21）：打印与日志是两件事——打印的不一定落日志，
        但落日志的内容必须先打印出来，便于人直接在运行消息里看到。
        """
        for path, step in _iter_steps(action_data["steps"]):
            params = step.get("params") or {}
            if params.get("log"):
                assert step.get("action_id") == "print", (
                    f"{path} 是非 print 步骤却带了 log —— 只记日志不打印，违反约定"
                )

    def test_each_loop_has_round_result_print(self, action_data):
        """三个抽奖循环的每个出口都要有「结果」打印，保证每轮必打一条"""
        loops = [
            step
            for step in action_data["steps"]
            if step.get("action_id") == "loop"
            and (step.get("params") or {}).get("loop_items_var")
        ]
        assert len(loops) == 3, f"期望 3 个抽奖循环，实际 {len(loops)}"

        for loop in loops:
            messages: list[str] = []

            def collect(nodes, messages=messages):
                for step in nodes or []:
                    params = step.get("params") or {}
                    if step.get("action_id") == "print":
                        messages.append(str(params.get("message", "")))
                    for key in ("loopBranch", "TrueBranch", "FalseBranch"):
                        collect(params.get(key), messages)

            collect(loop["params"]["loopBranch"])
            assert any("结果" in message for message in messages), (
                f"循环 {loop['params'].get('loop_items_var')} 缺少每轮结果打印"
            )

    def test_like_is_last_participation_step(self, action_data):
        """点赞必须排在评论 / 关注 / 转发之后、mark 之前

        点赞是「参与动作」里最容易被误当成参与凭证的一步（历史上甚至因此写出
        「already_liked → 已参与」的错误判定），所以顺序上必须最后做：
        前面的评论 / 转发没成功时，不能留下一个已点赞的状态。
        """
        exec_branches = _exec_branches(action_data)
        assert len(exec_branches) == 2, f"期望 2 个参与分支，实际 {len(exec_branches)}"

        def flatten(nodes):
            """前序遍历（含 if_else / loop 的子分支），近似执行顺序"""
            out = []
            for step in nodes or []:
                out.append(step)
                params = step.get("params") or {}
                for key in ("TrueBranch", "FalseBranch", "loopBranch"):
                    out.extend(flatten(params.get(key)))
            return out

        for exec_branch in exec_branches:
            flat = flatten(exec_branch)
            selectors = [((s.get("params") or {}).get("selector")) for s in flat]
            action_ids = [s.get("action_id") for s in flat]

            like_index = selectors.index(".side-toolbar__action.like:not(.is-active)")
            mark_index = action_ids.index("mark_resource_result")

            assert like_index < mark_index, "点赞必须在 mark_resource_result 之前"
            if "bili-comments #commentbox #pub button" in selectors:
                comment_index = selectors.index("bili-comments #commentbox #pub button")
                assert like_index > comment_index, "点赞必须排在上一条评论发布之后"

    def test_comment_verified_by_author_gate(self, action_data):
        """第三方评论：发评论后按「评论区作者是否包含自己」决定是否点赞/标记

        评论失败时不能留下点赞（点赞=已参与判据），因此点赞与 mark 都必须
        挂在表达式闸门的 True 分支里，False 分支只打印告警。

        校验失败时最多重试 3 次（刷新页面重新读评论区），重试闸门的 False 分支
        必须包含 navigate 刷新，且不得出现点赞 / mark。
        """
        exec_branch = _others_exec_branch(action_data)

        gates = [
            step
            for step in _flatten_steps(exec_branch)
            if step.get("action_id") == "if_else"
            and "self_nickname" in str((step.get("params") or {}).get("expression", ""))
        ]
        assert len(gates) == 3, "应有 1 个结论闸门 + 2 个重试闸门（最多校验 3 次）"

        # 结论闸门 = True 分支（含嵌套，转发确认闸门在里面）里带 mark_resource_result 的那个
        final_gates = [
            g
            for g in gates
            if any(
                s.get("action_id") == "mark_resource_result"
                for s in _flatten_steps((g.get("params") or {}).get("TrueBranch") or [])
            )
        ]
        assert len(final_gates) == 1, "缺少结论闸门（校验通过后标记已参与）"
        gate = final_gates[0]
        gate_params = gate["params"]
        true_ids = [
            s.get("action_id")
            for s in _flatten_steps(gate_params.get("TrueBranch") or [])
        ]
        false_ids = [s.get("action_id") for s in gate_params.get("FalseBranch") or []]

        # 评论作者选择器必须来自实测（bili-comments 的 open shadow DOM）
        gate_index = exec_branch.index(gate)
        pre_gate = [
            s
            for s in exec_branch[:gate_index]
            if s.get("action_id") in ("wait", "get_text")
        ][-2:]
        assert [s.get("params", {}).get("selector") for s in pre_gate] == [
            "bili-comment-thread-renderer",
            'a[href*="space.bilibili.com"]:not(#user-avatar)',
        ], "评论作者/条目的选择器被改动，需重新用真实页面核对"
        assert (pre_gate[1].get("params") or {}).get("item_selector") == "bili-comment-thread-renderer"

        assert "mark_resource_result" in true_ids, "校验通过后才允许标记已参与"
        assert "mark_resource_result" not in false_ids, "校验失败不得标记已参与"
        like_in_false = any(
            (s.get("params") or {}).get("selector") == ".side-toolbar__action.like:not(.is-active)"
            for s in gate_params.get("FalseBranch") or []
        )
        assert not like_in_false, "校验失败不得点赞（点赞是已参与判据）"
        # 失败分支：先探测验证码，再打印告警；不得点赞 / 不得标记
        assert "print" in false_ids, "校验失败要有告警打印"
        assert "mark_resource_result" not in false_ids

        # 重试闸门：False 分支必须刷新页面重读评论区，且绝不能点赞/标记
        retry_gates = [g for g in gates if g is not gate]
        for retry in retry_gates:
            retry_false = (retry.get("params") or {}).get("FalseBranch") or []
            retry_ids = [s.get("action_id") for s in retry_false]
            assert "navigate" in retry_ids, "校验未命中要刷新页面重试"
            assert "mark_resource_result" not in retry_ids
            assert "bili-comments bili-comment-thread-renderer" in [
                (s.get("params") or {}).get("selector") for s in retry_false
            ], "刷新后必须等评论区渲染再读取作者"

    def test_skip_and_fail_marked_correctly(self, action_data):
        """参与记录口径：页面未就绪=失败可重试；「已点赞」不得被当成已参与

        去重口径（`fetch_external_data.dedupe`）：success / skipped 不再捞起，
        failed 会重试。

        历史问题（2026-10-24 修复）：曾经的 `already_liked=true → status=skipped`
        会把「只点过赞、从没转发/评论」的动态永久写死成已参与，之后每轮取数都被
        自己挡掉 —— 表现就是「循环都在跑，但什么都不做，也不报错」。
        """
        for step in action_data["steps"]:
            if step.get("action_id") != "loop":
                continue
            body = (step.get("params") or {}).get("loopBranch") or []
            if len(body) < 4 or body[3].get("action_id") != "if_else":
                continue
            if_else = body[3]["params"]

            page_fail = if_else["TrueBranch"][0]["params"]
            assert page_fail.get("success") is False, "页面未就绪必须记为失败（可重试）"
            assert page_fail.get("status") == "failed"
            assert page_fail.get("fail_reason")

            flat = _flatten_steps(if_else["FalseBranch"])
            assert not [
                s
                for s in flat
                if s.get("action_id") == "mark_resource_result"
                and "已点赞" in str((s.get("params") or {}).get("fail_reason") or "")
            ], "已点赞不得被记为已参与（点赞 ≠ 转发/评论）"
            assert not [
                s
                for s in flat
                if s.get("action_id") == "if_else"
                and str(
                    (((s.get("params") or {}).get("condition") or {}).get("condition") or {}).get(
                        "field"
                    )
                )
                == "already_liked"
            ], "不得再按 already_liked 跳过整条参与流程"

    def test_forward_must_be_confirmed_before_mark(self, action_data):
        """转发必须等到分享弹窗关闭（转发生效）后才允许记成功

        历史问题：转发流程末尾无条件 `mark success`，而 `wait state=hidden` 超时
        也算成功（WaitAction 从不失败），等于「点完就算转发成功」。
        现在该 wait 必须产出 `share_closed`，并由 if_else 闸门决定记成功还是记失败。
        """
        branches = _exec_branches(action_data)
        assert len(branches) == 2, len(branches)
        for branch in branches:
            flat = _flatten_steps(branch)
            hidden_waits = [
                s
                for s in flat
                if s.get("action_id") == "wait"
                and (s.get("params") or {}).get("selector") == ".bili-dyn-share__wrap"
                and (s.get("params") or {}).get("state") == "hidden"
            ]
            assert hidden_waits, "缺少「分享弹窗关闭」的等待"
            for wait in hidden_waits:
                assert wait.get("output_vars") == ["share_closed"], (
                    "该 wait 必须把 element_found 产出为 share_closed，否则无法判断转发是否生效"
                )
            gates = [
                s
                for s in flat
                if s.get("action_id") == "if_else"
                and (s.get("params") or {}).get("expression") == "share_closed == true"
            ]
            assert gates, "缺少「转发已生效」闸门"
            for gate in gates:
                params = gate["params"]
                assert [
                    s.get("action_id") for s in params.get("TrueBranch") or []
                ] == ["mark_resource_result"], "转发生效才允许记成功"
                assert [
                    (s.get("params") or {}).get("status")
                    for s in params.get("FalseBranch") or []
                ] == ["failed"], "转发未确认必须记失败（下次重试）"

    def test_llm_failure_falls_back_to_copy_comment(self, action_data):
        """LLM 生成评论失败时要回退到「抄评论」（random_pick），不能整轮中断"""
        fb = _others_exec_branch(action_data)

        gen_index = [
            i
            for i, s in enumerate(fb)
            if s.get("action_id") == "if_else"
            and str(((s.get("params") or {}).get("condition") or {}).get("condition", {}).get("field"))
            == "comment_mode"
        ][0]
        gen_step = fb[gen_index]
        gen = gen_step["params"]
        llm_step = gen["TrueBranch"][0]
        assert llm_step.get("action_id") == "llm"
        assert llm_step.get("on_error") == "continue", "LLM 失败不能中断整轮"
        # 关键：包着 llm 的分支本身也要 continue。
        # 只给 llm 加 continue 时，分支最后一步失败仍会让 if_else 返回失败
        # （"if_else 分支 true 执行失败"），整轮在到达兜底闸门前就断了。
        assert gen_step.get("on_error") == "continue", "评论生成分支失败必须允许继续走兜底"

        fallback = fb[gen_index + 1]
        assert fallback.get("action_id") == "if_else"
        assert fallback["params"].get("expression") == "comment_text != ''"
        false_branch = fallback["params"].get("FalseBranch") or []
        assert [
            s.get("action_id") for s in false_branch
        ] == ["random_pick", "if_else"], "兜底要回退到抄评论，抄不到再用固定短评"
        # 固定短评兜底：抄评论（评论区没加载）为空时不能发空评论
        const_fallback = false_branch[1]
        assert const_fallback["params"].get("expression") == "comment_text == ''"
        pick = (const_fallback["params"].get("TrueBranch") or [])[0]
        assert pick.get("action_id") == "random_pick"
        assert (pick.get("params") or {}).get("items"), "固定短评兜底必须提供候选列表"

    def test_llm_empty_content_detected(self, action_data):
        """LLM 调用成功但返回空内容要单独判空并记录（否则会带着空评论去发布）"""
        # 按条件字段定位「评论来源」分支，不按固定下标：循环体前面增减 steps 很常见，
        # 硬编码下标会让这条断言在纯 JSON 编排调整后误报（实现没坏，测试先红）。
        exec_branch = _others_exec_branch(action_data)
        gen = next(
            step
            for step in exec_branch
            if step.get("action_id") == "if_else"
            and (
                ((step.get("params") or {}).get("condition") or {}).get("condition") or {}
            ).get("field")
            == "comment_mode"
        )
        assert gen.get("action_id") == "if_else"
        true_branch = (gen.get("params") or {}).get("TrueBranch") or []
        assert true_branch[0].get("action_id") == "llm"
        empty_check = true_branch[1]
        assert empty_check.get("action_id") == "if_else"
        expr = (empty_check.get("params") or {}).get("expression")
        assert expr == "comment_text == '' and llm_reply_model != ''", expr
        log_step = (empty_check["params"].get("TrueBranch") or [])[0]
        assert log_step.get("action_id") == "print"
        assert (log_step.get("params") or {}).get("log", {}).get("enabled") is True

    def test_editor_content_checked_before_publish(self, action_data):
        """发送前要核对评论输入框内容与待发内容一致，不一致则重填并记录"""
        exec_branch = _others_exec_branch(action_data)

        input_index = [
            i
            for i, s in enumerate(exec_branch)
            if s.get("action_id") == "input"
            and (s.get("params") or {}).get("selector") == "bili-comments #commentbox .brt-editor"
        ][0]
        check = exec_branch[input_index + 3]
        assert check.get("action_id") == "if_else"
        assert (check.get("params") or {}).get("expression") == (
            "editor_text != '' and (comment_text in editor_text or editor_text in comment_text)"
        )
        # 输入框内容取自编辑器文本
        reader = exec_branch[input_index + 2]
        assert reader.get("action_id") == "get_text"
        assert (reader.get("params") or {}).get("selector") == "bili-comments #commentbox .brt-editor"
        assert "editor_text" in (reader.get("output_vars") or [])

        refill_ids = [s.get("action_id") for s in check["params"].get("FalseBranch") or []]
        assert "input" in refill_ids, "内容不一致必须重填"
        assert "print" in refill_ids, "内容不一致必须记录日志"
        # 重填后要再核对一次，仍不一致也要留痕
        recheck = [
            s for s in check["params"].get("FalseBranch") or [] if s.get("action_id") == "if_else"
        ][0]
        assert (recheck.get("params") or {}).get("expression") == (
            "editor_text != '' and (comment_text in editor_text or editor_text in comment_text)"
        )
        assert "print" in [s.get("action_id") for s in recheck["params"].get("FalseBranch") or []]

        # 发布按钮必须在核对之后才点
        pub_index = [
            i
            for i, s in enumerate(exec_branch)
            if s.get("action_id") == "click"
            and (s.get("params") or {}).get("selector") == "bili-comments #commentbox #pub button"
        ][0]
        assert pub_index > input_index + 3

    def test_participation_action_order(self, action_data):
        """参与动作执行顺序：关注 → 评论 → 转发 → 点赞

        点赞是「已参与」判据，必须最后做；关注最先做（跳转空间页后再回动态页）。
        官方转发抽奖没有评论，顺序为 关注 → 转发 → 点赞。
        """
        selectors = {
            "关注": ".space-head-follow .space-follow-btn",
            "评论": "bili-comments #commentbox #pub button",
            "转发": ".bili-dyn-share-publishing__action",
            "点赞": ".side-toolbar__action.like",
        }

        def flatten(branch):
            out = []
            for step in branch or []:
                out.append(step)
                params = step.get("params") or {}
                for key in ("TrueBranch", "FalseBranch"):
                    out.extend(flatten(params.get(key)))
            return out

        for branch in _exec_branches(action_data):
            seq = [
                ((s.get("params") or {}).get("selector")) for s in flatten(branch)
            ]
            positions = [
                seq.index(selectors[name])
                for name in ("关注", "评论", "转发", "点赞")
                if selectors[name] in seq
            ]
            assert len(positions) >= 3, positions
            assert positions == sorted(positions), f"动作顺序不对: {positions}"

    def test_steps_compile(self, action_data):
        """步骤能通过参数模型归一化，并编译成 Pipeline"""
        steps = [
            workflow_step_adapter.validate_python(_ensure_action_type(step))
            for step in action_data["steps"]
        ]
        pipeline = PipelineBuilder.build(steps)
        assert len(pipeline.steps) == len(action_data["steps"])


    def test_llm_failure_sends_deduped_alerts(self, action_data):
        """LLM 失败要按错误类型发通知（Key 失效 / 额度耗尽 / 限流），且按天去重

        Key 失效、免费额度用尽这类问题不会让工作流失败（走兜底），
        如果不主动通知用户，配置坏了也毫无感知。
        """
        exec_branch = _others_exec_branch(action_data)

        notifies = [
            s for s in _flatten_steps(exec_branch) if s.get("action_id") == "notify"
        ]
        assert len(notifies) >= 3, "Key 失效 / 额度耗尽 / 限流 三种告警都要有"

        keys = {(n.get("params") or {}).get("dedupe_key") for n in notifies}
        assert {"llm_key_invalid", "llm_quota_exhausted", "llm_rate_limited"} <= keys
        for n in notifies:
            params = n.get("params") or {}
            assert params.get("once_per_day") is True, "循环里必须按天去重，否则会刷屏"
            assert params.get("level") in (1, 2, 3)
            assert n.get("on_error") == "continue", "通知失败不能中断本轮"


    def test_fallbacks_items_schema_matches_backend_model(self, action_data):
        """llm_fallbacks 的 items_schema 必须与后端模型一致（防止结构漂移）

        前端靠这份 schema 渲染结构化编辑器；手写 JSON 一旦和 LLMModelConfig 不一致，
        用户填的值在运行期会被后端拒绝。改动模型后请重新生成此字段。
        """
        from app.models.execution.action_param_models import LLMModelConfig

        var = [
            v for v in action_data.get("input_vars", []) if v.get("name") == "llm_fallbacks"
        ][0]
        expected = LLMModelConfig.model_json_schema()
        expected.pop("$defs", None)
        expected.pop("title", None)

        assert var.get("items_schema") == expected


@pytest.mark.skipif(
    not E2E_ENABLED,
    reason="真实页面校验默认开启；离线环境用 RPA_SELECTOR_E2E=0 关闭",
)
class TestSelectorsOnRealPage:
    """真实页面校验：选择器必须在**当前线上页面**上真的有命中

    用 `--headless=new` 走完整 chromium（Playwright 默认 headless 走 headless-shell，
    本机可能未安装对应版本），避免额外下载内核。
    """

    @pytest.mark.asyncio
    async def test_real_page_selectors(self, chromium_executable: str):
        """一次打开空间页，验证：昵称选择器有命中、失效选择器 0 命中、头部面板可命中

        浏览器可执行文件由 conftest 的 `chromium_executable` fixture 统一解析
        （环境变量 RPA_SELECTOR_CHROMIUM 优先，否则取本地已下载的最新 chromium），
        playwright 版本升级后期望的内核目录可能尚未下载，直接指定现成二进制
        可以避免为了跑一次校验去 `playwright install`。
        """
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                executable_path=chromium_executable,
                headless=False,
                args=["--headless=new", "--disable-blink-features=AutomationControlled"],
            )
            try:
                page = await browser.new_page(locale="zh-CN")
                await page.goto(SPACE_URL, wait_until="domcontentloaded", timeout=45000)
                # 空间页是 SPA：等 banner 区渲染
                await page.wait_for_selector(SPACE_NICKNAME_SELECTOR, timeout=20000)

                # 1) 正选：昵称节点命中且文本非空
                texts = [
                    text.strip()
                    for text in await page.locator(SPACE_NICKNAME_SELECTOR).all_text_contents()
                ]
                assert texts and texts[0], (
                    f"{SPACE_NICKNAME_SELECTOR} 命中 {len(texts)} 条，文本为空"
                )

                # 2) 反证：失效选择器在当前页面必须 0 命中
                for stale in STALE_SELECTORS:
                    assert await page.locator(stale).count() == 0, (
                        f"{stale} 竟然命中了，说明站点结构已变化，需重新核对选择器"
                    )

                # 3) 备用路径：hover 头像后，弹层里的昵称节点能命中（匿名访问时无头像，跳过）
                trigger = page.locator(".header-avatar-wrap").first
                if await trigger.count() > 0:
                    await trigger.hover()
                    await page.wait_for_timeout(1200)  # 弹层 200ms show-delay + 渲染
                    popover_name = page.locator(
                        ".header-avatar-wrap .avatar-panel-popover .nickname-item"
                    )
                    assert await popover_name.count() > 0, (
                        "hover 头像后仍未渲染面板昵称节点（.nickname-item）"
                    )
            finally:
                await browser.close()
