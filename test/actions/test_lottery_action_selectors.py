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
ACTION_JSON = PROJECT_ROOT / "action_104682832_参与B站抽奖.json"

#: 已失效的选择器：任何一处出现都说明回归了
STALE_SELECTORS = (
    ".header-avatar-wrap--name",
    ".h-name",
)

#: 空间页昵称节点（当前 B 站 fresh-space 页面实际结构）
SPACE_NICKNAME_SELECTOR = ".upinfo-detail__top .nickname"

#: 真实页面验证开关：**默认开启**（选择器只有打到真实页面才算验证过）；
#: 纯离线环境用 RPA_SELECTOR_E2E=0 关闭。
E2E_ENABLED = os.getenv("RPA_SELECTOR_E2E", "1") != "0"
#: 验证用空间页（公开主页即可，不依赖登录态）
SPACE_URL = os.getenv("RPA_SELECTOR_SPACE_URL", "https://space.bilibili.com/149416208")


def _chromium_executable() -> str | None:
    """优先复用已安装的 chromium：playwright 版本升级后期望的内核目录可能尚未下载，
    直接指定现成二进制可以避免为了跑一次校验去 `playwright install`。"""
    custom = os.getenv("RPA_SELECTOR_CHROMIUM")
    if custom and Path(custom).exists():
        return custom
    candidates = sorted(
        Path.home().glob(".cache/ms-playwright/chromium-*/chrome-linux64/chrome")
    )
    return str(candidates[-1]) if candidates else None


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
        """点赞是「已参与」的判据，必须排在评论 / 关注之后、mark 之前

        历史问题：点赞原本排在评论之前，评论失败时点赞已经生效，下一轮
        `already_liked=true` 直接判「已参与」并跳过 → 该内容永远补不上评论。
        """
        # 只有「官方转发 / 第三方评论」两个循环有 page_ok → 已点赞 → 参与 的三级分支
        exec_branches = []
        for step in action_data["steps"]:
            if step.get("action_id") != "loop":
                continue
            body = (step.get("params") or {}).get("loopBranch") or []
            page_ok_if = body[3] if len(body) > 3 else None
            if not page_ok_if or (page_ok_if.get("params") or {}).get("condition") is None:
                continue
            false_branch = (page_ok_if["params"]).get("FalseBranch") or []
            liked_if = false_branch[1] if len(false_branch) > 1 else None
            branch = ((liked_if or {}).get("params") or {}).get("FalseBranch")
            if branch:
                exec_branches.append(branch)
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
        """
        loop = [
            step
            for step in action_data["steps"]
            if step.get("action_id") == "loop"
            and "others" in str((step.get("params") or {}).get("loop_items_var", ""))
        ]
        assert len(loop) == 1, len(loop)
        body = loop[0]["params"]["loopBranch"]
        exec_branch = body[3]["params"]["FalseBranch"][1]["params"]["FalseBranch"]

        gates = [
            step
            for step in exec_branch
            if step.get("action_id") == "if_else"
            and "self_nickname" in str((step.get("params") or {}).get("expression", ""))
        ]
        assert len(gates) == 1, "缺少「评论作者是否为自己」的表达式闸门"

        gate_params = gates[0]["params"]
        true_ids = [s.get("action_id") for s in gate_params.get("TrueBranch") or []]
        false_ids = [s.get("action_id") for s in gate_params.get("FalseBranch") or []]

        # 评论作者选择器必须来自实测（bili-comments 的 open shadow DOM）
        gate_index = exec_branch.index(gates[0])
        pre_gate = exec_branch[gate_index - 2:gate_index]
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

    def test_skip_and_fail_marked_correctly(self, action_data):
        """参与记录：已点赞=终态跳过（去重），页面未就绪=失败可重试

        去重口径（`fetch_external_data.dedupe`）：success / skipped 不再捞起，
        failed 会重试。页面没打开根本没参与，若记成成功就会被永久跳过。
        """
        for step in action_data["steps"]:
            if step.get("action_id") != "loop":
                continue
            body = (step.get("params") or {}).get("loopBranch") or []
            if len(body) < 4 or body[3].get("action_id") != "if_else":
                continue
            if_else = body[3]["params"]
            inner = if_else["FalseBranch"][1]["params"]

            page_fail = if_else["TrueBranch"][0]["params"]
            assert page_fail.get("success") is False, "页面未就绪必须记为失败（可重试）"
            assert page_fail.get("status") == "failed"
            assert page_fail.get("fail_reason")

            liked = inner["TrueBranch"][0]["params"]
            assert liked.get("success") is True, "已点赞视为已参与，必须进记录避免重复参加"
            assert liked.get("status") == "skipped"

    def test_llm_failure_falls_back_to_copy_comment(self, action_data):
        """LLM 生成评论失败时要回退到「抄评论」（random_pick），不能整轮中断"""
        loop = [
            step
            for step in action_data["steps"]
            if step.get("action_id") == "loop"
            and "others" in str((step.get("params") or {}).get("loop_items_var", ""))
        ][0]
        fb = loop["params"]["loopBranch"][3]["params"]["FalseBranch"][1]["params"]["FalseBranch"]

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
        ] == ["random_pick"], "兜底要回退到抄评论"

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

        for step in action_data["steps"]:
            if step.get("action_id") != "loop":
                continue
            body = (step.get("params") or {}).get("loopBranch") or []
            if len(body) < 4 or body[3].get("action_id") != "if_else":
                continue
            inner_fb = body[3]["params"]["FalseBranch"]
            if len(inner_fb) < 2:
                continue
            branch = (inner_fb[1].get("params") or {}).get("FalseBranch")
            if not branch:
                continue

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
    async def test_real_page_selectors(self):
        """一次打开空间页，验证：昵称选择器有命中、失效选择器 0 命中、头部面板可命中"""
        from playwright.async_api import async_playwright

        executable = _chromium_executable()
        if not executable:
            pytest.skip("未找到可用的 chromium（可设置 RPA_SELECTOR_CHROMIUM 指定路径）")

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                executable_path=executable,
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
